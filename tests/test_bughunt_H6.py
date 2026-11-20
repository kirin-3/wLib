"""H6: the startup sweep deletes only trash folders that wLib named."""

import os
import time

import core.archive_install as ai
import core.game_files as gf
from core.archive_install import TRASH_PREFIX, sweep_trash


def _wait_gone(path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not os.path.exists(path):
            return True
        time.sleep(0.02)
    return False


def _wait_idle(timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with ai._trash_lock:
            if not ai._trash_in_flight:
                return
        time.sleep(0.02)


def test_sweep_keeps_user_folders_with_the_trash_prefix(tmp_path):
    lib = tmp_path / "lib"
    manual = lib / f"{TRASH_PREFIX}manual-backup"
    short = lib / f"{TRASH_PREFIX}2024"
    for folder in (manual, short):
        (folder / "saves").mkdir(parents=True)
        (folder / "saves" / "keep.txt").write_text("x")
    real = lib / f"{TRASH_PREFIX}{time.time_ns()}"
    (real / "a").mkdir(parents=True)

    assert sweep_trash(str(lib)) == 1
    assert _wait_gone(real)
    _wait_idle()
    assert (manual / "saves" / "keep.txt").is_file()
    assert (short / "saves" / "keep.txt").is_file()


def test_sweep_leftovers_keeps_manual_trash_named_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    lib = tmp_path / "lib"
    manual = lib / f"{TRASH_PREFIX}manual-backup"
    manual.mkdir(parents=True)
    (manual / "keep.txt").write_text("x")

    assert gf.sweep_leftovers([str(lib)]) == 0
    _wait_idle()
    assert (manual / "keep.txt").is_file()


def test_wlib_trash_names_match_the_sweep(tmp_path):
    lib = tmp_path / "lib"
    old = lib / "Game" / "1.0"
    old.mkdir(parents=True)
    assert ai.defer_remove_tree(str(old), str(lib)) is True
    # The name wLib makes is one the sweep accepts.
    name = f"{TRASH_PREFIX}{time.time_ns()}"
    assert ai._TRASH_NAME_RE.fullmatch(name)
