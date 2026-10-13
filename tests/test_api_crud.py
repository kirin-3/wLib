# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pytest

from core.api import Api
from core.database import add_game, create_collection, init_db


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_wlib_api_crud.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


def test_add_game_requires_non_empty_title():
    api = Api()
    # None title should return invalid_title error rather than duplicate_url or IntegrityError
    none_result = api.add_game(title=None, exe_path="test.exe")
    assert none_result["success"] is False
    assert none_result.get("error_code") == "invalid_title"

    # Empty string and whitespace titles should also be rejected
    empty_result = api.add_game(title="", exe_path="test.exe")
    assert empty_result["success"] is False
    assert empty_result.get("error_code") == "invalid_title"

    whitespace_result = api.add_game(title="   ", exe_path="test.exe")
    assert whitespace_result["success"] is False
    assert whitespace_result.get("error_code") == "invalid_title"


def test_update_game_validations_and_non_existent_id():
    api = Api()
    game_id = add_game(title="Valid Game", exe_path="/tmp/test.exe")
    assert game_id is not None

    # Updating non-existent game ID should return game_not_found
    missing_result = api.update_game(999999, {"title": "Ghost"})
    assert missing_result["success"] is False
    assert missing_result.get("error_code") == "game_not_found"

    negative_result = api.update_game(-1, {"title": "Ghost"})
    assert negative_result["success"] is False
    assert negative_result.get("error_code") == "game_not_found"

    # Non-scalar game_id should return error without raising sqlite3.ProgrammingError
    invalid_id_result = api.update_game([], {"title": "Ghost"})
    assert invalid_id_result["success"] is False

    # Non-mapping fields should return error without raising TypeError/ValueError
    invalid_fields_result = api.update_game(game_id, 123)
    assert invalid_fields_result["success"] is False
    assert invalid_fields_result.get("error_code") == "invalid_fields"

    str_fields_result = api.update_game(game_id, "invalid")
    assert str_fields_result["success"] is False
    assert str_fields_result.get("error_code") == "invalid_fields"

    # Blank title update should be rejected
    blank_title_result = api.update_game(game_id, {"title": "   "})
    assert blank_title_result["success"] is False
    assert blank_title_result.get("error_code") == "invalid_title"


def test_delete_game_clears_metadata_pending_and_handles_invalid_ids():
    api = Api()
    game_id = add_game(title="To Delete", exe_path="/tmp/del.exe")
    assert game_id is not None

    # Simulate pending metadata fetch for this game
    api._metadata_pending.add(game_id)
    assert game_id in api._metadata_pending

    result = api.delete_game(game_id)
    assert result["success"] is True
    # Deleting the game should clear pending metadata state
    assert game_id not in api._metadata_pending

    # Non-scalar game_id should not raise sqlite3.ProgrammingError
    invalid_del = api.delete_game([])
    assert invalid_del["success"] is False


def test_collection_methods_input_validation():
    api = Api()
    game_id = add_game(title="Collected Game", exe_path="/tmp/game.exe")
    assert game_id is not None
    _ = create_collection("RPG")

    # reorder_collections with None or non-sequence should return error without raising TypeError
    reorder_none = api.reorder_collections(None)
    assert reorder_none["success"] is False

    reorder_str = api.reorder_collections("RPG")
    assert reorder_str["success"] is False

    reorder_int = api.reorder_collections(123)
    assert reorder_int["success"] is False

    # set_game_collections with None, string or non-sequence should return error without raising TypeError
    set_none = api.set_game_collections(game_id, None)
    assert set_none["success"] is False

    set_str = api.set_game_collections(game_id, "RPG")
    assert set_str["success"] is False

    # set_game_collections with non-scalar game_id should return error without raising ProgrammingError
    set_invalid_id = api.set_game_collections([], ["RPG"])
    assert set_invalid_id["success"] is False

    # create_collection with non-scalar game_id should return error without raising ProgrammingError
    create_invalid_id = api.create_collection("New Col", game_id=[])
    assert create_invalid_id["success"] is False


