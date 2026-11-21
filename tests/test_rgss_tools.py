# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import struct
import time
from collections import namedtuple
from collections.abc import Sequence
from pathlib import Path

import pytest

from core import rgss_tools
from core.game_tools import GameToolError
from core.rgss_tools import (
    CHUNK_SIZE,
    FORMAT_V1,
    FORMAT_V3,
    MAX_NAME_LENGTH,
    RgssUnsupportedError,
    decode_entry_name,
    decrypt_chunk,
    detect_archive_format,
    discover_archives,
    extract_archives,
    inspect_state,
    parse_rgss_header,
    read_entry,
    read_rgss_index,
    set_use_extracted,
    write_entry,
)

MASK32 = 0xFFFFFFFF


def _advance(key: int) -> int:
    return (key * 7 + 3) & MASK32


def _xor_rgss_data(data: bytes, key: int) -> bytes:
    out = bytearray(data)
    length = len(out)
    pos = 0
    while pos + 4 <= length:
        word = struct.unpack_from("<I", out, pos)[0] ^ key
        struct.pack_into("<I", out, pos, word)
        key = _advance(key)
        pos += 4
    if pos < length:
        key_bytes = key.to_bytes(4, "little")
        for i in range(length - pos):
            out[pos + i] ^= key_bytes[i]
    return bytes(out)


def build_rgss_v1(
    path: Path,
    entries: Sequence[tuple[str | bytes, bytes]],
    *,
    encoding: str = "utf-8",
) -> None:
    """Build an RGSSAD v1 archive (RPG Maker XP / VX)."""
    buf = bytearray(b"RGSSAD\x00\x01")
    key = 0xDEADCAFE
    for raw_name, data in entries:
        name_bytes = raw_name if isinstance(raw_name, bytes) else raw_name.encode(encoding)
        buf += struct.pack("<I", len(name_bytes) ^ key)
        key = _advance(key)
        for byte in name_bytes:
            buf.append(byte ^ (key & 0xFF))
            key = _advance(key)
        buf += struct.pack("<I", len(data) ^ key)
        key = _advance(key)
        buf += _xor_rgss_data(data, key)
    path.write_bytes(bytes(buf))


def build_rgss_v3(
    path: Path,
    entries: Sequence[tuple[str | bytes, bytes]],
    *,
    seed: int = 0x12345678,
    encoding: str = "utf-8",
) -> None:
    """Build an RGSSAD v3 archive (RPG Maker VX Ace)."""
    key = (seed * 9 + 3) & MASK32
    key_bytes = key.to_bytes(4, "little")
    encoded_entries: list[tuple[bytes, bytes, int]] = []
    for idx, (raw_name, data) in enumerate(entries):
        name_bytes = raw_name if isinstance(raw_name, bytes) else raw_name.encode(encoding)
        file_key = ((seed + idx * 0x10203 + 0x55AA) & MASK32) or 1
        encoded_entries.append((name_bytes, data, file_key))

    # Header (12 bytes) + index entries + 16-byte zero terminator
    index_size = sum(16 + len(name_bytes) for name_bytes, _data, _fk in encoded_entries) + 16
    data_offset = 12 + index_size

    buf = bytearray(b"RGSSAD\x00\x03")
    buf += struct.pack("<I", seed)
    body = bytearray()
    for name_bytes, data, file_key in encoded_entries:
        offset = data_offset + len(body)
        buf += struct.pack(
            "<IIII",
            offset ^ key,
            len(data) ^ key,
            file_key ^ key,
            len(name_bytes) ^ key,
        )
        for i, byte in enumerate(name_bytes):
            buf.append(byte ^ key_bytes[i % 4])
        body += _xor_rgss_data(data, file_key)
    buf += struct.pack("<IIII", key, key, key, key)
    buf += body
    path.write_bytes(bytes(buf))


def make_rgss_game(tmp_path: Path, name: str = "Game") -> Path:
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Game.exe").touch()
    (folder / "Game.ini").write_text("[Game]\nTitle=Test\n", encoding="utf-8")
    return folder


