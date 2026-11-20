# SPDX-License-Identifier: GPL-3.0-or-later
"""Export an RPDL `.torrent` file: magnet link, safe file name and atomic write.

The file and the magnet link hold the user's tracker passkey. Callers must
never log them.
"""
from __future__ import annotations

import os
import re
import secrets
from contextlib import suppress
from urllib.parse import quote

from core.downloads.bencode import parse_torrent, trackers

_MAX_NAME_BYTES = 150
_SUFFIX = ".torrent"
_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')


def magnet_link(data: bytes) -> str:
    """`magnet:?xt=urn:btih:<hex>&xt=urn:btmh:1220<hex>&dn=<name>&tr=...`; ValueError for a bad torrent.

    `btih` holds the v1 hash and `btmh` the v2 hash (a SHA-256 multihash);
    a v1 torrent has only `btih`, a v2-only one only `btmh`, a hybrid both.
    """
    meta = parse_torrent(data)
    parts: list[str] = []
    if meta.info_hash != meta.info_hash_v2:
        parts.append(f"xt=urn:btih:{meta.info_hash}")
    if meta.info_hash_v2:
        parts.append(f"xt=urn:btmh:1220{meta.info_hash_v2}")
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

    Every call uses its own `.part` file, so two exports of one name never share
    it. The file keeps the umask mode: a watch-folder client can run as another
    user. An existing file with that name is replaced. On any error the `.part`
    file is removed.
    """
    final = os.path.join(folder, file_name)
    while True:
        part = os.path.join(folder, f".{file_name}.{secrets.token_hex(4)}.part")
        try:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        break
    try:
        with os.fdopen(fd, "wb") as handle:
            _ = handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, final)
    except BaseException:
        with suppress(OSError):
            os.remove(part)
        raise
    return final
