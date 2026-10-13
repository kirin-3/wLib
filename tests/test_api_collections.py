# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os

import pytest

from core.api import Api
from core.database import (
    add_game,
    get_library_filter_presets,
    get_setting,
    init_db,
    update_setting,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_wlib_api_collections.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


@pytest.fixture(autouse=True)
def _linux_api_by_default(monkeypatch):
    monkeypatch.setattr("core.api.is_linux", lambda: True)
    monkeypatch.setattr("core.api.is_windows", lambda: False)


def test_collection_api_methods():
    api = Api()
    game_id = add_game("Collected", "/tmp/collected.exe")
    other_id = add_game("Uncollected", "/tmp/other.exe")
    assert game_id is not None
    assert other_id is not None

    result = api.create_collection("Backlog", game_id=game_id)
    assert result["success"] is True
    assert result["collections"] == ["Backlog"]
    assert api.get_collections() == ["Backlog"]

    # Refusals carry {"success": False, "error": ...}.
    duplicate = api.create_collection("backlog")
    assert duplicate["success"] is False
    assert "already exists" in str(duplicate["error"])
    reserved = api.create_collection("All")
    assert reserved["success"] is False
    assert "reserved" in str(reserved["error"])
    missing_game = api.create_collection("With game", game_id=999999)
    assert missing_game["success"] is False

    renamed = api.rename_collection("Backlog", "To play")
    assert renamed["success"] is True
    assert renamed["collections"] == ["To play"]

    result = api.set_game_collections(game_id, ["to play"])
    assert result["success"] is True
    assert result["collections"] == ["To play"]
    unknown = api.set_game_collections(game_id, ["Ghost"])
    assert unknown["success"] is False

    result = api.create_collection("Zed")
    assert result["success"] is True
    reordered = api.reorder_collections(["Zed", "To play"])
    assert reordered["success"] is True
    assert reordered["collections"] == ["Zed", "To play"]
    bad_order = api.reorder_collections(["Zed"])
    assert bad_order["success"] is False

    deleted = api.delete_collection("To play")
    assert deleted["success"] is True
    assert deleted["collections"] == ["Zed"]
    assert api.delete_collection("To play")["success"] is False

    # Games expose collections as a decoded list[str].
    _ = api.set_game_collections(game_id, ["Zed"])
    games = {game["title"]: game for game in api.get_games()}
    assert games["Collected"]["collections"] == ["Zed"]
    assert games["Uncollected"]["collections"] == []


def test_collections_and_presets_in_settings_payload():
    api = Api()
    _ = api.create_collection("Backlog")
    saved = api.save_library_filter_presets(
        [
            {
                "name": "Ren'Py now",
                "filterCollection": "Favorites",
                "filterStatuses": ["Playing"],
                "filterInstallState": "installed",
                "filterEngines": ["Ren'Py"],
                "filterTags": ["dating-sim"],
                "sortBy": "rating",
                "sortDir": "desc",
            }
        ]
    )
    assert saved["success"] is True

    settings = api.get_settings()
    assert settings["collections"] == ["Backlog"]
    assert [preset["name"] for preset in settings["library_filter_presets"]] == [
        "Ren'Py now"
    ]
    assert get_setting("library_filter_presets") is not None


def test_save_library_filter_presets_validates_entries():
    api = Api()
    assert api.save_library_filter_presets("nope")["success"] is False
    assert api.save_library_filter_presets(["corrupt"])["success"] is False
    assert api.save_library_filter_presets([{"name": ""}])["success"] is False
    assert api.save_library_filter_presets([{"name": "x" * 41}])["success"] is False
    assert (
        api.save_library_filter_presets([{"name": "A"}, {"name": "a"}])["success"]
        is False
    )
    assert get_library_filter_presets() == []


def test_check_path_kinds(tmp_path, monkeypatch):
    api = Api()
    game_file = tmp_path / "Game.exe"
    game_file.write_bytes(b"game")
    folder = tmp_path / "folder"
    folder.mkdir()
    missing_file = tmp_path / "missing.exe"
    export_destination = folder / "wlib-export.json"

    assert api.check_path(str(game_file), "file") == {"ok": True, "reason": ""}
    assert api.check_path(str(missing_file), "file") == {
        "ok": False,
        "reason": "File not found",
    }
    # A folder given where a file is expected.
    assert api.check_path(str(folder), "file") == {
        "ok": False,
        "reason": "Expected a file",
    }

    assert api.check_path(str(folder), "dir") == {"ok": True, "reason": ""}
    assert api.check_path(str(missing_file), "dir") == {
        "ok": False,
        "reason": "Folder not found",
    }
    assert api.check_path(str(game_file), "dir") == {
        "ok": False,
        "reason": "Expected a folder",
    }

    # An export file that does not exist yet inside an existing folder is fine.
    assert api.check_path(str(export_destination), "parent") == {
        "ok": True,
        "reason": "",
    }
    assert api.check_path(str(folder / "missing" / "out.json"), "parent") == {
        "ok": False,
        "reason": "Folder not found",
    }
    assert api.check_path(str(folder), "parent") == {
        "ok": False,
        "reason": "Expected a file",
    }

    # Runner: a command name found through PATH.
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wine")
    assert api.check_path("wine", "runner") == {"ok": True, "reason": ""}
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert api.check_path("wine", "runner") == {
        "ok": False,
        "reason": "Command not found on PATH",
    }
    assert api.check_path(str(game_file), "runner") == {"ok": True, "reason": ""}
    assert api.check_path(str(missing_file), "runner") == {
        "ok": False,
        "reason": "File not found",
    }

    # Tilde expansion and empty values.
    assert api.check_path("~/definitely-not-here", "file") == {
        "ok": False,
        "reason": "File not found",
    }
    assert api.check_path("", "file") == {"ok": False, "reason": "Enter a path"}
    assert api.check_path("   ", "dir") == {"ok": False, "reason": "Enter a path"}
    assert api.check_path("/tmp/x", "bogus")["success"] is False


def test_backup_settings_group_round_trips_collection_list():
    from core.library_backup import _get_settings_payload

    update_setting("collections", json.dumps(["Backlog", "Best of"]))
    payload = _get_settings_payload(("collections",))
    assert payload == {"collections": json.dumps(["Backlog", "Best of"])}
