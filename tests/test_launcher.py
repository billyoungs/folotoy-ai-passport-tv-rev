"""Configuration, credential preservation and platform policy for the real launcher."""
from pathlib import Path
import contextlib
import importlib.util
import io
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tv_launcher', ROOT / 'tools/run_server.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='passport-tv-launcher-')
        self.root = Path(self.directory.name)
        for name in ('stream.env.example', 'channels.example.txt'):
            (self.root / name).write_bytes((ROOT / name).read_bytes())
        # Empty-template scenarios remain explicit despite the shipped default channel.
        (self.root / 'channels.example.txt').write_text('# No channels configured for this test\n')

    def tearDown(self):
        self.directory.cleanup()

    def test_shipped_example_bootstraps_without_prompt_and_keeps_user_agent(self):
        original = (ROOT / 'channels.example.txt').read_bytes()
        (self.root / 'channels.example.txt').write_bytes(original)
        with patch('builtins.input', side_effect=AssertionError('default channel must not prompt')):
            launcher.prepare_configuration(self.root, False)
        self.assertEqual((self.root / 'channels.txt').read_bytes(), original)
        entries = [line for line in original.decode().splitlines() if line.strip() and not line.startswith('#')]
        self.assertEqual(len(entries), 1)
        self.assertEqual([value.strip() for value in entries[0].split('|')][1::2], ['CCTV1', 'AptvPlayer-UA'])

    def test_Mac_disables_only_kernel_pacing_and_Linux_keeps_it(self):
        inherited = {'PATH': '/bin', 'TV_TCP_PACING_RATE': '1200000'}
        mac = launcher.effective_environment(self.root, inherited, 'darwin')
        linux = launcher.effective_environment(self.root, inherited, 'linux')
        self.assertEqual(mac['TV_TCP_PACING_RATE'], '0')
        self.assertEqual(linux['TV_TCP_PACING_RATE'], '1200000')
        self.assertEqual({k: v for k, v in mac.items() if k != 'TV_TCP_PACING_RATE'},
                         {k: v for k, v in linux.items() if k != 'TV_TCP_PACING_RATE'})
        self.assertEqual(mac['TV_FPS'], '30')
        self.assertEqual(mac['TV_ENCODE'], 'full')
        self.assertEqual(mac['TV_ADAPTIVE'], '0')
        self.assertEqual(mac['TV_RATE_MIN'], mac['TV_RATE_MAX'])
        self.assertEqual(mac['TV_WRITE_PACING_CHUNK'], '12288')

    def test_precedence_and_shell_text_are_data(self):
        (self.root / 'server.env').write_text('TV_PORT=18096\nTV_MAX_SESSIONS=4\nTV_NOTE=$(touch SHOULD_NOT_EXIST)\n')
        result = launcher.effective_environment(self.root, {'TV_PORT': '18097'}, 'linux')
        self.assertEqual(result['TV_PORT'], '18097')
        self.assertEqual(result['TV_MAX_SESSIONS'], '4')
        self.assertEqual(result['TV_NOTE'], '$(touch SHOULD_NOT_EXIST)')
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())

    def test_duplicate_invalid_keys_and_invalid_port_rejected(self):
        for content in ('TV_PORT=1\nTV_PORT=2', 'PATH=/tmp', 'TV_PORT=0', 'TV_PORT=65536'):
            with self.subTest(content=content):
                (self.root / 'server.env').write_text(content)
                with self.assertRaises(ValueError):
                    launcher.effective_environment(self.root, {}, 'linux')

    def test_environment_token_cannot_conflict_with_file(self):
        with self.assertRaises(ValueError):
            launcher.effective_environment(self.root, {'TV_PAIRING_TOKEN': 'not-a-real-token'}, 'linux')

    def test_first_setup_then_restart_preserves_all_files(self):
        with patch('builtins.input', side_effect=['Demo', 'https://example.com/live.m3u8']):
            launcher.prepare_configuration(self.root, True)
        names = ['server.env', 'pairing.token', 'pairing.code', 'channels.txt']
        before = {name: (self.root / name).read_bytes() for name in names}
        self.assertEqual(stat.S_IMODE((self.root / 'pairing.token').stat().st_mode), 0o600)
        self.assertEqual(len(before['pairing.token'].strip()), 48)
        self.assertRegex(before['pairing.code'].decode(), r'^[A-Z0-9]{4}\n$')
        self.assertEqual(stat.S_IMODE((self.root / 'pairing.code').stat().st_mode), 0o600)
        with patch('builtins.input', side_effect=AssertionError('must not prompt on restart')):
            launcher.prepare_configuration(self.root, True)
            launcher.prepare_configuration(self.root, False)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in names})

    def test_existing_long_token_and_paired_devices_survive_launcher_upgrade(self):
        (self.root / 'pairing.token').write_text('existing-legacy-secret-token\n')
        (self.root / 'paired-devices.json').write_text('{"aabbccddeeff":"' + 'a' * 64 + '"}\n')
        (self.root / 'channels.txt').write_text('demo | Demo | https://example.com/live\n')
        names = ['pairing.token', 'paired-devices.json', 'channels.txt']
        before = {name: (self.root / name).read_bytes() for name in names}
        launcher.prepare_configuration(self.root, False)
        self.assertRegex((self.root / 'pairing.code').read_text(), r'^[A-Z0-9]{4}\n$')
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in names})

    def test_show_pairing_only_displays_four_character_code(self):
        (self.root / 'pairing.token').write_text('never-display-this-signing-secret\n')
        (self.root / 'pairing.code').write_text('X9Y8\n')
        output = io.StringIO()
        with patch.object(launcher, 'ROOT', self.root), contextlib.redirect_stdout(output):
            self.assertEqual(launcher.main(['--show-pairing']), 0)
        self.assertEqual(output.getvalue(), 'X9Y8\n')

    def test_public_endpoint_override_precedence_and_pairing_check_paths(self):
        (self.root / 'server.env').write_text('TV_PUBLIC_HOST=tv.example.com\nTV_PUBLIC_PORT=10745\n')
        env = launcher.effective_environment(self.root, {'TV_PUBLIC_PORT': '10746'}, 'linux')
        self.assertEqual((env['TV_PUBLIC_HOST'], env['TV_PUBLIC_PORT']), ('tv.example.com', '10746'))
        with patch.object(launcher.subprocess, 'run') as run:
            launcher.check_configuration(self.root, env, self.root, Path('/python'))
        command = run.call_args.args[0]
        self.assertIn('PairingBackend.from_env', command[2])
        self.assertEqual(command[-2:], [str(self.root / 'pairing.code'), str(self.root / 'paired-devices.json')])

    def test_configuration_check_runs_real_service_without_listening_or_fetching_media(self):
        (self.root / 'channels.example.txt').write_bytes((ROOT / 'channels.example.txt').read_bytes())
        launcher.prepare_configuration(self.root, False)
        env = launcher.effective_environment(
            self.root, {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'}, sys.platform)
        launcher.check_configuration(self.root, env, launcher.service_directory(ROOT), Path(sys.executable))
        self.assertFalse((self.root / 'paired-devices.json').exists())

    def test_noninteractive_empty_channel_list_exits_without_prompt(self):
        with patch('builtins.input', side_effect=AssertionError('must not block')):
            with self.assertRaisesRegex(ValueError, 'channels.txt'):
                launcher.prepare_configuration(self.root, False)
        token = (self.root / 'pairing.token').read_bytes()
        with self.assertRaises(ValueError):
            launcher.prepare_configuration(self.root, False)
        self.assertEqual((self.root / 'pairing.token').read_bytes(), token)

    def test_first_setup_rejects_non_http_source(self):
        with patch('builtins.input', side_effect=['Demo', 'file:///private/video.mp4']):
            with self.assertRaises(ValueError):
                launcher.prepare_configuration(self.root, True)

    def test_missing_venv_dependency_is_actionable_before_creating_environment(self):
        with patch.object(launcher.importlib.util, 'find_spec', return_value=None):
            with self.assertRaisesRegex(ValueError, 'python3-venv'):
                launcher.ensure_runtime(self.root, self.root)
        self.assertFalse((self.root / '.venv').exists())


if __name__ == '__main__':
    unittest.main()
