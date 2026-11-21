# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811 - pytest fixtures imported from test_game_lookup
"""The catalog fills an empty installed version but never replaces it."""

from core import database
from core.thread_details import merge_thread_details
from test_game_lookup import catalog, lc_catalog  # noqa: F401 - fixtures


def test_merge_fills_an_empty_version_once():
    row: dict[str, object] = {"version": ""}
    first = merge_thread_details(row, {"version": "1.0"})
    assert first["version"] == "1.0"
    row.update(first)

    second = merge_thread_details(row, {"version": "2.0"})
    assert "version" not in second
    row.update(second)
    assert row["version"] == "1.0"
    # A user who clears the version gets the catalog one again.
    row["version"] = ""
    assert merge_thread_details(row, {"version": "2.0"})["version"] == "2.0"


def test_merge_still_follows_other_untouched_lc_fields():
    row: dict[str, object] = {"developer": "", "version": ""}
    row.update(merge_thread_details(row, {"developer": "Old", "version": "1.0"}))
    updates = merge_thread_details(row, {"developer": "New", "version": "2.0"})
    assert updates["developer"] == "New"
    assert "version" not in updates


def test_catalog_fill_keeps_the_installed_version(lc_catalog, monkeypatch):
    from core.api import Api

    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    sha = ["first"]
    monkeypatch.setattr(api.catalog, "installed_sha256", lambda: sha[0])
    database.update_setting("lc_enabled", "1")
    game = database.add_game("LC only", "", lc_url="https://lewdcorner.com/threads/23/")
    database.update_game(game, {"version": ""})

    _ = api._fill_thread_details_from_catalog()
    assert database.get_game(game)["version"] == "1.1"

    lc_catalog.execute(
        "UPDATE lc_threads SET version = '2.0', creator = 'New creator' WHERE thread_id = 23"
    )
    lc_catalog.commit()
    sha[0] = "second"
    _ = api._fill_thread_details_from_catalog()
    stored = database.get_game(game)
    assert stored["version"] == "1.1"
    assert stored["developer"] == "New creator"
