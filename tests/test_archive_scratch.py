"""Local install-scratch for network libraries: space limit, hard links, cleanup."""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections import namedtuple
from pathlib import Path

import pytest

import core.archive_install as ai

_Usage = namedtuple("_Usage", "total used free")
_GIB = 1024**3


def _fake_disk(monkeypatch, scratch_parent: Path, full_marker: str) -> None:
    """Scratch has room until a file named `full_marker` appears in it."""
    real = shutil.disk_usage

    def disk_usage(path):
        if os.path.realpath(str(path)).startswith(os.path.realpath(str(scratch_parent))):
            full = any(full_marker in files for _r, _d, files in os.walk(scratch_parent))
            free = 10 * 1024**2 if full else 100 * _GIB
            return _Usage(200 * _GIB, 200 * _GIB - free, free)
        return real(path)

    monkeypatch.setattr(ai.shutil, "disk_usage", disk_usage)


def _setup(tmp_path, monkeypatch):
    parent = tmp_path / "scratch"
    monkeypatch.setattr(ai, "_scratch_parent", lambda: str(parent))
    monkeypatch.setattr(ai, "SCRATCH_WATCH_SECONDS", 0.02, raising=False)
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"x" * 100)
    staging = tmp_path / "staging"
    staging.mkdir()
    return parent, archive, staging


def test_scratch_that_fills_up_falls_back_to_staging(tmp_path, monkeypatch):
    parent, archive, staging = _setup(tmp_path, monkeypatch)
    _fake_disk(monkeypatch, parent, "big.bin")

    def extract(folder, *args):
        (Path(folder) / "big.bin").write_bytes(b"x")
        stop = args[0] if args else None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if stop is not None and stop.is_set():
                raise ai.InstallCancelled()
            time.sleep(0.01)
        return 1

    used = ai._extract_tool_via_local_scratch(
        extract, str(archive), str(staging), 1, None, threading.Event()
    )
    assert used is False
    assert list(staging.iterdir()) == []
    assert list(parent.iterdir()) == []


def test_disk_full_error_in_scratch_falls_back_to_staging(tmp_path, monkeypatch):
    parent, archive, staging = _setup(tmp_path, monkeypatch)
    _fake_disk(monkeypatch, parent, "big.bin")

    def extract(folder, *_args):
        (Path(folder) / "big.bin").write_bytes(b"x")
        raise ai.ArchiveInstallError(ai._DISK_FULL_MESSAGE)

    used = ai._extract_tool_via_local_scratch(
        extract, str(archive), str(staging), 1, None, None
    )
    assert used is False
    assert list(staging.iterdir()) == []
    assert list(parent.iterdir()) == []


def test_other_scratch_errors_still_fail_the_install(tmp_path, monkeypatch):
    _parent, archive, staging = _setup(tmp_path, monkeypatch)

    def extract(_folder, *_args):
        raise ai.ArchiveInstallError("Extraction failed (bad data)")

    with pytest.raises(ai.ArchiveInstallError, match="bad data"):
        _ = ai._extract_tool_via_local_scratch(
            extract, str(archive), str(staging), 1, 10, None
        )


def test_user_cancel_during_scratch_extraction_cancels(tmp_path, monkeypatch):
    _parent, archive, staging = _setup(tmp_path, monkeypatch)
    cancel = threading.Event()

    def extract(_folder, stop):
        cancel.set()
        assert stop.wait(5)
        raise ai.InstallCancelled()

    with pytest.raises(ai.InstallCancelled):
        _ = ai._extract_tool_via_local_scratch(
            extract, str(archive), str(staging), 1, 10, cancel
        )


@pytest.mark.skipif(not hasattr(os, "link"), reason="needs hard links")
def test_scratch_copy_keeps_hard_links(tmp_path, monkeypatch):
    _parent, archive, staging = _setup(tmp_path, monkeypatch)

    def extract(folder, *_args):
        base = Path(folder)
        (base / "data").mkdir()
        (base / "data" / "big.bin").write_bytes(b"payload")
        os.link(base / "data" / "big.bin", base / "copy.bin")
        return 2

    assert ai._extract_tool_via_local_scratch(
        extract, str(archive), str(staging), 3, 10, None
    )
    first = os.lstat(staging / "data" / "big.bin")
    second = os.lstat(staging / "copy.bin")
    assert (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)
    assert (staging / "copy.bin").read_bytes() == b"payload"
    ai.check_symlinks_inside_staging(str(staging))


@pytest.mark.skipif(not hasattr(os, "link"), reason="needs hard links")
def test_hard_link_copy_falls_back_to_a_full_file(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"abc")
    os.link(src / "a.bin", src / "b.bin")
    dst = tmp_path / "dst"
    dst.mkdir()

    def no_link(*_args, **_kwargs):
        raise PermissionError("links not supported")

    monkeypatch.setattr(ai.os, "link", no_link)
    assert ai.copy_tree_parallel(str(src), str(dst), 1, hard_links=True) == 2
    assert (dst / "a.bin").read_bytes() == b"abc"
    assert (dst / "b.bin").read_bytes() == b"abc"


def _locked_tree(root: Path) -> Path:
    locked = root / "locked"
    locked.mkdir(parents=True)
    (locked / "f.txt").write_bytes(b"x")
    locked.chmod(0o500)
    return locked


_needs_posix_user = pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs a non-root POSIX user",
)


@_needs_posix_user
def test_cleanup_removes_a_tree_with_a_read_only_folder(tmp_path):
    tree = tmp_path / "staging"
    locked = _locked_tree(tree)
    try:
        ai.cleanup_staging_dir(str(tree))
        assert not tree.exists()
    finally:
        if locked.exists():
            locked.chmod(0o755)


@_needs_posix_user
def test_cleanup_does_not_change_modes_through_links(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o500)
    tree = tmp_path / "staging"
    tree.mkdir()
    (tree / "link").symlink_to(outside, target_is_directory=True)
    try:
        ai.cleanup_staging_dir(str(tree))
        assert not tree.exists()
        assert outside.stat().st_mode & 0o777 == 0o500
    finally:
        outside.chmod(0o755)


@_needs_posix_user
def test_stale_scratch_sweep_removes_read_only_folders(tmp_path):
    parent = tmp_path / "scratch"
    old = parent / f"{ai.SCRATCH_PREFIX}old"
    locked = _locked_tree(old)
    past = time.time() - 2 * 86400
    os.utime(old, (past, past))
    try:
        ai._sweep_stale_scratch(str(parent))
        assert not old.exists()
    finally:
        if locked.exists():
            locked.chmod(0o755)
