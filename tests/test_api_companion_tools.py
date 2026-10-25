# pyright: reportMissingImports=false
from unittest.mock import MagicMock

import pytest

from core import database as db
from core.api import Api


@pytest.fixture
def tools_api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "tools.db"))
    db.init_db()
    api = Api.__new__(Api)
    api.launcher = MagicMock()
    return api


def test_companion_api_roundtrip(tools_api, monkeypatch):
    api = tools_api
    game = db.add_game("Game", "/game.exe")
    assert api.save_companion_tool({"name": "Helper", "kind": "command", "arguments": "helper"})["success"]
    tool = api.list_companion_tools()["tools"][0]
    tool_id = tool["id"]
    assert api.save_companion_tool({**tool, "name": "Renamed"})["success"]
    assert api.set_game_companion_tool_override(game, tool_id, False)["success"]
    assert api.get_game_companion_tools(game)["tools"][0]["overridden"]
    assert "not shown" in api.launch_companion_tool(game, tool_id)["error"]
    assert api.set_game_companion_tool_override(game, tool_id, None)["success"]
    assert not api.get_game_companion_tools(game)["tools"][0]["overridden"]
    api.launcher.launch_companion_tool.return_value = {"success": False, "error": "Renamed: game is not running"}
    assert "not running" in api.launch_companion_tool(game, tool_id)["error"]
    api.launcher.launch_companion_tool.return_value = {"success": True}
    assert api.launch_companion_tool(game, tool_id)["success"]
    api.launcher.launch_companion_tool.assert_called_with(game, db.get_companion_tool(tool_id))
    api.launcher.list_launchable_tools.return_value = [tool]
    assert api.list_launchable_companion_tools(game)["tools"] == [tool]
    assert not api.launch_companion_tool(game, 9999)["success"]
    assert not api.launch_companion_tool(9999, tool_id)["success"]
    assert not api.list_launchable_companion_tools(9999)["success"]
    monkeypatch.setattr("core.api.is_linux", lambda: False)
    assert api.launch_companion_tool(game, tool_id)["code"] == "unsupported_platform"
    assert api.list_launchable_companion_tools(game)["code"] == "unsupported_platform"
    assert api.list_companion_tools()["tools"]
    assert api.delete_companion_tool(tool_id)["success"]
    assert api.list_companion_tools()["tools"] == []
