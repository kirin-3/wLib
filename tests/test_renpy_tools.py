# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import codecs
import io
import os
import pickle
import zlib
from collections import namedtuple
from pathlib import Path

import pytest

from core import renpy_tools
from core.game_tools import GameToolError
from core.renpy_tools import (
    RpaUnsupportedError,
    extract_archives,
    inflate_capped,
    list_archives,
    load_index,
    parse_rpa_header,
    read_rpa_index,
)


def build_rpa(
    path: Path,
    files: dict[str, bytes],
    version: str = "3.0",
    key_fields: tuple[int, ...] = (0x42424242,),
    prefixes: dict[str, bytes] | None = None,
) -> None:
    """Write a minimal RPA archive the way Ren'Py's tools lay it out."""
    key = 0
    for field in key_fields:
        key ^= field
    if version == "2.0":
        key = 0
    prefixes = prefixes or {}
    header_size = 64
    body = bytearray()
    index: dict[bytes, list[tuple[object, ...]]] = {}
    for name, data in files.items():
        offset = header_size + len(body)
        body += data
        entry: tuple[object, ...] = (offset ^ key, len(data) ^ key)
        if name in prefixes:
            entry = (*entry, prefixes[name])
        index[name.encode()] = [entry]
    packed = zlib.compress(pickle.dumps(index, protocol=2))
    index_offset = header_size + len(body)
    if version == "2.0":
        line = f"RPA-2.0 {index_offset:016x}\n"
    elif version == "3.0":
        fields = " ".join(f"{field:08x}" for field in key_fields)
        line = f"RPA-3.0 {index_offset:016x} {fields}\n"
    else:
        fields = " ".join(f"{field:08x}" for field in key_fields)
        line = f"RPA-3.2 {index_offset:016x} 00000000 {fields}\n"
    header = line.encode().ljust(header_size, b"\0")
    path.write_bytes(header + bytes(body) + packed)


def make_game(tmp_path: Path) -> Path:
    game = tmp_path / "Game"
    (game / "renpy").mkdir(parents=True)
    (game / "game").mkdir()
    return game


def no_progress(_done: int, _total: int, _current: str) -> None:
    return None


def never() -> bool:
    return False