# ── Discovery and format detection ──


def test_discover_archives_top_level_case_and_backup(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v1(game / "Game.RGSSAD", [("a.txt", b"1")])
    build_rgss_v3(game / "Extra.rgss3a.wlib-backup", [("b.txt", b"2")])
    build_rgss_v3(game / "Patch.rgss2a", [("c.txt", b"3")])
    (game / "notes.txt").write_text("hi", encoding="utf-8")
    sub = game / "Sub"
    sub.mkdir()
    build_rgss_v3(sub / "Hidden.rgss3a", [("d.txt", b"4")])
    os.symlink(game / "Game.RGSSAD", game / "Link.rgss3a")

    found = discover_archives(str(game))
    assert [(a.name, a.filename, a.backup) for a in found] == [
        ("Extra.rgss3a", "Extra.rgss3a.wlib-backup", True),
        ("Game.RGSSAD", "Game.RGSSAD", False),
        ("Patch.rgss2a", "Patch.rgss2a", False),
    ]
    assert [detect_archive_format(a.path) for a in found] == [
        FORMAT_V3,
        FORMAT_V1,
        FORMAT_V3,
    ]


def test_format_detected_from_header_not_extension(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    # A v3 archive named .rgss2a must be recognized as RGSSAD v3.
    v3_named_v2 = game / "Game.rgss2a"
    build_rgss_v3(v3_named_v2, [("Data\\Scripts.rvdata2", b"scripts")])
    assert detect_archive_format(str(v3_named_v2)) == FORMAT_V3

    # A v1 archive named .rgss3a must be recognized as RGSSAD v1.
    v1_named_v3 = game / "Legacy.rgss3a"
    build_rgss_v1(v1_named_v3, [("Data\\Scripts.rxdata", b"scripts")])
    assert detect_archive_format(str(v1_named_v3)) == FORMAT_V1


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"RGSSAD\x00",
        b"RGSSAD\x00\x00",
        b"RGSSAD\x00\x02",
        b"RGSSAD\x00\x04",
        b"CUSTOM\x00\x03",
        b"not an archive",
    ],
)
def test_parse_header_rejects_unsupported(raw: bytes, tmp_path: Path) -> None:
    with pytest.raises(RgssUnsupportedError):
        _ = parse_rgss_header(raw)
    bad = tmp_path / "Bad.rgss3a"
    bad.write_bytes(raw)
    assert detect_archive_format(str(bad)) == ""


# ── Index reading and name decoding ──


def test_read_v1_and_v3_indexes(tmp_path: Path) -> None:
    v1 = tmp_path / "Game.rgssad"
    build_rgss_v1(
        v1,
        [
            ("Graphics\\Pictures\\title.png", b"png-data"),
            ("Data\\Scripts.rxdata", b"scripts"),
        ],
    )
    idx1 = read_rgss_index(str(v1))
    assert idx1.version == 1
    assert idx1.format == FORMAT_V1
    assert idx1.skipped_invalid == 0
    assert [(e.name, e.size) for e in idx1.entries] == [
        ("Graphics/Pictures/title.png", 8),
        ("Data/Scripts.rxdata", 7),
    ]

    v3 = tmp_path / "Game.rgss3a"
    build_rgss_v3(
        v3,
        [
            ("Graphics\\Faces\\Actor1.png", b"face"),
            ("Data\\Scripts.rvdata2", b"rv2"),
        ],
    )
    idx3 = read_rgss_index(str(v3))
    assert idx3.version == 3
    assert idx3.format == FORMAT_V3
    assert idx3.skipped_invalid == 0
    assert [(e.name, e.size) for e in idx3.entries] == [
        ("Graphics/Faces/Actor1.png", 4),
        ("Data/Scripts.rvdata2", 3),
    ]


