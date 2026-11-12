# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from core.api import Api
from core.catalog import CatalogClient
from core.database import get_setting, init_db, update_setting


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    init_db()


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(
        CatalogClient, "start_background", lambda self, force=False: calls.append(force) or True
    )
    return calls


def test_startup_check_runs_when_enabled(started, tmp_path):
    api = Api()
    assert started == [False]
    assert api.catalog.data_dir == str(tmp_path / "data")


def test_startup_check_skipped_when_disabled(started):
    update_setting("catalog_enabled", "0")
    Api()
    assert started == []


def test_status_and_check_now(started):
    api = Api()
    status = api.get_catalog_status()
    assert status["success"] is True and status["enabled"] is True
    assert status["installed"] is False
    assert set(status) >= {"built_at", "sources", "last_check", "last_error", "checking"}
    result = api.check_catalog_now()
    assert result["success"] is True and result["started"] is True
    assert started[-1] is True


def test_check_now_refused_when_disabled(started):
    update_setting("catalog_enabled", "0")
    result = Api().check_catalog_now()
    assert result == {"success": False, "error": "The game catalog is turned off"}


def test_setting_round_trips_and_turning_off_deletes_files(started, tmp_path):
    api = Api()
    assert api.get_settings()["catalog_enabled"] is True
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "catalog.db").write_bytes(b"x")
    (data / "catalog.json").write_text("{}")
    assert api.save_settings({"catalog_enabled": False})["success"] is True
    assert get_setting("catalog_enabled") == "0"
    assert api.get_settings()["catalog_enabled"] is False
    assert not os.path.exists(data / "catalog.db")
    assert not os.path.exists(data / "catalog.json")
    started.clear()
    assert api.save_settings({"catalog_enabled": True})["success"] is True
    assert get_setting("catalog_enabled") == "1"
    assert started == [True]
    # Saving other settings leaves it alone.
    api.save_settings({"proton_path": ""})
    assert get_setting("catalog_enabled") == "1"


def test_a_locked_catalog_file_returns_an_error_payload(started, tmp_path, monkeypatch):
    import core.catalog as catalog_module

    api = Api()
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "catalog.db").write_bytes(b"x")
    (data / "catalog.json").write_text("{}")

    real_remove = os.remove

    def locked(path):
        if os.path.basename(str(path)) == "catalog.db":
            raise PermissionError(13, "file is locked")
        real_remove(path)

    monkeypatch.setattr(catalog_module.os, "remove", locked)
    result = api.save_settings({"catalog_enabled": False})
    assert result["success"] is False and "locked" in str(result["error"])
    # The setting still turned off, so the leftover file is never read.
    assert get_setting("catalog_enabled") == "0"


def test_startup_with_unreachable_catalog_does_not_block(monkeypatch, tmp_path):
    import time

    monkeypatch.undo()
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WLIB_CATALOG_MANIFEST_URL", "http://127.0.0.1:9/v1/manifest.json")
    began = time.monotonic()
    api = Api()
    assert time.monotonic() - began < 5
    for _ in range(250):
        if not api.get_catalog_status()["checking"]:
            break
        time.sleep(0.02)
    status = api.get_catalog_status()
    assert status["success"] is True and status["last_error"]


def test_game_screenshots_use_catalog_without_changing_library(started, monkeypatch):
    import json
    from core import database, game_lookup

    game_id = database.add_game("A", "", "https://f95zone.to/threads/123/")
    with database.get_connection() as conn:
        conn.execute("UPDATE games SET screenshots = ? WHERE id = ?", (json.dumps(["https://stored/a"]), game_id))
    before = database.get_game(game_id)
    monkeypatch.setattr(game_lookup, "f95_thread_screenshots", lambda _: ["https://preview.f95zone.to/a"])
    api = Api()
    result = api.get_game_screenshots(game_id)
    assert result == {"success": True, "screenshots": [{"thumb": "https://preview.f95zone.to/a", "full": "https://attachments.f95zone.to/a"}], "source": "catalog", "in_catalog": True}
    for value in ([], None):
        monkeypatch.setattr(game_lookup, "f95_thread_screenshots", lambda _, value=value: value)
        result = api.get_game_screenshots(game_id)
        assert result["source"] == "library" and result["in_catalog"] == (value is not None)
        assert result["screenshots"] == [{"thumb": "https://stored/a", "full": "https://stored/a"}]
    assert database.get_game(game_id) == before
    assert api.get_game_screenshots(-1)["success"] is False


