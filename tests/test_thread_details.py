# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811 - pytest fixtures imported from test_api_catalog
"""F95 thread details on games: the merge rules, the catalog fill and backups."""

import json
import sqlite3
from pathlib import Path

import pytest

from core import database
from core.api import Api
from core.library_backup import SECTION_METADATA
from core.thread_details import BASELINE_COLUMN, merge_thread_details
from test_api_catalog import rating_catalog, setup_test_db, started  # noqa: F401 - fixtures

CATALOG = {
    "overview": "A game.",
    "original_title": "原題",
    "aliases": ["Alias"],
    "language": "English (MTL), Japanese",
    "external_links": ["https://www.patreon.com/dev"],
}


def test_merge_fills_empty_fields_and_records_the_baseline():
    updates = merge_thread_details({"overview": "", "aliases": "[]"}, CATALOG)
    assert updates["overview"] == "A game."
    assert updates["aliases"] == '["Alias"]'
    assert json.loads(str(updates[BASELINE_COLUMN]))["language"] == "English (MTL), Japanese"


def test_merge_keeps_user_edits_and_follows_untouched_fields():
    first = merge_thread_details({}, CATALOG)
    row = {**first, "overview": "My own text"}
    newer = {**CATALOG, "overview": "Newer text", "language": "English"}
    updates = merge_thread_details(row, newer)
    assert "overview" not in updates
    assert updates["language"] == "English"
    # Nothing new: nothing to write.
    assert merge_thread_details({**row, **updates}, newer) == {}


def test_a_field_without_a_baseline_keeps_its_value():
    updates = merge_thread_details({"original_title": "Typed by hand"}, CATALOG)
    assert "original_title" not in updates
    assert updates["overview"] == "A game."


def _details_catalog(tmp_path: Path, thread_id: int, **fields: object) -> None:
    values = {**CATALOG, **fields}
    ids = {"patreon": [{"id": "dev", "url": url} for url in values["external_links"]]}  # pyright: ignore[reportGeneralTypeIssues]
    with sqlite3.connect(tmp_path / "catalog.db") as catalog:
        catalog.execute(
            "INSERT OR REPLACE INTO f95_thread_details (thread_id, thread_ts, fetched_at, status, "
            + "overview, original_title, aliases, language, languages, external_ids) "
            + "VALUES (?, 1, 'x', 'ok', ?, ?, ?, ?, '[]', ?)",
            (
                thread_id,
                values["overview"],
                values["original_title"],
                json.dumps(values["aliases"]),
                values["language"],
                json.dumps(ids),
            ),
        )


def _game(thread_id: int) -> int:
    game_id = database.add_game("G", "", f"https://f95zone.to/threads/{thread_id}/")
    assert game_id is not None
    return game_id


def _row(game_id: int) -> dict[str, object]:
    return next(game for game in database.get_all_games() if game["id"] == game_id)


def test_fill_applies_new_snapshots_and_keeps_edits(rating_catalog, tmp_path, monkeypatch):
    _add_thread, sha = rating_catalog
    _details_catalog(tmp_path, 1)
    filled, other = _game(1), _game(2)
    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)

    assert api._fill_thread_details_from_catalog() == 1
    game = _row(filled)
    assert game["overview"] == "A game."
    assert game["aliases"] == ["Alias"]
    assert game["external_links"] == ["https://www.patreon.com/dev"]
    assert _row(other)["overview"] == ""

    _ = database.update_game(filled, {"original_title": "My title"})
    _details_catalog(tmp_path, 1, overview="Changed", original_title="新しい")
    # Same snapshot: nothing is read again.
    assert api._fill_thread_details_from_catalog() == 0
    sha["value"] = "sha-2"
    assert api._fill_thread_details_from_catalog() == 1
    game = _row(filled)
    assert game["overview"] == "Changed"
    assert game["original_title"] == "My title"


def test_a_game_added_later_is_filled_without_a_new_snapshot(rating_catalog, tmp_path, monkeypatch):
    _details_catalog(tmp_path, 5)
    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    assert api._fill_thread_details_from_catalog() == 0
    game_id = _game(5)
    assert api._fill_thread_details_from_catalog([game_id]) == 1
    assert _row(game_id)["language"] == "English (MTL), Japanese"


