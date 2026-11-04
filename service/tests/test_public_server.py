import os, socket, struct, threading, time, json, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import public_server as ps
from server.protocol import ProtocolError

KEY = b"k" * 40

class Tickets(unittest.TestCase):
    def test_valid(self):
        exp = int(time.time()) + 600
        t = f"4c11ae30ed48.{exp}." + ps.sign(KEY, "4c11ae30ed48", exp)
        self.assertEqual(ps.verify_ticket(KEY, t), "4c11ae30ed48")
    def test_rejects(self):
        now = int(time.time())
        good = lambda d, e: f"{d}.{e}." + ps.sign(KEY, d, e)
        signed = good("4c11", now + 60)
        tampered = signed[:-1] + ("1" if signed[-1] == "0" else "0")
        bad = [None, "", "a.b.c", good("4c11", now - 1), good("4c11", now + 3600),
               tampered, good("4C11", now + 60),
               f"4c11.{now+60}." + ps.sign(b"x" * 40, "4c11", now + 60)]
        for t in bad:
            with self.assertRaises(ProtocolError):
                ps.verify_ticket(KEY, t)
    def test_key_file_mode(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "k"); open(p, "wb").write(KEY)
            os.chmod(p, 0o644)
            with self.assertRaises(ValueError): ps.load_key(p)
            os.chmod(p, 0o600)
            self.assertEqual(ps.load_key(p), KEY)

if __name__ == "__main__":
    unittest.main()
