# SPDX-License-Identifier: GPL-3.0-or-later
"""Export an RPDL `.torrent` file: magnet link, safe file name and atomic write.

The file and the magnet link hold the user's tracker passkey. Callers must
never log them.
"""
from __future__ import annotations

import os
import re
from contextlib import suppress
from urllib.parse import quote

from core.downloads.bencode import parse_torrent, trackers

_MAX_NAME_BYTES = 150
_SUFFIX = ".torrent"
_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')


def magnet_link(data: bytes) -> str:
    """`magnet:?xt=urn:btih:<hex>&dn=<name>&tr=...`; ValueError for a bad torrent."""
    meta = parse_torrent(data)
    parts = [f"xt=urn:btih:{meta.info_hash}"]
    if meta.name:
        parts.append(f"dn={quote(meta.name, safe='')}")
    parts.extend(f"tr={quote(url, safe='')}" for url in trackers(data))
    return "magnet:?" + "&".join(parts)


def safe_file_name(name: str, fallback: str) -> str:
    """A file name for `name` that ends in `.torrent`; `fallback` when nothing is left."""
    text = _UNSAFE.sub("_", name or "")
    if text.lower().endswith(_SUFFIX):
        text = text[: -len(_SUFFIX)]
    text = text.strip(". ")
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_NAME_BYTES:
        text = encoded[:_MAX_NAME_BYTES].decode("utf-8", "ignore").rstrip(". ")
    if not text:
        text = _UNSAFE.sub("_", fallback).strip(". ") or "torrent"
    return text + _SUFFIX


def write_torrent(folder: str, file_name: str, data: bytes) -> str:
    """Write `data` to `folder/file_name` through a hidden `.part` file; return the path.

    An existing file with that name is replaced. On any error the `.part` file is removed.
    """
    final = os.path.join(folder, file_name)
    part = os.path.join(folder, f".{file_name}.part")
    try:
        with open(part, "wb") as handle:
            _ = handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, final)
    except BaseException:
        with suppress(OSError):
            os.remove(part)
        raise
    return final
