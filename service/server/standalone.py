"""Passport TV entry without a FoloTalk account or ticket issuer.

Devices pair over HTTP with a reusable code and receive permanent media tickets.
Legacy connections can still authenticate with the owner-configured token. Shared
encoding, explicit quality selection, pacing and queue bounds use the same
implementation as the signed-ticket FoloTalk entry.
"""
from __future__ import annotations
import argparse
from functools import partial
import signal
import sys
from pathlib import Path
from . import tv_server
from .live import CHANNELS
from .public_server import PublicServer, Session
from .pairing import PairingBackend, verify_media_ticket
from .protocol import ProtocolError
from .shared import from_env

class PairingSession(Session):
    def __init__(self, *args, pairing=None):
        super().__init__(*args)
        self.pairing = pairing

    def authenticate_hello(self, hello, payload):
        try:
            tv_server.authenticate(hello, self.key)
            return 'paired'
        except ProtocolError:
            device = verify_media_ticket(self.key, payload.get('token'))
            if self.pairing is not None and not self.pairing.bound_device(device):
                raise ProtocolError('authentication failed')
            return device

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=10745)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--code-file', type=Path)
    parser.add_argument('--devices-file', type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    try:
        token = tv_server.load_token(args.token_file)
        if len(token)>127:
            raise ValueError("pairing token exceeds device capacity")
        pairing = PairingBackend.from_env(
            token, args.code_file or args.token_file.with_name('pairing.code'),
            args.devices_file or args.token_file.with_name('paired-devices.json'))
    except (OSError, ValueError) as error:
        print('pairing configuration unusable: '+type(error).__name__, file=sys.stderr)
        return 2
    if not CHANNELS:
        print('no channels configured', file=sys.stderr)
        return 2
    server = PublicServer(token, args.bind, args.port,
                          session_factory=partial(PairingSession, pairing=pairing),
                          http_handler=pairing.handle)
    try:
        server.shared, server.shared_loops = from_env(server.logger)
    except ValueError:
        print('shared live configuration invalid', file=sys.stderr)
        return 2
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: server.stop.set())
    try:
        server.serve()
    finally:
        if server.shared is not None:
            server.shared.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