def test_name_decoding_utf8_shift_jis_and_undecodable(tmp_path: Path) -> None:
    jp_name = "Graphics\\Pictures\\タイトル.png"
    sjis_bytes = jp_name.encode("cp932")
    # 0x81 0x20 is an invalid lead+trail pair in cp932 and invalid in UTF-8.
    bad_bytes = b"Graphics\\\x81\x20.png"

    assert decode_entry_name(sjis_bytes) == "Graphics/Pictures/タイトル.png"
    assert decode_entry_name(bad_bytes) is None

    v1 = tmp_path / "ShiftJis.rgssad"
    build_rgss_v1(
        v1,
        [
            (sjis_bytes, b"img"),
            (bad_bytes, b"skipme"),
            ("Audio\\BGM\\Theme.ogg", b"ogg"),
        ],
    )
    idx = read_rgss_index(str(v1))
    assert [e.name for e in idx.entries] == [
        "Graphics/Pictures/タイトル.png",
        "Audio/BGM/Theme.ogg",
    ]
    assert idx.skipped_invalid == 1

    v3 = tmp_path / "ShiftJis.rgss3a"
    build_rgss_v3(v3, [(sjis_bytes, b"img"), (bad_bytes, b"skipme")])
    idx3 = read_rgss_index(str(v3))
    assert [e.name for e in idx3.entries] == ["Graphics/Pictures/タイトル.png"]
    assert idx3.skipped_invalid == 1


def test_index_rejects_bad_version_overlong_name_and_truncation(tmp_path: Path) -> None:
    # Bad version byte
    bad_ver = tmp_path / "BadVer.rgss3a"
    bad_ver.write_bytes(b"RGSSAD\x00\x02" + b"\x00" * 32)
    with pytest.raises(RgssUnsupportedError):
        _ = read_rgss_index(str(bad_ver))

    # Over-long name length in v1 (> MAX_NAME_LENGTH)
    overlong_v1 = tmp_path / "Overlong.rgssad"
    key = 0xDEADCAFE
    overlong_v1.write_bytes(
        b"RGSSAD\x00\x01" + struct.pack("<I", (MAX_NAME_LENGTH + 1) ^ key)
    )
    with pytest.raises(RgssUnsupportedError):
        _ = read_rgss_index(str(overlong_v1))

    # Zero-length name in v3
    zero_v3 = tmp_path / "Zero.rgss3a"
    seed = 0x11111111
    k3 = (seed * 9 + 3) & MASK32
    zero_v3.write_bytes(
        b"RGSSAD\x00\x03"
        + struct.pack("<I", seed)
        + struct.pack("<IIII", 100 ^ k3, 4 ^ k3, 1 ^ k3, 0 ^ k3)
    )
    with pytest.raises(RgssUnsupportedError):
        _ = read_rgss_index(str(zero_v3))

    # Over-long name length in v3
    overlong_v3 = tmp_path / "Overlong.rgss3a"
    overlong_v3.write_bytes(
        b"RGSSAD\x00\x03"
        + struct.pack("<I", seed)
        + struct.pack("<IIII", 100 ^ k3, 4 ^ k3, 1 ^ k3, (MAX_NAME_LENGTH + 1) ^ k3)
    )
    with pytest.raises(RgssUnsupportedError):
        _ = read_rgss_index(str(overlong_v3))

    # Truncated v3 index (cuts off before terminator)
    trunc_v3 = tmp_path / "Trunc.rgss3a"
    build_rgss_v3(trunc_v3, [("a.txt", b"hello"), ("b.txt", b"world")])
    raw = trunc_v3.read_bytes()
    # Keep header + seed + partial first entry
    trunc_v3.write_bytes(raw[:20])
    with pytest.raises(RgssUnsupportedError):
        _ = read_rgss_index(str(trunc_v3))

    # Truncated v1 entry header (cuts off mid-header)
    trunc_v1 = tmp_path / "Trunc.rgssad"
    trunc_v1.write_bytes(b"RGSSAD\x00\x01\x01\x02")
    with pytest.raises(RgssUnsupportedError):
        _ = read_rgss_index(str(trunc_v1))


