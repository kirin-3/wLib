# SPDX-License-Identifier: GPL-3.0-or-later
"""Uninstall and move bridge methods."""

import json
import os
import time

import pytest

from core.api import Api
from core.database import add_game, get_game, init_db, update_game, update_setting
import core.game_files as gf


def _wait_gone(path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while os.path.lexists(path) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not os.path.lexists(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path / "home"))
    init_db()
    library = tmp_path / "Library"
    library.mkdir()
    update_setting("library_folders", json.dumps([str(library)]))
    update_setting("library_folder", str(library))
    backups = tmp_path / "save-backups"
    monkeypatch.setattr(Api, "_save_backups_root", staticmethod(lambda: str(backups)))
    api = Api()
    monkeypatch.setattr(api, "get_running_games", lambda: [])
    return api, library, backups


def _install(folder, title="Game", **fields):
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "Game.exe"
    exe.write_bytes(b"MZ")
    game_id = add_game(title=title, exe_path=str(exe))
    assert game_id is not None
    if fields:
        _ = update_game(game_id, fields)
    return game_id


def _saves_inside(api, monkeypatch, folder):
    monkeypatch.setattr(api, "_effective_saves_folder", lambda gid: str(folder))


def test_uninstall_keeps_game_as_wishlist(env):
    api, library, _backups = env
    game_id = _install(library / "Game", progress="Chapter 2")
    plan = api.plan_game_uninstall([game_id])
    assert plan["success"] is True
    assert plan["games"][0]["root"] == str(library / "Game")
    result = api.uninstall_games([game_id], {"backup_saves": False})
    assert result["success"] is True, result
    row = get_game(game_id)
    assert row is not None
    assert row["exe_path"] == "" and row["progress"] == "Chapter 2"
    assert not (library / "Game").exists()


def test_uninstall_remove_too_with_backup(env, monkeypatch):
    api, library, backups = env
    game_id = _install(library / "Game")
    saves = library / "Game" / "www" / "save"
    saves.mkdir(parents=True)
    (saves / "file1.rpgsave").write_text("data")
    _saves_inside(api, monkeypatch, saves)
    plan = api.plan_game_uninstall([game_id])["games"][0]
    assert plan["saves_inside_root"] is True
    result = api.uninstall_games(
        [game_id], {"backup_saves": True, "remove_from_library": True}
    )
    assert result["success"] is True, result
    assert get_game(game_id) is None
    assert not (library / "Game").exists()
    # The backup outlives the entry, moved aside under removed/.
    kept = list((backups / "removed").iterdir())
    assert len(kept) == 1


def test_uninstall_empty_saves_folder_continues(env, monkeypatch):
    api, library, _backups = env
    game_id = _install(library / "Game")
    saves = library / "Game" / "save"
    saves.mkdir()
    _saves_inside(api, monkeypatch, saves)
    assert api.uninstall_games([game_id], {"backup_saves": True})["success"] is True


def test_failed_backup_keeps_files(env, monkeypatch):
    import core.game_saves

    api, library, _backups = env
    game_id = _install(library / "Game")
    saves = library / "Game" / "save"
    saves.mkdir()
    (saves / "a.sav").write_text("x")
    _saves_inside(api, monkeypatch, saves)

    def full_disk(*_args, **_kwargs):
        raise core.game_saves.GameSavesError("Backup failed: No space left on device")

    monkeypatch.setattr(core.game_saves, "backup_saves", full_disk)
    result = api.uninstall_games([game_id], {"backup_saves": True})
    assert result["success"] is False
    assert "No space left" in str(result["error"])
    assert (library / "Game" / "Game.exe").is_file()
    row = get_game(game_id)
    assert row is not None and row["exe_path"]
    assert game_id not in api._uninstalling


