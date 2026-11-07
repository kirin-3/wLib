# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the few `.torrent` facts the main process needs, without libtorrent."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import cast

Value = int | bytes | list["Value"] | dict[bytes, "Value"]


@dataclass(frozen=True)
class TorrentMeta:
    info_hash: str
    name: str
    size: int
    private: bool
    file_count: int


def _decode(data: bytes, pos: int, depth: int, spans: dict[bytes, tuple[int, int]] | None) -> tuple[Value, int]:
    if depth > 64 or pos >= len(data):
        raise ValueError("bad bencode")
    ch = data[pos : pos + 1]
    if ch == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1 : end]), end + 1
    if ch == b"l":
        items: list[Value] = []
        pos += 1
        while data[pos : pos + 1] != b"e":
            item, pos = _decode(data, pos, depth + 1, None)
            items.append(item)
        return items, pos + 1
    if ch == b"d":
        result: dict[bytes, Value] = {}
        pos += 1
        while data[pos : pos + 1] != b"e":
            key, pos = _decode(data, pos, depth + 1, None)
            if not isinstance(key, bytes):
                raise ValueError("bad bencode")
            start = pos
            value, pos = _decode(data, pos, depth + 1, None)
            if spans is not None:
                spans[key] = (start, pos)
            result[key] = value
        return result, pos + 1
    if ch.isdigit():
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        end = colon + 1 + length
        if end > len(data):
            raise ValueError("bad bencode")
        return data[colon + 1 : end], end
    raise ValueError("bad bencode")


def parse_torrent(data: bytes) -> TorrentMeta:
    """Info hash, name, total size and private flag; ValueError for anything else."""
    try:
        spans: dict[bytes, tuple[int, int]] = {}
        top, end = _decode(data, 0, 0, spans)
        if end != len(data) or not isinstance(top, dict):
            raise ValueError("bad torrent")
        info = top.get(b"info")
        if not isinstance(info, dict) or b"info" not in spans:
            raise ValueError("bad torrent")
        start, stop = spans[b"info"]
        name = info.get(b"name")
        files = info.get(b"files")
        if isinstance(files, list):
            size = 0
            for entry in cast(list[Value], files):
                length = entry.get(b"length") if isinstance(entry, dict) else None
                if not isinstance(length, int):
                    raise ValueError("bad torrent")
                size += length
            count = len(files)
        else:
            length = info.get(b"length")
            if not isinstance(length, int):
                raise ValueError("bad torrent")
            size, count = length, 1
        return TorrentMeta(
            info_hash=hashlib.sha1(data[start:stop]).hexdigest(),
            name=name.decode("utf-8", "replace") if isinstance(name, bytes) else "",
            size=size,
            private=info.get(b"private") == 1,
            file_count=count,
        )
    except (IndexError, ValueError) as exc:
        raise ValueError("bad torrent") from exc