def test_v1_data_past_eof_skips_and_ends_walk(tmp_path: Path) -> None:
    archive = tmp_path / "CutData.rgssad"
    build_rgss_v1(
        archive,
        [
            ("first.txt", b"good"),
            ("truncated.bin", b"A" * 100),
            ("unreachable.txt", b"never"),
        ],
    )
    raw = archive.read_bytes()
    # Truncate inside the second entry's data
    # first entry: 8 (hdr) + 4 + 9 + 4 + 4 = 29; second entry hdr: 4 + 13 + 4 = 21 -> data starts at 50
    archive.write_bytes(raw[:70])

    idx = read_rgss_index(str(archive))
    assert [e.name for e in idx.entries] == ["first.txt"]
    assert idx.skipped_invalid == 1


def test_v3_data_past_eof_skips_entry_and_continues(tmp_path: Path) -> None:
    archive = tmp_path / "CutV3.rgss3a"
    build_rgss_v3(
        archive,
        [
            ("first.txt", b"good"),
            ("second.bin", b"B" * 200),
        ],
    )
    raw = archive.read_bytes()
    # Keep full index + first entry data, cut off part of second entry's data
    archive.write_bytes(raw[:-50])

    idx = read_rgss_index(str(archive))
    assert [e.name for e in idx.entries] == ["first.txt"]
    assert idx.skipped_invalid == 1


# ── Streamed decryption ──


@pytest.mark.parametrize("builder", [build_rgss_v1, build_rgss_v3])
def test_streamed_decrypt_round_trip_sizes(tmp_path: Path, builder) -> None:
    payloads = {
        "size0.bin": b"",
        "size1.bin": b"\xab",
        "size3.bin": b"\x01\x02\x03",
        "size4.bin": b"\xde\xad\xbe\xef",
        "size5.bin": b"hello",
        "nested/over_1mib.bin": bytes((i * 31 + 7) & 0xFF for i in range(CHUNK_SIZE + 17)),
    }
    archive = tmp_path / "Game.rgss3a"
    builder(archive, [(name.replace("/", "\\"), data) for name, data in payloads.items()])

    index = read_rgss_index(str(archive))
    out = tmp_path / "out"
    with open(archive, "rb") as file:
        for entry in index.entries:
            dest = out / entry.name
            write_entry(file, entry, str(dest), lambda: False)

    for name, expected in payloads.items():
        assert (out / name).read_bytes() == expected


