from unittest.mock import MagicMock

import pytest

from core.database import init_db
from core.game_launch import launch_tracked, request_from_game


@pytest.fixture(autouse=True)
def _library_db():
    # request_from_game reads settings from the (isolated) library database.
    init_db()


def test_request_from_game_maps_launch_settings():
    game = {
        "exe_path": "/game.exe", "command_line_args": "--test",
        "run_japanese_locale": 1, "run_wayland": 1, "auto_inject_ce": 1,
        "custom_prefix": "/prefix", "proton_version": "/proton", "launch_mode": "native",
    }
    assert request_from_game(game) == {
        **game, "require_tracked": True, "run_japanese_locale": True, "run_wayland": True, "auto_inject_ce": True,
    }


@pytest.mark.parametrize("path", ["", "/game.html", "/game.htm"])
def test_request_refuses_wishlist_and_browser_games(path):
    with pytest.raises(ValueError):
        request_from_game({"exe_path": path, "html_player": "browser"})


def test_early_ticks_and_tool_overrides(monkeypatch):
    game = {"id": 7, "exe_path": "/game", "version": "1", "gamemode": "on",
            "mangohud": "off", "sandbox_enabled": 1, "block_network": 1}
    monkeypatch.setattr("core.database.get_all_games", lambda: [game])
    monkeypatch.setattr("core.database.list_game_launch_targets", lambda _: [])
    monkeypatch.setattr("core.database.start_play_session", lambda *_: 42)
    record = MagicMock(return_value="1")
    monkeypatch.setattr("core.database.record_play_tick", record)
    events = []
    launcher = MagicMock()
    def launch(*args, **kwargs):
        kwargs["on_exit_callback"](12, True)
        return {"success": True}
    launcher.launch.side_effect = launch
    assert launch_tracked(launcher, 7, request_from_game(game), events.append)["success"]
    record.assert_called_once_with(7, 42, 12, True)
    assert events[0]["sessionId"] == 42
    assert launcher.launch.call_args.kwargs["gamemode"] is True
    assert launcher.launch.call_args.kwargs["mangohud"] is False
    assert launcher.launch.call_args.kwargs["sandbox_enabled"] is True
    assert launcher.launch.call_args.kwargs["block_network"] is True


def _launch_kwargs(monkeypatch, game):
    monkeypatch.setattr("core.database.get_all_games", lambda: [game])
    monkeypatch.setattr("core.database.list_game_launch_targets", lambda _: [])
    import os

    monkeypatch.setattr("core.game_run_lock.acquire_launch_lock", lambda _game_id: os.open(os.devnull, os.O_RDONLY))
    launcher = MagicMock()
    launcher.launch.return_value = {"success": True, "tracks_playtime": False}
    assert launch_tracked(launcher, game["id"], request_from_game(game))["success"]
    return launcher.launch.call_args.kwargs


def test_gamescope_default_follows_global_on(monkeypatch):
    from core.database import update_setting

    update_setting("gamescope_enabled", "true")
    update_setting("gamescope_filter", "fsr")
    kwargs = _launch_kwargs(monkeypatch, {"id": 8, "exe_path": "/game", "gamescope": "default"})
    assert kwargs["gamescope"]["filter"] == "fsr"
    assert kwargs["gamescope"]["window_mode"] == "fullscreen"


def test_gamescope_off_passes_nothing(monkeypatch):
    from core.database import update_setting

    update_setting("gamescope_enabled", "true")
    kwargs = _launch_kwargs(monkeypatch, {"id": 8, "exe_path": "/game", "gamescope": "off"})
    assert "gamescope" not in kwargs


def test_gamescope_on_with_overrides(monkeypatch):
    from core.database import update_setting

    update_setting("gamescope_enabled", "false")
    update_setting("gamescope_args", "--hdr-enabled")
    game = {
        "id": 8, "exe_path": "/game", "gamescope": "on",
        "gamescope_resolution": "816x624", "gamescope_scaler": "integer",
        "gamescope_filter": "", "gamescope_args": "--force-grab-cursor",
    }
    kwargs = _launch_kwargs(monkeypatch, game)
    assert kwargs["gamescope"] == {
        "window_mode": "fullscreen",
        "output_resolution": "",
        "game_resolution": "816x624",
        "scaler": "integer",
        "filter": "linear",
        "fps_limit": "",
        "args": ["--hdr-enabled", "--force-grab-cursor"],
    }
