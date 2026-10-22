# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.api import Api
from core.database import add_game, init_db


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    init_db()


@pytest.mark.parametrize("title", [None, "", "   ", [], {"name": "x"}])
def test_add_game_rejects_invalid_title(title):
    result = Api().add_game(title=title, exe_path="game.exe")
    assert result["success"] is False
    assert result["error_code"] == "invalid_title"


def test_tag_color_set_change_clear():
    api = Api()
    assert api.set_tag_color(" Sandbox ", "green")["tag_colors"] == {"sandbox": "green"}
    assert api.set_tag_color("SANDBOX", "blue")["tag_colors"] == {"sandbox": "blue"}
    assert api.set_tag_color("other", "pink")["success"]
    assert not api.set_tag_color("Sandbox", "magenta")["success"]
    assert api.get_settings()["tag_colors"] == {"sandbox": "blue", "other": "pink"}
    assert api.set_tag_color("sandbox", None)["tag_colors"] == {"other": "pink"}
    assert api.set_tag_color("OTHER", "")["tag_colors"] == {}
    assert not api.set_tag_color(" ", "blue")["success"]


def test_diagnostics_payloads_and_log_folder_error(tmp_path, monkeypatch):
    import json
    from core import diagnostics

    api = Api()
    monkeypatch.setattr(diagnostics, "host_which", lambda name: None)
    monkeypatch.setattr(api, "get_available_runners", lambda: {"runners": []})
    monkeypatch.setattr(
        api,
        "get_extension_service_status",
        lambda: {"success": True, "reachable": False},
    )
    for method in (
        api.get_diagnostics,
        api.get_diagnostics_tools,
        api.get_diagnostics_report,
    ):
        result = method()
        assert result["success"], result
        json.dumps(result)
    monkeypatch.setattr("core.host_platform.get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        api,
        "_open_path_with_system_handler",
        lambda path: {"success": False, "error": "No file manager"},
    )
    result = api.open_log_folder()
    assert not result["success"]
    assert str(tmp_path / "logs") in result["error"]
    assert (tmp_path / "logs").is_dir()


def test_add_game_strips_title_and_reports_duplicate_url():
    api = Api()
    api._fetch_added_game_metadata = lambda *args: None
    url = "https://f95zone.to/threads/game.1/"
    first = api.add_game(title="  Game  ", exe_path="a.exe", f95_url=url)
    assert first["success"] is True
    assert first["title"] == "Game"
    second = api.add_game(title="Other", exe_path="b.exe", f95_url=url)
    assert second["error_code"] == "duplicate_url"


def test_update_game_validation():
    api = Api()
    game_id = add_game(title="Game", exe_path="game.exe")
    assert api.update_game(999999, {"title": "Ghost"})["error_code"] == "game_not_found"
    assert api.update_game(game_id, 123)["error_code"] == "invalid_fields"
    for title in ("   ", []):
        assert (
            api.update_game(game_id, {"title": title})["error_code"] == "invalid_title"
        )
    assert api.update_game(game_id, {"title": " Renamed "})["success"] is True


def test_delete_game_clears_metadata_pending():
    api = Api()
    game_id = add_game(title="Game", exe_path="game.exe")
    api._metadata_pending.add(game_id)
    assert api.delete_game(game_id)["success"] is True
    assert game_id not in api._metadata_pending


def test_check_path_rejects_folders_for_file_kinds(tmp_path):
    api = Api()
    assert api.check_path(str(tmp_path), "runner")["reason"] == "Expected a file"
    assert (
        api.check_path(str(tmp_path / "missing") + "/", "parent")["reason"]
        == "Expected a file"
    )
    assert api.check_path(str(tmp_path / "save.json"), "parent")["ok"] is True
