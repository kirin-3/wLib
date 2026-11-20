"""H21: the RPG Maker save editor must not overwrite a slot that changed
after it was read (for example, a game wrote it during the backup).
"""

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from test_rpgm_saves import _names, _slot_file, _stat

from core import rpgm_saves as rs

_PATCH: list[object] = [{"op": "set_switch", "id": 1, "value": False}]


def _write(path: Path, backup: Callable[[], str]) -> str:
    size, mtime = _stat(path)
    return rs.write_save(str(path), _PATCH, size, mtime, _names(), backup)


def _no_temp(folder: Path) -> bool:
    return not list(folder.glob("*.wlib-tmp"))


def test_write_during_backup_is_kept(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)
    newer = path.read_bytes() + b" "
    calls: list[int] = []

    def backup() -> str:
        calls.append(1)
        _ = path.write_bytes(newer)  # the game saves while wLib backs up
        return "b"

    with pytest.raises(rs.SaveChanged):
        _ = _write(path, backup)
    assert calls == [1]  # the backup was made and is kept
    assert path.read_bytes() == newer
    assert _no_temp(tmp_path)


def test_same_size_and_mtime_change_is_caught_by_content(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)
    original = path.read_bytes()
    stamp = os.stat(path).st_mtime_ns
    # Same length, different bytes, and the old mtime put back.
    newer = original[:-1] + (b"A" if original[-1:] != b"A" else b"B")

    def backup() -> str:
        with open(path, "r+b") as handle:
            _ = handle.write(newer)
        os.utime(path, ns=(stamp, stamp))
        return "b"

    with pytest.raises(rs.SaveChanged):
        _ = _write(path, backup)
    assert path.read_bytes() == newer
    assert _no_temp(tmp_path)


def test_replaced_file_is_caught_by_identity(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)
    original = path.read_bytes()
    stamp = os.stat(path).st_mtime_ns
    other = tmp_path / "other.bin"

    def backup() -> str:
        # Same bytes and mtime, but a new file (new inode) took the name.
        _ = other.write_bytes(original)
        os.utime(other, ns=(stamp, stamp))
        os.replace(other, path)
        return "b"

    with pytest.raises(rs.SaveChanged):
        _ = _write(path, backup)
    assert path.read_bytes() == original
    assert _no_temp(tmp_path)


def test_file_removed_before_replace_is_not_recreated(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)

    def backup() -> str:
        path.unlink()
        return "b"

    with pytest.raises(rs.SaveChanged):
        _ = _write(path, backup)
    assert not path.exists()
    assert _no_temp(tmp_path)


def test_unchanged_file_is_still_written(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)
    original = path.read_bytes()

    assert _write(path, lambda: "b") == "b"
    assert path.read_bytes() != original
    assert _no_temp(tmp_path)
