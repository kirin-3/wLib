# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic Game-List tables and memos for parser and import tests."""

from pathlib import Path
from struct import pack_into

import pytest

from core import gamelist_hyperfile
from core.backup_format import BackupValidationError
from core.gamelist_hyperfile import read_gamelist_table


def gamelist_record(
    record_id: int = 1,
    *,
    name: str = "Sample Game",
    url: str = "https://f95zone.to/threads/1234/",
    guid: str = "",
    version: str = "1.0",
    homepage: str = "",
    developer: str = "Sample Studio",
    added: tuple[int, int, int, int] = (8, 1, 2018, 48_001_000),
    memo_pointer: int = 0,
    state: int = 1,
) -> bytes:
    row = bytearray(2456)
    row[:3] = bytes((0x10, state, 0))
    pack_into("<I", row, 12, record_id)
    for offset, value in [(16, name), (218, version), (320, homepage), (1463, developer)]:
        encoded = value.encode("utf-16-le")
        row[offset:offset + len(encoded)] = encoded
    url_bytes = url.encode("ascii")
    row[1975:1975 + len(url_bytes)] = url_bytes
    pack_into("<BBHI", row, 2237, *added)
    pack_into("<I", row, 2253, memo_pointer)
    row[2372:2408] = (guid or f"00000000-0000-0000-0000-{record_id:012x}").encode("ascii")
    return bytes(row)


def write_gamelist(
    directory: Path,
    *records: bytes,
    header: bytes = b"PCS\0" + bytes(66),
    memo: bytes = b"",
) -> Path:
    path = directory / "Games.fic"
    _ = path.write_bytes(header + b"".join(records))
    if memo:
        _ = (directory / "Games.mmo").write_bytes(memo)
    return path


def gamelist_memo(text: bytes, stale: bytes = b"") -> bytes:
    memo = bytearray(8 + 128)  # First block starts at byte 8.
    memo[:4] = b"PCS\0"
    memo[8:10] = b"\x07\xf4"
    pack_into("<I", memo, 10, 19 + len(text))
    pack_into("<I", memo, 8 + 36, len(text))
    memo[48:48 + len(text + stale)] = text + stale
    return bytes(memo)


@pytest.mark.parametrize("header_size", [4, 8191])
def test_reads_records_with_variable_header(tmp_path, header_size):
    path = write_gamelist(
        tmp_path,
        gamelist_record(name="Sakura 桜", guid="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"),
        gamelist_record(2, name="Second", url="", state=3),
        header=b"PCS\0" + b"\x9a" * (header_size - 4),
    )
    result = read_gamelist_table(path)
    first, second = result.records
    assert (result.full, result.passed, result.failed, result.partial, result.deleted) == (2, 2, 0, 0, 0)
    assert (first.name, first.guid, first.added, first.developer) == (
        "Sakura 桜", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "2018-01-08T13:20:01", "Sample Studio",
    )
    assert second.url == ""


def test_rejects_missing_signature(tmp_path):
    path = write_gamelist(tmp_path, gamelist_record(), header=b"JUNK")
    with pytest.raises(BackupValidationError, match="not a Game-List library") as error:
        _ = read_gamelist_table(path)
    assert error.value.error_code == "invalid_source"


def test_rejects_shifted_layout(tmp_path):
    row = gamelist_record()
    path = write_gamelist(tmp_path, row[1975:] + row[:1975])
    with pytest.raises(BackupValidationError, match="version is not supported") as error:
        _ = read_gamelist_table(path)
    assert error.value.error_code == "unsupported_version"


def test_caps_table_size(tmp_path, monkeypatch):
    path = write_gamelist(tmp_path, gamelist_record())
    monkeypatch.setattr(gamelist_hyperfile, "_MAX_FILE_SIZE", path.stat().st_size - 1)
    with pytest.raises(BackupValidationError, match="version is not supported"):
        _ = read_gamelist_table(path)


def test_counts_damaged_partial_and_deleted_rows(tmp_path):
    damaged = bytearray(gamelist_record(1))
    damaged[2372] = ord("X")
    path = write_gamelist(
        tmp_path, bytes(damaged),
        *(gamelist_record(i) for i in range(2, 41)),
        gamelist_record(41, state=2), gamelist_record(42)[:600],
    )
    result = read_gamelist_table(path)
    assert (result.full, result.passed, result.failed, result.partial, result.deleted) == (41, 39, 1, 1, 1)
    assert [record.record_id for record in result.records] == list(range(2, 41))


def test_rejects_mostly_unreadable_rows(tmp_path):
    path = write_gamelist(tmp_path, gamelist_record(), bytes(2456))
    with pytest.raises(BackupValidationError, match="version is not supported"):
        _ = read_gamelist_table(path)


def test_deleted_row_has_no_game_even_when_it_is_the_only_row(tmp_path):
    result = read_gamelist_table(write_gamelist(tmp_path, gamelist_record(state=2)))
    assert (result.full, result.passed, result.failed, result.partial, result.deleted) == (1, 0, 0, 0, 1)
    assert result.records == []


def test_skips_malformed_utf16_name(tmp_path):
    damaged = bytearray(gamelist_record(11))
    damaged[16:20] = b"\x00\xd8\0\0"  # Unpaired surrogate followed by a terminator.
    path = write_gamelist(
        tmp_path, *(gamelist_record(i) for i in range(1, 11)), bytes(damaged)
    )
    result = read_gamelist_table(path)
    assert result.passed == 10
    assert result.failed == 1


def test_reads_memo_length_and_ignores_bad_pointer_and_stale_tail(tmp_path):
    path = write_gamelist(
        tmp_path,
        gamelist_record(1, memo_pointer=8),
        gamelist_record(2, memo_pointer=10_000),
        memo=gamelist_memo(b"Completed;RPGM", b";Unity;stale"),
    )
    result = read_gamelist_table(path)
    assert [record.memo_text for record in result.records] == ["Completed;RPGM", ""]
    assert result.passed == 2


def test_rejects_memo_length_outside_its_block(tmp_path):
    memo = bytearray(gamelist_memo(b"Completed;RPGM", b";Unity;stale"))
    pack_into("<I", memo, 8 + 36, 100)
    path = write_gamelist(
        tmp_path, gamelist_record(memo_pointer=8), memo=bytes(memo) + bytes(128)
    )
    result = read_gamelist_table(path)
    assert result.records[0].memo_text == ""


def test_reads_memo_continuation(tmp_path):
    head, tail = b"Completed;RPGM;" + 73 * b"x", b";end"
    memo = bytearray(8 + 256)
    memo[:4] = b"PCS\0"
    memo[8:10] = b"\x07\xe4"
    pack_into("<I", memo, 8 + 2, 19 + len(head))
    pack_into("<Q", memo, 8 + 11, 136)
    pack_into("<I", memo, 8 + 36, len(head + tail))
    memo[48:136] = head
    memo[136:138] = b"\x07\xd4"
    pack_into("<I", memo, 136 + 2, len(tail))
    memo[157:161] = tail
    path = write_gamelist(tmp_path, gamelist_record(memo_pointer=8), memo=bytes(memo))
    assert read_gamelist_table(path).records[0].memo_text == (head + tail).decode()