def tree(root: Path) -> dict[str, bytes]:
    return {
        os.path.relpath(p, root).replace(os.sep, "/"): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── Console ──


def test_console_on_developer_and_mode_change(tmp_path):
    game = make_game(tmp_path)
    rpy = game / "game" / "zzz_wlib_console.rpy"
    rpyc = game / "game" / "zzz_wlib_console.rpyc"
    assert renpy_tools.console_state(str(game)) == "off"

    assert renpy_tools.set_console(str(game), True) == "on"
    text = rpy.read_text(encoding="utf-8")
    assert "init 999 python:" in text
    assert "config.console = True" in text
    assert "config.developer" not in text
    assert renpy_tools.console_state(str(game)) == "on"

    rpyc.write_bytes(b"compiled")
    assert renpy_tools.set_console(str(game), True, developer=True) == "developer"
    assert "config.developer = True" in rpy.read_text(encoding="utf-8")
    assert not rpyc.exists()
    assert renpy_tools.console_state(str(game)) == "developer"

    rpyc.write_bytes(b"compiled")
    assert renpy_tools.set_console(str(game), True, developer=False) == "on"
    assert "config.developer" not in rpy.read_text(encoding="utf-8")
    assert not rpyc.exists()


def test_console_unchanged_keeps_compiled_file(tmp_path):
    game = make_game(tmp_path)
    _ = renpy_tools.set_console(str(game), True)
    rpyc = game / "game" / "zzz_wlib_console.rpyc"
    rpyc.write_bytes(b"compiled")
    _ = renpy_tools.set_console(str(game), True)
    assert rpyc.exists()


def test_console_rpyc_only_is_on_and_off_deletes_both(tmp_path):
    game = make_game(tmp_path)
    rpy = game / "game" / "zzz_wlib_console.rpy"
    rpyc = game / "game" / "zzz_wlib_console.rpyc"
    rpyc.write_bytes(b"compiled")
    assert renpy_tools.console_state(str(game)) == "on"
    assert renpy_tools.set_console(str(game), False) == "off"
    assert not rpyc.exists()

    _ = renpy_tools.set_console(str(game), True)
    rpyc.write_bytes(b"compiled")
    assert renpy_tools.set_console(str(game), False) == "off"
    assert not rpy.exists() and not rpyc.exists()
    assert renpy_tools.set_console(str(game), False) == "off"


# ── RPA header and index ──


def test_parse_headers():
    assert parse_rpa_header(b"RPA-2.0 0000000000000010").offset == 16
    v3 = parse_rpa_header(b"RPA-3.0 0000000000000020 000000ff 0000ff00")
    assert (v3.magic, v3.offset, v3.key) == ("RPA-3.0", 32, 0xFFFF)
    v32 = parse_rpa_header(b"RPA-3.2 0000000000000020 12345678 0000000f 000000f0")
    assert (v32.magic, v32.offset, v32.key) == ("RPA-3.2", 32, 0xFF)


@pytest.mark.parametrize(
    "line",
    [
        b"RPA-1.0 0000",
        b"RPA-3.0 nothex 00000001",
        b"RPA-3.0 0000000000000020 zz",
        b"RPA-3.0",
        b"\xff\xfe",
        b"",
    ],
)
def test_parse_header_rejects(line):
    with pytest.raises(RpaUnsupportedError):
        parse_rpa_header(line)


def test_inflate_cap_and_truncation():
    data = zlib.compress(b"x" * 1000)
    assert inflate_capped(io.BytesIO(data), cap=1000) == b"x" * 1000
    with pytest.raises(RpaUnsupportedError):
        inflate_capped(io.BytesIO(data), cap=999)
    with pytest.raises(RpaUnsupportedError):
        inflate_capped(io.BytesIO(data[:-4]), cap=1000)
    with pytest.raises(RpaUnsupportedError):
        inflate_capped(io.BytesIO(b"not zlib"), cap=1000)


@pytest.mark.parametrize("version", ["2.0", "3.0", "3.2"])
def test_read_index_versions(tmp_path, version):
    archive = tmp_path / "a.rpa"
    build_rpa(archive, {"a/b.png": b"hello", "c.txt": b"world"}, version, (0x1234, 0xFF))
    index = read_rpa_index(str(archive))
    assert index.magic == f"RPA-{version}"
    got = {}
    with open(archive, "rb") as file:
        for entry in index.entries:
            (offset, length, prefix), = entry.segments
            _ = file.seek(offset)
            got[entry.name] = prefix + file.read(length)
    assert got == {"a/b.png": b"hello", "c.txt": b"world"}


def test_python2_style_byte_names_and_prefix(tmp_path):
    py2_index = b"\x80\x02}U\x05a.png]K\x00K\x03\x86as."
    assert load_index(py2_index) == {b"a.png": [(0, 3)]}

    archive = tmp_path / "p.rpa"
    build_rpa(archive, {"x.bin": b"BODY"}, prefixes={"x.bin": b"PRE"})
    entry = read_rpa_index(str(archive)).entries[0]
    (offset, length, prefix), = entry.segments
    assert prefix == b"PRE"
    assert length == 4  # the prefix is not counted in length (renpy/loader.py)
    assert entry.size == 7


def _write_tool_archive(path: Path, files: dict[str, bytes], protocol: int) -> None:
    # Same layout as Ren'Py's launcher/game/archiver.rpy: a 34-byte header,
    # "Made with Ren'Py." padding before each file, and 3-tuples with b"".
    key = 0x42424242
    out = bytearray(b"RPA-3.0 XXXXXXXXXXXXXXXX XXXXXXXX\n")
    index: dict[str, list[tuple[int, int, bytes]]] = {}
    for name, data in files.items():
        out += b"Made with Ren'Py."
        index[name] = [(len(out) ^ key, len(data) ^ key, b"")]
        out += data
    index_offset = len(out)
    out += zlib.compress(pickle.dumps(index, protocol))
    out[:34] = b"RPA-3.0 %016x %08x\n" % (index_offset, key)
    path.write_bytes(bytes(out))


@pytest.mark.parametrize(
    "protocol",
    # Ren'Py's archiver uses the highest protocol; rpatool writes protocol 2,
    # where an empty prefix is stored as a bytes() call.
    [pickle.HIGHEST_PROTOCOL, 2],
)
def test_archives_written_by_renpy_and_rpatool_extract(tmp_path, protocol):
    game = make_game(tmp_path)
    files = {"images/bg.png": b"PNGDATA" * 10, "audio/theme.ogg": b"OggS"}
    _write_tool_archive(game / "game" / "archive.rpa", files, protocol)
    out = tmp_path / "out"
    out.mkdir()

    result = extract_archives(str(game), "folder", str(out), no_progress, never)

    assert result["unsupported"] == []
    assert tree(out / "archive") == files


def test_restricted_bytes_and_encode_calls_are_blocked():
    class HugeBytes:
        def __reduce__(self):
            return (bytes, (10**12,))

    class OtherCodec:
        def __reduce__(self):
            return (codecs.encode, (b"x" * 10, "zlib"))

    for evil in (HugeBytes(), OtherCodec()):
        with pytest.raises(RpaUnsupportedError):
            load_index(pickle.dumps(evil, protocol=2))
    assert load_index(pickle.dumps({"a": [(1, 2, b"")]}, protocol=2)) == {
        "a": [(1, 2, b"")]
    }
    assert load_index(pickle.dumps({"a": [(1, 2, b"\xff")]}, protocol=2)) == {
        "a": [(1, 2, b"\xff")]
    }


def test_malicious_pickle_runs_nothing(tmp_path):
    marker = tmp_path / "pwned"

    class Evil:
        def __reduce__(self):
            return (os.system, (f"touch {marker}",))

    with pytest.raises(RpaUnsupportedError):
        load_index(pickle.dumps(Evil(), protocol=2))
    assert not marker.exists()

    packed = zlib.compress(pickle.dumps(Evil(), protocol=2))
    archive = tmp_path / "evil.rpa"
    header = f"RPA-3.0 {64:016x} 00000000\n".encode().ljust(64, b"\0")
    archive.write_bytes(header + packed)
    with pytest.raises(RpaUnsupportedError):
        read_rpa_index(str(archive))
    assert not marker.exists()


def test_index_skips_bad_names_and_out_of_range(tmp_path):
    archive = tmp_path / "b.rpa"
    build_rpa(archive, {"ok.txt": b"fine"})
    raw = archive.read_bytes()
    size = len(raw)
    index = {
        b"ok.txt": [(64, 4)],
        b"\xff\xfe.bin": [(64, 4)],
        b"past.bin": [(100_000, 50)],
        b"neg.bin": [(-1, 4)],
    }
    packed = zlib.compress(pickle.dumps(index, protocol=2))
    header = f"RPA-2.0 {size:016x}\n".encode().ljust(64, b"\0")
    archive.write_bytes(header + raw[64:] + packed)
    parsed = read_rpa_index(str(archive))
    assert [entry.name for entry in parsed.entries] == ["ok.txt"]
    assert parsed.skipped_invalid == 3


# ── Listing ──


def test_list_archives_excludes_urm(tmp_path):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {"a": b"1"})
    (game / "game" / "sub").mkdir()
    build_rpa(game / "game" / "sub" / "audio.rpa", {"a": b"1"})
    build_rpa(game / "game" / "0x52_URM.rpa", {"a": b"1"})
    (game / "game" / "junk.rpa").write_bytes(b"nope")
    listed = {a["name"]: a["format"] for a in list_archives(str(game))}
    assert listed == {
        "images.rpa": "RPA-3.0",
        "sub/audio.rpa": "RPA-3.0",
        "junk.rpa": "",
    }


