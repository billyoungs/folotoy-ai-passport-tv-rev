import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import protocol


class Clock:
    def __init__(self):
        self.now = 10.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class WritePacing(unittest.TestCase):
    def test_12k_writes_keep_spacing_and_exact_bytes(self):
        clock = Clock()
        writes = []

        class Socket:
            def send(self, data):
                writes.append((clock.now, bytes(data)))
                return len(data)

        data = bytes(range(256)) * 100
        with patch.object(protocol, "time", clock), patch.object(protocol, "_wait"):
            self.assertTrue(protocol._write_slice(Socket(), memoryview(data), 11,
                                                  protocol.WritePacer(1000000, 12288)))
        self.assertEqual([len(c) for _, c in writes], [12288, 12288, 1024])
        self.assertEqual(b"".join(c for _, c in writes), data)
        for (a, chunk), (b, _) in zip(writes, writes[1:]):
            self.assertAlmostEqual(b-a, len(chunk)/1000000)

    def test_larger_batches_keep_bytes_rate_and_deadline(self):
        clock = Clock()
        chunks = []

        class Socket:
            def send(self, data):
                size = min(2500, len(data))  # Exercise partial sends in a larger batch.
                chunks.append((clock.now, bytes(data[:size])))
                return size

        data = bytes(range(256)) * 100
        with patch.object(protocol, "time", clock), patch.object(protocol, "_wait"):
            self.assertTrue(protocol._write_slice(Socket(), memoryview(data), 11,
                                                  protocol.WritePacer(1000000, 4096)))
        self.assertEqual(b"".join(c for _, c in chunks), data)
        for (a, chunk), (b, _) in zip(chunks, chunks[1:]):
            self.assertGreaterEqual(b-a+1e-12, len(chunk)/1000000)
        self.assertAlmostEqual(chunks[-1][0]-chunks[0][0],
                               (len(data)-len(chunks[-1][1]))/1000000)
        for invalid in (0, -1, 16385):
            with self.assertRaises(ValueError):
                protocol.WritePacer(1000000, invalid)

    def test_partial_writes_preserve_bytes_and_spacing(self):
        clock = Clock()
        chunks = []

        class Socket:
            def send(self, data):
                size = min(700, len(data))
                chunks.append((clock.now, bytes(data[:size])))
                return size

        data = bytes(range(256)) * 32
        pacer = protocol.WritePacer(1000000)
        with patch.object(protocol, "time", clock), patch.object(protocol, "_wait"):
            self.assertTrue(protocol._write_slice(Socket(), memoryview(data), 11, pacer))
        self.assertEqual(b"".join(c for _, c in chunks), data)
        for (a, chunk), (b, _) in zip(chunks, chunks[1:]):
            self.assertGreaterEqual(b-a+1e-12, len(chunk)/1000000)
            self.assertLessEqual(len(chunk), 1200)

    def test_idle_or_socket_stall_never_earns_burst_credit(self):
        clock = Clock()
        pacer = protocol.WritePacer(1000000)
        with patch.object(protocol, "time", clock):
            pacer.sent(1200, clock.now)
            clock.now += 2
            self.assertTrue(pacer.wait(20))
            pacer.sent(1200, clock.now)
            before = clock.now
            self.assertTrue(pacer.wait(20))
            self.assertAlmostEqual(clock.now-before, .0012)

    def test_deadline_stops_before_another_partial_write(self):
        clock = Clock()
        chunks = []

        class Socket:
            def send(self, data):
                chunks.append(bytes(data))
                return len(data)

        with patch.object(protocol, "time", clock), patch.object(protocol, "_wait"):
            self.assertFalse(protocol._write_slice(Socket(), memoryview(b'x'*2400),
                                                   10.0005, protocol.WritePacer(1000000)))
        self.assertEqual(chunks, [b'x'*1200])


if __name__ == '__main__':
    unittest.main()
