#!/usr/bin/env python3
"""Bump the TV firmware version used for versioned merged-firmware filenames.

The version lives in firmware/CMakeLists.txt as
``project(FoloToy-AI-Passport VERSION X.Y.Z)`` and is read at build time by
``tools/build.sh`` to produce ``build/FoloToy-AI-Passport-v<version>-full.bin``.

Versioning rule: start at 1.0.0; each digit is a base-10 position and when a
position reaches 10 it carries to the next higher position (0..9 per position,
except the leading position which may grow, e.g. 9.9.9 -> 10.0.0).

Examples:
    1.0.0 -> 1.0.1 -> ... -> 1.0.9 -> 1.1.0 -> ... -> 1.9.9 -> 2.0.0
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMAKE_LISTS = REPO_ROOT / "firmware" / "CMakeLists.txt"
PROJECT_RE = re.compile(
    r"^(project\(\s*[^()\s]+\s+VERSION\s+)(\d+(?:\.\d+)*)(\s*\)\s*)$",
    re.MULTILINE,
)


def read_version(path: Path = CMAKE_LISTS) -> str:
    text = path.read_text(encoding="utf-8")
    match = PROJECT_RE.search(text)
    if not match:
        raise ValueError(f"{path}: 未找到 project(... VERSION ...) 行")
    return match.group(2)


def write_version(version: str, path: Path = CMAKE_LISTS) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = PROJECT_RE.subn(
        lambda m: f"{m.group(1)}{version}{m.group(3)}", text, count=1
    )
    if count != 1:
        raise ValueError(f"{path}: 未找到 project(... VERSION ...) 行")
    path.write_text(new_text, encoding="utf-8")


def parse_version(version: str) -> list[int]:
    if not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise ValueError(f"无效版本号: {version!r}")
    return [int(part) for part in version.split(".")]


def format_version(parts: list[int]) -> str:
    return ".".join(str(part) for part in parts)


def next_version(version: str) -> str:
    """Return the next version per the carry rule (each digit resets at 10)."""
    parts = parse_version(version)
    i = len(parts) - 1
    while i >= 0:
        parts[i] += 1
        if parts[i] < 10:
            break
        parts[i] = 0
        i -= 1
    if i < 0:
        # Every position carried; the leading position becomes 10 (e.g. 9.9.9 -> 10.0.0).
        parts[0] = 10
    return format_version(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--current",
        action="store_true",
        help="只打印当前版本，不修改文件",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印下一个版本，不修改文件",
    )
    args = parser.parse_args(argv)

    current = read_version()
    if args.current:
        print(current)
        return 0

    bumped = next_version(current)
    if args.dry_run:
        print(bumped)
        return 0

    write_version(bumped)
    print(f"{current} -> {bumped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())