# ── Extraction ──


def test_folder_mode_layout_and_game_untouched(tmp_path):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {"bg/room.png": b"png", "s.rpyc": b"c"})
    build_rpa(game / "game" / "extra.rpa", {"a.txt": b"a"}, "2.0")
    build_rpa(game / "game" / "0x52_URM.rpa", {"urm.txt": b"u"})
    out = tmp_path / "rip"
    out.mkdir()
    before = tree(game)

    result = extract_archives(str(game), "folder", str(out), no_progress, never)

    assert tree(game) == before
    assert tree(out) == {
        "images/bg/room.png": b"png",
        "images/s.rpyc": b"c",
        "extra/a.txt": b"a",
    }
    assert result["files"] == 3
    assert result["unsupported"] == []
    assert result["cancelled"] is False


def test_folder_mode_overwrites_and_rejects_output_inside_game(tmp_path):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {"a.txt": b"new"})
    out = tmp_path / "rip"
    (out / "images").mkdir(parents=True)
    (out / "images" / "a.txt").write_bytes(b"old")
    _ = extract_archives(str(game), "folder", str(out), no_progress, never)
    assert (out / "images" / "a.txt").read_bytes() == b"new"

    for bad in (str(game / "game"), str(game), "relative", str(tmp_path / "missing"), ""):
        with pytest.raises(GameToolError):
            _ = extract_archives(str(game), "folder", bad, no_progress, never)


def test_in_place_skips_scripts_and_existing(tmp_path):
    game = make_game(tmp_path)
    archive = game / "game" / "images.rpa"
    build_rpa(
        archive,
        {"bg/room.png": b"png", "script.rpyc": b"c", "x.rpy": b"x", "keep.txt": b"new"},
    )
    (game / "game" / "keep.txt").write_bytes(b"mine")

    result = extract_archives(str(game), "in_place", "", no_progress, never)

    assert tree(game / "game") == {
        "images.rpa": archive.read_bytes(),
        "bg/room.png": b"png",
        "keep.txt": b"mine",
    }
    assert result["files"] == 1
    assert result["skipped_scripts"] == 2
    assert result["skipped_existing"] == 1
    assert result["skipped"] == 3


