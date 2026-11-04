import os
import random
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import frames, live, tv_server
from server.protocol import AUDIO_BYTES, Kind, receive_packet
from server.rate import FramePacer


def channel():
    return live.LiveChannel(next(iter(live.CHANNELS.values())))


def restore(packets):
    raw = []
    for packet in packets:
        if packet[0] != len(raw):
            raise AssertionError("missing or repeated stripe")
        raw.extend(frames.unpack(packet))
    if len(raw) != frames.STRIPES:
        raise AssertionError("incomplete frame")
    return b"".join(raw)


class FullFrames(unittest.TestCase):
    def test_low_budget_keeps_pixels_and_next_frame_repairs_a_dropped_frame(self):
        c = channel()
        c.set_video_rate(40000)
        source = [random.Random(i).randbytes(frames.FRAME_PIXELS) for i in range(3)]
        with patch.object(live, "ENCODE", "full"):
            for i, raw in enumerate(source):
                c._push_video(raw, i * 125)
            packets = list(c.video)
            # No encoding is allowed on the sender's path, even at minimum budget.
            with patch.object(frames, "frame_packets", side_effect=AssertionError("sender encoded")), \
                 patch.object(live.perceptual, "encode_within", side_effect=AssertionError("lossy encode")):
                self.assertEqual(restore(c._delta_encode(packets[0])), source[0])
                # Pretend the device discarded frame 1. Frame 2 still stands alone.
                self.assertEqual(restore(c._delta_encode(packets[2])), source[2])
        self.assertEqual(c.coarse_frames, 0)

    def test_slow_video_compression_does_not_hold_audio_lock(self):
        c = channel()
        c._push_audio(b"audio", 0)
        entered, release, audio_done = threading.Event(), threading.Event(), threading.Event()
        result = []

        def compress(_):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test did not release compressor")
            return [b"packet"]

        def take_audio():
            result.append(c.pop_audio())
            audio_done.set()

        with patch.object(live, "ENCODE", "full"), patch.object(frames, "frame_packets", compress):
            producer = threading.Thread(target=c._push_video, args=(bytes(frames.FRAME_PIXELS), 0))
            reader = threading.Thread(target=take_audio)
            try:
                producer.start()
                self.assertTrue(entered.wait(1))
                reader.start()
                self.assertTrue(audio_done.wait(0.3), "audio waited for video compression")
                self.assertEqual(result[0][0], b"audio")
            finally:
                release.set()
                producer.join(2)
                if reader.ident is not None:
                    reader.join(2)

    def test_budget_wait_keeps_sending_audio_and_complete_video(self):
        c = channel()
        c.fps = 8
        c.calibrate_from_source()
        raw = random.Random(42).randbytes(frames.FRAME_PIXELS)
        server = tv_server.AVServer(None)
        sender, receiver = socket.socketpair()
        sender.setblocking(False)
        receiver.setblocking(False)
        errors = []
        received = []
        audio_arrivals = []
        write_requests = []

        class RecordingSocket:
            def fileno(self):
                return sender.fileno()

            def recv(self, *args):
                return sender.recv(*args)

            def send(self, data):
                write_requests.append(len(data))
                return sender.send(data)

        def run():
            try:
                server._pace_live(RecordingSocket(), c, 1)
            except Exception as error:
                errors.append(error)
            finally:
                sender.close()

        with patch.object(live, "ENCODE", "full"), \
             patch.object(tv_server, "ADAPTIVE", False), \
             patch.object(tv_server, "WRITE_PACING_RATE", 250000), \
             patch.object(tv_server, "WRITE_PACING_CHUNK", 12288), \
             patch.object(frames, "PACKET_TARGET_BYTES", 12288), \
             patch.dict(os.environ, {"TV_RATE_START": "40000"}):
            for i in range(64):
                c._push_video(raw, i * 125)
            for i in range(200):
                c._push_audio(bytes(AUDIO_BYTES), i * 40)
            worker = threading.Thread(target=run)
            timer = threading.Timer(2.0, server.stop.set)
            try:
                worker.start()
                timer.start()
                while True:
                    try:
                        packet = receive_packet(receiver, timeout=4)
                        received.append(packet)
                        if packet.kind == Kind.PCM:
                            audio_arrivals.append(time.monotonic())
                    except EOFError:
                        break
            finally:
                server.stop.set()
                timer.cancel()
                worker.join(3)
                receiver.close()
        self.assertEqual(errors, [])
        self.assertFalse(worker.is_alive())
        # Stripe-aligned protocol packets need not fill the 12 KiB target.
        self.assertGreater(max(write_requests), 8192, "outer sender still capped writes at 4 KiB")
        self.assertLessEqual(max(write_requests), 12288)
        audio = [p for p in received if p.kind == Kind.PCM]
        video = [p for p in received if p.kind == Kind.JPEG]
        starts = [i for i, p in enumerate(video) if not p.flags]
        self.assertGreaterEqual(len(audio), 40, "video budget held up audio")
        self.assertLess(max(b-a for a, b in zip(audio_arrivals, audio_arrivals[1:])),
                        0.2, "paced video starved audio between complete packets")
        self.assertGreaterEqual(len(starts), 1)
        self.assertLessEqual(len(starts), 2, "full frames exceeded the byte budget")
        for lo, hi in zip(starts, starts[1:] + [len(video)]):
            self.assertEqual(restore([p.payload for p in video[lo:hi]]), raw)
        self.assertEqual([p.seq for p in received], list(range(2, len(received) + 2)))
        self.assertEqual([p.pts_ms for p in audio], list(range(0, len(audio) * 40, 40)))


class FrameBudget(unittest.TestCase):
    def test_rate_change_repays_existing_debt_at_each_rate(self):
        p = FramePacer(100000, 0)
        p.sent(20000, 0)
        self.assertFalse(p.ready(0.1))
        p.set_rate(40000, 0.1)
        self.assertFalse(p.ready(0.3))
        self.assertTrue(p.ready(0.351))

    def test_idle_does_not_accumulate_credit_and_transmission_time_counts(self):
        p = FramePacer(40000, 0)
        self.assertTrue(p.ready(10))
        p.sent(20000, 10)
        self.assertFalse(p.ready(10.1))
        self.assertTrue(p.ready(10.501))
        p.sent(20000, 10.501)
        self.assertFalse(p.ready(10.6))


if __name__ == "__main__":
    unittest.main()