def test_write_entry_cancel_between_chunks_cleans_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "Game.rgss3a"
    build_rgss_v3(archive, [("big.bin", b"Z" * 200)])
    monkeypatch.setattr(rgss_tools, "CHUNK_SIZE", 16)
    index = read_rgss_index(str(archive))
    dest = tmp_path / "out" / "big.bin"
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    with open(archive, "rb") as file:
        with pytest.raises(rgss_tools._Cancelled):
            write_entry(file, index.entries[0], str(dest), cancelled)

    assert not dest.exists()
    assert not [p for p in (tmp_path / "out").rglob("*") if p.name.endswith(".wlib-partial")]


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("WLIB_PERF_TESTS") != "1",
    reason="timing check for the dev host; set WLIB_PERF_TESTS=1 to run it",
)
def test_decrypt_throughput_64mib() -> None:
    chunk = bytes(range(256)) * (CHUNK_SIZE // 256)
    assert len(chunk) == CHUNK_SIZE
    key = 0xDEADCAFE
    start = time.perf_counter()
    for _ in range(64):
        plain, key = decrypt_chunk(chunk, key)
        assert len(plain) == CHUNK_SIZE
    elapsed = time.perf_counter() - start
    mib_per_sec = 64.0 / elapsed
    assert mib_per_sec >= 20.0, f"Decrypt throughput too low: {mib_per_sec:.1f} MiB/s"


# ── Extraction ──


def _no_progress(_done: int, _total: int, _current: str) -> None:
    return None


def _never() -> bool:
    return False


def _tree(root: Path) -> dict[str, bytes]:
    return {
        os.path.relpath(p, root).replace(os.sep, "/"): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_extract_folder_mode_overwrites_and_keeps_game_untouched(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(
        game / "Game.rgss3a",
        [
            ("Graphics\\Faces\\Actor1.png", b"face-new"),
            ("Data\\Scripts.rvdata2", b"scripts"),
        ],
    )
    out = tmp_path / "rip"
    (out / "Game" / "Graphics" / "Faces").mkdir(parents=True)
    (out / "Game" / "Graphics" / "Faces" / "Actor1.png").write_bytes(b"face-old")
    before = _tree(game)

    result = extract_archives(str(game), "folder", str(out), _no_progress, _never)

    assert _tree(game) == before
    assert _tree(out) == {
        "Game/Data/Scripts.rvdata2": b"scripts",
        "Game/Graphics/Faces/Actor1.png": b"face-new",
    }
    assert result["files"] == 2
    assert result["skipped"] == 0
    assert result["failed"] == 0
    assert result["unsupported"] == []
    assert result["cancelled"] is False


def test_extract_in_place_includes_scripts_no_overwrite_first_archive_wins(
    tmp_path: Path,
) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(
        game / "A_Main.rgss3a",
        [
            ("Data\\Scripts.rvdata2", b"scripts-a"),
            ("Audio\\BGM\\Theme.ogg", b"from-a"),
            ("Graphics\\Pictures\\title.png", b"title-a"),
        ],
    )
    build_rgss_v3(
        game / "B_Extra.rgss3a",
        [
            ("Graphics\\Pictures\\title.png", b"title-b"),
            ("Graphics\\Pictures\\extra.png", b"extra-b"),
        ],
    )
    (game / "Audio" / "BGM").mkdir(parents=True)
    (game / "Audio" / "BGM" / "Theme.ogg").write_bytes(b"loose-custom")

    result = extract_archives(str(game), "in_place", "", _no_progress, _never)

    assert (game / "A_Main.rgss3a").is_file()
    assert (game / "B_Extra.rgss3a").is_file()
    assert (game / "Data" / "Scripts.rvdata2").read_bytes() == b"scripts-a"
    assert (game / "Audio" / "BGM" / "Theme.ogg").read_bytes() == b"loose-custom"
    assert (game / "Graphics" / "Pictures" / "title.png").read_bytes() == b"title-a"
    assert (game / "Graphics" / "Pictures" / "extra.png").read_bytes() == b"extra-b"
    assert result["files"] == 3
    assert result["skipped_existing"] == 2
    assert result["skipped"] == 2
    assert result["cancelled"] is False


def test_extract_skips_path_traversal_and_symlinks(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(
        game / "Evil.rgss3a",
        [
            ("..\\..\\.bashrc", b"pwned"),
            ("/abs.txt", b"abs"),
            ("C:\\win.txt", b"win"),
            ("Graphics\\link\\a.png", b"escaped"),
            ("Graphics\\ok.png", b"ok"),
        ],
    )
    out = tmp_path / "rip"
    outside = tmp_path / "outside"
    (out / "Evil" / "Graphics").mkdir(parents=True)
    outside.mkdir()
    os.symlink(outside, out / "Evil" / "Graphics" / "link")

    result = extract_archives(str(game), "folder", str(out), _no_progress, _never)

    assert (out / "Evil" / "Graphics" / "ok.png").read_bytes() == b"ok"
    assert list(outside.iterdir()) == []
    assert not (tmp_path / ".bashrc").exists()
    assert result["files"] == 1
    assert result["skipped_unsafe"] == 4


def test_extract_rejects_output_inside_game_and_bad_modes(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(game / "Game.rgss3a", [("a.txt", b"1")])
    sub = game / "sub"
    sub.mkdir()
    for bad in (str(game), str(sub), "relative", str(tmp_path / "missing"), ""):
        with pytest.raises(GameToolError):
            _ = extract_archives(str(game), "folder", bad, _no_progress, _never)
    with pytest.raises(GameToolError):
        _ = extract_archives(str(game), "bogus", str(tmp_path), _no_progress, _never)


def test_extract_low_free_space_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(game / "Game.rgss3a", [("a.bin", b"x" * 100)])
    out = tmp_path / "rip"
    out.mkdir()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        "core.rgss_tools.shutil.disk_usage", lambda _path: usage(1000, 990, 10)
    )

    with pytest.raises(GameToolError, match="Not enough free space"):
        _ = extract_archives(str(game), "folder", str(out), _no_progress, _never)

    assert _tree(out) == {}


def test_extract_cancel_after_n_files_leaves_no_partial(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(
        game / "Game.rgss3a",
        [(f"f{i}.bin", b"x" * 32) for i in range(5)],
    )
    out = tmp_path / "rip"
    out.mkdir()
    seen: list[str] = []

    def progress(_done: int, _total: int, current: str) -> None:
        if current:
            seen.append(current)

    result = extract_archives(
        str(game), "folder", str(out), progress, lambda: len(seen) > 2
    )

    assert result["cancelled"] is True
    assert result["files"] == 2
    assert not [p for p in out.rglob("*") if p.name.endswith(".wlib-partial")]


def test_extract_unsupported_archive_next_to_good_one(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v1(game / "Good.rgssad", [("Data\\Scripts.rxdata", b"ok")])
    (game / "Bad.rgss3a").write_bytes(b"CUSTOM\x00\x09junk")
    out = tmp_path / "rip"
    out.mkdir()

    result = extract_archives(str(game), "folder", str(out), _no_progress, _never)

    assert result["files"] == 1
    assert result["unsupported"] == ["Bad.rgss3a"]
    assert _tree(out) == {"Good/Data/Scripts.rxdata": b"ok"}


def test_extract_no_archives_raises(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    with pytest.raises(GameToolError):
        _ = extract_archives(str(game), "in_place", "", _no_progress, _never)


# ── In-game switch and tab state ──


def test_switch_on_after_full_extract_and_off(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(
        game / "Game.rgss3a",
        [
            ("Data\\Scripts.rvdata2", b"scripts"),
            ("Graphics\\Pictures\\title.png", b"123456"),
        ],
    )
    before = inspect_state(str(game))
    assert before["missing"] == 2
    assert before["differs"] == 0
    assert before["use_extracted"] is False
    assert before["backup"] is False
    assert before["archives"] == [
        {
            "name": "Game.rgss3a",
            "size": (game / "Game.rgss3a").stat().st_size,
            "format": FORMAT_V3,
            "supported": True,
            "entries": 2,
        }
    ]

    _ = extract_archives(str(game), "in_place", "", _no_progress, _never)
    after_extract = inspect_state(str(game))
    assert after_extract["missing"] == 0
    assert after_extract["differs"] == 0
    assert after_extract["use_extracted"] is False

    assert set_use_extracted(str(game), True) is True
    assert not (game / "Game.rgss3a").exists()
    assert (game / "Game.rgss3a.wlib-backup").is_file()
    on_state = inspect_state(str(game))
    assert on_state["use_extracted"] is True
    assert on_state["backup"] is True
    assert on_state["archives"][0]["name"] == "Game.rgss3a"
    assert on_state["archives"][0]["format"] == FORMAT_V3

    assert set_use_extracted(str(game), False) is False
    assert (game / "Game.rgss3a").is_file()
    assert not (game / "Game.rgss3a.wlib-backup").exists()
    assert (game / "Data" / "Scripts.rvdata2").read_bytes() == b"scripts"
    off_state = inspect_state(str(game))
    assert off_state["use_extracted"] is False
    assert off_state["backup"] is False


def test_switch_refuses_missing_files_with_count(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(
        game / "Game.rgss3a",
        [(f"Graphics\\f{i}.png", b"img") for i in range(5)],
    )
    (game / "Graphics").mkdir()
    (game / "Graphics" / "f0.png").write_bytes(b"img")

    with pytest.raises(GameToolError, match="4"):
        _ = set_use_extracted(str(game), True)

    assert (game / "Game.rgss3a").is_file()
    assert not (game / "Game.rgss3a.wlib-backup").exists()


def test_switch_refuses_unsupported_archive(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(game / "Good.rgss3a", [("a.txt", b"a")])
    (game / "Bad.rgss3a").write_bytes(b"CUSTOM\x00\x09")
    _ = extract_archives(str(game), "in_place", "", _no_progress, _never)

    state = inspect_state(str(game))
    by_name = {a["name"]: a for a in state["archives"]}
    assert by_name["Bad.rgss3a"]["supported"] is False
    assert by_name["Bad.rgss3a"]["format"] == ""
    assert by_name["Bad.rgss3a"]["entries"] == 0

    with pytest.raises(GameToolError, match="unsupported"):
        _ = set_use_extracted(str(game), True)

    assert (game / "Good.rgss3a").is_file()
    assert (game / "Bad.rgss3a").is_file()


def test_switch_refuses_when_both_plain_and_backup_exist(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(game / "Game.rgss3a", [("a.txt", b"a")])
    build_rgss_v3(game / "Game.rgss3a.wlib-backup", [("a.txt", b"a")])
    (game / "a.txt").write_bytes(b"a")

    state = inspect_state(str(game))
    assert state["use_extracted"] is False
    assert state["backup"] is True

    with pytest.raises(GameToolError, match="Game\\.rgss3a\\.wlib-backup"):
        _ = set_use_extracted(str(game), True)

    with pytest.raises(GameToolError, match="Game\\.rgss3a"):
        _ = set_use_extracted(str(game), False)

    assert (game / "Game.rgss3a").is_file()
    assert (game / "Game.rgss3a.wlib-backup").is_file()


def test_switch_rolls_back_when_second_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(game / "A.rgss3a", [("a.txt", b"a")])
    build_rgss_v3(game / "B.rgss3a", [("b.txt", b"b")])
    _ = extract_archives(str(game), "in_place", "", _no_progress, _never)

    real_rename = os.rename
    calls = {"n": 0}

    def flaky_rename(src: str, dst: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk busy")
        real_rename(src, dst)

    monkeypatch.setattr(rgss_tools.os, "rename", flaky_rename)

    with pytest.raises(GameToolError, match="disk busy"):
        _ = set_use_extracted(str(game), True)

    # First rename must have been rolled back
    assert (game / "A.rgss3a").is_file()
    assert not (game / "A.rgss3a.wlib-backup").exists()
    assert (game / "B.rgss3a").is_file()
    assert not (game / "B.rgss3a.wlib-backup").exists()


def test_differs_is_size_only(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    build_rgss_v3(
        game / "Game.rgss3a",
        [
            ("same_size.png", b"AAAA"),
            ("diff_size.png", b"BBBB"),
        ],
    )
    # Same size, different content -> NOT counted as differs
    (game / "same_size.png").write_bytes(b"ZZZZ")
    # Different size -> counted as differs
    (game / "diff_size.png").write_bytes(b"BBBBBB")

    state = inspect_state(str(game))
    assert state["missing"] == 0
    assert state["differs"] == 1


def test_read_entry_hit_miss_cap_and_backup(tmp_path: Path) -> None:
    game = make_rgss_game(tmp_path)
    payload = b"\x04\x08" + b"X" * 100
    build_rgss_v3(
        game / "Game.rgss3a",
        [
            ("Data\\Items.rvdata2", payload),
            ("Data/Big.rvdata2", b"B" * 200),
        ],
    )

    # Case-insensitive and slash-normalized hit
    assert read_entry(str(game), "data/items.rvdata2", 1024) == payload
    assert read_entry(str(game), "DATA\\ITEMS.RVDATA2", 1024) == payload
    # Miss
    assert read_entry(str(game), "Data/Missing.rvdata2", 1024) is None
    # Size cap enforced
    assert read_entry(str(game), "Data/Big.rvdata2", 100) is None
    assert read_entry(str(game), "Data/Big.rvdata2", 200) == b"B" * 200

    # Backup-named archive (.wlib-backup) is still readable
    backup_game = make_rgss_game(tmp_path, "BackupGame")
    build_rgss_v1(
        backup_game / "Game.rgssad.wlib-backup",
        [("Data/System.rxdata", b"xp-system-bytes")],
    )
    assert read_entry(str(backup_game), "data/system.rxdata", 1024) == b"xp-system-bytes"
