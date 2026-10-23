# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from core import game_saves
from core.game_saves import (
    GameSavesError,
    NothingToBackUpError,
    SaveLocation,
    backup_saves,
    detect_save_locations,
    game_backup_dir,
    import_saves,
    list_backups,
    new_backup_name,
    prune_backups,
    resolve_backup,
    resolve_effective_folder,
    validate_save_override,
)

NOW = datetime(2026, 10, 23, 12, 0, 0)


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text)
    return path


def _tree(root: Path) -> dict[str, str]:
    return {
        os.path.relpath(p, root).replace(os.sep, "/"): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(autouse=True)
def _linux_host(monkeypatch, tmp_path, tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("saves-db")
    monkeypatch.setattr("core.database.DB_PATH", str(db_dir / "saves.db"))
    from core.database import init_db

    init_db()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


# ── 2.1 Detection ────────────────────────────────────────────────────────


def test_exe_folder_with_loose_saves_is_not_eligible(tmp_path):
    game = tmp_path / "game"
    _ = _write(game / "run.exe")
    _ = _write(game / "slot1.save")

    results = detect_save_locations(str(game / "run.exe"), "Run", windows=False)

    assert [(r["path"], r["eligible"]) for r in results] == [(str(game), False)]


def test_in_folder_save_dir_is_eligible(tmp_path):
    game = tmp_path / "game"
    _ = _write(game / "run.exe")
    _ = _write(game / "game" / "saves" / "1.save")

    results = detect_save_locations(str(game / "run.exe"), "Run", windows=False)

    assert [(r["path"], r["eligible"]) for r in results] == [
        (str(game / "game" / "saves"), True)
    ]


def test_renpy_folder_is_eligible(tmp_path):
    saves = tmp_path / "home" / ".renpy" / "MyGame-123"
    saves.mkdir(parents=True)

    results = detect_save_locations(
        str(tmp_path / "missing" / "run.exe"), "My Game", windows=False
    )

    assert [(r["path"], r["eligible"]) for r in results] == [(str(saves), True)]


# ── 2.2 Effective folder ─────────────────────────────────────────────────


def _loc(path: str, eligible: bool = True) -> SaveLocation:
    return {"path": path, "type": "t", "description": "d", "eligible": eligible}


def test_resolve_effective_folder_rules():
    assert resolve_effective_folder("/o", [_loc("/a"), _loc("/b")]) == "/o"
    assert resolve_effective_folder("  ", [_loc("/a")]) == "/a"
    assert resolve_effective_folder("", [_loc("/a"), _loc("/b")]) == ""
    assert resolve_effective_folder("", [_loc("/exe", eligible=False)]) == ""
    assert resolve_effective_folder("", [_loc("/exe", False), _loc("/a")]) == "/a"
    assert resolve_effective_folder("", []) == ""


# ── 2.3 Names, listing, pruning ──────────────────────────────────────────


def test_backup_names_get_a_suffix_on_collision(tmp_path):
    assert new_backup_name(str(tmp_path), NOW) == "2026-10-23_12-00-00"
    (tmp_path / "2026-10-23_12-00-00").mkdir()
    assert new_backup_name(str(tmp_path), NOW) == "2026-10-23_12-00-00-1"
    (tmp_path / "2026-10-23_12-00-00-1").mkdir()
    assert new_backup_name(str(tmp_path), NOW) == "2026-10-23_12-00-00-2"


def test_list_backups_orders_newest_first_and_ignores_foreign(tmp_path):
    names = [
        "2026-10-23_12-00-00",
        "2026-10-23_12-00-00-2",
        "2026-10-23_12-00-00-10",
        "2026-10-22_08-00-00",
    ]
    for name in names:
        (tmp_path / name).mkdir()
    (tmp_path / "my own notes").mkdir()
    (tmp_path / ".2026-10-24_00-00-00.partial").mkdir()
    _ = _write(tmp_path / "2026-10-25_00-00-00")  # a file, not a backup

    assert list_backups(str(tmp_path)) == [
        "2026-10-23_12-00-00-10",
        "2026-10-23_12-00-00-2",
        "2026-10-23_12-00-00",
        "2026-10-22_08-00-00",
    ]
    assert list_backups(str(tmp_path / "nope")) == []


def test_prune_keeps_newest_foreign_folders_and_protected(tmp_path):
    for day in range(1, 6):
        (tmp_path / f"2026-10-0{day}_00-00-00").mkdir()
    (tmp_path / "keep me").mkdir()

    deleted = prune_backups(str(tmp_path), 2, protect={"2026-10-01_00-00-00"})

    assert sorted(deleted) == ["2026-10-02_00-00-00", "2026-10-03_00-00-00"]
    assert sorted(os.listdir(tmp_path)) == [
        "2026-10-01_00-00-00",
        "2026-10-04_00-00-00",
        "2026-10-05_00-00-00",
        "keep me",
    ]


def test_prune_with_none_deletes_nothing(tmp_path):
    for day in range(1, 4):
        (tmp_path / f"2026-10-0{day}_00-00-00").mkdir()
    assert prune_backups(str(tmp_path), None) == []
    assert len(os.listdir(tmp_path)) == 3


# ── 2.4 Backup ───────────────────────────────────────────────────────────


def test_backup_copies_files_and_mtimes(tmp_path):
    saves = tmp_path / "saves"
    first = _write(saves / "1-1-LT1.save", "a")
    _ = _write(saves / "sub" / "persistent", "b")
    (saves / "empty-dir").mkdir()
    os.utime(first, (1_000_000_000, 1_000_000_000))
    root = tmp_path / "backups"

    info = backup_saves(str(saves), str(root), 7, None, now=NOW)

    backup = Path(info.path)
    assert backup == root / "7" / "2026-10-23_12-00-00"
    assert _tree(backup) == {"1-1-LT1.save": "a", "sub/persistent": "b"}
    assert (backup / "empty-dir").is_dir()
    assert int((backup / "1-1-LT1.save").stat().st_mtime) == 1_000_000_000
    assert (info.files, info.bytes, info.skipped_links) == (2, 2, 0)
    assert list_backups(game_backup_dir(str(root), 7)) == [info.name]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_backup_skips_symlinks(tmp_path):
    saves = tmp_path / "saves"
    _ = _write(saves / "real.save")
    outside = _write(tmp_path / "outside" / "secret.txt")
    try:
        os.symlink(outside, saves / "link.txt")
        os.symlink(outside.parent, saves / "linked-dir", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted")

    info = backup_saves(str(saves), str(tmp_path / "b"), 1, None, now=NOW)

    assert _tree(Path(info.path)) == {"real.save": "x"}
    assert info.skipped_links == 2


@pytest.mark.parametrize("kind", ["missing", "empty", "only_empty_dirs"])
def test_backup_of_nothing_creates_no_folder(tmp_path, kind):
    saves = tmp_path / "saves"
    if kind != "missing":
        saves.mkdir()
    if kind == "only_empty_dirs":
        (saves / "sub").mkdir()
    root = tmp_path / "backups"

    with pytest.raises(NothingToBackUpError):
        _ = backup_saves(str(saves), str(root), 1, None, now=NOW)

    assert not root.exists() or not any(root.rglob("*"))


def test_failed_copy_leaves_no_partial_or_final_folder(tmp_path, monkeypatch):
    saves = tmp_path / "saves"
    _ = _write(saves / "a.save")
    _ = _write(saves / "b.save")
    real_copy2 = shutil.copy2
    calls = {"n": 0}

    def flaky(src, dst, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(28, "No space left on device")
        return real_copy2(src, dst, **kwargs)

    monkeypatch.setattr(game_saves.shutil, "copy2", flaky)
    root = tmp_path / "backups"

    with pytest.raises(GameSavesError, match="Backup failed"):
        _ = backup_saves(str(saves), str(root), 1, None, now=NOW)

    assert os.listdir(root / "1") == []


def test_backup_prunes_after_success_and_keeps_new_one(tmp_path):
    saves = tmp_path / "saves"
    _ = _write(saves / "a.save")
    root = tmp_path / "backups"
    for day in (1, 2, 3):
        (root / "1" / f"2026-10-0{day}_00-00-00").mkdir(parents=True)

    info = backup_saves(str(saves), str(root), 1, 2, now=NOW)

    assert set(info.pruned) == {"2026-10-01_00-00-00", "2026-10-02_00-00-00"}
    assert list_backups(str(root / "1")) == [info.name, "2026-10-03_00-00-00"]


def test_backup_refuses_when_saves_folder_contains_backups(tmp_path):
    saves = tmp_path / "saves"
    _ = _write(saves / "a.save")

    with pytest.raises(GameSavesError, match="contains"):
        _ = backup_saves(str(saves), str(saves / "inner"), 1, None, now=NOW)


# ── 2.5 Import ───────────────────────────────────────────────────────────


def test_import_merges_and_backs_up_first(tmp_path):
    saves = tmp_path / "saves"
    _ = _write(saves / "1.save", "old1")
    _ = _write(saves / "2.save", "old2")
    source = tmp_path / "source"
    _ = _write(source / "2.save", "new2")
    _ = _write(source / "3.save", "new3")
    root = tmp_path / "backups"

    info = import_saves(str(source), str(saves), str(root), 5, None, now=NOW)

    assert _tree(saves) == {"1.save": "old1", "2.save": "new2", "3.save": "new3"}
    assert info.backup is not None
    assert _tree(Path(info.backup.path)) == {"1.save": "old1", "2.save": "old2"}
    assert list_backups(str(root / "5")) == [info.backup.name]


def test_failed_pre_import_backup_writes_nothing(tmp_path, monkeypatch):
    saves = tmp_path / "saves"
    _ = _write(saves / "1.save", "old")
    source = tmp_path / "source"
    _ = _write(source / "1.save", "new")
    _ = _write(source / "2.save", "new")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(game_saves.shutil, "copy2", boom)

    with pytest.raises(GameSavesError, match="stopped to protect"):
        _ = import_saves(str(source), str(saves), str(tmp_path / "b"), 1, None)

    assert _tree(saves) == {"1.save": "old"}


def test_import_rejects_overlapping_source(tmp_path):
    saves = tmp_path / "saves"
    _ = _write(saves / "1.save")
    _ = _write(saves / "inner" / "2.save")
    _ = _write(tmp_path / "other.save")
    root = str(tmp_path / "b")

    for source in (saves, saves / "inner", tmp_path):
        with pytest.raises(GameSavesError, match="cannot be the saves folder"):
            _ = import_saves(str(source), str(saves), root, 1, None)

    assert not os.path.exists(root)
    assert _tree(saves) == {"1.save": "x", "inner/2.save": "x"}


@pytest.mark.parametrize("direction", ["file_over_dir", "dir_over_file"])
def test_import_type_conflict_stops_before_any_write(tmp_path, direction):
    saves = tmp_path / "saves"
    source = tmp_path / "source"
    _ = _write(source / "a.save", "new")
    if direction == "file_over_dir":
        _ = _write(source / "slot", "file")
        _ = _write(saves / "slot" / "inner.save", "old")
    else:
        _ = _write(source / "slot" / "inner.save", "new")
        _ = _write(saves / "slot", "old")
    root = tmp_path / "b"

    with pytest.raises(GameSavesError, match="Cannot import"):
        _ = import_saves(str(source), str(saves), str(root), 1, None)

    assert not (saves / "a.save").exists()
    assert not root.exists()


def test_import_creates_missing_target_without_backup(tmp_path):
    source = tmp_path / "source"
    _ = _write(source / "a.save", "a")
    _ = _write(source / "sub" / "b.save", "b")
    saves = tmp_path / "new" / "saves"
    root = tmp_path / "b"

    info = import_saves(str(source), str(saves), str(root), 1, None)

    assert _tree(saves) == {"a.save": "a", "sub/b.save": "b"}
    assert info.backup is None
    assert not root.exists()


def test_import_rejects_missing_or_empty_source(tmp_path):
    saves = tmp_path / "saves"
    with pytest.raises(GameSavesError, match="does not exist"):
        _ = import_saves(str(tmp_path / "nope"), str(saves), str(tmp_path), 1, None)
    (tmp_path / "empty").mkdir()
    with pytest.raises(GameSavesError, match="no files"):
        _ = import_saves(str(tmp_path / "empty"), str(saves), str(tmp_path), 1, None)


def test_restore_with_keep_one_never_prunes_its_own_source(tmp_path):
    saves = tmp_path / "saves"
    _ = _write(saves / "a.save", "first")
    root = tmp_path / "backups"
    first = backup_saves(str(saves), str(root), 1, 1, now=NOW)
    _ = _write(saves / "a.save", "second")

    info = import_saves(
        first.path, str(saves), str(root), 1, 1, now=datetime(2026, 10, 24)
    )

    assert _tree(saves) == {"a.save": "first"}
    assert info.backup is not None
    assert os.path.isdir(first.path)
    assert set(list_backups(str(root / "1"))) == {first.name, info.backup.name}


# ── 2.6 Override validation and backup lookup ────────────────────────────


def test_override_validation(tmp_path):
    root = str(tmp_path / "save-backups")
    assert validate_save_override("", root) == ""
    assert validate_save_override("   ", root) == ""

    home = tmp_path / "home"
    assert validate_save_override("~/.renpy/Game-1", root) == str(
        home / ".renpy" / "Game-1"
    )
    missing = tmp_path / "does" / "not" / "exist"
    assert validate_save_override(str(missing), root) == str(missing)

    with pytest.raises(GameSavesError, match="absolute"):
        _ = validate_save_override("relative/path", root)
    with pytest.raises(GameSavesError, match="backups folder"):
        _ = validate_save_override(os.path.join(root, "1", "x"), root)
    with pytest.raises(GameSavesError, match="backups folder"):
        _ = validate_save_override(root, root)
    existing_file = _write(tmp_path / "file.txt")
    with pytest.raises(GameSavesError, match="file"):
        _ = validate_save_override(str(existing_file), root)


def test_resolve_backup_rejects_traversal_and_missing(tmp_path):
    root = tmp_path / "backups"
    (root / "1" / "2026-10-23_12-00-00").mkdir(parents=True)
    (root / "2" / "2026-10-23_12-00-00").mkdir(parents=True)

    assert resolve_backup(str(root), 1, "2026-10-23_12-00-00") == str(
        root / "1" / "2026-10-23_12-00-00"
    )
    for bad in ("../2/2026-10-23_12-00-00", "..", "", "x", "2026-10-23_12-00-01"):
        with pytest.raises(GameSavesError):
            _ = resolve_backup(str(root), 1, bad)
