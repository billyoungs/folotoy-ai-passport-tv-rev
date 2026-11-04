#!/usr/bin/env python3
"""Actual launcher → FFmpeg → FAV1 receive/decode check, using generated media only."""
from pathlib import Path
import argparse
import functools
import http.server
import http.client
import secrets
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tv_smoke_launcher', ROOT / 'tools/run_server.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
SERVICE = launcher.service_directory(ROOT)
sys.path.insert(0, str(SERVICE))
from server.protocol import Kind, Packet, json_bytes, json_object, send_packet, receive_packet
from server import frames, adpcm


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def copyfile(self, source, output):
        try:
            super().copyfile(source, output)
        except (BrokenPipeError, ConnectionResetError):
            pass  # ffprobe deliberately closes its HTTP input early.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='passport TV smoke ') as directory:
        root = Path(directory)
        (root / 'tools').mkdir()
        for name in ('start-server.sh', 'Start TV.command', 'stream.env.example', 'channels.example.txt'):
            shutil.copy2(ROOT / name, root / name)
        shutil.copy2(ROOT / 'tools/run_server.py', root / 'tools/run_server.py')
        shutil.copytree(SERVICE / 'server', root / 'service/server', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(SERVICE / 'requirements.txt', root / 'service/requirements.txt')
        media = root / 'synthetic.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000', '-t', '30',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-movflags', '+faststart', '-y', str(media)], check=True)
        httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(root)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        (root / 'channels.txt').write_text(f'demo | Synthetic | http://127.0.0.1:{httpd.server_port}/synthetic.mp4\n')
        (root / 'server.env').write_text(f'TV_BIND=127.0.0.1\nTV_PORT={port}\n')
        env = {key: value for key, value in os.environ.items() if not key.startswith('TV_')}
        env['PYTHON'] = sys.executable
        entry = root / 'start-server.sh'
        with (root / 'setup.log').open('w') as log:
            setup = subprocess.run([str(entry), '--setup-only'], cwd='/', env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        if setup.returncode:
            print((root / 'setup.log').read_text()[-6000:], file=sys.stderr)
            raise RuntimeError('launcher setup failed; diagnostics above')
        protected = {name: (root / name).read_bytes() for name in ('channels.txt', 'server.env', 'pairing.token', 'pairing.code')}
        pairing_code = protected['pairing.code'].decode().strip()
        assert len(pairing_code) == 4
        credential = secrets.token_hex(32)
        def authorize(path, payload):
            request = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                request.request('POST', path, json.dumps(payload), {'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + credential})
                reply = request.getresponse()
                assert reply.status == 200, reply.status
                return json.loads(reply.read())
            finally:
                request.close()
        subprocess.run([str(entry), '--check'], cwd='/', env=env, stdout=subprocess.DEVNULL, check=True)
        video, audio, nonzero, current, configuration = [], 0, 0, [], None
        proc = None
        try:
            with (root / 'server.log').open('w') as log:
                proc = subprocess.Popen([str(entry)], cwd='/', env=env, stdout=log, stderr=subprocess.STDOUT)
                for _ in range(150):
                    if proc.poll() is not None:
                        raise RuntimeError('launcher/server exited before accepting connections')
                    try:
                        connection = socket.create_connection(('127.0.0.1', port), timeout=.2)
                        break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('launcher did not start listener')
                connection.close()  # Readiness probe must not wait for HELLO during HTTP pairing.
                grant = authorize('/social/tv/activate', {'device': '001122334455', 'code': pairing_code})
                refreshed = authorize('/social/tv/config', {'device': '001122334455'})
                assert grant == refreshed and grant['host'] == '127.0.0.1' and grant['port'] == port
                token = grant['ticket']
                assert token != protected['pairing.token'].decode().strip()
                with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
                    connection.setblocking(False)
                    send_packet(connection, Packet(Kind.HELLO, 0, 0, 0, json_bytes(dict(version=1, channel='demo', quality='ultra', token=token))))
                    deadline = time.monotonic() + 40
                    while time.monotonic() < deadline:
                        packet = receive_packet(connection, timeout=20 if not video else 8)
                        now = time.monotonic()
                        if packet.kind == Kind.ERROR:
                            raise RuntimeError('service rejected synthetic playback')
                        if packet.kind == Kind.CONFIG:
                            configuration = json_object(packet.payload)
                            assert (configuration['fps'], configuration['width'], configuration['height']) == (30, 320, 180)
                        if packet.kind == Kind.PCM:
                            audio += 1
                            nonzero += bool(any(adpcm.decode(packet.payload, 640)))
                        if packet.kind == Kind.JPEG:
                            if not packet.flags:
                                assert not current
                            assert packet.payload[0] == len(current)
                            current.extend(frames.unpack(packet.payload))
                            if len(current) == frames.STRIPES:
                                assert len(b''.join(current)) == 320 * 180
                                video.append(now)
                                current = []
                                if now - video[0] >= 8:
                                    break
                assert configuration is not None and len(video) >= 180 and audio >= 150 and nonzero >= 150
                fps = (len(video) - 1) / (video[-1] - video[0])
                assert fps >= 28, fps
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            httpd.shutdown()
            httpd.server_close()
        assert protected == {name: (root / name).read_bytes() for name in protected}
        assert 'Installing service dependencies' not in (root / 'server.log').read_text()
        result = {'status': 'PASS', 'platform': sys.platform, 'actual_launcher_bootstrap': True,
                  'configuration_retained': True, 'restart_reused_dependencies': True,
                  'four_character_pairing': True, 'bound_credential_refresh': True,
                  'source': 'generated testsrc2 + sine; no external media',
                  'measurement': 'software full-frame arrival and ADPCM decode; no LCD/speaker capture',
                  'fps': round(fps, 3), 'frames': len(video), 'audio_blocks': audio,
                  'nonzero_audio_blocks': nonzero, 'max_frame_gap_ms': round(max(b-a for a,b in zip(video,video[1:]))*1000, 2)}
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
