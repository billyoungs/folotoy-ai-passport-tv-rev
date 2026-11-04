import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server import frames, overlay, reconnect
from server.protocol import Kind, VIDEO_CONTINUES


class SnowNoticeTests(unittest.TestCase):
    def test_snow_is_monochrome_changes_and_preserves_previous_picture(self):
        original = bytes([13]) * frames.FRAME_PIXELS
        first = overlay.reconnect_frame(original)
        self.assertEqual(original, bytes([13]) * frames.FRAME_PIXELS)
        self.assertEqual(len(first), frames.FRAME_PIXELS)
        self.assertEqual(set(first), {overlay.BLACK, overlay.WHITE})
        self.assertNotEqual(first, overlay.reconnect_frame(original))
        for y in range(0, 20, 2):
            for x in range(0, frames.WIDTH - 1, 2):
                p = y * frames.WIDTH + x
                self.assertEqual(first[p:p + 2], first[p + frames.WIDTH:p + frames.WIDTH + 2])
                self.assertEqual(first[p], first[p + 1])

    def test_black_card_and_white_text_are_legible_without_previous_frame(self):
        with patch.object(overlay.os, 'urandom', side_effect=lambda n: bytes([1]) * n):
            raw = overlay.reconnect_frame(None)
        scale = 1  # Original compact 5x7 pixel letters.
        left = (frames.WIDTH - overlay._text_columns(overlay.NOTICE_TEXT) * scale) // 2
        top = (frames.HEIGHT - 7 * scale) // 2
        self.assertEqual(raw[(top - 1) * frames.WIDTH + left], overlay.BLACK)
        self.assertEqual(raw[top * frames.WIDTH + left], overlay.WHITE)
        with self.assertRaises(ValueError):
            overlay.reconnect_frame(b'wrong geometry')

    def test_notice_roundtrips_as_complete_frame_with_contiguous_wire_state(self):
        server = SimpleNamespace(frame_deadline_s=2.5, video_sent=0)
        wire = reconnect.WireState(seq=19, audio_pts=500, audio_stamp=460, video_stamp=450)
        packets = []
        expected = overlay.reconnect_frame(None)
        with patch.object(reconnect.overlay, 'reconnect_frame', return_value=expected), \
             patch.object(reconnect, 'send_packet', side_effect=lambda c, p, **kw: packets.append(p)):
            shown = reconnect._send_notice(server, object(), 7, wire, None)
        self.assertEqual(shown, expected)
        self.assertEqual([p.seq for p in packets], list(range(19, wire.seq)))
        stripes = []
        for index, packet in enumerate(packets):
            self.assertEqual(packet.kind, Kind.JPEG)
            self.assertEqual(packet.flags, VIDEO_CONTINUES if index else 0)
            self.assertEqual(packet.pts_ms, 460)
            self.assertEqual(packet.payload[0], len(stripes))
            stripes.extend(frames.unpack(packet.payload))
        self.assertEqual(b''.join(stripes), expected)
        self.assertEqual(server.video_sent, len(packets))
        self.assertLess(sum(len(p.payload) for p in packets), 12288)

    def test_animation_keeps_audio_ticks_and_stops_when_source_resumes(self):
        clock = SimpleNamespace(now=0.0)
        channel = SimpleNamespace(shown=None, audio=[0] * 4)
        channel.failure = lambda: None
        channel.video_pending = lambda: clock.now >= 1.06
        events = []
        def wait(*args):
            clock.now += .01
            return True
        server = SimpleNamespace(stop=threading.Event(), make_channel=lambda: self.fail('source still alive'),
                                 _wait_until=wait, frame_deadline_s=2.5, phase='')
        def notice(*args):
            events.append(('notice', clock.now))
            clock.now += .002
            return b'notice'
        with patch.object(reconnect.time, 'monotonic', side_effect=lambda: clock.now), \
             patch.object(reconnect, '_send_notice', side_effect=notice), \
             patch.object(reconnect, '_send_silence', side_effect=lambda *a: events.append(('audio', clock.now))):
            result = reconnect.hold(server, object(), 1, channel, reconnect.WireState(2, 0), 0, 0)
        self.assertIs(result, channel)
        notices = [at for kind, at in events if kind == 'notice']
        audio = [at for kind, at in events if kind == 'audio']
        self.assertGreaterEqual(len(notices), 4)
        self.assertTrue(all(b - a >= .25 for a, b in zip(notices, notices[1:])))
        self.assertLess(max(b - a for a, b in zip(audio, audio[1:])), .04)
        self.assertLess(notices[-1], 1.06)


if __name__ == '__main__':
    unittest.main()
