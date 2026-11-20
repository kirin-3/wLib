# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from core import database, steam_env
from core.api import Api
from core.steam_shortcuts import read_vdf, sync_games


@pytest.mark.parametrize("enabled", [False, True])
def test_saving_html_player_default_reconciles_steam_shortcut(
    enabled: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database.init_db()
    database.update_setting("steam_games_mode", "all-installed")
    if enabled:
        database.update_setting("html_builtin_player", "false")
    page = tmp_path / "index.html"
    page.touch()
    game_id = database.add_game("HTML game", str(page))

    root = tmp_path / "Steam"
    config = root / "userdata/123/config"
    config.mkdir(parents=True)
    shortcuts = config / "shortcuts.vdf"
    script = tmp_path / "wlib-steam"
    monkeypatch.setattr(steam_env, "steam_running", lambda _: False)
    initial = sync_games(root, "123", script)
    assert initial["success"] and initial["added"] == (0 if enabled else 1)
    if shortcuts.exists():
        os.utime(shortcuts, (1, 1))

    api = Api()
    steam_sync = Mock()
    steam_sync.schedule.side_effect = lambda: sync_games(root, "123", script)
    monkeypatch.setattr(api, "steam_sync", steam_sync)

    assert api.save_settings({"html_builtin_player": enabled})["success"]
    steam_sync.schedule.assert_called_once_with()
    entries = read_vdf(shortcuts.read_bytes())["shortcuts"]
    assert isinstance(entries, dict)
    if enabled:
        entry = entries["0"]
        assert isinstance(entry, dict) and entry["LaunchOptions"] == str(game_id)
    else:
        assert entries == {}
