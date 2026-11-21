#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dev-only script: round-trip every RGSS Marshal file under one or more folders.

Checks loose *.rxdata / *.rvdata / *.rvdata2 files and matching entries inside
RGSS archives (via core.rgss_tools). Exits non-zero on any decode error or byte
mismatch. Never commit real game files.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.rgss_tools import (  # noqa: E402
    RgssUnsupportedError,
    decrypt_chunk,
    discover_archives,
    read_rgss_index,
)
from core.ruby_marshal import (  # noqa: E402
    MAX_BYTES,
    MarshalUnsupported,
    dump_all,
    load_all,
)

MARSHAL_EXTS = (".rxdata", ".rvdata", ".rvdata2")


def _check_bytes(label: str, raw: bytes) -> tuple[bool, str]:
    try:
        dumps = load_all(raw)
        encoded = dump_all(dumps)
    except MarshalUnsupported as exc:
        return False, f"UNSUPPORTED {label}: {exc}"
    if encoded != raw:
        pos = next(
            (i for i, (a, b) in enumerate(zip(raw, encoded, strict=False)) if a != b),
            min(len(raw), len(encoded)),
        )
        return (
            False,
            f"MISMATCH {label}: len {len(raw)} -> {len(encoded)}, first diff at byte {pos}",
        )
    return True, ""


def check_folder(folder: str) -> tuple[int, int, list[str]]:
    checked = 0
    mismatches = 0
    errors: list[str] = []

    for dirpath, _dirnames, filenames in os.walk(folder):
        for filename in sorted(filenames):
            if not filename.lower().endswith(MARSHAL_EXTS):
                continue
            path = os.path.join(dirpath, filename)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            checked += 1
            raw = Path(path).read_bytes()
            ok, msg = _check_bytes(path, raw)
            if not ok:
                mismatches += 1
                errors.append(msg)

        for archive in discover_archives(dirpath):
            try:
                index = read_rgss_index(archive.path)
            except RgssUnsupportedError as exc:
                errors.append(f"ARCHIVE UNSUPPORTED {archive.path}: {exc}")
                continue
            with open(archive.path, "rb") as handle:
                for entry in index.entries:
                    if not entry.name.lower().endswith(MARSHAL_EXTS):
                        continue
                    if entry.size > MAX_BYTES:
                        errors.append(
                            f"SKIP TOO LARGE {archive.path}:{entry.name} ({entry.size} bytes)"
                        )
                        continue
                    checked += 1
                    _ = handle.seek(entry.offset)
                    encrypted = handle.read(entry.size)
                    raw, _next_key = decrypt_chunk(encrypted, entry.key)
                    ok, msg = _check_bytes(f"{archive.path}:{entry.name}", raw)
                    if not ok:
                        mismatches += 1
                        errors.append(msg)

    return checked, mismatches, errors


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"Usage: {argv[0]} <folder> [<folder> ...]", file=sys.stderr)
        return 2
    total_checked = 0
    total_mismatches = 0
    for folder in argv[1:]:
        if not os.path.isdir(folder):
            print(f"Not a folder: {folder}", file=sys.stderr)
            return 2
        checked, mismatches, errors = check_folder(folder)
        total_checked += checked
        total_mismatches += mismatches
        print(f"{folder}: checked={checked}, mismatches={mismatches}")
        for err in errors:
            print(f"  {err}", file=sys.stderr)
    print(f"TOTAL: checked={total_checked}, mismatches={total_mismatches}")
    return 1 if total_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
