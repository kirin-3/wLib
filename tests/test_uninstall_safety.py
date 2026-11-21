# SPDX-License-Identifier: GPL-3.0-or-later
"""Uninstall refuses link roots, keeps the game when no rename works, and
backs up loose save files next to the executable."""

import os

import pytest

import core.game_files as gf
from core.database import add_game, get_game, init_db
from core.game_saves import backup_saves, game_backup_dir, list_backups


@pytest.fixture
def lib(tmp_path, monkeypatch):
    init_db()
    library = tmp_path / "Library"
    library.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    ctx = gf.FilesContext(
        library_folders=[str(library)],
        downloads_folder=str(tmp_path / "Downloads"),
        pattern="{creator}/{title}/{version}",
    )
    return library, ctx


def _add(exe):
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    game_id = add_game(title="Game", exe_path=str(exe), developer="Dev")
    assert game_id is not None
    return game_id


def _hooks(**extra):
    return gf.UninstallHooks(
        running=lambda _gid: False,
        busy=lambda _gid: None,
        saves_folder=lambda _gid: "",
        **extra,
    )


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_link_root_is_refused_and_real_root_works(lib, tmp_path):
    library, ctx = lib
    real = tmp_path / "OtherDrive" / "Game"
    real.mkdir(parents=True)
    exe = real / "Game.exe"
    link = library / "Game"
    link.symlink_to(real, target_is_directory=True)
    game_id = _add(link / "Game.exe")

    plan = gf.plan_uninstall([game_id], {}, _hooks(), ctx)[0]
    assert plan["root"] == str(link)
    assert plan["removable"] is False
    assert str(real) in str(plan["reason"])

    results = gf.run_uninstall(
        [game_id], {}, backup=False, remove_from_library=False, hooks=_hooks(), ctx=ctx
    )
    assert results[0]["success"] is False
    assert link.is_symlink() and exe.exists()
    game = get_game(game_id)
    assert game is not None and game["exe_path"]

    results = gf.run_uninstall(
        [game_id],
        {game_id: str(real)},
        backup=False,
        remove_from_library=False,
        hooks=_hooks(),
        ctx=ctx,
    )
    assert results[0]["success"] is True
    assert not real.exists()


def test_failed_renames_keep_files_and_entry(lib, monkeypatch):
    library, ctx = lib
    root = library / "Game"
    game_id = _add(root / "Game.exe")
    real_rename = os.rename

    def refuse(src, dst):
        if os.path.basename(str(dst)).startswith(gf.UNINSTALL_PREFIX):
            raise PermissionError(13, "Read-only file system")
        real_rename(src, dst)

    monkeypatch.setattr(gf.os, "rename", refuse)
    results = gf.run_uninstall(
        [game_id], {}, backup=False, remove_from_library=False, hooks=_hooks(), ctx=ctx
    )
    assert results[0]["success"] is False
    assert "nothing was deleted" in str(results[0]["error"])
    assert "Read-only file system" in str(results[0]["error"])
    assert (root / "Game.exe").exists()
    game = get_game(game_id)
    assert game is not None and game["exe_path"] == str(root / "Game.exe")


def test_loose_saves_are_planned_and_backed_up(lib, tmp_path):
    library, ctx = lib
    root = library / "Game"
    game_id = _add(root / "Game.exe")
    (root / "Save01.rvdata2").write_bytes(b"save")
    (root / "Data.rvdata2x").write_bytes(b"x")

    plan = gf.plan_uninstall([game_id], {}, _hooks(), ctx)[0]
    assert plan["saves_folder"] == str(root)
    assert plan["saves_inside_root"] is True
    assert plan["save_files"] == ["Save01.rvdata2"]

    backups_root = tmp_path / "backups"
    calls = []

    def backup_files(gid, folder, files):
        calls.append((gid, folder, list(files)))
        _ = backup_saves(folder, str(backups_root), gid, None, files=files)
        return None

    results = gf.run_uninstall(
        [game_id],
        {},
        backup=True,
        remove_from_library=False,
        hooks=_hooks(backup_save_files=backup_files),
        ctx=ctx,
    )
    assert results[0]["success"] is True
    assert calls == [(game_id, str(root), ["Save01.rvdata2"])]
    backup_dir = game_backup_dir(str(backups_root), game_id)
    names = list_backups(backup_dir)
    assert len(names) == 1
    assert sorted(os.listdir(os.path.join(backup_dir, names[0]))) == ["Save01.rvdata2"]


def test_loose_save_backup_error_keeps_files(lib):
    library, ctx = lib
    root = library / "Game"
    game_id = _add(root / "Game.exe")
    (root / "Save01.rvdata2").write_bytes(b"save")
    results = gf.run_uninstall(
        [game_id],
        {},
        backup=True,
        remove_from_library=False,
        hooks=_hooks(backup_save_files=lambda _g, _f, _n: "disk full"),
        ctx=ctx,
    )
    assert results[0]["success"] is False
    assert "disk full" in str(results[0]["error"])
    assert (root / "Save01.rvdata2").exists()
