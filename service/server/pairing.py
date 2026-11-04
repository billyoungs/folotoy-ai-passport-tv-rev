"""Permanent public pairing, using the private service's device HTTP contract.

Only credential digests are stored. Media tickets use a separate HMAC domain
and have no clock dependency; rotating pairing.token invalidates all tickets.
"""
from __future__ import annotations

from collections import deque
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import threading
import time

from .protocol import ProtocolError, json_object

DEVICE = re.compile(r'[0-9a-f]{12}', re.ASCII)
CREDENTIAL = re.compile(r'[0-9a-f]{64}', re.ASCII)
CODE = re.compile(r'[A-Za-z0-9]{4}', re.ASCII)
MAX_DEVICES = 10000
MAX_STATE_BYTES = 1024 * 1024
WRONG_LIMIT = 10
WRONG_WINDOW = 900
HTTP_TIMEOUT = 3
MAX_HEADERS = 4096
MAX_BODY = 256


def owner_bytes(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('pairing file must be owner-only, regular, mode 0600')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('pairing file too large')
    return raw


def load_code(path: Path) -> str:
    code = owner_bytes(path, 5).removesuffix(b'\n').decode('ascii')
    if not CODE.fullmatch(code):
        raise ValueError('pairing code must be four ASCII letters or digits')
    return code.upper()


def media_ticket(key: bytes, device: str) -> str:
    message = f'public-tv-v1.{device}'
    signature = hmac.new(key, message.encode('ascii'), hashlib.sha256).hexdigest()
    return f'p1.{device}.{signature}'


def verify_media_ticket(key: bytes, ticket: object) -> str:
    if not isinstance(ticket, str) or len(ticket) != 80:
        raise ProtocolError('authentication failed')
    parts = ticket.split('.')
    if (len(parts) != 3 or parts[0] != 'p1' or not DEVICE.fullmatch(parts[1]) or
            not CREDENTIAL.fullmatch(parts[2]) or
            not hmac.compare_digest(ticket, media_ticket(key, parts[1]))):
        raise ProtocolError('authentication failed')
    return parts[1]


def hostname(value: str) -> str:
    if not value or len(value) > 63 or not value.isascii():
        raise ValueError('invalid public host')
    value = value.lower().removesuffix('.')
    if re.fullmatch(r'[0-9.]+', value):
        address = ipaddress.IPv4Address(value)
        if address.is_unspecified or address.is_multicast or address == ipaddress.IPv4Address('255.255.255.255'):
            raise ValueError('invalid public host')
        return str(address)
    if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
           for label in value.split('.')):
        raise ValueError('invalid public host')
    return value


def port_number(value: str) -> int:
    if not re.fullmatch(r'[0-9]{1,5}', value) or not 1 <= int(value) <= 65535:
        raise ValueError('invalid public port')
    return int(value)


class HTTPError(Exception):
    def __init__(self, status: int):
        self.status = status


