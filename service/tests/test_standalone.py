import contextlib
import io
import tempfile
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import Mock
from server.standalone import PairingSession, main
from server.public_server import Session
from server.protocol import Kind, Packet, ProtocolError, json_bytes, send_packet
from server.live import DEFAULT_CHANNEL

class StandaloneTests(unittest.TestCase):
    def handshake(self, payload, cls=PairingSession):
        session=cls(b'standalone-test-token-1234',threading.Event(),lambda _:None)
        session._live_session=Mock()
        sender,receiver=socket.socketpair()
        with sender,receiver:
            send_packet(sender,Packet(Kind.HELLO,0,0,0,json_bytes(payload)))
            session._session(receiver)
        return session

    def test_pairing_without_account_or_signed_ticket(self):
        s=self.handshake({'version':1,'token':'standalone-test-token-1234','quality':'ultra','channel':''})
        self.assertEqual(s.device,'paired')
        s._live_session.assert_called_once()
        self.assertEqual(s._live_session.call_args.args[1],DEFAULT_CHANNEL)
        self.assertEqual(s.profile.name,'ultra')

    def test_all_explicit_profiles_use_existing_session_path(self):
        for name in ['smooth','standard','ultra']:
            s=self.handshake({'version':1,'token':'standalone-test-token-1234','quality':name})
            self.assertEqual(s.profile.name,name)

    def test_missing_wrong_and_unknown_profiles_rejected(self):
        for extra in [{},{'token':'wrong-test-token-1234'},{'token':'standalone-test-token-1234','quality':'unknown'}]:
            with self.subTest(extra=extra),self.assertRaises(ProtocolError):
                self.handshake({'version':1,**extra})

    def test_token_file_must_fit_device_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'pairing.token';p.write_text('t'*128);p.chmod(0o600)
            error=io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(main(['--token-file',str(p)]),2)
            self.assertNotIn('t'*128,error.getvalue())

    def test_production_ticket_entry_does_not_accept_pairing_token(self):
        with self.assertRaises(ProtocolError):
            self.handshake({'version':1,'token':'standalone-test-token-1234'},cls=Session)

if __name__=='__main__':unittest.main()