def test_in_place_first_archive_wins_for_shared_files(tmp_path):
    # Ren'Py reads the first archive in name order, so that copy is the one kept.
    game = make_game(tmp_path)
    build_rpa(game / "game" / "a.rpa", {"bg/room.png": b"from-a"})
    build_rpa(game / "game" / "b.rpa", {"bg/room.png": b"from-b", "other.png": b"o"})

    result = extract_archives(str(game), "in_place", "", no_progress, never)

    assert (game / "game" / "bg" / "room.png").read_bytes() == b"from-a"
    assert (game / "game" / "other.png").read_bytes() == b"o"
    assert result["files"] == 2
    assert result["skipped_existing"] == 1


def test_unsafe_and_out_of_range_entries_are_skipped(tmp_path):
    game = make_game(tmp_path)
    archive = game / "game" / "evil.rpa"
    build_rpa(archive, {"ok.txt": b"fine"})
    raw = archive.read_bytes()
    size = len(raw)
    index = {
        b"../../escape.txt": [(64, 4)],
        b"/abs.txt": [(64, 4)],
        b"C:/drive.txt": [(64, 4)],
        b"..\\back.txt": [(64, 4)],
        b"a/../../up.txt": [(64, 4)],
        b"ok.txt": [(64, 4)],
        b"past.bin": [(100_000, 50)],
    }
    packed = zlib.compress(pickle.dumps(index, protocol=2))
    header = f"RPA-2.0 {size:016x}\n".encode().ljust(64, b"\0")
    archive.write_bytes(header + raw[64:] + packed)
    out = tmp_path / "rip"
    out.mkdir()

    result = extract_archives(str(game), "folder", str(out), no_progress, never)

    assert tree(tmp_path / "rip") == {"evil/ok.txt": b"fine"}
    assert result["skipped_unsafe"] == 5
    assert result["skipped_invalid"] == 1
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "up.txt").exists()


def test_symlink_in_target_is_not_written_through(tmp_path):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {"bg/room.png": b"png", "top.png": b"t"})
    out = tmp_path / "rip"
    outside = tmp_path / "outside"
    (out / "images").mkdir(parents=True)
    outside.mkdir()
    os.symlink(outside, out / "images" / "bg")

    result = extract_archives(str(game), "folder", str(out), no_progress, never)

    assert list(outside.iterdir()) == []
    assert (out / "images" / "top.png").read_bytes() == b"t"
    assert result["skipped_unsafe"] == 1


def test_cancel_stops_before_next_file_without_partials(tmp_path):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {f"f{i}.bin": b"x" * 10 for i in range(5)})
    out = tmp_path / "rip"
    out.mkdir()
    seen: list[str] = []

    def progress(_done: int, _total: int, current: str) -> None:
        if current:
            seen.append(current)

    result = extract_archives(
        str(game), "folder", str(out), progress, lambda: len(seen) > 1
    )

    assert result["cancelled"] is True
    assert result["files"] == 1
    assert not [p for p in out.rglob("*") if p.name.endswith(".wlib-partial")]


def test_cancel_inside_a_large_file_removes_partial(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {"big.bin": b"x" * 100})
    out = tmp_path / "rip"
    out.mkdir()
    monkeypatch.setattr(renpy_tools, "CHUNK_SIZE", 10)
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    result = extract_archives(str(game), "folder", str(out), no_progress, cancelled)

    assert result["cancelled"] is True
    assert result["files"] == 0
    assert tree(out) == {}


def test_low_free_space_writes_nothing(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {"a.bin": b"x" * 100})
    out = tmp_path / "rip"
    out.mkdir()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        "core.renpy_tools.shutil.disk_usage", lambda _path: usage(1000, 990, 10)
    )

    with pytest.raises(GameToolError, match="Not enough free space"):
        _ = extract_archives(str(game), "folder", str(out), no_progress, never)

    assert tree(out) == {}


def test_unsupported_archive_is_reported_others_extracted(tmp_path):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "good.rpa", {"a.txt": b"a"})
    (game / "game" / "bad.rpa").write_bytes(b"RPA-9.9 0000\n")
    out = tmp_path / "rip"
    out.mkdir()

    result = extract_archives(str(game), "folder", str(out), no_progress, never)

    assert result["unsupported"] == ["bad.rpa"]
    assert tree(out) == {"good/a.txt": b"a"}


def test_no_archives_is_an_error(tmp_path):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "0x52_URM.rpa", {"a": b"1"})
    with pytest.raises(GameToolError):
        _ = extract_archives(str(game), "in_place", "", no_progress, never)
