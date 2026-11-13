# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installs into libraries on network filesystems (NFS/SMB).

The network paths must produce exactly the tree the local path produces;
they only change how many round trips it takes to get there.
"""
import os
import shutil
import stat
import sys
import time
import zipfile

import pytest

import core.archive_install as ai
from core.archive_install import (
    TRASH_PREFIX,
    apply_execute_bits,
    copy_tree_parallel,
    defer_remove_tree,
    extract_zip,
    filesystem_type,
    sweep_trash,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix modes")

ELF = b"\x7fELF\x02\x01\x01" + b"\0" * 9


def _entry(zf, name, data, mode=None):
    info = zipfile.ZipInfo(name)
    if mode is not None:
        info.create_system = 3
        info.external_attr = (mode & 0xFFFF) << 16
    zf.writestr(info, data)


def _mixed_zip(path):
    with zipfile.ZipFile(path, "w") as zf:
        _entry(zf, "Game/", b"", 0o40755)
        _entry(zf, "Game/game.sh", b"#!/bin/sh\n")  # no mode: needs +x by name
        _entry(zf, "Game/lib/engine", ELF + b"x" * 5000)  # no mode: ELF
        _entry(zf, "Game/lib/libfoo.so", ELF + b"y", 0o100644)  # mode, ELF
        _entry(zf, "Game/lib/run.x86_64", b"z", 0o100600)  # owner-only read
        _entry(zf, "Game/data/readme.txt", b"hello")
        _entry(zf, "Game/data/secret.txt", b"s", 0o100600)
        _entry(zf, "Game/data/script", b"#!", 0o100755)
        for i in range(40):
            _entry(zf, f"Game/data/sub{i % 4}/f{i}.bin", os.urandom(3000), 0o100644)
        _entry(zf, "Game/empty/", b"")


def _snapshot(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            data = b""
            if stat.S_ISREG(st.st_mode):
                with open(path, "rb") as f:
                    data = f.read()
            elif stat.S_ISLNK(st.st_mode):
                data = os.readlink(path).encode()
            out[os.path.relpath(path, root)] = (stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), data)
    return out


@pytest.mark.parametrize("workers", [1, 6])
def test_extract_zip_exec_bits_matches_separate_pass(tmp_path, workers):
    archive = tmp_path / "g.zip"
    _mixed_zip(archive)
    reference = tmp_path / "ref"
    extract_zip(str(archive), str(reference))
    apply_execute_bits(str(reference))

    fast = tmp_path / "fast"
    count = extract_zip(str(archive), str(fast), workers=workers, exec_bits=True)

    assert _snapshot(fast) == _snapshot(reference)
    assert count == 49  # 2 folder entries + 47 files
    assert os.access(fast / "Game" / "game.sh", os.X_OK)
    assert os.access(fast / "Game" / "lib" / "engine", os.X_OK)
    assert stat.S_IMODE(os.stat(fast / "Game" / "lib" / "run.x86_64").st_mode) == 0o700
    assert not os.access(fast / "Game" / "data" / "readme.txt", os.X_OK)


def test_extract_zip_parallel_surfaces_corrupt_member(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(8):
            zf.writestr(f"d{i}/f.bin", b"A" * 4096)
    raw = bytearray(archive.read_bytes())
    pos = raw.find(b"A" * 4096, len(raw) // 2)
    raw[pos] = ord("B")  # CRC mismatch in one member
    archive.write_bytes(bytes(raw))
    with pytest.raises(ai.ArchiveInstallError):
        extract_zip(str(archive), str(tmp_path / "out"), workers=4)


def test_copy_tree_parallel_preserves_tree(tmp_path):
    src = tmp_path / "src"
    (src / "a" / "b").mkdir(parents=True)
    (src / "a" / "b" / "run").write_bytes(ELF)
    os.chmod(src / "a" / "b" / "run", 0o755)
    (src / "a" / "only-me").write_bytes(b"p")
    os.chmod(src / "a" / "only-me", 0o600)
    for i in range(30):
        (src / f"f{i}.dat").write_bytes(os.urandom(2048))
    os.symlink("b/run", src / "a" / "link")
    os.symlink("b", src / "a" / "dirlink")
    (src / "empty").mkdir()
    old = 1_500_000_000_000_000_000
    os.utime(src / "a" / "only-me", ns=(old, old))
    os.utime(src / "a" / "b", ns=(old + 7, old + 7))
    dst = tmp_path / "dst"
    dst.mkdir()

    copied = copy_tree_parallel(str(src), str(dst), workers=5)

    assert copied == 34
    assert _snapshot(dst) == _snapshot(src)
    # Direct extraction keeps archive times; the scratch copy must too.
    assert os.stat(dst / "a" / "only-me").st_mtime_ns == old
    assert os.stat(dst / "a" / "b").st_mtime_ns == old + 7
    assert os.stat(dst / "f0.dat").st_mtime_ns == os.stat(src / "f0.dat").st_mtime_ns


def test_copy_tree_parallel_reports_bytes_and_keeps_tree(tmp_path):
    import threading

    src = tmp_path / "src"
    (src / "a").mkdir(parents=True)
    (src / "a" / "big.bin").write_bytes(os.urandom(3 * 1024 * 1024 + 7))
    for i in range(10):
        (src / f"f{i}.dat").write_bytes(os.urandom(1000 + i))
    os.symlink("a/big.bin", src / "link")
    plain = tmp_path / "plain"
    counted = tmp_path / "counted"
    plain.mkdir()
    counted.mkdir()
    lock = threading.Lock()
    seen: list[int] = []

    def on_bytes(n: int) -> None:
        with lock:
            seen.append(n)

    # Without the callback (the archive install call) the copy is unchanged.
    assert copy_tree_parallel(str(src), str(plain), workers=4) == 12
    assert copy_tree_parallel(str(src), str(counted), workers=4, on_bytes=on_bytes) == 12
    assert _snapshot(plain) == _snapshot(src) == _snapshot(counted)
    assert sum(seen) == ai.tree_bytes(str(src))


def test_filesystem_type_picks_deepest_mount(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    games = tmp_path / "mnt" / "nas games"
    (games / "lib").mkdir(parents=True)
    escaped = str(tmp_path / "mnt").replace(" ", "\\040")
    escaped_games = str(games).replace(" ", "\\040")
    mountinfo.write_text(
        "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
        f"40 22 0:50 / {escaped} rw - tmpfs tmpfs rw\n"
        f"41 40 0:51 / {escaped_games} rw - nfs4 nas:/tank/games rw,vers=4.2\n"
    )
    assert filesystem_type(str(games / "lib" / "not-yet"), mountinfo=str(mountinfo)) == "nfs4"
    assert filesystem_type(str(tmp_path / "mnt" / "x"), mountinfo=str(mountinfo)) == "tmpfs"
    assert filesystem_type(str(tmp_path), mountinfo=str(mountinfo)) == "ext4"


def _wait_gone(path, timeout=5.0):
    end = time.monotonic() + timeout
    while os.path.exists(path) and time.monotonic() < end:
        time.sleep(0.02)
    return not os.path.exists(path)


def test_defer_remove_tree_renames_then_deletes(tmp_path):
    lib = tmp_path / "lib"
    old = lib / "Game" / "1.0"
    (old / "deep").mkdir(parents=True)
    (old / "deep" / "f").write_text("x")

    assert defer_remove_tree(str(old), str(lib)) is True
    assert not old.exists()
    trash = [p for p in os.listdir(lib) if p.startswith(TRASH_PREFIX)]
    for name in trash:
        assert _wait_gone(lib / name)
    assert defer_remove_tree(str(lib / "missing"), str(lib)) is False


def test_sweep_trash_deletes_leftover_trash(tmp_path):
    lib = tmp_path / "lib"
    leftover = lib / f"{TRASH_PREFIX}123"
    (leftover / "a").mkdir(parents=True)
    (leftover / "a" / "f").write_text("x")
    keep = lib / "Game"
    keep.mkdir()
    assert sweep_trash(str(lib)) == 1
    assert _wait_gone(leftover)
    assert keep.is_dir()


def test_defer_remove_tree_needs_a_trash_root(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    assert defer_remove_tree(str(old), "") is False
    assert old.is_dir()


def test_trash_root_is_the_containing_library_folder(tmp_path):
    lib = tmp_path / "lib"
    nested = lib / "nested"
    (nested / "Game").mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "Game"
    outside.mkdir(parents=True)
    folders = [str(lib), str(nested)]
    assert ai._trash_root_for(str(nested / "Game"), folders) == os.path.realpath(nested)
    assert ai._trash_root_for(str(outside), folders) == ""
    assert ai._trash_root_for(str(lib), folders) == ""


def test_filesystem_type_detects_gvfs_smb(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    share = tmp_path / "gvfs"
    share.mkdir()
    mountinfo.write_text(
        "22 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
        f"50 22 0:60 / {share} rw - fuse.gvfsd-fuse gvfsd-fuse rw\n"
    )
    fstype = filesystem_type(str(share / "Games"), mountinfo=str(mountinfo))
    assert fstype.lower() in ai.NETWORK_FS_TYPES


def test_extract_zip_opens_files_in_binary_mode(tmp_path, monkeypatch):
    # Windows os.open() is text mode without O_BINARY, which rewrites "\n".
    sentinel = os.O_CLOEXEC
    monkeypatch.setattr(ai, "_O_BINARY", sentinel)
    real_open = os.open
    flags_seen = []

    def spy(path, flags, *args, **kwargs):
        flags_seen.append(flags)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(ai.os, "open", spy)
    archive = tmp_path / "g.zip"
    _mixed_zip(archive)
    extract_zip(str(archive), str(tmp_path / "out"), workers=4)
    assert flags_seen and all(f & sentinel for f in flags_seen)


def _isolated_db(tmp_path, monkeypatch):
    import core.database as database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "net-test.db"))
    database.init_db()
    return database


def test_network_install_and_update_defers_old_folder(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    monkeypatch.setattr(ai, "is_network_filesystem", lambda _p: True)
    lib = tmp_path / "lib"

    first = tmp_path / "Game-1.0.zip"
    _mixed_zip(first)
    r1 = ai.install_archive(
        str(first), library_folder=str(lib), pattern="{title}/{version}",
        title="Game", version="1.0",
    )
    assert r1["exe_path"] == str(lib / "Game" / "1.0" / "game.sh")
    assert os.access(str(r1["exe_path"]), os.X_OK)
    reference = tmp_path / "ref"
    extract_zip(str(first), str(reference))
    apply_execute_bits(str(reference))
    assert _snapshot(lib / "Game" / "1.0") == _snapshot(reference / "Game")

    second = tmp_path / "Game-2.0.zip"
    _mixed_zip(second)
    r2 = ai.install_archive(
        str(second), library_folder=str(lib), pattern="{title}/{version}",
        title="Game", version="2.0", game_id=int(str(r1["game_id"])), remove_old=True,
    )
    assert r2["removed_old"] is True
    assert not (lib / "Game" / "1.0").exists()
    for name in os.listdir(lib):
        if name.startswith(TRASH_PREFIX):
            assert _wait_gone(lib / name)
    assert db.get_all_games()[0]["version"] == "2.0"


@pytest.mark.parametrize("listed_size", [10, None])
def test_network_tool_archive_skips_scratch_without_local_space(tmp_path, monkeypatch, listed_size):
    _isolated_db(tmp_path, monkeypatch)
    monkeypatch.setattr(ai, "is_network_filesystem", lambda _p: True)
    scratch_parent = tmp_path / "scratch"
    monkeypatch.setattr(ai, "_scratch_parent", lambda: str(scratch_parent))
    archive = tmp_path / "Game-1.0.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c")
    seen = {}

    def fake_extract(tool, archive_path, dest, cancel=None):
        seen["dest"] = dest
        os.makedirs(os.path.join(dest, "Game"), exist_ok=True)
        with open(os.path.join(dest, "Game", "Game.sh"), "wb") as f:
            f.write(b"#!/bin/sh\n")
        return 1

    real_disk_usage = shutil.disk_usage

    def disk_usage(folder):
        usage = real_disk_usage(folder)
        if os.path.realpath(folder) == os.path.realpath(scratch_parent):
            # Room for the archive itself, but not the reserve on top.
            return usage._replace(free=ai.SCRATCH_RESERVE_BYTES)
        return usage

    monkeypatch.setattr(ai, "detect_archive_tool", lambda *_a, **_k: ("/usr/bin/7z", ""))
    monkeypatch.setattr(ai, "uncompressed_size", lambda *_a, **_k: listed_size)
    monkeypatch.setattr(ai.shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(ai, "extract_with_tool", fake_extract)
    lib = tmp_path / "lib"
    result = ai.install_archive(
        str(archive), library_folder=str(lib), pattern="{title}", title="Game",
    )
    assert not seen["dest"].startswith(str(scratch_parent))
    assert result["exe_path"] == str(lib / "Game" / "Game.sh")
    assert os.access(str(result["exe_path"]), os.X_OK)


def test_network_tool_archive_goes_through_local_scratch(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    monkeypatch.setattr(ai, "is_network_filesystem", lambda _p: True)
    scratch_parent = tmp_path / "scratch"
    monkeypatch.setattr(ai, "_scratch_parent", lambda: str(scratch_parent))
    archive = tmp_path / "Game-1.0.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c")
    seen = {}

    def fake_extract(tool, archive_path, dest, cancel=None):
        seen["dest"] = dest
        os.makedirs(os.path.join(dest, "Game", "lib"), exist_ok=True)
        with open(os.path.join(dest, "Game", "lib", "engine"), "wb") as f:
            f.write(ELF)
        with open(os.path.join(dest, "Game", "Game.sh"), "wb") as f:
            f.write(b"#!/bin/sh\n")
        return 2

    monkeypatch.setattr(ai, "detect_archive_tool", lambda *_a, **_k: ("/usr/bin/7z", ""))
    monkeypatch.setattr(ai, "uncompressed_size", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "extract_with_tool", fake_extract)
    lib = tmp_path / "lib"
    result = ai.install_archive(
        str(archive), library_folder=str(lib), pattern="{title}", title="Game",
    )
    assert seen["dest"].startswith(str(scratch_parent))
    assert not os.path.exists(seen["dest"])  # scratch cleaned up
    assert result["exe_path"] == str(lib / "Game" / "Game.sh")
    assert os.access(str(lib / "Game" / "lib" / "engine"), os.X_OK)
    assert os.access(str(result["exe_path"]), os.X_OK)


@pytest.mark.parametrize(
    ("cores", "network", "expected"),
    [(1, False, 1), (8, False, 8), (64, False, ai.ZIP_MAX_WORKERS), (4, True, 16), (24, True, 24)],
)
def test_zip_extract_workers_follow_cores(monkeypatch, cores, network, expected):
    monkeypatch.setattr(ai, "usable_cpu_count", lambda: cores)
    assert ai.zip_extract_workers(network) == expected


def test_usable_cpu_count_is_positive():
    assert ai.usable_cpu_count() >= 1


def test_local_zip_install_uses_one_worker_per_core(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    monkeypatch.setattr(ai, "is_network_filesystem", lambda _p: False)
    monkeypatch.setattr(ai, "usable_cpu_count", lambda: 6)
    real_extract = ai.extract_zip
    seen = {}

    def spy(archive_path, staging_dir, **kwargs):
        seen.update(kwargs)
        return real_extract(archive_path, staging_dir, **kwargs)

    monkeypatch.setattr(ai, "extract_zip", spy)
    archive = tmp_path / "Game-1.0.zip"
    _mixed_zip(archive)
    lib = tmp_path / "lib"
    result = ai.install_archive(
        str(archive), library_folder=str(lib), pattern="{title}", title="Game",
    )
    assert seen == {"workers": 6, "exec_bits": True, "cancel": None}
    reference = tmp_path / "ref"
    extract_zip(str(archive), str(reference))
    apply_execute_bits(str(reference))
    assert _snapshot(lib / "Game") == _snapshot(reference / "Game")
    assert os.access(str(result["exe_path"]), os.X_OK)
