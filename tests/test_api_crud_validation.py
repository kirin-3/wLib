# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for game/library/collection/wishlist/filter-preset/settings CRUD hardening."""

import os
import threading

import pytest

from core.api import Api
from core.database import add_game, init_db, update_setting


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "crud-validation.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield


@pytest.fixture
def api():
    inst = Api()
    # Never hit the network in CRUD tests; keep the pending flag only.
    inst._fetch_added_game_metadata = lambda *a, **k: (
        inst._metadata_pending.discard(a[0]) if a else None
    )
    return inst


def test_delete_game_rejects_invalid_ids(api):
    gid = add_game("Real", "real.exe")
    assert gid is not None
    for bad in (
        [1],
        {"a": 1},
        None,
        -5,
        0,
        True,
        False,
        1.5,
        10**30,
        999999,
        "bad",
        "",
    ):
        result = api.delete_game(bad)
        assert result["success"] is False
        assert result["error_code"] == "game_not_found"
    # Valid string id still works (JS numbers may arrive as strings).
    assert api.delete_game(str(gid))["success"] is True
    assert api.delete_game(gid)["success"] is False


def test_delete_game_bool_does_not_delete_row(api):
    _ = add_game("Bool", "bool.exe")
    assert api.delete_game(True)["success"] is False
    # Game 1 must still exist; True == 1 in SQLite so this guards a wrong-row delete.
    titles = [g["title"] for g in api.get_games()]
    assert "Bool" in titles


def test_get_launch_targets_invalid_returns_empty(api):
    assert api.get_launch_targets([1]) == []
    assert api.get_launch_targets({"a": 1}) == []
    assert api.get_launch_targets(10**30) == []
    assert api.get_launch_targets(None) == []
    assert api.get_launch_targets("bad") == []


def test_create_launch_target_rejects_bad_game_id(api):
    for bad in ([1], {"x": 1}, None, -1, 0, True, 10**30, "bad"):
        result = api.create_launch_target(bad, "L", "p.exe")
        assert result["success"] is False
        assert result["error_code"] == "game_not_found"


def test_update_launch_target_rejects_bad_input(api):
    gid = add_game("LT", "lt.exe")
    assert gid is not None
    target = api.create_launch_target(gid, "Main", "main.exe")
    assert target["success"] is True
    tid = target["target"]["id"]
    # Non-mapping fields must not raise.
    for bad_fields in (123, [1, 2], "label", 4.5):
        result = api.update_launch_target(tid, bad_fields)
        assert result["success"] is False
        assert result["error_code"] == "invalid_target"
    for bad_id in ([1], {"a": 1}, None, -1, 0, True, 10**30, "bad"):
        result = api.update_launch_target(bad_id, {"label": "x"})
        assert result["success"] is False
        assert result["error_code"] == "target_not_found"


def test_delete_launch_target_rejects_bad_ids(api):
    for bad in ([1], {"a": 1}, None, -1, 0, True, 10**30, "bad"):
        result = api.delete_launch_target(bad)
        assert result["success"] is False
        assert result["error_code"] == "target_not_found"


def test_reorder_launch_targets_rejects_bad_game_id(api):
    gid = add_game("Reorder", "r.exe")
    t1 = api.create_launch_target(gid, "A", "a.exe")["target"]["id"]
    t2 = api.create_launch_target(gid, "B", "b.exe")["target"]["id"]
    for bad in ([1], {"a": 1}, None, -1, 0, True, 10**30, "bad"):
        result = api.reorder_launch_targets(bad, [t1, t2])
        assert result["success"] is False
    # Valid reorder still works.
    assert api.reorder_launch_targets(gid, [t2, t1])["success"] is True
    assert api.reorder_launch_targets(str(gid), [t1, t2])["success"] is True


def test_reorder_collections_rejects_non_list(api):
    api.create_collection("Backlog")
    for bad in (None, 123, {"a": 1}, True):
        result = api.reorder_collections(bad)
        assert result["success"] is False
    # String is not a valid order list (previously iterated as chars).
    assert api.reorder_collections("Backlog")["success"] is False


def test_set_game_collections_rejects_bad_input(api):
    gid = add_game("Col", "c.exe")
    api.create_collection("Backlog")
    assert api.set_game_collections(gid, None)["success"] is False
    assert api.set_game_collections(gid, "Backlog")["success"] is False
    assert api.set_game_collections([gid], ["Backlog"])["success"] is False
    assert api.set_game_collections(None, ["Backlog"])["success"] is False
    assert api.set_game_collections(999999, ["Backlog"])["success"] is False


def test_create_collection_rejects_bad_game_id(api):
    result = api.create_collection("WithBadGame", game_id=[1])
    assert result["success"] is False
    assert result["error_code"] == "game_not_found"
    assert api.create_collection("WithBadGame2", game_id="bad")["success"] is False