BUILT_AT = "2026-11-01T12:00:00Z"


@pytest.fixture
def rating_catalog(started, tmp_path, monkeypatch):
    """A fake installed catalog next to the library database."""
    import sqlite3

    from catalog_server.store import CATALOG_SCHEMA

    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.executescript(CATALOG_SCHEMA)
    conn.execute("INSERT INTO meta (key, value) VALUES ('built_at', ?)", (BUILT_AT,))
    conn.commit()
    conn.close()
    sha = {"value": "sha-1"}
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: sha["value"])

    def add_thread(thread_id: int, rating: float | None) -> None:
        with sqlite3.connect(tmp_path / "catalog.db") as catalog:
            catalog.execute(
                "INSERT OR REPLACE INTO f95_threads (thread_id, title, rating, url, first_seen, last_seen) VALUES (?, 'T', ?, 'u', '', '')",
                (thread_id, rating),
            )

    return add_thread, sha


def _rated_game(thread_id: int, rating: str = "", read_at: str = "") -> int:
    from core import database

    game_id = database.add_game("G", "", f"https://f95zone.to/threads/{thread_id}/")
    assert game_id is not None
    _ = database.update_game(game_id, {"rating": rating, "rating_updated_at": read_at})
    return game_id


def _rating(game_id: int) -> tuple[object, object]:
    from core import database

    game = database.get_game(game_id)
    assert game is not None
    return game["rating"], game["rating_updated_at"]


def test_catalog_fill_follows_the_rating_rules(rating_catalog, monkeypatch):
    add_thread, _sha = rating_catalog
    empty = _rated_game(1)
    newer_page = _rated_game(2, "4.7/5 (130 reviews)", "2026-11-02T08:00:00+00:00")
    older_different = _rated_game(3, "4.4/5 (80 reviews)", "2026-10-01T08:00:00+00:00")
    older_same = _rated_game(4, "4.6/5 (120 reviews)", "2026-10-01T08:00:00+00:00")
    no_time = _rated_game(5, "3.0/5")
    zero = _rated_game(6)
    missing = _rated_game(7, "4.0/5 (5 reviews)")
    for thread_id, rating in ((1, 4.3), (2, 4.6), (3, 4.6), (4, 4.62), (5, 3.5), (6, 0.0), (7, None)):
        add_thread(thread_id, rating)

    api = Api()
    refreshed: list[int] = []
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: refreshed.append(1))

    assert api._fill_ratings_from_catalog() == 3
    assert _rating(empty) == ("4.3/5", BUILT_AT)
    assert _rating(newer_page) == ("4.7/5 (130 reviews)", "2026-11-02T08:00:00+00:00")
    assert _rating(older_different) == ("4.6/5", BUILT_AT)
    assert _rating(older_same) == ("4.6/5 (120 reviews)", "2026-10-01T08:00:00+00:00")
    assert _rating(no_time) == ("3.5/5", BUILT_AT)
    assert _rating(zero) == ("", "")
    assert _rating(missing) == ("4.0/5 (5 reviews)", "")
    assert refreshed == [1]
    assert get_setting("catalog_rating_fill_sha") == "sha-1"


def test_catalog_fill_runs_once_per_snapshot(rating_catalog):
    add_thread, sha = rating_catalog
    game_id = _rated_game(1)
    add_thread(1, 4.3)
    api = Api()
    assert api._fill_ratings_from_catalog() == 1

    _ = Api()  # a restart with the same snapshot
    from core import database

    _ = database.update_game(game_id, {"rating": "", "rating_updated_at": ""})
    assert api._fill_ratings_from_catalog() == 0
    assert _rating(game_id) == ("", "")
    sha["value"] = "sha-2"
    assert api._fill_ratings_from_catalog() == 1
    assert _rating(game_id) == ("4.3/5", BUILT_AT)


def test_catalog_fill_does_nothing_when_the_catalog_is_off(rating_catalog):
    add_thread, _sha = rating_catalog
    game_id = _rated_game(1)
    add_thread(1, 4.3)
    update_setting("catalog_enabled", "0")
    assert Api()._fill_ratings_from_catalog() == 0
    assert _rating(game_id) == ("", "")
    assert get_setting("catalog_rating_fill_sha") in (None, "")


def test_catalog_fill_is_the_install_callback(started):
    api = Api()
    assert api.catalog.on_installed == api._fill_ratings_from_catalog
