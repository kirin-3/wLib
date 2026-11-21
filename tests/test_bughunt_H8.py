# SPDX-License-Identifier: GPL-3.0-or-later
"""H8: an uninstall deletes nothing until the library change is saved."""

import os
import sqlite3
import time

import pytest

import core.database as database
import core.game_files as gf
from core.database import add_game, get_game, init_db


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
        pattern="{title}",
    )
    return library, ctx


def _install(folder):
    exe = folder / "Game.exe"
    folder.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    (folder / "save.dat").write_text("progress")
    game_id = add_game(title="Game", exe_path=str(exe), developer="Dev")
    assert game_id is not None
    return game_id, exe


def _hooks(remove_entry=lambda _gid: None):
    return gf.UninstallHooks(
        running=lambda _gid: False,
        busy=lambda _gid: None,
        saves_folder=lambda _gid: "",
        remove_entry=remove_entry,
    )


def _locked(_game_id):
    raise sqlite3.OperationalError("database is locked")


def _wait_gone(path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while os.path.lexists(path) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not os.path.lexists(path)


@pytest.mark.parametrize("inside_library", [True, False])
def test_failed_commit_puts_the_folder_back(lib, tmp_path, monkeypatch, inside_library):
    library, ctx = lib
    root = (library if inside_library else tmp_path / "Elsewhere") / "Game"
    game_id, exe = _install(root)
    deletes: list[str] = []
    monkeypatch.setattr(
        gf, "remove_tree_collect", lambda path: deletes.append(path) or []
    )
    monkeypatch.setattr(
        gf, "defer_remove_tree", lambda path, trash: deletes.append(path) or True
    )
    monkeypatch.setattr(database, "clear_game_install", _locked)

    results = gf.run_uninstall(
        [game_id],
        {game_id: str(root)},
        backup=False,
        remove_from_library=False,
        hooks=_hooks(),
        ctx=ctx,
    )

    assert results[0]["success"] is False
    assert "nothing was deleted" in str(results[0]["error"])
    assert "database is locked" in str(results[0]["error"])
    assert deletes == []
    assert (root / "save.dat").read_text() == "progress"
    game = get_game(game_id)
    assert game is not None and game["exe_path"] == str(exe)
    assert gf.list_jobs("trash") == []
    assert not [name for name in os.listdir(root.parent) if name.startswith(".wlib-")]


def test_failed_remove_entry_puts_the_folder_back(lib):
    library, ctx = lib
    root = library / "Game"
    game_id, _exe = _install(root)

    results = gf.run_uninstall(
        [game_id],
        {game_id: str(root)},
        backup=False,
        remove_from_library=True,
        hooks=_hooks(remove_entry=_locked),
        ctx=ctx,
    )

    assert results[0]["success"] is False
    assert (root / "save.dat").read_text() == "progress"


def test_failed_commit_keeps_staged_folder_when_root_came_back(lib, monkeypatch):
    library, ctx = lib
    root = library / "Game"
    game_id, _exe = _install(root)

    def lock_after_recreate(_game_id):
        root.mkdir()
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(database, "clear_game_install", lock_after_recreate)
    results = gf.run_uninstall(
        [game_id],
        {game_id: str(root)},
        backup=False,
        remove_from_library=False,
        hooks=_hooks(),
        ctx=ctx,
    )

    assert results[0]["success"] is False
    staged = [
        name for name in os.listdir(library) if name.startswith(gf.UNINSTALL_PREFIX)
    ]
    assert len(staged) == 1
    assert staged[0] in str(results[0]["error"])
    assert (library / staged[0] / "save.dat").read_text() == "progress"


def test_failed_commit_without_rename_deletes_nothing(lib, monkeypatch):
    library, ctx = lib
    root = library / "Game"
    game_id, _exe = _install(root)

    def refuse(_src, _dst):
        raise PermissionError("busy")

    monkeypatch.setattr(gf.os, "rename", refuse)
    monkeypatch.setattr(database, "clear_game_install", _locked)
    results = gf.run_uninstall(
        [game_id],
        {game_id: str(root)},
        backup=False,
        remove_from_library=False,
        hooks=_hooks(),
        ctx=ctx,
    )

    assert results[0]["success"] is False
    time.sleep(0.1)
    assert (root / "save.dat").read_text() == "progress"


@pytest.mark.parametrize(
    ("inside_library", "how"), [(True, "library"), (False, "recorded")]
)
def test_delete_starts_only_after_commit(
    lib, tmp_path, monkeypatch, inside_library, how
):
    library, ctx = lib
    root = (library if inside_library else tmp_path / "Elsewhere") / "Game"
    game_id, _exe = _install(root)
    seen: list[list[str]] = []
    real_clear = database.clear_game_install

    def clear_and_look(gid):
        # The folder is set aside, not deleted, and not swept at a restart.
        seen.append(sorted(os.listdir(root.parent)))
        assert gf.list_jobs("trash") == []
        assert gf.sweep_leftovers(ctx.library_folders) == 0
        real_clear(gid)

    monkeypatch.setattr(database, "clear_game_install", clear_and_look)
    results = gf.run_uninstall(
        [game_id],
        {game_id: str(root)},
        backup=False,
        remove_from_library=False,
        hooks=_hooks(),
        ctx=ctx,
    )

    assert results[0]["success"] is True
    assert results[0]["trash"] == how
    assert len(seen) == 1 and len(seen[0]) == 1
    assert seen[0][0].startswith(gf.UNINSTALL_PREFIX)
    staged = root.parent / seen[0][0]
    assert not root.exists()
    deadline = time.monotonic() + 5
    while os.listdir(root.parent) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert os.listdir(root.parent) == []
    assert _wait_gone(str(staged))
    deadline = time.monotonic() + 5
    while gf.list_jobs("trash") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gf.list_jobs("trash") == []
    game = get_game(game_id)
    assert game is not None and game["exe_path"] == ""
