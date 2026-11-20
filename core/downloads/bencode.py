# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the few `.torrent` facts the main process needs, without libtorrent."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import cast

Value = int | bytes | list["Value"] | dict[bytes, "Value"]


@dataclass(frozen=True)
class TorrentMeta:
    """`info_hash` is the hex SHA-1 v1 hash, or the hex SHA-256 v2 hash of a v2-only torrent.

    `info_hash_v2` is the hex SHA-256 v2 hash of a v2 or hybrid torrent, else "".
    """

    info_hash: str
    name: str
    size: int
    private: bool
    file_count: int
    info_hash_v2: str = ""


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


def _file_tree_size(tree: Value | None, depth: int = 0) -> tuple[int, int]:
    """Total length and file count of a v2 `file tree`; an empty key holds a file's own fields."""
    if depth > 64 or not isinstance(tree, dict) or not tree:
        raise ValueError("bad torrent")
    size = count = 0
    for key, node in tree.items():
        if key == b"":
            length = node.get(b"length") if isinstance(node, dict) else None
            if not isinstance(length, int) or length < 0:
                raise ValueError("bad torrent")
            size, count = size + length, count + 1
        else:
            sub_size, sub_count = _file_tree_size(node, depth + 1)
            size, count = size + sub_size, count + sub_count
    return size, count


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
        v2 = info.get(b"meta version") == 2
        v2_only = v2 and files is None and b"length" not in info
        if v2_only:
            # v2-only (BEP 52): no v1 file list, so the v2 hash names the torrent.
            size, count = _file_tree_size(info.get(b"file tree"))
        elif isinstance(files, list):
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
        v2_hash = hashlib.sha256(data[start:stop]).hexdigest() if v2 else ""
        return TorrentMeta(
            info_hash=v2_hash if v2_only else hashlib.sha1(data[start:stop]).hexdigest(),
            name=name.decode("utf-8", "replace") if isinstance(name, bytes) else "",
            size=size,
            private=info.get(b"private") == 1,
            file_count=count,
            info_hash_v2=v2_hash,
        )
    except (IndexError, ValueError) as exc:
        raise ValueError("bad torrent") from exc


def trackers(data: bytes) -> list[str]:
    """`announce` then every `announce-list` tier, in file order, without duplicates.

    Entries that are not byte strings, and tiers that are not lists, are skipped.
    ValueError when the data is not a bencoded dictionary.
    """
    try:
        top, _end = _decode(data, 0, 0, None)
    except (IndexError, ValueError) as exc:
        raise ValueError("bad torrent") from exc
    if not isinstance(top, dict):
        raise ValueError("bad torrent")
    found: list[str] = []

    def add(value: Value) -> None:
        if not isinstance(value, bytes):
            return
        text = value.decode("utf-8", "replace").strip()
        if text and text not in found:
            found.append(text)

    add(top.get(b"announce", b""))
    tiers = top.get(b"announce-list")
    if isinstance(tiers, list):
        for tier in cast(list[Value], tiers):
            if isinstance(tier, list):
                for entry in cast(list[Value], tier):
                    add(entry)
    return found