def test_a_catalog_without_the_details_table_changes_nothing(rating_catalog, tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / "catalog.db") as catalog:
        catalog.execute("DROP TABLE f95_thread_details")
    game_id = _game(1)
    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    assert api._fill_thread_details_from_catalog() == 0
    assert _row(game_id)["overview"] == ""


def test_update_game_cleans_the_fields_and_a_new_thread_clears_them():
    game_id = _game(1)
    applied = database.update_game(
        game_id,
        {
            "aliases": "One, Two ,One",
            "external_links": ["https://itch.io/x", "not a link", "https://itch.io/x"],
            "overview": "  Text  ",
        },
    )
    assert applied["aliases"] == ["One", "Two"]
    assert applied["external_links"] == ["https://itch.io/x"]
    assert _row(game_id)["overview"] == "Text"
    _ = database.update_game(game_id, {"f95_url": "https://f95zone.to/threads/2/"})
    game = _row(game_id)
    assert (game["overview"], game["aliases"], game[BASELINE_COLUMN]) == ("", [], None)


def test_details_and_baseline_round_trip_through_a_backup(tmp_path, rating_catalog, monkeypatch):
    _details_catalog(tmp_path, 1)
    game_id = _game(1)
    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    _ = api._fill_thread_details_from_catalog()
    _ = database.update_game(game_id, {"overview": "Edited"})
    exported = api.export_library_backup(destination_path=str(tmp_path / "b.json"))
    assert exported["success"] is True
    path = Path(str(exported["path"]))
    metadata = json.loads(path.read_text(encoding="utf-8"))["games"][0]["metadata"]
    assert metadata["overview"] == "Edited"
    assert metadata["aliases"] == ["Alias"]
    assert json.loads(metadata[BASELINE_COLUMN])["overview"] == "A game."

    with database.get_connection() as conn:
        conn.execute(
            f"UPDATE games SET overview = '', aliases = '[]', {BASELINE_COLUMN} = NULL WHERE id = ?",
            (game_id,),
        )
    assert api.import_library_backup(str(path), {"sections": [SECTION_METADATA]})["success"]
    game = _row(game_id)
    assert (game["overview"], game["aliases"]) == ("Edited", ["Alias"])
    # The restored baseline still protects the edit from the catalog.
    assert merge_thread_details(game, CATALOG).get("overview") is None


@pytest.mark.parametrize("value", [None, "not json", "[]"])
def test_a_bad_baseline_in_a_backup_means_never_filled(value):
    from core.library_backup import _normalize_game_field  # pyright: ignore[reportPrivateUsage]

    assert _normalize_game_field(BASELINE_COLUMN, value) is None


def test_catalog_fill_reports_a_newly_abandoned_game_once(rating_catalog, tmp_path, monkeypatch):
    _add_thread, sha = rating_catalog
    _details_catalog(tmp_path, 1)
    watched, archived, unknown = _game(1), _game(2), _game(3)
    for game_id in (watched, archived):
        _ = database.update_game(game_id, {"thread_onhold": 0, "thread_abandoned": 0})
    _ = database.update_game(archived, {"play_status": "Archived"})
    entry = {"thread_onhold": False, "thread_abandoned": True, "thread_states_seen_at": "2025-11-19T10:00:00Z"}
    monkeypatch.setattr("core.game_lookup.f95_thread_entries", lambda ids: {thread: dict(entry) for thread in ids})
    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)

    _ = api._fill_thread_details_from_catalog()
    assert [_row(game_id)["thread_abandoned"] for game_id in (watched, archived, unknown)] == [1, 1, 1]
    # Only a known 0 -> 1 change of a game that is not Archived is reported.
    assert api.take_abandoned_notices() == [{"id": watched, "title": "G"}]
    assert api.take_abandoned_notices() == []
    sha["value"] = "sha-2"
    _ = api._fill_thread_details_from_catalog()
    assert api.take_abandoned_notices() == []


def test_a_read_that_changes_no_state_does_not_bump_the_sync_time():
    game_id = _game(7)
    _ = database.update_game(game_id, {"thread_onhold": 0, "thread_abandoned": 0})
    before = database.get_setting("sync_local_modified_at")
    _ = database.update_game(game_id, {"thread_states_at": "2025-11-19T10:00:00Z"})
    assert database.get_setting("sync_local_modified_at") == before
    _ = database.update_game(game_id, {"thread_abandoned": 1})
    assert database.get_setting("sync_local_modified_at") != before
