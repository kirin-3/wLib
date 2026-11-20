# SPDX-License-Identifier: GPL-3.0-or-later
"""A cancelled update install leaves the game and a moved download as they were."""
import json
import threading
import time

import pytest

import core.archive_install as ai
from core.archive_install import InstallCancelled, install_archive
from core.downloads import torrents
from core.downloads.torrents import TorrentSupervisor


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    import core.database as database

    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "db.sqlite"))
    database.init_db()
    sup = TorrentSupervisor(command=lambda: (["true"], {}), free_space_fn=lambda p: 10**12, options=dict)
    monkeypatch.setattr(torrents, "supervisor", sup)
    return sup


def _old_game(tmp_path):
    import core.database as database

    old = tmp_path / "lib" / "Game" / "1.0"
    (old / "game" / "saves").mkdir(parents=True)
    (old / "Game.exe").write_text("old exe")
    (old / "slot1.sav").write_text("player save")
    (old / "game" / "saves" / "1-1.save").write_text("renpy save")
    return old, database.add_game("Game", str(old / "Game.exe"), version="1.0")


def _download(tmp_path):
    folder = tmp_path / "data" / "downloads" / "torrents" / ("ab" * 20)
    game = folder / "data" / "Game-2.0-pc"
    game.mkdir(parents=True)
    (game / "Game.exe").write_text("new exe")
    (game / "slot1.sav").write_text("download save")
    (folder / "state.json").write_text(json.dumps({
        "job_id": "j1", "game_id": 1, "size": 10, "phase": "ended", "updated": time.time(),
    }))
    return folder, game


def _files(root):
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def test_cancel_after_carrying_saves_restores_the_moved_download(tmp_path, env, monkeypatch):
    old, game_id = _old_game(tmp_path)
    folder, game = _download(tmp_path)
    env.load()
    before = _files(game)
    cancel = threading.Event()
    real_copy = ai.copy_infolder_saves

    def copy_then_cancel(*args, **kwargs):
        result = real_copy(*args, **kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(ai, "copy_infolder_saves", copy_then_cancel)
    with pytest.raises(InstallCancelled):
        install_archive(
            str(folder / "data"), library_folder=str(tmp_path / "lib"), pattern="{title}/{version}",
            title="Game", version="2.0", game_id=game_id, source_kind="folder", cancel=cancel,
        )
    # The download is back in its own layout with its own files only.
    assert _files(game) == before
    assert [p.name for p in game.iterdir() if p.name.startswith(".wlib")] == []
    assert (old / "slot1.sav").read_text() == "player save"
    assert not (tmp_path / "lib" / "Game" / "2.0").exists()


def test_cancel_accepted_before_moving_stage_is_published_stops_the_install(tmp_path):
    import core.database as database

    old, game_id = _old_game(tmp_path)
    archive = tmp_path / "Game-2.0.zip"
    import zipfile

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Game.exe", "new exe")
    cancel = threading.Event()

    def on_stage(stage):
        # The bridge accepted a cancel just before this stage went out.
        if stage == "moving into place":
            cancel.set()

    with pytest.raises(InstallCancelled):
        install_archive(
            str(archive), library_folder=str(tmp_path / "lib"), pattern="{title}/{version}",
            title="Game", version="2.0", game_id=game_id, remove_old=True,
            progress_cb=on_stage, cancel=cancel,
        )
    assert not (tmp_path / "lib" / "Game" / "2.0").exists()
    assert (old / "Game.exe").read_text() == "old exe"
    assert database.get_all_games()[0]["version"] == "1.0"


def test_successful_moved_update_carries_saves_and_drops_the_undo_folder(tmp_path, env):
    _old, game_id = _old_game(tmp_path)
    folder, _game = _download(tmp_path)
    env.load()
    result = install_archive(
        str(folder / "data"), library_folder=str(tmp_path / "lib"), pattern="{title}/{version}",
        title="Game", version="2.0", game_id=game_id, source_kind="folder", remove_old=False,
    )
    dest = tmp_path / "lib" / "Game" / "2.0"
    assert result["success"] is True
    assert (dest / "slot1.sav").read_text() == "player save"
    assert (dest / "game" / "saves" / "1-1.save").read_text() == "renpy save"
    assert [p.name for p in dest.iterdir() if p.name.startswith(".wlib")] == []
