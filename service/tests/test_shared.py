import collections
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import frames, live, shared, public_server
from server.protocol import AUDIO_BYTES, Packet, Kind, json_bytes, ProtocolError


def wait_for(predicate, timeout=2):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.002)
    raise AssertionError('condition did not become true')


class FakeSource(live.LiveChannel):
    instances = []

    def __init__(self, *args):
        super().__init__(*args)
        self.closed_count = 0
        FakeSource.instances.append(self)

    def start(self):
        self.calibrate_from_source()

    def close(self):
        self.closed_count += 1
        self.stop.set()


class Sharing(unittest.TestCase):
    def setUp(self):
        self.mode = patch.object(live, 'ENCODE', 'full'); self.mode.start()
        self.pools = []
        FakeSource.instances = []
        self.key = next(iter(live.CHANNELS))
        self.url = live.CHANNELS[self.key]
        self.picture = bytes([42])*frames.FRAME_PIXELS
        self.parts = tuple(frames.frame_packets(self.picture))
        self.sound = bytes(AUDIO_BYTES)

    def tearDown(self):
        for pool in self.pools:
            pool.close()
        self.mode.stop()

    def pool(self, **kwargs):
        p = shared.SharedPool(factory=FakeSource, **kwargs)
        self.pools.append(p)
        return p

    def subscriber(self, pool, key=None):
        key = key or self.key
        c = pool.channel_factory(key)(live.CHANNELS[key]); c.build_palette(); c.start()
        return c

    def publish(self, p, start, count):
        items = []
        for n in range(start, start+count):
            items.extend([('audio', n*40., self.sound), ('video', n*40., self.parts)])
        p.publish(items)

    def test_simultaneous_viewers_share_one_encoder_and_identical_payload_objects(self):
        pool = self.pool(); viewers = []; errors = []
        def connect():
            try:viewers.append(self.subscriber(pool))
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=connect) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(pool.started, 1)
        self.assertEqual(len(FakeSource.instances), 1)
        p=viewers[0].producer;self.publish(p,0,100)
        for v in viewers:
            self.assertTrue(v.prebuffered())
            self.assertEqual(v.palette, viewers[0].palette)
            self.assertIs(v.video[0], viewers[0].video[0])
            self.assertIs(v.audio[0], viewers[0].audio[0])
        self.assertEqual(b''.join(s for part in viewers[0].video[0] for s in frames.unpack(part)),self.picture)

    def test_late_join_has_current_content_and_independent_zero_based_clock(self):
        pool=self.pool();a=self.subscriber(pool);self.publish(a.producer,0,100)
        for _ in range(80):a.pop_audio();a.pop_video()
        self.publish(a.producer,100,150)
        b=self.subscriber(pool)
        self.assertGreater(b.base, 5000)
        self.assertEqual(b.pop_audio()[1],0)
        self.assertGreater(a.pop_audio()[1],0)
        frame, stamp=b.pop_video()
        self.assertEqual(stamp,0)
        self.assertEqual(frame,self.parts)

    def test_closing_or_slowing_one_viewer_does_not_stop_or_corrupt_another(self):
        pool=self.pool();slow=self.subscriber(pool);fast=self.subscriber(pool)
        for n in range(live.PCM_QUEUE_CHUNKS+10):
            self.publish(fast.producer,n,1)
            fast.pop_audio();fast.pop_video()
        self.assertIsNotNone(slow.failure())
        self.assertIsNone(fast.failure())
        slow.close();slow.close()
        self.assertFalse(fast.producer.source.stop.is_set())
        self.assertEqual(pool.snapshot()['subscribers'],1)
        self.assertGreaterEqual(fast._queued_bytes,0)

    def test_history_has_time_and_byte_bounds_and_frames_are_not_split(self):
        pool=self.pool(max_bytes=frames.FRAME_BYTES)
        a=self.subscriber(pool);p=a.producer
        self.publish(p,0,500)
        self.assertLessEqual(p.bytes,pool.max_bytes)
        self.assertGreaterEqual(p.audio[0][0],p.audio[-1][0]-pool.history_s*1000)
        for _,parts in p.video:self.assertEqual(parts,self.parts)

    def test_default_buffer_accepts_uncompressible_startup_without_quality_reduction(self):
        pool=self.pool();a=self.subscriber(pool)
        # Deflate can be larger than raw pixels. Three seconds must fit even
        # when every complete frame is close to the uncompressed size.
        large=(bytes(frames.FRAME_BYTES+512),)
        items=[('audio',n*40.,self.sound) for n in range(80)]
        items += [('video',n*1000/30,large) for n in range(96)]
        a.producer.publish(sorted(items,key=lambda x:x[1]))
        self.assertTrue(a.prebuffered())
        self.assertIsNone(a.failure())
        self.assertGreater(a._queued_bytes,4*1024*1024)

    def test_source_failure_replacement_is_single_flight_and_close_is_idempotent(self):
        pool=self.pool();a=self.subscriber(pool);b=self.subscriber(pool);old=a.producer
        old.source.error=live.LiveError('synthetic failure')
        wait_for(lambda:old.error is not None)
        self.assertIsNotNone(a.failure());self.assertIsNotNone(b.failure())
        old.retry_at=0
        c=self.subscriber(pool);d=self.subscriber(pool)
        self.assertIs(c.producer,d.producer)
        self.assertEqual(pool.started,2)
        a.close();b.close()
        self.assertFalse(c.producer.stop.is_set())
        wait_for(lambda:old.source.closed_count==1)

    def test_idle_release_stops_encoder_once_and_pool_shutdown_rejects_new_clients(self):
        pool=self.pool(idle_s=.03);a=self.subscriber(pool);p=a.producer;a.close()
        wait_for(lambda:p.source.closed_count==1)
        self.assertEqual(pool.snapshot()['sources'],0)
        pool.close()
        with self.assertRaises(live.LiveError):self.subscriber(pool)

    def test_cancel_during_acquire_cannot_leave_a_subscription_registered(self):
        pool=self.pool()
        c=pool.channel_factory(self.key)(self.url)
        acquire=pool.acquire
        def cancelled(subscriber):
            subscriber.close()
            return acquire(subscriber)
        with patch.object(pool,'acquire',cancelled):
            with self.assertRaises(live.LiveError):c.start()
        self.assertEqual(pool.snapshot()['subscribers'],0)

    def test_shutdown_during_startup_cleans_up_the_source_after_start_returns(self):
        entered,release=threading.Event(),threading.Event()
        class SlowStart(FakeSource):
            def start(self):
                entered.set();release.wait(2)
                self.calibrate_from_source()
        pool=shared.SharedPool(factory=SlowStart);self.pools.append(pool)
        c=pool.channel_factory(self.key)(self.url)
        errors=[]
        def start():
            try:c.start()
            except live.LiveError:errors.append('cancelled')
        t=threading.Thread(target=start);t.start();self.assertTrue(entered.wait(1))
        # acquire publishes the producer before returning it to start().
        with pool.lock:producer=next(iter(pool.entries.values()))
        producer.request_stop();release.set();t.join(2)
        wait_for(lambda:producer.source.closed_count==1)
        self.assertFalse(t.is_alive())
        self.assertEqual(errors,['cancelled'])

    def test_channel_and_loop_mode_are_isolated_and_no_client_can_choose_a_file(self):
        pool=self.pool();a=self.subscriber(pool)
        with self.assertRaises(ValueError):pool.channel_factory(self.key)('/etc/passwd')
        b=pool.channel_factory(self.key,loop=True)(self.url);b.build_palette();b.start()
        self.assertIsNot(a.producer,b.producer)
        self.assertTrue(b.producer.source.loop_input)
        self.assertFalse(a.producer.source.loop_input)

    def test_configuration_is_opt_in_full_frames_and_fixed_budget_only(self):
        with patch.dict(os.environ,{},clear=True):self.assertEqual(shared.from_env(),(None,set()))
        for env in ({'TV_SHARED_LIVE':'1'}, {'TV_SHARED_LIVE':'1','TV_ADAPTIVE':'0','TV_SHARED_LOOP_CHANNELS':'unknown'}):
            with patch.dict(os.environ,env,clear=True):
                with self.assertRaises(ValueError):shared.from_env()
        with patch.object(live,'ENCODE','delta'):
            with self.assertRaises(ValueError):self.pool()

    def test_authentication_precedes_shared_routing_and_replay_takes_precedence(self):
        key=b'k'*40;device='000000000001';expiry=int(time.time())+60
        token=f'{device}.{expiry}.'+public_server.sign(key,device,expiry)
        pool=Mock();factory=Mock();pool.channel_factory.return_value=factory
        hello=Packet(Kind.HELLO,0,0,0,json_bytes({'version':1,'token':token,'channel':self.key}))
        session=public_server.Session(key,threading.Event(),lambda _:None,shared=pool,shared_loops={self.key})
        with patch('server.public_server.receive_packet',return_value=hello),patch.object(session,'_live_session'):
            session._session(None)
        pool.channel_factory.assert_called_once_with(self.key,loop=True)
        self.assertIs(session.channel_factory,factory)
        pool.reset_mock()
        session=public_server.Session(key,threading.Event(),lambda _:None,shared=pool)
        bad=Packet(Kind.HELLO,0,0,0,json_bytes({'version':1,'token':'invalid','channel':self.key}))
        with patch('server.public_server.receive_packet',return_value=bad):
            with self.assertRaises(ProtocolError):session._session(None)
        pool.channel_factory.assert_not_called()
        replay=Mock();replay.matches.return_value=True
        session=public_server.Session(key,threading.Event(),lambda _:None,replay=replay,shared=pool)
        with patch('server.public_server.receive_packet',return_value=hello),patch.object(session,'_live_session'):
            session._session(None)
        self.assertEqual(session.channel_factory,replay.channel)
        pool.channel_factory.assert_not_called()


if __name__ == '__main__':unittest.main()
