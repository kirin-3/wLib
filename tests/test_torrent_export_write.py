# SPDX-License-Identifier: GPL-3.0-or-later
"""Exported `.torrent` files: each write has its own temporary file and keeps the umask mode."""
import os
import sys
import threading

import pytest

from core.downloads import torrent_export
from core.downloads.torrent_export import write_torrent


def test_overlapping_exports_of_one_name_do_not_share_a_part_file(tmp_path, monkeypatch):
    real_fsync = os.fsync
    both_open = threading.Barrier(2, timeout=5)
    parts: set[str] = set()

    def fsync(fd: int) -> None:
        parts.add(os.readlink(f"/proc/self/fd/{fd}") if sys.platform == "linux" else str(fd))
        _ = both_open.wait()  # Both writers hold their temporary file before either renames it.
        real_fsync(fd)

    monkeypatch.setattr(torrent_export.os, "fsync", fsync)
    errors: list[BaseException] = []

    def run(data: bytes) -> None:
        try:
            _ = write_torrent(str(tmp_path), "game.torrent", data)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(b"A" * 1000,)), threading.Thread(target=run, args=(b"B" * 1000,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(parts) == 2
    assert (tmp_path / "game.torrent").read_bytes() in (b"A" * 1000, b"B" * 1000)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["game.torrent"]


def test_failed_write_removes_its_part_file(tmp_path, monkeypatch):
    def fail(_fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(torrent_export.os, "fsync", fail)
    with pytest.raises(OSError):
        _ = write_torrent(str(tmp_path), "game.torrent", b"data")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_export_keeps_the_umask_mode(tmp_path):
    old = os.umask(0o022)
    try:
        path = write_torrent(str(tmp_path), "game.torrent", b"data")
    finally:
        _ = os.umask(old)
    assert os.stat(path).st_mode & 0o777 == 0o644


def test_export_opens_its_part_file_in_binary_mode(tmp_path, monkeypatch):
    # Windows os.open() is text mode without O_BINARY: LF bytes become CR LF.
    o_binary = 0x40000000
    real_open = os.open
    flags_seen: list[int] = []

    def fake_open(path, flags, mode=0o777, *args, **kwargs):
        flags_seen.append(flags)
        return real_open(path, flags & ~o_binary, mode, *args, **kwargs)

    monkeypatch.setattr(os, "O_BINARY", o_binary, raising=False)
    monkeypatch.setattr(torrent_export.os, "open", fake_open)
    path = write_torrent(str(tmp_path), "game.torrent", b"d4:info\n\ne")
    assert flags_seen and all(flags & o_binary for flags in flags_seen)
    assert open(path, "rb").read() == b"d4:info\n\ne"
