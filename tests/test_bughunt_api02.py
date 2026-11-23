# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for the API contract bug hunt (settings, LC attach, removal)."""

import pytest

from core import database
from core.api import Api


# ── Bug 1: a partial settings save must not write back stale omitted fields ──

_PATH_KEYS = (
    "proton_path",
    "wine_prefix_path",
    "urm_rpa_path",
    database.RPGMAKER_LINUX_RUNNER_SETTING,
)


def test_overlapping_settings_saves_keep_both_edits(monkeypatch):
    api = Api()
    for key in _PATH_KEYS:
        database.update_setting(key, "/old")
    database.update_setting("enable_logging", "false")
    real_update = database.update_settings
    calls: list[int] = []

    def interleaved(values, **kwargs):
        # A second field's save commits after the first request read its
        # values and before the first request commits.
        if not calls:
            calls.append(1)
            edit: dict[str, object] = {key: "/new" for key in _PATH_KEYS}
            edit["enable_logging"] = True
            assert api.save_settings(edit) == {"success": True}
        real_update(values, **kwargs)

    monkeypatch.setattr(database, "update_settings", interleaved)
    assert api.save_settings({"update_notifications": False}) == {"success": True}
    monkeypatch.setattr(database, "update_settings", real_update)

    for key in _PATH_KEYS:
        assert database.get_setting(key) == "/new", key
    assert database.get_setting("enable_logging") == "true"
    assert database.get_setting("update_notifications") == "off"


def test_partial_settings_save_still_clears_and_normalizes():
    api = Api()
    for key in _PATH_KEYS:
        database.update_setting(key, "/old")
    database.update_setting("enable_logging", "true")
    clear: dict[str, object] = {key: None for key in _PATH_KEYS}
    clear["enable_logging"] = "False"
    assert api.save_settings(clear) == {"success": True}
    for key in _PATH_KEYS:
        assert database.get_setting(key) == "", key
    assert database.get_setting("enable_logging") == "false"
    assert api.save_settings(
        {"urm_rpa_path": "  /urm  ", database.RPGMAKER_LINUX_RUNNER_SETTING: " /r "}
    ) == {"success": True}
    assert database.get_setting("urm_rpa_path") == "/urm"
    assert database.get_setting(database.RPGMAKER_LINUX_RUNNER_SETTING) == "/r"


# ── Bug 2: attaching an LC thread to a wishlisted partner keeps the chosen exe ──


@pytest.fixture
def lc_partner(monkeypatch):
    import core.game_lookup

    monkeypatch.setattr(core.game_lookup, "f95_for_lc", lambda _lc_id: "101")
    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    return api


def test_lc_attach_installs_a_wishlisted_partner(lc_partner):
    api = lc_partner
    partner = database.add_game("Partner", "", "https://f95zone.to/threads/101/")
    result = api.add_game(
        "Page name",
        "/games/partner/game.sh",
        version="0.5",
        lc_url="https://lewdcorner.com/threads/21/",
    )
    assert result["success"] and result["attached_lc"]
    assert result["id"] == partner
    game = database.get_game(partner)
    assert game["lc_url"].endswith("/21/")
    assert game["exe_path"] == "/games/partner/game.sh"
    assert game["version"] == "0.5"
    assert game["title"] == "Partner"


def test_lc_attach_keeps_existing_exe_and_version(lc_partner):
    api = lc_partner
    partner = database.add_game(
        "Partner", "/old/game.sh", "https://f95zone.to/threads/101/", version="0.1"
    )
    result = api.add_game(
        "Page name",
        "/games/new/game.sh",
        version="0.5",
        lc_url="https://lewdcorner.com/threads/21/",
    )
    assert result["success"] and result["attached_lc"]
    game = database.get_game(partner)
    assert game["exe_path"] == "/old/game.sh"
    assert game["version"] == "0.1"


def test_lc_attach_without_version_keeps_it_and_url_only_attach_works(lc_partner):
    api = lc_partner
    partner = database.add_game(
        "Partner", "", "https://f95zone.to/threads/101/", version="0.1"
    )
    result = api.add_game(
        "Page name", "/games/new/game.sh", lc_url="https://lewdcorner.com/threads/21/"
    )
    assert result["success"] and result["attached_lc"]
    game = database.get_game(partner)
    assert game["exe_path"] == "/games/new/game.sh"
    assert game["version"] == "0.1"
    other = database.add_game("Other", "", "https://f95zone.to/threads/102/")
    assert database.attach_lc_thread(
        other, "https://lewdcorner.com/threads/22/", "https://f95zone.to/threads/102/"
    )
    assert database.get_game(other)["exe_path"] == ""


# ── Bug 3: a mod tool cannot start while the game is being removed ──


def test_mod_tool_refused_while_removal_runs(monkeypatch):
    from core.game_tools import GameToolError

    api = Api()
    game_id = database.add_game(title="Game", exe_path="game.exe")
    real_delete = api.cover_cache.delete_game
    seen: list[str] = []

    def delete_with_tool(gid: int) -> None:
        try:
            with api._tool_run(gid, "extract"):
                seen.append("tool ran")
        except GameToolError as exc:
            seen.append(str(exc))
        with api._status_lock:
            seen.append(str(api._game_busy_reason(gid)))
        real_delete(gid)

    monkeypatch.setattr(api.cover_cache, "delete_game", delete_with_tool)
    assert api.delete_game(game_id) == {"success": True}
    assert "tool ran" not in seen
    assert "removed" in seen[0]
    assert database.get_game(game_id) is None
    with api._status_lock:
        assert api._game_busy_reason(game_id) is None


def test_failed_removal_releases_the_game(monkeypatch):
    import sqlite3

    api = Api()
    game_id = database.add_game(title="Game", exe_path="game.exe")

    def locked(_gid: int) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(api.cover_cache, "delete_game", locked)
    assert api.delete_game(game_id) == {
        "success": False,
        "error": "database is locked",
    }
    with api._status_lock:
        assert api._game_busy_reason(game_id) is None
    with api._tool_run(game_id, "extract"):
        pass
