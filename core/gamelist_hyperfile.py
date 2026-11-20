# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the Game-List HyperFile layout used by Game-List 2.17.18.14."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from struct import unpack_from
from unicodedata import category

from core.backup_format import BackupValidationError

_SIGNATURE = b"PCS\0"
_MAX_FILE_SIZE = 256 * 1024 * 1024
_RECORD_SIZE = 2456
_GUID_OFFSET = 2372
_GUID = re.compile(rb"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\x00", re.I)
_ROW_HEADERS = (b"\x10\x01\x00", b"\x10\x02\x00", b"\x10\x03\x00")
_DELETED = b"\x10\x02\x00"


@dataclass(frozen=True, slots=True)
class GameListRecord:
    url: str
    guid: str
    record_id: int
    name: str
    version: str
    homepage: str
    developer: str
    added: str
    memo_text: str


@dataclass(frozen=True, slots=True)
class GameListTable:
    records: list[GameListRecord]
    full: int
    passed: int
    failed: int
    partial: int
    deleted: int


def _unsupported() -> BackupValidationError:
    return BackupValidationError("This Game-List version is not supported.", "unsupported_version")


def _uint32(data: bytes, offset: int) -> int:
    return unpack_from("<I", data, offset)[0]


def _wide_text(data: bytes) -> str:
    # Split on a UTF-16 code unit boundary before decoding. Bytes after the
    # terminator may be stale, including malformed code units.
    end = next((i for i in range(0, len(data), 2) if data[i:i + 2] == b"\0\0"), len(data))
    value = data[:end].decode("utf-16-le")
    # isprintable() excludes Unicode space separators such as non-breaking spaces.
    if end == len(data) or any(
        char == "\ufffd" or (not char.isprintable() and category(char) != "Zs")
        for char in value
    ):
        raise ValueError("Invalid Game-List text")
    return value.strip()


def _record_id(row: bytes) -> int:
    if row[:3] in _ROW_HEADERS:
        value = _uint32(row, 12)
        if 1 <= value <= 10_000_000:
            return value
    return 0


def _added_date(row: bytes, max_year: int) -> str:
    day, month, year, milliseconds = unpack_from("<BBHI", row, 2237)
    if not 2000 <= year <= max_year or milliseconds >= 86_400_000:
        return ""
    try:
        return (datetime(year, month, day) + timedelta(milliseconds=milliseconds)).isoformat()
    except ValueError:
        return ""


def _memo_data_end(memo: bytes, pointer: int, kind: int) -> int:
    # The first 21 bytes describe the allocation. The stored length excludes
    # that header and may end well before the allocated space.
    if not len(_SIGNATURE) <= pointer <= len(memo) - 21:
        return 0
    exponent, marker = memo[pointer:pointer + 2]
    if not 7 <= exponent <= 28 or marker != kind:
        return 0
    end = pointer + 21 + _uint32(memo, pointer + 2)
    return end if end <= min(len(memo), pointer + (1 << exponent)) else 0


def _memo_text(memo: bytes, pointer: int) -> str:
    end = _memo_data_end(memo, pointer, 0xF4)
    if not end:
        end = _memo_data_end(memo, pointer, 0xE4)
    text_start = pointer + 40
    if end < text_start:
        return ""
    length = _uint32(memo, pointer + 36)
    text_end = text_start + length
    if text_end <= end:
        return memo[text_start:text_end].decode("cp1252", errors="replace")
    if memo[pointer + 1] != 0xE4:
        return ""

    continuation = unpack_from("<Q", memo, pointer + 11)[0]
    continuation_end = _memo_data_end(memo, continuation, 0xD4)
    tail_start = continuation + 21
    tail_end = tail_start + (text_end - end)
    if tail_end > continuation_end:
        return ""
    return (memo[text_start:end] + memo[tail_start:tail_end]).decode("cp1252", errors="replace")


def _decode_record(row: bytes, memo: bytes, max_year: int) -> GameListRecord | None:
    guid = _GUID.match(row, _GUID_OFFSET)
    record_id = _record_id(row)
    if not guid or not record_id:
        return None
    url_bytes, terminator, _ = row[1975:2231].partition(b"\0")
    if not terminator or any(not 32 <= byte <= 126 for byte in url_bytes):
        return None
    url = url_bytes.decode("ascii")
    if url and not url.lower().startswith(("http://", "https://")):
        return None
    try:
        name = _wide_text(row[16:218])
        version = _wide_text(row[218:320])
        homepage = _wide_text(row[320:840])
        developer = _wide_text(row[1463:1975])
    except ValueError:
        return None
    if not name:
        return None
    return GameListRecord(
        url, guid[0][:-1].decode("ascii").lower(), record_id, name, version,
        homepage, developer, _added_date(row, max_year),
        _memo_text(memo, _uint32(row, 2253)),
    )


def _first_record(data: bytes, max_year: int) -> int:
    for guid in _GUID.finditer(data):
        start = guid.start() - _GUID_OFFSET
        if start < len(_SIGNATURE) or start + _RECORD_SIZE > len(data):
            continue
        if _decode_record(data[start:start + _RECORD_SIZE], b"", max_year) is not None:
            # Once the alignment is known, include preceding rows whose
            # headers survived even if a damaged field kept them from anchoring.
            while start - _RECORD_SIZE >= len(_SIGNATURE):
                previous = start - _RECORD_SIZE
                if not _record_id(data[previous:previous + 16]):
                    break
                start = previous
            return start
    raise _unsupported()


def _read_file(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as handle:
        return handle.read(_MAX_FILE_SIZE + 1)


def read_gamelist_table(path: Path) -> GameListTable:
    """Validate and read a table and its optional sibling memo without locking."""
    try:
        data = _read_file(path)
    except FileNotFoundError as exc:
        raise BackupValidationError("Game-List library was not found.", "file_not_found") from exc
    except OSError as exc:
        raise BackupValidationError(f"Could not read Game-List library: {exc}", "read_failed") from exc
    if len(data) > _MAX_FILE_SIZE:
        raise _unsupported()
    if not data.startswith(_SIGNATURE):
        raise BackupValidationError("The file is not a Game-List library.", "invalid_source")

    max_year = datetime.now().year + 1
    start = _first_record(data, max_year)
    full, remainder = divmod(len(data) - start, _RECORD_SIZE)
    try:
        memo = _read_file(path.with_name("Games.mmo"))
    except OSError:
        memo = b""
    if len(memo) > _MAX_FILE_SIZE or not memo.startswith(_SIGNATURE):
        memo = b""

    records: list[GameListRecord] = []
    failed = deleted = 0
    for offset in range(start, start + full * _RECORD_SIZE, _RECORD_SIZE):
        row = data[offset:offset + _RECORD_SIZE]
        if row.startswith(_DELETED) and _record_id(row):
            deleted += 1
        elif (record := _decode_record(row, memo, max_year)) is None:
            failed += 1
        else:
            records.append(record)
    if failed * 10 > full:
        raise _unsupported()
    return GameListTable(records, full, len(records), failed, int(bool(remainder)), deleted)
