import collections
import os
import random
import socket
import sys
import threading
import unittest
import zlib
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import frames, tv_server
from server.rate import ByteRate


class StreamTuning(unittest.TestCase):
    def test_single_slow_write_does_not_collapse_picture_quality(self):
        rate = ByteRate(12, start=150000, minimum=40000, maximum=500000)
        rate.observe(17000, worst_write_ms=610, frames=1)
        self.assertEqual(rate.rate, 127500)
        for _ in range(20):
            rate.observe(20000, worst_write_ms=400, frames=3)
        self.assertEqual(rate.rate, 40000)

    def test_idle_and_isolated_drop_do_not_reduce_rate(self):
        rate = ByteRate(12, start=150000, minimum=40000, maximum=500000)
        rate.observe(0, worst_write_ms=0)
        rate.observe(10000, worst_write_ms=0, dropped=1, frames=1)
        self.assertEqual(rate.rate, 150000)

    def test_fast_link_recovers_gradually(self):
        rate = ByteRate(12, start=100000, minimum=40000, maximum=500000)
        rate.observe(90000, worst_write_ms=1, frames=12)
        rate.observe(90000, worst_write_ms=1, frames=12)
        self.assertEqual(rate.rate, 105000)

    def test_small_packets_preserve_all_stripes(self):
        raw = random.Random(42).randbytes(frames.FRAME_PIXELS)
        stripes = frames.compress_stripes(raw)
        with patch.object(frames, "PACKET_TARGET_BYTES", 4096):
            packets = frames.pack_stripes(stripes)
        self.assertGreater(len(packets), 1)
        restored = []
        for packet in packets:
            first, count = packet[:2]
            self.assertEqual(first, len(restored))
            self.assertLessEqual(len(packet), 4096)
            lengths = [int.from_bytes(packet[2 + 2*i:4 + 2*i], "big") for i in range(count)]
            offset = 2 + 2 * count
            for length in lengths:
                restored.append(zlib.decompress(packet[offset:offset + length]))
                offset += length
            self.assertEqual(offset, len(packet))
        self.assertEqual(b"".join(restored), raw)

    def test_fill_refreshes_small_changes_within_available_budget(self):
        shown = bytes(frames.FRAME_PIXELS)
        raw = bytearray(shown)
        for stripe in range(frames.STRIPES):
            raw[stripe * frames.STRIPE_PIXELS] = stripe + 1
        raw = bytes(raw)
        target = 10000
        chosen = frames.choose_stripes(raw, shown, tick=0, allowed=target)
        filled = frames.fill_stripes(raw, shown, chosen, target)
        self.assertEqual(sum(bool(part) for part in chosen), 1)
        self.assertEqual(sum(bool(part) for part in filled), frames.STRIPES)
        self.assertLessEqual(frames._wire_size(filled), target)
        self.assertEqual(frames.apply_stripes(raw, shown, filled), raw)

    def test_unwritable_socket_reduces_rate_without_a_write(self):
        class Channel:
            fps = 12
            audio = collections.deque([b"audio"])
            video = collections.deque([b"video"], maxlen=2)
            dropped_video = 0
            session_clock = type("Clock", (), {"diagnostics": lambda self: "test"})()

            def failure(self): return None
            def audio_pending(self): return True
            def video_pending(self): return True
            def has_data(self): return True
            def picture_lag_s(self): return 0
            def set_video_rate(self, rate): self.rate = rate

        sender, receiver = socket.socketpair()
        sender.setblocking(False)
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        try:
            while True:
                sender.send(b"x" * 4096)
        except BlockingIOError:
            pass
        server = tv_server.AVServer(None)
        channel = Channel()
        timer = threading.Timer(1.3, server.stop.set)
        try:
            with patch.object(tv_server, "ADAPTIVE", True), patch.dict(os.environ, {"TV_RATE_START": "150000"}):
                timer.start()
                server._pace_live(sender, channel, 1)
            self.assertLess(channel.rate, 150000)
            self.assertEqual(server.audio_sent, 0)
        finally:
            timer.cancel()
            sender.close()
            receiver.close()


if __name__ == "__main__":
    unittest.main()