def test_save_settings_rejects_non_mapping(api):
    for bad in (None, "bad", [1], 123):
        result = api.save_settings(bad)
        assert result["success"] is False


def test_save_settings_rejects_non_string_paths(api):
    assert api.save_settings({"proton_path": {"bad": 1}})["success"] is False
    assert api.save_settings({"wine_prefix_path": ["x"]})["success"] is False
    assert api.save_settings({"proton_path": 123})["success"] is False
    # Valid string still saves.
    assert api.save_settings({"proton_path": "/tmp/wine"})["success"] is True


def test_save_backup_keep_huge_numeric_value_does_not_crash_settings(api):
    update_setting("save_backup_keep", "9" * 5000)

    # Loading settings must treat the unparseable stored value as "keep all".
    assert api.get_settings()["save_backup_keep"] == "all"
    # Saving the same oversized value is rejected, not raised into the bridge.
    assert api.save_settings({"save_backup_keep": "9" * 5000})["success"] is False


def test_update_game_rejects_unbindable_values(api):
    gid = add_game("Upd", "u.exe")
    for bad in ({"bad": 1}, [1, 2], (1, 2)):
        result = api.update_game(gid, {"version": bad})
        assert result["success"] is False
    # Huge id must not raise OverflowError.
    assert api.update_game(10**30, {"title": "x"})["success"] is False


def test_check_path_missing_args_returns_error(api):
    assert api.check_path()["success"] is False
    assert api.check_path(None, None)["success"] is False


def test_add_game_rejects_non_string_exe(api):
    for bad in (None, ["x"], {"x": 1}, 123):
        result = api.add_game(title="T", exe_path=bad)
        assert result["success"] is False
        assert result["error_code"] == "invalid_exe_path"
    # Wishlist (empty string) still allowed.
    ok = api.add_game(title="Wish", exe_path="")
    assert ok["success"] is True


def test_scan_in_library_handles_tilde(api, tmp_path, monkeypatch):
    # Simulate $HOME so "~" expands to a real folder we control.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # expanduser caches HOME on some platforms; patch it directly too.
    monkeypatch.setattr(
        os.path,
        "expanduser",
        lambda p: str(home) + p[1:] if str(p).startswith("~") else p,
    )

    library = home / "library" / "MyGame"
    library.mkdir(parents=True)
    exe = library / "game.exe"
    exe.write_bytes(b"MZ")

    added = api.add_game(title="MyGame", exe_path="~/library/MyGame/game.exe")
    assert added["success"] is True

    result = api.scan_games_folder(str(home / "library"))
    assert result["success"] is True
    assert len(result["games"]) == 1
    assert result["games"][0]["in_library"] is True


def test_metadata_pending_uses_lock(api):
    assert hasattr(api, "_metadata_lock")
    # get_games must not raise while a background discard happens.
    gid = add_game("Race", "race.exe")
    api._metadata_pending.add(gid)
    errors = []

    def churn():
        for _ in range(500):
            api._metadata_pending.add(gid)
            with api._metadata_lock:
                api._metadata_pending.discard(gid)

    threads = [threading.Thread(target=churn) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        for _ in range(200):
            api.get_games()
    except Exception as exc:  # pragma: no cover - must not happen after fix
        errors.append(exc)
    for t in threads:
        t.join()
    assert not errors


def test_malformed_f95_url_does_not_raise(api, tmp_path):
    from core.f95zone import extract_thread_id, normalize_thread_url

    assert normalize_thread_url("http://[invalid") == ""
    assert extract_thread_id("http://[invalid") == ""
    exe = tmp_path / "game.exe"
    exe.write_text("x")
    res = api.add_game(title="T", exe_path=str(exe), f95_url="http://[invalid")
    assert res["success"] is True
    assert api.update_game(res["id"], {"f95_url": "http://[invalid"})["success"] is True


def test_save_backup_keep_setting_roundtrip_and_validation(api):
    assert api.get_settings()["save_backup_keep"] == "all"
    assert api.save_settings({"save_backup_keep": 5})["success"] is True
    assert api.get_settings()["save_backup_keep"] == 5
    assert api.save_settings({"save_backup_keep": "12"})["success"] is True
    assert api.get_settings()["save_backup_keep"] == 12
    assert api.save_settings({"save_backup_keep": "all"})["success"] is True
    assert api.get_settings()["save_backup_keep"] == "all"
    api.save_settings({"save_backup_keep": 3})
    for bad in (0, 100, -1, 2.5, "x", "", None, True, [1], {"a": 1}, "\u0663"):
        assert api.save_settings({"save_backup_keep": bad})["success"] is False, bad
    assert api.get_settings()["save_backup_keep"] == 3
