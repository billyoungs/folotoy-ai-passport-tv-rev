"""Operator-only, expiring fixed-source comparison for one authenticated device.

The input changes; the normal decoder, pixel format, audio, pacing and wire
protocol do not. No client payload may select a local file.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .live import CHANNELS, LiveChannel


@dataclass(frozen=True)
class Replay:
    path: Path
    device: str
    channel_id: str
    until: int
    sha256: str

    @classmethod
    def from_env(cls) -> Replay | None:
        names = ('TV_REPLAY_FILE', 'TV_REPLAY_DEVICE', 'TV_REPLAY_CHANNEL', 'TV_REPLAY_UNTIL')
        values = [os.environ.get(name, '') for name in names]
        if not any(values):
            return None
        path, device, channel_id, until = values
        if not all(values) or not re.fullmatch(r'[0-9a-f]{1,32}', device) or channel_id not in CHANNELS:
            raise ValueError('replay requires file, device id, explicit allowed channel and expiry')
        expiry = int(until)
        # Expired configuration is harmless after a restart; it never routes a
        # new session to the file. Refuse accidental unbounded experiments.
        if expiry > time.time() + 24 * 3600:
            raise ValueError('replay expiry must be within 24 hours')
        source = Path(path)
        if not source.is_absolute() or not source.is_file():
            raise ValueError('replay source must be an absolute regular file')
        digest = hashlib.sha256()
        with source.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return cls(source, device, channel_id, expiry, digest.hexdigest())

    def matches(self, device: str, channel_id: str) -> bool:
        return device == self.device and channel_id == self.channel_id and time.time() < self.until

    def channel(self, url: str, ffmpeg: str = 'ffmpeg', user_agent: str = '') -> LiveChannel:
        # Keep normal channel validation. Only trusted operator configuration
        # changes the actual source after validating the requested channel.
        channel = LiveChannel(url, ffmpeg, user_agent)
        channel.url = str(self.path)
        channel.user_agent = ''
        channel.loop_input = True
        return channel
