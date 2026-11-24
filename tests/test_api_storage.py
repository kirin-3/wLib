# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge tests for core.api_mixins.storage."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from core import database
from core.api import Api


@pytest.fixture
def isolated_api(tmp_path: Path, monkeypatch) -> tuple[Api, Path]:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(database, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()
    api = Api()
    return api, data_dir


def test_storage_bridge_scan_cleanup_pick_clear_and_open(
    isolated_api: tuple[Api, Path], tmp_path: Path, monkeypatch
):
    api, data_dir = isolated_api

    lib = tmp_path / "Library"
    game_dir = lib / "Dev" / "Quest"
    exe = game_dir / "bin" / "quest.exe"
    exe.parent.mkdir(parents=True)
    _ = exe.write_bytes(b"q" * 8192)
    database.update_setting("library_folders", f'["{lib}"]')
    database.update_setting("library_folder", str(lib))
    gid = database.add_game("Quest", str(exe))

    # Add a finished download archive to clean up.
    dl_dir = data_dir / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    archive = dl_dir / "quest.zip"
    _ = archive.write_bytes(b"z" * 16384)

    # Initial empty state.
    state0 = api.get_storage_state()
    assert state0["success"] is True
    assert state0["report"] is None
    assert isinstance(state0["actions"], list)

    # Start scan & wait.
    start_res = api.start_storage_scan()
    assert start_res["success"] is True
    assert api.storage.wait_idle(timeout=5.0)

    state1 = api.get_storage_state()
    assert state1["success"] is True
    report = cast(dict[str, object], state1["report"])
    assert report is not None
    games_map = cast(dict[str, dict[str, object]], report["games"])
    assert str(gid) in games_map

    # Stop when idle returns success cleanly.
    stop_res = api.stop_storage_scan()
    assert stop_res["success"] is True

    # Preview & run cleanup (and error shapes).
    bad_prev = api.preview_storage_cleanup("")
    assert bad_prev["success"] is False
    bad_prev2 = api.preview_storage_cleanup("unknown_action")
    assert bad_prev2["success"] is False

    prev = api.preview_storage_cleanup("download_archives")
    assert prev["success"] is True
    assert prev["count"] == 1

    bad_run = api.run_storage_cleanup("")
    assert bad_run["success"] is False
    bad_run2 = api.run_storage_cleanup("nonexistent-token")
    assert bad_run2["success"] is False
    assert bad_run2["code"] == "preview_expired"

    run_res = api.run_storage_cleanup(str(prev["preview_id"]))
    assert run_res["success"] is True
    assert run_res["removed"] == 1
    assert not archive.exists()

    # Set & clear picked game folder (success and error shapes).
    assert api.set_storage_game_folder(99999, str(game_dir))["success"] is False
    assert api.set_storage_game_folder(gid, "")["success"] is False
    elsewhere = tmp_path / "Elsewhere"
    elsewhere.mkdir()
    assert api.set_storage_game_folder(gid, str(elsewhere))["success"] is False

    set_res = api.set_storage_game_folder(gid, str(game_dir))
    assert set_res["success"] is True
    assert Path(str(set_res["root"])).resolve() == game_dir.resolve()

    assert api.clear_storage_game_folder(99999)["success"] is False
    clear_res = api.clear_storage_game_folder(gid)
    assert clear_res["success"] is True
    assert clear_res["cleared"] is True

    # open_storage_location success and error shapes.
    opened: list[str] = []
    monkeypatch.setattr(
        api,
        "_open_path_with_system_handler",
        lambda path: (opened.append(path), {"success": True})[1],
    )
    assert api.open_storage_location("")["success"] is False
    assert api.open_storage_location("game:99999")["success"] is False
    assert api.open_storage_location(f"game:{gid}")["success"] is True
    assert opened and Path(opened[-1]).is_dir()
    assert api.open_storage_location("download_archives")["success"] is True
