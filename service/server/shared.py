"""One full-frame encoder per source/profile; independent subscriber clocks.

Only immutable encoded payloads are shared. Socket writes, sequence numbers,
timeline mapping and the existing sender pacing stay inside each AVServer.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import threading
import time

from . import frames, live
from .live import LiveChannel, LiveError
from .media import AUDIO_CHUNK_MS, FPS


class SharedPool:
    def __init__(self, *, factory=LiveChannel, history_s=3.5,
                 max_bytes=8 * 1024 * 1024, idle_s=30.0, logger=lambda _: None):
        if live.ENCODE != 'full':
            raise ValueError('shared live requires TV_ENCODE=full')
        if history_s < live.PREBUFFER_SECONDS or max_bytes < frames.FRAME_BYTES:
            raise ValueError('shared history cannot satisfy the startup buffer')
        self.factory, self.history_s, self.max_bytes = factory, history_s, max_bytes
        self.idle_s, self.logger = idle_s, logger
        self.lock = threading.Lock()
        self.entries = {}
        self.closed = False
        self.started = 0
        self._all = set()
        self._wake = threading.Event()
        self._janitor = threading.Thread(target=self._reap, name='shared-idle', daemon=True)
        self._janitor.start()

    def channel_factory(self, channel_id, *, loop=False, profile=None):
        def make(url, ffmpeg='ffmpeg', user_agent=''):
            # Routing stays server-owned and scoped to this exact channel.
            if live.CHANNELS.get(channel_id) != url:
                raise ValueError('shared source does not match selected channel')
            return Subscription(self, channel_id, url, ffmpeg, user_agent, loop, profile)
        return make

    def acquire(self, subscriber):
        key = (subscriber.channel_id, subscriber.url, subscriber.ffmpeg,
               subscriber.user_agent, subscriber.loop, FPS, frames.WIDTH,
               frames.HEIGHT, frames.PACKET_TARGET_BYTES, live.source_graph(FPS))
        retired = None
        with self.lock:
            if self.closed:
                raise LiveError('shared pool closed')
            producer = self.entries.get(key)
            if producer is not None and producer.error is not None and time.monotonic() >= producer.retry_at:
                retired = producer
                producer = None
            if producer is None:
                producer = Producer(self, key, subscriber)
                self.entries[key] = producer
                self._all.add(producer)
                self.started += 1
                producer.thread.start()
            producer.add(subscriber)
        if retired is not None:
            retired.request_stop()
        return producer

    def release(self, subscriber):
        with self.lock:
            producer = subscriber.producer
            if producer is not None:
                producer.remove(subscriber)

    def snapshot(self):
        with self.lock:
            return {'encoders_started': self.started, 'sources': len(self.entries),
                    'subscribers': sum(p.count() for p in self.entries.values())}

    def _reap(self):
        while not self._wake.wait(.1):
            expired = []
            with self.lock:
                now = time.monotonic()
                for key, producer in list(self.entries.items()):
                    if producer.idle(now, self.idle_s):
                        del self.entries[key]
                        expired.append(producer)
                self._all = {p for p in self._all if p.thread.is_alive() or p in self.entries.values()}
            for producer in expired:
                producer.request_stop()

    def close(self):
        with self.lock:
            self.closed = True
            producers = list(self._all)
            self.entries.clear()
        self._wake.set()
        for producer in producers:
            producer.request_stop()
        for producer in producers:
            producer.thread.join(12)
        self._janitor.join(1)


class Producer:
    def __init__(self, pool, key, subscriber):
        self.pool = pool
        self.source = pool.factory(subscriber.url, subscriber.ffmpeg, subscriber.user_agent)
        self.source.loop_input = subscriber.loop
        # Every quality joins the same decoded content, including a looping
        # recording. Starting another file decoder would rewind a quality switch.
        self.ident = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:12]
        self.lock = threading.Lock()
        self.subscribers = set()
        self.audio, self.video = collections.deque(), collections.deque()
        self.variants = {}  # immutable derived frames, bounded by native history
        self.bytes = 0
        self.error = None
        self.retry_at = 0.0
        self.unused_at = time.monotonic()
        self.ready, self.stop = threading.Event(), threading.Event()
        self.started_at = time.monotonic()
        self.last_media = {'audio': None, 'video': None}
        self._close_lock = threading.Lock()
        self._source_closed = False
        self.thread = threading.Thread(target=self._run, name='shared-'+self.ident, daemon=True)

    def add(self, subscriber):
        with self.lock:
            subscriber.producer = self
            self.subscribers.add(subscriber)
            self._seed(subscriber)
            if self.error is not None:
                subscriber.fail(self.error)

    def remove(self, subscriber):
        with self.lock:
            self.subscribers.discard(subscriber)
            if not self.subscribers:
                self.unused_at = time.monotonic()

    def count(self):
        with self.lock:
            return len(self.subscribers)

    def idle(self, now, delay):
        with self.lock:
            return not self.subscribers and now-self.unused_at >= delay

    def _seed(self, subscriber):
        if subscriber.base is not None or not self.audio or not self.video:
            return
        earliest = max(self.audio[0][0], self.video[0][0])
        audio = [x for x in self.audio if x[0] >= earliest]
        if not audio:
            return
        subscriber.base = audio[0][0]
        for stamp, payload in audio:
            subscriber.offer('audio', stamp, payload)
        for stamp, payload in self.video:
            if stamp >= subscriber.base:
                self._offer(subscriber, 'video', stamp, payload)

    def _offer(self, subscriber, kind, stamp, payload):
        if subscriber.stop.is_set() or subscriber.error is not None:
            return
        profile = subscriber.profile
        if kind == 'video' and profile is not None:
            if profile.fps < self.source.fps:
                # Choose frames by content time, never by arrival time. Native
                # frames keep their timestamps; slower profiles do not advance
                # or restart the source clock.
                before = stamp-1000/self.source.fps
                if int((stamp+.1)*profile.fps//1000) == int((before+.1)*profile.fps//1000):
                    return
            if profile.detail_numerator != profile.detail_denominator:
                key = (profile, stamp)
                if key not in self.variants:
                    self.variants[key] = profile.render(payload)
                payload = self.variants[key]
        subscriber.offer(kind, stamp, payload)

    def publish(self, items):
        with self.lock:
            initialized = {s for s in self.subscribers if s.base is not None}
            for kind, stamp, payload in items:
                queue = self.audio if kind == 'audio' else self.video
                queue.append((stamp, payload))
                self.bytes += len(payload) if kind == 'audio' else sum(map(len, payload))
            # Evict by time and by bytes, preserving whole video frames.
            if self.audio and self.video:
                cutoff = min(self.audio[-1][0], self.video[-1][0])-self.pool.history_s*1000
                for kind, queue in (('audio', self.audio), ('video', self.video)):
                    while queue and queue[0][0] < cutoff:
                        self._evict(kind, queue)
            while self.bytes > self.pool.max_bytes and (self.audio or self.video):
                kind = 'audio' if self.audio and (not self.video or self.audio[0][0] <= self.video[0][0]) else 'video'
                self._evict(kind, self.audio if kind == 'audio' else self.video)
            for subscriber in self.subscribers:
                if subscriber in initialized:
                    for kind, stamp, payload in items:
                        if stamp >= subscriber.base:
                            self._offer(subscriber, kind, stamp, payload)
                else:
                    self._seed(subscriber)
            # A source can deliver a batch longer than the retained window.
            # Variants made while feeding that batch must not outlive native
            # frames already evicted before fan-out began.
            retained = {stamp for stamp, _ in self.video}
            for key in [key for key in self.variants if key[1] not in retained]:
                del self.variants[key]

    def _evict(self, kind, queue):
        stamp, payload = queue.popleft()
        if kind == 'video':
            for key in [key for key in self.variants if key[1] == stamp]:
                del self.variants[key]
        self.bytes -= len(payload) if kind == 'audio' else sum(map(len, payload))

    def _run(self):
        self.pool.logger('shared source='+self.ident+' starting')
        try:
            if self.stop.is_set():
                return
            self.source.build_palette()
            self.source.start()
            self.ready.set()
            while not self.stop.is_set():
                error = self.source.failure()
                if error is not None:
                    raise LiveError('shared encoder stopped')
                items = []
                with self.source.lock:
                    while self.source.audio:
                        items.append(('audio', self.source.audio_content.popleft(), self.source.audio.popleft()))
                    while self.source.video:
                        items.append(('video', self.source.video_content.popleft(), tuple(self.source.video.popleft())))
                if items:
                    for kind, _, _ in items:
                        self.last_media[kind] = time.monotonic()
                    self.publish(items)
                else:
                    self.stop.wait(.002)
                now = time.monotonic()
                if any(last is not None and now-last > 5 for last in self.last_media.values()):
                    raise LiveError('shared source stalled')
                if any(last is None for last in self.last_media.values()) and now-self.started_at > live.PREBUFFER_TIMEOUT_S:
                    raise LiveError('shared source has no media')
        except Exception as error:
            with self.lock:
                self.error = LiveError('shared source failed: '+type(error).__name__)
                self.retry_at = time.monotonic()+2
                for subscriber in self.subscribers:
                    subscriber.fail(self.error)
            self.pool.logger('shared source='+self.ident+' failed='+type(error).__name__)
        finally:
            self.ready.set()
            self._close_source()
            self.pool.logger('shared source='+self.ident+' stopped')

    def request_stop(self):
        self.stop.set()
        # Do not close an unstarted source and then let startup launch a child
        # after its one-time cleanup has already run. Startup is bounded; its
        # worker owns cleanup until ready is set.
        if self.ready.is_set():
            self._close_source()

    def _close_source(self):
        with self._close_lock:
            if not self._source_closed:
                self._source_closed = True
                try:
                    self.source.close()
                except Exception as error:
                    self.pool.logger('shared source='+self.ident+' close_error='+type(error).__name__)


class Subscription(LiveChannel):
    def __init__(self, pool, channel_id, url, ffmpeg, user_agent, loop, profile=None):
        super().__init__(url, ffmpeg, user_agent)
        self.lock = threading.RLock()
        self.pool, self.channel_id, self.loop = pool, channel_id, loop
        self.profile = profile
        self.producer = None
        self.base = None
        self._released = False
        self._queued_bytes = 0

    @property
    def full_frames(self):
        return True

    def start(self):
        if self.stop.is_set():
            raise LiveError('shared subscription cancelled')
        producer = self.pool.acquire(self)
        if self.stop.is_set():
            self.pool.release(self)
            raise LiveError('shared subscription cancelled')
        if not producer.ready.wait(live.DECODER_CONNECT_TIMEOUT_S+2):
            raise LiveError('shared encoder startup timed out')
        if self.stop.is_set() or producer.stop.is_set():
            self.pool.release(self)
            raise LiveError('shared subscription cancelled')
        if self.failure() is not None:
            raise LiveError('shared encoder unavailable')
        self.fps = min(self.profile.fps, producer.source.fps) if self.profile is not None else producer.source.fps
        self.calibrate_from_source()

    def offer(self, kind, stamp, payload):
        with self.lock:
            if self.stop.is_set() or self.error is not None:
                return
            queue = self.audio if kind == 'audio' else self.video
            size = len(payload) if kind == 'audio' else sum(map(len, payload))
            # A slow device is detached through its own existing reconnect path.
            # It can never block the producer or change another viewer's quality.
            if len(queue) == queue.maxlen or self._queued_bytes+size > self.pool.max_bytes:
                self.error = LiveError('shared subscriber exceeded its bounded buffer')
                return
            queue.append(payload)
            (self.audio_content if kind == 'audio' else self.video_content).append(stamp)
            self._queued_bytes += size
            if kind == 'audio':
                self.produced_audio += 1
                self._audio_advanced = True
            else:
                self.produced_video += 1
                self._video_advanced = True

    def fail(self, error):
        with self.lock:
            if self.error is None:
                self.error = error

    def pop_audio(self):
        with self.lock:
            result = super().pop_audio()
            if result is not None:
                self._queued_bytes -= len(result[0])
            return result

    def _delta_encode(self, packets):
        self._queued_bytes -= sum(map(len, packets))
        return packets

    def _give_up_frame(self):
        self._queued_bytes -= sum(map(len, self.video[0]))
        super()._give_up_frame()

    def close(self):
        self.stop.set()
        if not self._released:
            self._released = True
            self.pool.release(self)
        with self.lock:
            self.audio.clear(); self.audio_content.clear()
            self.video.clear(); self.video_content.clear()
            self._queued_bytes = 0


def from_env(logger=lambda _: None):
    if os.environ.get('TV_SHARED_LIVE', '0') != '1':
        return None, set()
    if os.environ.get('TV_ADAPTIVE', '1') != '0':
        raise ValueError('shared live requires the fixed playback budget')
    loops = set(filter(None, os.environ.get('TV_SHARED_LOOP_CHANNELS', '').split(',')))
    if not loops <= live.CHANNELS.keys():
        raise ValueError('shared loop channel is not in the channel table')
    return SharedPool(logger=logger), loops
