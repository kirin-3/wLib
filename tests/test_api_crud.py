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
