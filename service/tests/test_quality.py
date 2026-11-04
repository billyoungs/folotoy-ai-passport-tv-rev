import os,sys,threading,unittest
from unittest.mock import patch,Mock
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import frames,live,quality,shared,public_server,tv_server
from server.protocol import Kind,Packet,json_bytes,ProtocolError
from test_shared import FakeSource

class Profiles(unittest.TestCase):
 def test_explicit_profiles_and_original_ultra_graph(self):
  self.assertIsNone(quality.requested(None))
  self.assertEqual([x.fps for x in quality.PROFILES.values()],[8,15,30])
  self.assertEqual(live.source_graph(30),live.source_graph(30,quality.PROFILES['ultra']))
  audio=live.source_graph(30).split(';')[1]
  for p in quality.PROFILES.values():
   graph=live.source_graph(p.fps,p)
   self.assertEqual(graph.split(';')[1],audio)
   self.assertIn('format=rgb8,showinfo',graph)
   self.assertIn(f'fps={p.fps}',graph)
  for bad in ('',3,{},'../file','auto'):
   with self.assertRaises(ProtocolError):quality.requested(bad)

 def test_source_profile_is_per_instance_and_overrides_fps_without_mutating_environment(self):
  before=dict(os.environ)
  c=live.LiveChannel(next(iter(live.CHANNELS.values())))
  for p in quality.PROFILES.values():
   c.profile=p;self.assertEqual(c.resolve_fps(),p.fps)
   cmd=live.source_command(c.url,1,2,'ffmpeg',fps=c.fps,profile=p)
   self.assertEqual(cmd[cmd.index('-filter_complex')+1],live.source_graph(p.fps,p))
  self.assertEqual(dict(os.environ),before)

 def test_profiles_share_one_live_source_and_legacy_ultra_reuses(self):
  with patch.object(live,'ENCODE','full'):
   pool=shared.SharedPool(factory=FakeSource)
   try:
    key=next(iter(live.CHANNELS));url=live.CHANNELS[key];clients=[]
    for p in quality.PROFILES.values():
     a=pool.channel_factory(key,profile=p)(url);a.build_palette();a.start();clients.append(a)
     b=pool.channel_factory(key,profile=p)(url);b.build_palette();b.start();clients.append(b)
     self.assertIs(a.producer,b.producer)
     self.assertIsNone(a.producer.source.profile)
    self.assertEqual(pool.started,1)
    with patch.object(shared,'FPS',shared.FPS):
     old=pool.channel_factory(key)(url);old.build_palette();old.start()
     self.assertIs(old.producer,clients[-1].producer)
    for c in clients:c.close()
    old.close()
   finally:pool.close()


 def test_profile_frame_selection_and_cache_keep_one_live_content_clock(self):
  from server.protocol import AUDIO_BYTES
  with patch.object(live,'ENCODE','full'):
   pool=shared.SharedPool(factory=FakeSource)
   try:
    channel=next(iter(live.CHANNELS));url=live.CHANNELS[channel];clients=[]
    for p in quality.PROFILES.values():
     for _ in range(2):
      c=pool.channel_factory(channel,profile=p)(url);c.build_palette();c.start();clients.append(c)
    producer=clients[0].producer;producer.source.fps=30
    # The source already has native encoded frames. Derivation is once per
    # content frame/profile, independent of the number of viewers.
    raw=bytes(i%256 for i in range(frames.FRAME_PIXELS))
    native=tuple(frames.frame_packets(raw));audio=bytes(AUDIO_BYTES)
    items=[('audio',i*40.,audio) for i in range(80)]
    items += [('video',i*1000/30,native) for i in range(90)]
    producer.publish(sorted(items,key=lambda x:x[1]))
    self.assertEqual([len(c.video) for c in clients],[24,24,45,45,90,90])
    for a,b in zip(clients[::2],clients[1::2]):
     self.assertIs(a.video[0],b.video[0]);self.assertIs(a.audio[0],b.audio[0])
    self.assertIs(clients[-1].video[0],native)
    self.assertNotEqual(clients[0].video[0],native)
    self.assertEqual(pool.started,1)
    # A different profile joining later starts at the retained live position,
    # rather than reopening the recording at content time zero.
    for n in range(90,300):
     for c in clients:
      while c.audio:c.pop_audio()
      while c.video:c.video.popleft();c.video_content.popleft();c._queued_bytes=0
     producer.publish([('audio',n*1000/30,audio),('video',n*1000/30,native)])
    late=pool.channel_factory(channel,profile=quality.PROFILES['smooth'])(url)
    late.build_palette();late.start();self.assertGreater(late.base,5000)
    self.assertIs(late.producer,producer)
    # A single burst can be longer than the history window. Fan-out must
    # discard derived frames whose native history was already evicted.
    burst=[('audio',10000+i*40.,audio) for i in range(500)]
    burst += [('video',10000+i*1000/30,native) for i in range(600)]
    producer.publish(sorted(burst,key=lambda x:x[1]))
    retained={stamp for stamp,_ in producer.video}
    self.assertTrue(all(stamp in retained for _,stamp in producer.variants))
   finally:pool.close()

 def test_public_request_routes_after_authentication_and_reconnect_keeps_profile(self):
  import time
  key=b'k'*40;device='a';exp=int(time.time())+60
  token=f'{device}.{exp}.'+public_server.sign(key,device,exp)
  pool=Mock();factory=Mock();pool.channel_factory.return_value=factory
  for p in quality.PROFILES.values():
   session=public_server.Session(key,threading.Event(),lambda _:None,shared=pool)
   hello=Packet(Kind.HELLO,0,0,0,json_bytes(dict(version=1,token=token,channel=next(iter(live.CHANNELS)),quality=p.name)))
   with patch('server.public_server.receive_packet',return_value=hello),patch.object(session,'_live_session'):
    session._session(None)
   pool.channel_factory.assert_called_with(next(iter(live.CHANNELS)),loop=False,profile=p)
   self.assertIs(session.profile,p)
   session.channel_name=next(iter(live.CHANNELS));session.make_channel()
   self.assertTrue(factory.called)

if __name__=='__main__':unittest.main()
