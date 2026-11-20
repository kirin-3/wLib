# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core import database, game_run_lock
from core.game_launch import launch_tracked


@pytest.mark.skipif(sys.platform != "linux", reason="Linux launch lock")
def test_html_settings_error_releases_launch_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database.init_db()
    target = tmp_path / "index.html"
    _ = target.write_text("<html></html>")
    game_id = database.add_game(title="Game", exe_path=str(target))
    assert game_id is not None

    request = {"exe_path": str(target), "check_web_media": True}
    launcher = MagicMock()
    launcher.launch.return_value = {"success": True, "tracks_playtime": False}
    get_setting = database.get_setting
    acquire_lock = game_run_lock.acquire_launch_lock
    first_fd: int | None = None

    def capture_lock(locked_game_id: int) -> int | None:
        nonlocal first_fd
        first_fd = acquire_lock(locked_game_id)
        return first_fd

    def fail_html_setting(key: str) -> str | None:
        if key == "html_builtin_player":
            raise sqlite3.OperationalError("settings unavailable")
        return get_setting(key)

    with monkeypatch.context() as failure:
        failure.setattr(game_run_lock, "acquire_launch_lock", capture_lock)
        failure.setattr(database, "get_setting", fail_html_setting)
        try:
            result = launch_tracked(launcher, game_id, request)
        except sqlite3.OperationalError:
            result = None

    launcher.launch.assert_not_called()
    retry = launch_tracked(launcher, game_id, request)
    if retry.get("code") == "already_running" and first_fd is not None:
        os.close(first_fd)  # Clean up the leaked lock when testing the old code.

    assert retry["success"], retry
    assert result == {"success": False, "error": "settings unavailable"}