def test_custom_prefix_inside_and_outside_root(env, tmp_path):
    api, library, _backups = env
    inside_id = _install(library / "A", title="A")
    (library / "A" / "prefix").mkdir()
    _ = update_game(inside_id, {"custom_prefix": str(library / "A" / "prefix")})
    outside_prefix = tmp_path / "prefixes" / "b"
    outside_prefix.mkdir(parents=True)
    outside_id = _install(library / "B", title="B", custom_prefix=str(outside_prefix))
    result = api.uninstall_games([inside_id, outside_id], {})
    assert result["success"] is True, result
    assert not (library / "A").exists()
    assert outside_prefix.is_dir()


def test_partial_bulk_failure(env):
    api, library, _backups = env
    good = _install(library / "Good", title="Good")
    wish = add_game(title="Wish", exe_path="")
    unsafe = _install(library / "Pack" / "X", title="X")
    _install(library / "Pack" / "Y", title="Y")
    result = api.uninstall_games(
        [good, wish, unsafe], {"root_overrides": {str(unsafe): str(library / "Pack")}}
    )
    assert result["success"] is False
    assert (result["uninstalled"], result["failed"]) == (1, 2)
    assert result["error"] == gf.NOT_INSTALLED
    assert not (library / "Good").exists()
    assert (library / "Pack" / "X" / "Game.exe").is_file()


def test_running_or_busy_game_refused(env, monkeypatch):
    api, library, _backups = env
    game_id = _install(library / "Game")
    monkeypatch.setattr(api, "get_running_games", lambda: [game_id])
    result = api.uninstall_games([game_id], {})
    assert result["error"] == "Close the game before uninstalling it."
    monkeypatch.setattr(api, "get_running_games", lambda: [])
    api._tool_status[game_id] = {"running": True}
    result = api.uninstall_games([game_id], {})
    assert "mod tool" in str(result["error"])
    assert (library / "Game" / "Game.exe").is_file()


def test_uninstalling_and_moving_games_are_busy(env):
    api, library, _backups = env
    game_id = _install(library / "Game")
    api._uninstalling.add(game_id)
    with api._status_lock:
        assert api._game_busy_reason(game_id) == gf.UNINSTALLING_BUSY
    assert api.delete_game(game_id)["error"] == gf.UNINSTALLING_BUSY
    api._uninstalling.clear()
    assert api.delete_game(game_id)["success"] is True


def test_move_bridge_moves_and_reports(env, tmp_path):
    api, library, _backups = env
    game_id = _install(library / "Game")
    dest = tmp_path / "new"
    dest.mkdir()
    plan = api.plan_game_move([game_id], str(dest))
    assert plan["success"] is True
    assert plan["games"][0]["move_reason"] == ""
    started = api.start_game_move([game_id], str(dest))
    assert started == {"success": True, "queued": [game_id]}
    deadline = time.monotonic() + 5
    summary = None
    while time.monotonic() < deadline:
        status = api.get_move_status()
        if "summary" in status:
            summary = status["summary"]
            break
        time.sleep(0.01)
    assert summary is not None and summary["moved"] == 1
    row = get_game(game_id)
    assert row is not None
    assert row["exe_path"] == str(dest / "Game" / "Game.exe")


def test_move_queue_blocks_launch_and_delete(env, tmp_path, monkeypatch):
    import threading

    api, library, _backups = env
    game_id = _install(library / "Game")
    gate = threading.Event()
    started = threading.Event()

    def held(job, cancel, on_bytes, set_total):
        started.set()
        assert gate.wait(5)
        return gf.MoveOutcome("cancelled")

    monkeypatch.setattr(api._game_moves, "_runner", held)
    _ = api.start_game_move([game_id], str(tmp_path))
    assert started.wait(5)
    result = api.delete_game(game_id)
    assert result["error"] == gf.MOVING_BUSY
    assert api.uninstall_games([game_id], {})["error"] == gf.MOVING_BUSY
    _ = api.cancel_game_move(True)
    gate.set()
    deadline = time.monotonic() + 5
    while api.get_move_status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert api.delete_game(game_id)["success"] is True


def test_start_move_needs_existing_destination(env, tmp_path):
    api, library, _backups = env
    game_id = _install(library / "Game")
    result = api.start_game_move([game_id], str(tmp_path / "missing"))
    assert result["success"] is False