def test_launch_target_methods_input_validation():
    api = Api()
    game_id = add_game(title="Target Game", exe_path="/tmp/game.exe")
    assert game_id is not None

    # get_launch_targets with non-scalar ID should return empty list rather than raising ProgrammingError
    assert api.get_launch_targets([]) == []
    assert api.get_launch_targets(-1) == []

    # create_launch_target with invalid game_id should return game_not_found rather than raising ProgrammingError
    target_create_invalid = api.create_launch_target([], "Target", "/tmp/t.exe")
    assert target_create_invalid["success"] is False
    assert target_create_invalid.get("error_code") == "game_not_found"

    target_create_neg = api.create_launch_target(-1, "Target", "/tmp/t.exe")
    assert target_create_neg["success"] is False
    assert target_create_neg.get("error_code") == "game_not_found"

    # Create a valid target for update/delete tests
    valid_create = api.create_launch_target(game_id, "Target 1", "/tmp/t1.exe")
    assert valid_create["success"] is True
    target_id = valid_create["target"]["id"]

    # update_launch_target with invalid target_id or non-mapping fields
    update_invalid_id = api.update_launch_target([], {"label": "New"})
    assert update_invalid_id["success"] is False
    assert update_invalid_id.get("error_code") == "target_not_found"

    update_invalid_fields = api.update_launch_target(target_id, 123)
    assert update_invalid_fields["success"] is False

    # delete_launch_target with non-scalar target_id
    del_invalid_id = api.delete_launch_target([])
    assert del_invalid_id["success"] is False
    assert del_invalid_id.get("error_code") == "target_not_found"

    # reorder_launch_targets with non-scalar game_id
    reorder_invalid_game = api.reorder_launch_targets([], [target_id])
    assert reorder_invalid_game["success"] is False


def test_path_handling_inspect_scan_open_folder(tmp_path, monkeypatch):
    api = Api()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    game_folder = fake_home / "Games" / "MyGame"
    game_folder.mkdir(parents=True)
    game_exe = game_folder / "game.exe"
    game_exe.write_bytes(b"MZfake_exe")

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    # inspect_game_path should expand ~ and strip whitespace
    inspect_tilde = api.inspect_game_path("~/Games/MyGame/game.exe")
    assert inspect_tilde["success"] is True

    inspect_spaces = api.inspect_game_path(f"  {game_exe}  ")
    assert inspect_spaces["success"] is True

    # scan_games_folder should expand ~ and strip whitespace
    scan_tilde = api.scan_games_folder("~/Games")
    assert scan_tilde["success"] is True
    assert len(scan_tilde["games"]) == 1

    # open_folder should handle None and ~ without raising TypeError
    open_none = api.open_folder(None)
    assert open_none["success"] is False

    open_empty = api.open_folder("   ")
    assert open_empty["success"] is False


def test_check_path_runner_directory_and_parent_trailing_slash(tmp_path):
    api = Api()
    folder = tmp_path / "folder"
    folder.mkdir()

    # Runner path pointing to a directory should report "Expected a file", not "File not found"
    runner_dir_check = api.check_path(str(folder), "runner")
    assert runner_dir_check == {"ok": False, "reason": "Expected a file"}

    # Parent path ending in trailing slash indicates directory, should report "Expected a file"
    parent_slash_check = api.check_path(f"{folder}/missing/", "parent")
    assert parent_slash_check == {"ok": False, "reason": "Expected a file"}


def test_save_settings_validation():
    api = Api()
    # Passing None or non-mapping should return error without raising TypeError
    res_none = api.save_settings(None)
    assert res_none["success"] is False
    assert "Settings must be an object" in str(res_none.get("error"))

    res_int = api.save_settings(123)
    assert res_int["success"] is False
    assert "Settings must be an object" in str(res_int.get("error"))
