# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, read-only LiteDB 4 reader for Playnite library snapshots.

Layout reference: LiteDB v4.1.4 (MIT), Engine/Pages and Document/Bson.
Only live documents reached through the collection's primary index are read.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID
from typing import cast, final

from core.backup_format import BackupValidationError

PAGE_SIZE = 4096
MAX_DOCUMENT_SIZE = 16 * 1024 * 1024
EMPTY_PAGE = 0xFFFFFFFF


def _invalid() -> BackupValidationError:
    return BackupValidationError(
        "The file is not a Playnite library.", "invalid_source"
    )


@final
class _Cursor:
    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.pos = offset

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise _invalid()
        start = self.pos
        self.pos += count
        return self.data[start : self.pos]

    def number(self, fmt: str) -> int:
        return int(cast(int, struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]))

    def string(self) -> str:
        return self.take(self.number("<i")).decode("utf-8")

    def address(self) -> tuple[int, int]:
        return self.number("<I"), self.number("<H")

    def cstring(self) -> str:
        end = self.data.find(b"\0", self.pos)
        if end < 0:
            raise _invalid()
        result = self.take(end - self.pos).decode("utf-8")
        _ = self.take(1)
        return result


def _document(cursor: _Cursor, depth: int = 0) -> dict[str, object]:
    if depth > 100:
        raise _invalid()
    size = cursor.number("<i")
    if size < 5 or size > MAX_DOCUMENT_SIZE:
        raise _invalid()
    # A bounded subcursor also prevents nested values from crossing their parent.
    body = _Cursor(cursor.take(size - 4))
    result: dict[str, object] = {}
    while body.pos < len(body.data) - 1:
        kind = body.number("<B")
        key = body.cstring()
        value: object
        if kind == 1:
            value = struct.unpack("<d", body.take(8))[0]
        elif kind == 2:
            length = body.number("<i")
            if length < 1:
                raise _invalid()
            value = body.take(length - 1).decode("utf-8")
            if body.take(1) != b"\0":
                raise _invalid()
        elif kind in (3, 4):
            nested = _document(body, depth + 1)
            if kind == 4:
                if list(nested) != [str(i) for i in range(len(nested))]:
                    raise _invalid()
                value = list(nested.values())
            else:
                value = nested
        elif kind == 5:
            length = body.number("<i")
            subtype = body.number("<B")
            binary = body.take(length)
            # LiteDB writes .NET Guid.ToByteArray(), including for subtype 4.
            value = str(UUID(bytes_le=binary)) if subtype == 4 else binary
        elif kind == 7:
            value = body.take(12).hex()
        elif kind == 8:
            flag = body.number("<B")
            if flag not in (0, 1):
                raise _invalid()
            value = bool(flag)
        elif kind == 9:
            ms = body.number("<q")
            if ms == 253402300800000:
                value = datetime.max.replace(tzinfo=timezone.utc)
            else:
                value = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
                    milliseconds=ms
                )
        elif kind in (10, 127, 255):
            value = None
        elif kind == 16:
            value = body.number("<i")
        elif kind == 18:
            value = body.number("<q")
        else:
            raise _invalid()
        result[key] = value
    if body.take(1) != b"\0" or body.pos != len(body.data):
        raise _invalid()
    return result


def decode_bson(data: bytes) -> dict[str, object]:
    try:
        cursor = _Cursor(data)
        result = _document(cursor)
        if cursor.pos != len(data):
            raise _invalid()
        return result
    except (
        ValueError,
        UnicodeError,
        struct.error,
        OverflowError,
        RecursionError,
    ) as exc:
        raise _invalid() from exc