class PairingBackend:
    def __init__(self, key: bytes, code_file: Path, devices_file: Path,
                 public_host: str | None = None, public_port: str | None = None):
        self.key = key
        self.code = load_code(code_file)
        self.path = devices_file
        self.public_host = hostname(public_host) if public_host is not None else None
        self.public_port = port_number(public_port) if public_port is not None else None
        self.lock = threading.Lock()
        self.failed = False
        # A global bounded window cannot be evaded by changing device IDs/IPs.
        self.wrong = deque(maxlen=WRONG_LIMIT)
        try:
            devices = json_object(owner_bytes(self.path, MAX_STATE_BYTES))
        except FileNotFoundError:
            devices = {}
        if (len(devices) > MAX_DEVICES or
                any(not DEVICE.fullmatch(device) or not isinstance(digest, str) or
                    not CREDENTIAL.fullmatch(digest) for device, digest in devices.items())):
            raise ValueError('invalid pairing state')
        self.devices = devices

    @classmethod
    def from_env(cls, key: bytes, code_file: Path, devices_file: Path):
        return cls(key, code_file, devices_file,
                   os.environ.get('TV_PUBLIC_HOST'), os.environ.get('TV_PUBLIC_PORT'))

    def endpoint(self, authority: str, port: int) -> tuple[str, int]:
        host, separator, supplied_port = authority.partition(':')
        host = hostname(host)
        supplied = port_number(supplied_port) if separator else port
        return self.public_host or host, self.public_port or supplied

    def bound_device(self, device: str) -> bool:
        with self.lock:
            return not self.failed and device in self.devices

    def _persist(self, devices: dict[str, str]) -> None:
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.pairing-', dir=self.path.parent)
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(devices, stream, separators=(',', ':'), sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            # Even a failure after rename must never expose the previous binding.
            self.failed = True
            raise HTTPError(503) from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def configuration(self, path: str, body: dict, authorization: str,
                      authority: str, port: int) -> dict:
        expected = {'device', 'code'} if path == '/social/tv/activate' else {'device'}
        if body.keys() != expected:
            raise HTTPError(400)
        device = body.get('device')
        credential = authorization.removeprefix('Bearer ')
        if (not isinstance(device, str) or not DEVICE.fullmatch(device) or
                not authorization.startswith('Bearer ') or not CREDENTIAL.fullmatch(credential)):
            raise HTTPError(403)
        try:
            host, port = self.endpoint(authority, port)
        except ValueError:
            raise HTTPError(400) from None
        digest = hashlib.sha256(credential.encode('ascii')).hexdigest()
        # Serialize reads and durable replacement: an in-flight old credential
        # cannot pass config after a completed re-pair, including on write failure.
        with self.lock:
            if self.failed:
                raise HTTPError(503)
            if path == '/social/tv/activate':
                now = time.monotonic()
                while self.wrong and self.wrong[0] <= now - WRONG_WINDOW:
                    self.wrong.popleft()
                if len(self.wrong) >= WRONG_LIMIT:
                    raise HTTPError(429)
                code = body.get('code')
                if (not isinstance(code, str) or not CODE.fullmatch(code) or
                        not hmac.compare_digest(code.upper(), self.code)):
                    self.wrong.append(now)
                    raise HTTPError(403)
                if len(self.devices) >= MAX_DEVICES and device not in self.devices:
                    raise HTTPError(503)
                updated = {**self.devices, device: digest}
                self._persist(updated)
                self.devices = updated
            elif not hmac.compare_digest(self.devices.get(device, ''), digest):
                raise HTTPError(403)
            return {'host': host, 'port': port, 'ticket': media_ticket(self.key, device)}

    def _request(self, connection: socket.socket):
        deadline = time.monotonic() + HTTP_TIMEOUT

        def receive(size):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPError(408)
            connection.settimeout(remaining)
            try:
                data = connection.recv(size)
            except socket.timeout:
                raise HTTPError(408) from None
            if not data:
                raise HTTPError(400)
            return data

        raw = b''
        while b'\r\n\r\n' not in raw:
            if len(raw) >= MAX_HEADERS:
                raise HTTPError(431)
            raw += receive(min(1024, MAX_HEADERS - len(raw)))
        head, body = raw.split(b'\r\n\r\n', 1)
        try:
            lines = head.decode('ascii').split('\r\n')
            method, path, version = lines[0].split(' ')
            if version not in ('HTTP/1.0', 'HTTP/1.1'):
                raise ValueError
            headers = {}
            for line in lines[1:]:
                name, separator, value = line.partition(':')
                if not separator or not re.fullmatch(r'[A-Za-z0-9-]+', name):
                    raise ValueError
                name, value = name.lower(), value.strip(' \t')
                if name in headers or any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise ValueError
                headers[name] = value
            if 'transfer-encoding' in headers or 'host' not in headers:
                raise ValueError
            length = headers.get('content-length', '')
            if not re.fullmatch(r'[0-9]{1,3}', length) or int(length) > MAX_BODY:
                raise ValueError
            length = int(length)
        except (UnicodeError, ValueError):
            raise HTTPError(400) from None
        if path not in ('/social/tv/activate', '/social/tv/config'):
            raise HTTPError(404)
        if method != 'POST':
            raise HTTPError(405)
        if len(body) > length:
            raise HTTPError(400)
        while len(body) < length:
            body += receive(length - len(body))
        try:
            body = json_object(body)
        except (ProtocolError, UnicodeError, ValueError):
            raise HTTPError(400) from None
        return path, body, headers.get('authorization', ''), headers['host']

    def handle(self, connection: socket.socket, port: int) -> None:
        try:
            path, body, authorization, authority = self._request(connection)
            result = self.configuration(path, body, authorization, authority, port)
            status = 200
        except HTTPError as error:
            status = error.status
            result = {'error': 'request failed'}
        except (OSError, ValueError):
            status, result = 400, {'error': 'request failed'}
        raw = json.dumps(result, separators=(',', ':')).encode('ascii')
        extra = f'Retry-After: {WRONG_WINDOW}\r\n' if status == 429 else ''
        response = (f'HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\n'
                    f'Content-Length: {len(raw)}\r\nCache-Control: no-store\r\n'
                    f'X-Content-Type-Options: nosniff\r\n{extra}Connection: close\r\n\r\n')
        try:
            connection.settimeout(1)
            connection.sendall(response.encode('ascii') + raw)
        except OSError:
            pass
