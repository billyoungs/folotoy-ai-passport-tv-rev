"""Exercise pairing and the unchanged media session over the same real listener."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from server import frames, live, pairing, public_server, tv_server
from server.protocol import Kind, Packet, json_bytes, json_object, receive_packet, send_packet, ProtocolError
from server.standalone import PairingSession

DEVICE = 'aabbccddeeff'
CREDENTIAL = 'a' * 64
NEW_CREDENTIAL = 'b' * 64
KEY = b'standalone-persistent-secret-123456'


class MemoryChannel(live.LiveChannel):
    """Real queue/clock/encoding path, without an external decoder or live URL."""
    @property
    def full_frames(self):
        return True

    def start(self):
        self.calibrate_from_source()
        self.fps = 25
        for n in range(12):
            self._push_audio(bytes(324), n * 40.)
            self._push_video(bytes([42]) * frames.FRAME_PIXELS, n * 40.)

    def prebuffered(self):
        return True

    def close(self):
        self.stop.set()


class MemorySession(PairingSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel_factory = MemoryChannel


class SignedMemorySession(public_server.Session):
    def __init__(self, *args):
        super().__init__(*args)
        self.channel_factory = MemoryChannel


class PairingSockets(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tv-public-pairing-')
        self.root = Path(self.temporary.name)
        for name, value in [('pairing.code', b'A1B2\n'), ('pairing.token', KEY + b'\n')]:
            p = self.root / name
            p.write_bytes(value)
            p.chmod(0o600)
        self.logs = []
        self.start()

    def start(self, **overrides):
        self.backend = pairing.PairingBackend(
            tv_server.load_token(self.root / 'pairing.token'),
            self.root / 'pairing.code', self.root / 'paired-devices.json', **overrides)
        ready = threading.Event()

        def log(message):
            self.logs.append(message)
            if 'listening on' in message:
                ready.set()

        self.server = public_server.PublicServer(KEY, '127.0.0.1', 0, logger=log,
                                                session_factory=partial(MemorySession, pairing=self.backend),
                                                http_handler=self.backend.handle)
        self.thread = threading.Thread(target=self.server.serve, daemon=True)
        self.thread.start()
        self.assertTrue(ready.wait(3), 'listener did not start')
        self.port = self.server.port

    def stop(self):
        self.server.stop.set()
        self.thread.join(7)
        self.assertFalse(self.thread.is_alive())

    def tearDown(self):
        self.stop()
        self.temporary.cleanup()

    def request(self, action='activate', credential=CREDENTIAL, device=DEVICE,
                code='a1b2', host=None, body=None):
        if body is None:
            body = {'device': device}
            if action == 'activate':
                body['code'] = code
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=4)
        try:
            connection.request('POST', '/social/tv/' + action, json.dumps(body),
                               {'Authorization': 'Bearer ' + credential,
                                'Host': host or f'TV.Example.com:{self.port}',
                                'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def media(self, token):
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as connection:
            send_packet(connection, Packet(Kind.HELLO, 0, 0, 0,
                        json_bytes({'version': 1, 'token': token, 'channel': '', 'quality': 'smooth'})))
            config = receive_packet(connection)
            self.assertEqual(config.kind, Kind.CONFIG)
            self.assertEqual(config.seq, 0)
            self.assertEqual(json_object(config.payload)['quality'], 'smooth')
            palette = receive_packet(connection)
            self.assertEqual((palette.kind, palette.seq), (Kind.PALETTE, 1))
            kinds = set()
            for seq in range(2, 22):
                packet = receive_packet(connection)
                self.assertEqual(packet.session, config.session)
                self.assertEqual(packet.seq, seq)
                kinds.add(packet.kind)
                if {Kind.PCM, Kind.JPEG} <= kinds:
                    break
            self.assertTrue({Kind.PCM, Kind.JPEG} <= kinds)
            send_packet(connection, Packet(Kind.END, config.session, 0, 0))

    def test_reusable_code_restart_permanent_ticket_and_legacy_media(self):
        status, first = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(set(first), {'host', 'port', 'ticket'})
        self.assertEqual((first['host'], first['port']), ('tv.example.com', self.port))
        self.assertLessEqual(len(first['ticket']), 127)
        second_status, second = self.request(device='001122334455', credential=NEW_CREDENTIAL)
        self.assertEqual(second_status, 200)
        self.assertNotEqual(first['ticket'], second['ticket'])
        path = self.root / 'paired-devices.json'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())[DEVICE], hashlib.sha256(CREDENTIAL.encode()).hexdigest())
        self.assertNotIn(CREDENTIAL, path.read_text())
        self.assertEqual(self.request('config')[0], 200)
        self.media(first['ticket'])
        self.media(KEY.decode())
        self.stop()
        with patch('time.time', return_value=4102444800):
            self.start()
            status, restored = self.request('config')
            self.assertEqual(status, 200)
            self.assertEqual(restored['ticket'], first['ticket'])
            self.media(restored['ticket'])
            self.assertEqual(self.request(device='112233445566')[0], 200)
        for secret in (CREDENTIAL, NEW_CREDENTIAL, KEY.decode(), first['ticket'], 'A1B2'):
            self.assertNotIn(secret, '\n'.join(self.logs))

    def test_wrong_code_and_bounded_global_rate_limit(self):
        for n in range(pairing.WRONG_LIMIT):
            self.assertEqual(self.request(device=f'{n:012x}', code='ZZZZ')[0], 403)
        self.assertFalse((self.root / 'paired-devices.json').exists())
        self.assertEqual(self.request()[0], 429)
        self.assertEqual(len(self.backend.wrong), pairing.WRONG_LIMIT)
        with patch.object(pairing.time, 'monotonic', return_value=time.monotonic() + pairing.WRONG_WINDOW + 1):
            self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request('config', credential=NEW_CREDENTIAL)[0], 403)
        self.assertEqual(self.request('config', device='112233445566')[0], 403)

    def test_repair_serializes_old_config_behind_durable_write(self):
        self.assertEqual(self.request()[0], 200)
        entered, release = threading.Event(), threading.Event()
        persist = self.backend._persist

        def paused(devices):
            entered.set()
            self.assertTrue(release.wait(3))
            persist(devices)

        with patch.object(self.backend, '_persist', side_effect=paused), ThreadPoolExecutor(2) as pool:
            repair = pool.submit(self.request, credential=NEW_CREDENTIAL)
            self.assertTrue(entered.wait(2))
            old = pool.submit(self.request, 'config')
            try:
                time.sleep(.05)
                self.assertFalse(old.done(), 'old config escaped persistence lock')
            finally:
                release.set()
            self.assertEqual(repair.result()[0], 200)
            self.assertEqual(old.result()[0], 403)
        self.assertEqual(self.request('config', credential=NEW_CREDENTIAL)[0], 200)
        self.stop()
        self.start()
        self.assertEqual(self.request('config')[0], 403)
        self.assertEqual(self.request('config', credential=NEW_CREDENTIAL)[0], 200)

    def test_failed_replace_latches_closed_and_preserves_previous_disk_state(self):
        self.assertEqual(self.request()[0], 200)
        before = (self.root / 'paired-devices.json').read_bytes()
        with patch.object(pairing.os, 'replace', side_effect=OSError('simulated disk failure')):
            self.assertEqual(self.request(credential=NEW_CREDENTIAL)[0], 503)
        self.assertEqual(self.request('config')[0], 503)
        self.assertEqual(self.request('config', credential=NEW_CREDENTIAL)[0], 503)
        self.assertEqual((self.root / 'paired-devices.json').read_bytes(), before)
        self.assertEqual(list(self.root.glob('.pairing-*')), [])

    def test_directory_sync_failure_never_returns_success_or_old_binding(self):
        self.assertEqual(self.request()[0], 200)
        sync = pairing.os.fsync

        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('simulated directory sync failure')
            sync(fd)

        with patch.object(pairing.os, 'fsync', side_effect=fail_directory):
            self.assertEqual(self.request(credential=NEW_CREDENTIAL)[0], 503)
        self.assertEqual(self.request('config')[0], 503)
        self.assertEqual(self.request('config', credential=NEW_CREDENTIAL)[0], 503)

    def test_file_sync_failure_does_not_create_a_binding(self):
        with patch.object(pairing.os, 'fsync', side_effect=OSError('simulated file sync failure')):
            self.assertEqual(self.request()[0], 503)
        self.assertFalse((self.root / 'paired-devices.json').exists())
        self.assertEqual(self.backend.devices, {})
        self.assertEqual(self.request('config')[0], 503)

    def test_bad_device_credential_and_ascii_code_never_persist(self):
        for kwargs in [{'device': 'AABBCCDDEEFF'}, {'device': 'aabbcc'},
                       {'credential': 'A' * 64}, {'credential': 'a' * 63},
                       {'code': 'Ａ1B2'}, {'code': 'A1B2\n'}, {'code': 'A1B22'},
                       {'code': ['A1B2']}, {'body': {'device': DEVICE, 'code': 'A1B2', 'extra': 1}}]:
            with self.subTest(kwargs=kwargs):
                self.assertIn(self.request(**kwargs)[0], (400, 403))
        self.assertFalse((self.root / 'paired-devices.json').exists())

    def test_host_validation_precedes_persistence_and_overrides_work(self):
        for host in ['tv.example.com/path', 'user@tv.example.com', 'a..com', '-bad.com',
                     '999.0.0.1', 'tv.example.com:0', 'tv.example.com:65536',
                     'tv.example.com:123:x', '[::1]:10745', '0.0.0.0', 'https://tv.example.com',
                     'a' * 32 + '.' + 'b' * 31]:
            with self.subTest(host=host):
                self.assertEqual(self.request(host=host)[0], 400)
        self.assertFalse((self.root / 'paired-devices.json').exists())
        self.assertEqual(self.request(host='127.0.0.1')[1]['port'], self.port)
        before = (self.root / 'paired-devices.json').read_bytes()
        self.assertEqual(self.request(credential=NEW_CREDENTIAL, host='a' * 64)[0], 400)
        self.assertEqual((self.root / 'paired-devices.json').read_bytes(), before)
        self.assertEqual(self.request('config')[0], 200)
        self.stop()
        self.start(public_host='PUBLIC.Example.com', public_port='10746')
        response = self.request(host='127.0.0.1:12345')[1]
        self.assertEqual((response['host'], response['port']), ('public.example.com', 10746))

    def raw_request(self, raw):
        with socket.create_connection(('127.0.0.1', self.port), timeout=4) as connection:
            connection.sendall(raw)
            return connection.recv(4096)

    def test_bounded_http_framing_duplicate_headers_and_json(self):
        headers = b'POST /social/tv/activate HTTP/1.1\r\nHost: tv.example.com\r\n'
        for suffix in [b'Content-Length: 999\r\n\r\n',
                       b'Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}',
                       b'Content-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}',
                       b'Content-Length: 23\r\n\r\n{"device":1,"device":2}',
                       b'X-Large: ' + b'a' * pairing.MAX_HEADERS]:
            with self.subTest(suffix=suffix[:50]):
                self.assertIn(self.raw_request(headers + suffix).split(b' ')[1], [b'400', b'431'])
        self.assertEqual(self.request()[0], 200)

    def test_partial_http_request_has_absolute_deadline_and_releases_slot(self):
        with patch.object(pairing, 'HTTP_TIMEOUT', .1):
            before = time.monotonic()
            reply = self.raw_request(b'POST /social/tv/activate HTTP/1.1\r\nHost: tv.example.com\r\n'
                                     b'Content-Length: 200\r\n\r\n{')
            self.assertEqual(reply.split(b' ')[1], b'408')
            self.assertLess(time.monotonic() - before, 1)
        self.assertEqual(self.request()[0], 200)

    def test_signed_entry_keeps_direct_admission_and_ticket_authentication(self):
        self.stop()
        ready = threading.Event()
        self.server = public_server.PublicServer(
            KEY, '127.0.0.1', 0, session_factory=SignedMemorySession,
            logger=lambda message: ready.set() if 'listening on' in message else None)
        self.thread = threading.Thread(target=self.server.serve, daemon=True)
        self.thread.start()
        self.assertTrue(ready.wait(3))
        self.port = self.server.port
        expiry = int(time.time()) + 600
        token = f'{DEVICE}.{expiry}.' + public_server.sign(KEY, DEVICE, expiry)
        with patch.object(self.server, '_dispatch', side_effect=AssertionError('unexpected dispatch')) as dispatch:
            self.media(token)
            dispatch.assert_not_called()
        with socket.create_connection(('127.0.0.1', self.port), timeout=2) as connection:
            send_packet(connection, Packet(Kind.HELLO, 0, 0, 0,
                        json_bytes({'version': 1, 'token': KEY.decode()})))
            with self.assertRaises((EOFError, ConnectionResetError)):
                receive_packet(connection)

    def test_http_available_when_media_capacity_full(self):
        held = []
        try:
            for _ in range(public_server.MAX_SESSIONS):
                connection = socket.create_connection(('127.0.0.1', self.port), timeout=3)
                held.append(connection)
                send_packet(connection, Packet(Kind.HELLO, 0, 0, 0,
                            json_bytes({'version': 1, 'token': KEY.decode()})))
                self.assertEqual(receive_packet(connection).kind, Kind.CONFIG)
            self.assertEqual(self.request()[0], 200)
            with socket.create_connection(('127.0.0.1', self.port), timeout=3) as extra:
                send_packet(extra, Packet(Kind.HELLO, 0, 0, 0, json_bytes({'version': 1, 'token': KEY.decode()})))
                self.assertEqual(receive_packet(extra).kind, Kind.ERROR)
        finally:
            for connection in held:
                connection.close()

    def test_handler_exception_releases_slot_and_listener_keeps_serving(self):
        def broken_handler(*_):
            raise RuntimeError(CREDENTIAL)

        self.server.http_handler = broken_handler
        with socket.create_connection(('127.0.0.1', self.port), timeout=2) as connection:
            connection.sendall(b'POST')
            try:
                self.assertEqual(connection.recv(1), b'')
            except ConnectionResetError:
                pass
        self.server.http_handler = self.backend.handle
        self.assertEqual(self.request()[0], 200)
        self.assertNotIn(CREDENTIAL, '\n'.join(self.logs))

    def test_signed_but_unbound_ticket_is_rejected_on_media_listener(self):
        token = pairing.media_ticket(KEY, DEVICE)
        with socket.create_connection(('127.0.0.1', self.port), timeout=2) as connection:
            send_packet(connection, Packet(Kind.HELLO, 0, 0, 0,
                        json_bytes({'version': 1, 'token': token})))
            with self.assertRaises((EOFError, ConnectionResetError)):
                receive_packet(connection)
        self.assertEqual(self.request()[0], 200)
        self.media(token)

    def test_unauthenticated_dispatch_is_bounded_and_recovers(self):
        held = []
        try:
            for _ in range(public_server.MAX_DISPATCH):
                held.append(socket.create_connection(('127.0.0.1', self.port), timeout=3))
                held[-1].sendall(b'P')
            deadline = time.monotonic() + 2
            while self.server.dispatch_slots.acquire(blocking=False):
                self.server.dispatch_slots.release()
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.005)
            self.assertLessEqual(sum(t.name == 'tv-dispatch' for t in threading.enumerate()), public_server.MAX_DISPATCH)
            with socket.create_connection(('127.0.0.1', self.port), timeout=2) as extra:
                self.assertEqual(extra.recv(1), b'')
        finally:
            for connection in held:
                connection.close()
        # Dispatch sniffing has an absolute two-second deadline.
        deadline = time.monotonic() + 4
        while any(t.name == 'tv-dispatch' for t in threading.enumerate()):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        self.assertEqual(self.request()[0], 200)


class PairingFiles(unittest.TestCase):
    def test_advertised_host_and_port_reject_invalid_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            code = Path(directory) / 'pairing.code'
            code.write_text('A1B2\n')
            code.chmod(0o600)
            for overrides in [{'TV_PUBLIC_HOST': 'a' * 64}, {'TV_PUBLIC_HOST': 'http://tv.example.com'},
                              {'TV_PUBLIC_HOST': ''}, {'TV_PUBLIC_PORT': '65536'},
                              {'TV_PUBLIC_PORT': '0'}, {'TV_PUBLIC_PORT': ' 10745'}]:
                with self.subTest(overrides=overrides), patch.dict(os.environ, overrides, clear=True):
                    with self.assertRaises(ValueError):
                        pairing.PairingBackend.from_env(KEY, code, Path(directory) / 'paired-devices.json')

    def test_state_and_code_reject_insecure_corrupt_oversized_or_symlink_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, state = root / 'pairing.code', root / 'paired-devices.json'
            code.write_text('A1B2\n')
            code.chmod(0o600)
            for raw in [b'[]', b'{"bad":"state"}', b'{', b'x' * (pairing.MAX_STATE_BYTES + 1)]:
                state.write_bytes(raw)
                state.chmod(0o600)
                with self.assertRaises(ValueError):
                    pairing.PairingBackend(KEY, code, state)
            state.write_text('{}')
            state.chmod(0o644)
            with self.assertRaises(ValueError):
                pairing.PairingBackend(KEY, code, state)
            state.unlink()
            state.symlink_to(code)
            with self.assertRaises(OSError):
                pairing.PairingBackend(KEY, code, state)

    def test_ticket_tampering_other_secret_and_private_ticket_entry_rejected(self):
        ticket = pairing.media_ticket(KEY, DEVICE)
        self.assertEqual(pairing.verify_media_ticket(KEY, ticket), DEVICE)
        for bad in [None, ticket.replace(DEVICE, '001122334455'), ticket + 'x',
                    ticket.replace('p1.', 'p2.'), 'p1.' + DEVICE + '.' + 'Ａ' * 64]:
            with self.assertRaises(ProtocolError):
                pairing.verify_media_ticket(KEY, bad)
        with self.assertRaises(ProtocolError):
            pairing.verify_media_ticket(b'other-secret', ticket)
        with self.assertRaises(ProtocolError):
            public_server.verify_ticket(KEY, ticket)


if __name__ == '__main__':
    unittest.main()