@final
class LiteDBReader:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.page_count = self.path.stat().st_size // PAGE_SIZE
        self.index_pages: dict[
            int, dict[int, tuple[tuple[int, int], tuple[int, int]]]
        ] = {}
        self.data_pages: dict[int, dict[int, tuple[int, bytes]]] = {}

    def _page(self, page_id: int, kind: int) -> bytes:
        if page_id < 0 or page_id >= self.page_count:
            raise _invalid()
        with self.path.open("rb") as stream:
            _ = stream.seek(page_id * PAGE_SIZE)
            data = stream.read(PAGE_SIZE)
        if (
            len(data) != PAGE_SIZE
            or struct.unpack_from("<I", data)[0] != page_id
            or data[4] != kind
        ):
            raise _invalid()
        return data

    def _node(
        self, address: tuple[int, int]
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        page_id, index = address
        if page_id not in self.index_pages:
            page = self._page(page_id, 3)
            cursor = _Cursor(page, 25)
            nodes: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {}
            for _ in range(struct.unpack_from("<H", page, 13)[0]):
                slot = cursor.number("<H")
                levels = cursor.number("<B")
                if not 1 <= levels <= 32 or slot in nodes:
                    raise _invalid()
                _ = cursor.take(13)  # index slot and document prev/next addresses
                key_size = cursor.number("<H")
                _ = cursor.take(key_size + 1)  # key type followed by its payload
                block = cursor.address()
                _ = cursor.address()  # previous node at the bottom level
                next_node = cursor.address()
                _ = cursor.take((levels - 1) * 12)
                nodes[slot] = (block, next_node)
            self.index_pages[page_id] = nodes
        if index not in self.index_pages[page_id]:
            raise _invalid()
        return self.index_pages[page_id][index]

    def _data(self, address: tuple[int, int]) -> bytes:
        page_id, index = address
        if page_id not in self.data_pages:
            page = self._page(page_id, 4)
            cursor = _Cursor(page, 25)
            blocks: dict[int, tuple[int, bytes]] = {}
            for _ in range(struct.unpack_from("<H", page, 13)[0]):
                slot = cursor.number("<H")
                extend = cursor.number("<I")
                data = cursor.take(cursor.number("<H"))
                if slot in blocks:
                    raise _invalid()
                blocks[slot] = (extend, data)
            self.data_pages[page_id] = blocks
        if index not in self.data_pages[page_id]:
            raise _invalid()
        extend, initial = self.data_pages[page_id][index]
        data = bytearray(initial)
        visited: set[int] = set()
        while extend != EMPTY_PAGE:
            if extend in visited:
                raise _invalid()
            visited.add(extend)
            page = self._page(extend, 5)
            size = struct.unpack_from("<H", page, 13)[0]
            if size > PAGE_SIZE - 25 or len(data) + size > MAX_DOCUMENT_SIZE:
                raise _invalid()
            data.extend(page[25 : 25 + size])
            extend = struct.unpack_from("<I", page, 9)[0]
        return bytes(data)

    def read_collection(self, name: str) -> list[dict[str, object]]:
        try:
            if self.path.stat().st_size % PAGE_SIZE:
                raise _invalid()
            header = self._page(0, 1)
            cursor = _Cursor(header, 25)
            if (
                cursor.take(len(b"** This is a LiteDB file **"))
                != b"** This is a LiteDB file **"
                or cursor.number("<B") != 7
            ):
                raise _invalid()
            _ = cursor.take(12)
            if any(cursor.take(20)):
                raise BackupValidationError(
                    "Encrypted Playnite libraries are not supported.", "invalid_source"
                )
            _ = cursor.take(16)
            collections: dict[str, int] = {}
            for _ in range(cursor.number("<B")):
                collection_name = cursor.string()
                collections[collection_name.casefold()] = cursor.number("<I")
            if header[-1]:
                raise BackupValidationError(
                    "Close Playnite and try again.", "source_locked"
                )
            if name.casefold() not in collections:
                raise _invalid()
            cursor = _Cursor(self._page(collections[name.casefold()], 2), 25)
            if cursor.string().casefold() != name.casefold():
                raise _invalid()
            count = cursor.number("<q")
            _ = cursor.take(4)
            if cursor.string().split("=")[0] != "_id":
                raise _invalid()
            _ = cursor.take(1)
            head, tail = cursor.address(), cursor.address()
            _, current = self._node(head)
            visited = {head}
            documents: list[dict[str, object]] = []
            while current != tail:
                if current in visited:
                    raise _invalid()
                visited.add(current)
                block, current = self._node(current)
                documents.append(decode_bson(self._data(block)))
            if len(documents) != count:
                raise _invalid()
            return documents
        except BackupValidationError:
            # Locked and encrypted libraries carry their own error code/message.
            raise
        except (
            ValueError,
            UnicodeError,
            struct.error,
            OverflowError,
            RecursionError,
        ) as exc:
            raise _invalid() from exc
