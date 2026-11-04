"""重连提示：黑白电视雪花背景及居中的文字。

用内置 5x7 点阵字体直接写入索引数组，不依赖图像库。索引对应固定 3-3-2 调色板
（`media.default_palette`）：0 为黑，255 为白。
"""

from __future__ import annotations

import os

from . import frames

NOTICE_TEXT = "RECONNECTING..."

BLACK = 0
WHITE = 255

_GLYPHS: dict[str, tuple[str, ...]] = {
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "I": (".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    ".": ("..", "..", "..", "..", "..", "##", "##"),
}
_GLYPH_ROWS = 7
_LETTER_GAP = 1
_BOX_PAD = 8


def _text_columns(text: str) -> int:
    return sum(len(_GLYPHS[c][0]) for c in text) + _LETTER_GAP * (len(text) - 1)


def reconnect_frame(base: bytes | None) -> bytes:
    """生成黑白雪花，不修改上一帧；黑框确保提示文字清晰。"""
    if base is not None and len(base) != frames.FRAME_PIXELS:
        raise ValueError(f"frame is {len(base)} bytes, expected {frames.FRAME_PIXELS}")
    width, height = frames.WIDTH, frames.HEIGHT
    # 2x2 dots resemble analogue static while compressing much better than
    # independent random pixels. They use the existing black/white palette.
    dots_w, dots_h = (width + 1) // 2, (height + 1) // 2
    noise = os.urandom(dots_w * dots_h)
    pixels = bytearray(frames.FRAME_PIXELS)
    for y in range(0, height, 2):
        row = bytes(WHITE if noise[(y // 2) * dots_w + x // 2] & 1 else BLACK
                    for x in range(width))
        pixels[y * width:(y + 1) * width] = row
        if y + 1 < height:
            pixels[(y + 1) * width:(y + 2) * width] = row
    columns = _text_columns(NOTICE_TEXT)
    scale = 1
    text_w, text_h = columns * scale, _GLYPH_ROWS * scale
    left, top = (width - text_w) // 2, (height - text_h) // 2

    for y in range(max(0, top - _BOX_PAD), min(height, top + text_h + _BOX_PAD)):
        row = y * width
        for x in range(max(0, left - _BOX_PAD), min(width, left + text_w + _BOX_PAD)):
            pixels[row + x] = BLACK

    x0 = left
    for char in NOTICE_TEXT:
        glyph = _GLYPHS[char]
        for gy, line in enumerate(glyph):
            for gx, cell in enumerate(line):
                if cell != "#":
                    continue
                for dy in range(scale):
                    row = (top + gy * scale + dy) * width
                    for dx in range(scale):
                        pixels[row + x0 + gx * scale + dx] = WHITE
        x0 += (len(glyph[0]) + _LETTER_GAP) * scale
    return bytes(pixels)
