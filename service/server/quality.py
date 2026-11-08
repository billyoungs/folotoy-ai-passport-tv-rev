"""Explicit viewer-selected profiles; never change process-wide tuning."""
from dataclasses import dataclass
from functools import lru_cache
import numpy as np
from . import frames
from .protocol import ProtocolError


@dataclass(frozen=True)
class Quality:
    name: str
    fps: int
    detail_numerator: int
    detail_denominator: int

    def video_filter(self, geometry: "frames.Geometry" = frames.DEFAULT_GEOMETRY) -> str:
        if self.detail_numerator == self.detail_denominator:
            return ''
        width = max(2, geometry.width * self.detail_numerator // self.detail_denominator // 2 * 2)
        height = max(2, geometry.height * self.detail_numerator // self.detail_denominator // 2 * 2)
        # Restore the fixed wire geometry on the server. The device needs no
        # scaler or additional frame memory. Ultra takes the original path.
        return (f'scale={width}:{height}:flags=area,'
                f'scale={geometry.width}:{geometry.height}:flags=bilinear')

    def render(self, packets, geometry: "frames.Geometry" = frames.DEFAULT_GEOMETRY):
        """Reduce detail once on the shared source, retaining fixed wire size.

        Native RGB332 indices are sampled directly, so this adds no colour
        quantization or device-side scaler. Ultra retains the original bytes.
        """
        if self.detail_numerator == self.detail_denominator:
            return packets
        raw = b''.join(stripe for part in packets for stripe in frames.unpack(part, geometry))
        reduced = np.frombuffer(raw, dtype=np.uint8)[_sample_map(
            geometry.width, geometry.height, self.detail_numerator, self.detail_denominator)]
        return tuple(frames.frame_packets(reduced.tobytes(), geometry))


@lru_cache(maxsize=8)
def _sample_map(width, height, numerator, denominator):
    w = max(2, width*numerator//denominator//2*2)
    h = max(2, height*numerator//denominator//2*2)
    x = np.arange(width)*w//width*width//w
    y = np.arange(height)*h//height*height//h
    mapping = (y[:,None]*width+x).astype(np.int32).ravel()
    mapping.flags.writeable = False
    return mapping


PROFILES = {
    'smooth': Quality('smooth', 8, 1, 2),
    'standard': Quality('standard', 15, 4, 5),
    'ultra': Quality('ultra', 30, 1, 1),
}


def requested(value):
    # Older clients retain the deployment's original profile.
    if value is None:
        return None
    if not isinstance(value, str) or value not in PROFILES:
        raise ProtocolError('unsupported quality')
    return PROFILES[value]
