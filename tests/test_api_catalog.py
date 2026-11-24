# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import os

import json

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
        CatalogClient,
        "start_background",
        lambda self, force=False: calls.append(force) or True,
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
    assert set(status) >= {
        "built_at",
        "sources",
        "last_check",
        "last_error",
        "checking",
    }
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
    monkeypatch.setenv(
        "WLIB_CATALOG_MANIFEST_URL", "http://127.0.0.1:9/v1/manifest.json"
    )
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
        conn.execute(
            "UPDATE games SET screenshots = ? WHERE id = ?",
            (json.dumps(["https://stored/a"]), game_id),
        )
    before = database.get_game(game_id)
    monkeypatch.setattr(
        game_lookup,
        "f95_thread_screenshots",
        lambda _: ["https://preview.f95zone.to/a"],
    )
    api = Api()
    result = api.get_game_screenshots(game_id)
    assert result == {
        "success": True,
        "screenshots": [
            {
                "thumb": "https://preview.f95zone.to/a",
                "full": "https://attachments.f95zone.to/a",
            }
        ],
        "source": "catalog",
        "in_catalog": True,
    }
    for value in ([], None):
        monkeypatch.setattr(
            game_lookup, "f95_thread_screenshots", lambda _, value=value: value
        )
        result = api.get_game_screenshots(game_id)
        assert result["source"] == "library" and result["in_catalog"] == (
            value is not None
        )
        assert result["screenshots"] == [
            {"thumb": "https://stored/a", "full": "https://stored/a"}
        ]
    assert database.get_game(game_id) == before
    assert api.get_game_screenshots(-1)["success"] is False


def test_lc_only_game_gets_catalog_screenshots_as_local_addresses(started, monkeypatch):
    from core import database, game_covers, game_lookup

    lc_shots = [
        "https://lewdcorner.com/attachments/a-png.1/",
        "https://lewdcorner.com/attachments/b-png.2/",
    ]
    game_id = database.add_game("LC", "", lc_url="https://lewdcorner.com/threads/21/")
    monkeypatch.setattr(
        game_lookup,
        "lc_thread_screenshots",
        lambda thread_id: lc_shots if thread_id == "21" else None,
    )
    api = Api()
    result = api.get_game_screenshots(game_id)
    local = [shot["full"] for shot in result["screenshots"]]
    assert all(u.startswith(game_covers.LC_IMAGE_ADDRESS) for u in local)
    assert result == {
        "success": True,
        "screenshots": [{"thumb": u, "full": u} for u in local],
        "source": "catalog",
        "in_catalog": True,
    }
    assert [api.lc_image_keys.get(u.rsplit("/", 1)[1]) for u in local] == lc_shots
    assert "lewdcorner.com" not in json.dumps(result)
    # An F95 game with an inactive LC thread keeps its own list.
    both = database.add_game(
        "Both",
        "",
        "https://f95zone.to/threads/5/",
        lc_url="https://lewdcorner.com/threads/22/",
        lc_mode="off",
    )
    monkeypatch.setattr(game_lookup, "f95_thread_screenshots", lambda _id: [])
    assert api.get_game_screenshots(both)["screenshots"] == []


def test_lc_screenshots_leave_out_images_the_extension_refuses(started, monkeypatch):
    from core import database, game_covers, game_lookup

    full = "https://lewdcorner.com/attachments/a-png.1/"
    shots = [
        "https://lewdcorner.com/data/attachments/1/1-a.jpg?hash=x",
        "https://lewdcorner.com/proxy.php?image=https%3A%2F%2Fexample.org%2Fa.png",
        full,
    ]
    game_id = database.add_game("LC", "", lc_url="https://lewdcorner.com/threads/21/")
    monkeypatch.setattr(game_lookup, "lc_thread_screenshots", lambda _id: shots)
    api = Api()
    shots_out = api.get_game_screenshots(game_id)["screenshots"]
    assert len(shots_out) == 1 and shots_out[0]["thumb"] == shots_out[0]["full"]
    assert shots_out[0]["full"].startswith(game_covers.LC_IMAGE_ADDRESS)
    assert api.lc_image_keys.get(shots_out[0]["full"].rsplit("/", 1)[1]) == full


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

    def add_thread(
        thread_id: int,
        rating: float | None,
        rating_count: int | None = None,
        detail_status: str = "ok",
    ) -> None:
        with sqlite3.connect(tmp_path / "catalog.db") as catalog:
            catalog.execute(
                "INSERT OR REPLACE INTO f95_threads (thread_id, title, rating, url, first_seen, last_seen) VALUES (?, 'T', ?, 'u', '', '')",
                (thread_id, rating),
            )
            if rating_count is not None:
                catalog.execute(
                    "INSERT OR REPLACE INTO f95_thread_details (thread_id, fetched_at, status, rating_count) VALUES (?, ?, ?, ?)",
                    (thread_id, BUILT_AT, detail_status, rating_count),
                )

    return add_thread, sha


def _rated_game(
    thread_id: int,
    rating: str = "",
    read_at: str = "",
    rating_count: int | None = None,
) -> int:
    from core import database

    game_id = database.add_game("G", "", f"https://f95zone.to/threads/{thread_id}/")
    assert game_id is not None
    _ = database.update_game(
        game_id,
        {
            "rating": rating,
            "rating_count": rating_count,
            "rating_updated_at": read_at,
        },
    )
    return game_id


def _rating(game_id: int) -> tuple[object, object, object]:
    from core import database

    game = database.get_game(game_id)
    assert game is not None
    return game["rating"], game["rating_count"], game["rating_updated_at"]


def test_catalog_fill_follows_the_rating_rules(rating_catalog, monkeypatch):
    add_thread, _sha = rating_catalog
    empty = _rated_game(1)
    empty_with_count = _rated_game(8)
    empty_singular = _rated_game(9)
    empty_thousands = _rated_game(10)
    newer_page = _rated_game(2, "4.7/5 (130 reviews)", "2026-11-02T08:00:00+00:00", 130)
    older_different = _rated_game(
        3, "4.4/5 (80 reviews)", "2026-10-01T08:00:00+00:00", 80
    )
    older_same_no_cat_count = _rated_game(
        4, "4.6/5 (120 reviews)", "2026-10-01T08:00:00+00:00", 120
    )
    missing_count_same_rating = _rated_game(
        11, "4.6/5", "2026-10-01T08:00:00+00:00", None
    )
    newer_count_same_rating = _rated_game(
        12, "4.6/5 (120 reviews)", "2026-10-01T08:00:00+00:00", 120
    )
    same_rating_and_count = _rated_game(
        13, "4.6/5 (120 reviews)", "2026-10-01T08:00:00+00:00", 120
    )
    zero_catalog_count = _rated_game(14)
    no_time = _rated_game(5, "3.0/5")
    zero = _rated_game(6)
    missing = _rated_game(7, "4.0/5 (5 reviews)", "", 5)

    for thread_id, rating, count in (
        (1, 4.3, None),
        (8, 4.3, 85),
        (9, 4.1, 1),
        (10, 4.6, 1234),
        (2, 4.6, 125),
        (3, 4.6, None),
        (4, 4.62, None),
        (11, 4.6, 140),
        (12, 4.6, 150),
        (13, 4.6, 120),
        (14, 4.2, 0),
        (5, 3.5, None),
        (6, 0.0, None),
        (7, None, None),
    ):
        add_thread(thread_id, rating, count)

    api = Api()
    refreshed: list[int] = []
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: refreshed.append(1))

    assert api._fill_ratings_from_catalog() == 9
    assert _rating(empty) == ("4.3/5", None, BUILT_AT)
    assert _rating(empty_with_count) == ("4.3/5 (85 reviews)", 85, BUILT_AT)
    assert _rating(empty_singular) == ("4.1/5 (1 review)", 1, BUILT_AT)
    assert _rating(empty_thousands) == ("4.6/5 (1,234 reviews)", 1234, BUILT_AT)
    assert _rating(newer_page) == (
        "4.7/5 (130 reviews)",
        130,
        "2026-11-02T08:00:00+00:00",
    )
    assert _rating(older_different) == ("4.6/5", None, BUILT_AT)
    assert _rating(older_same_no_cat_count) == (
        "4.6/5 (120 reviews)",
        120,
        "2026-10-01T08:00:00+00:00",
    )
    assert _rating(missing_count_same_rating) == (
        "4.6/5 (140 reviews)",
        140,
        BUILT_AT,
    )
    assert _rating(newer_count_same_rating) == ("4.6/5 (150 reviews)", 150, BUILT_AT)
    assert _rating(same_rating_and_count) == (
        "4.6/5 (120 reviews)",
        120,
        "2026-10-01T08:00:00+00:00",
    )
    assert _rating(zero_catalog_count) == ("4.2/5", None, BUILT_AT)
    assert _rating(no_time) == ("3.5/5", None, BUILT_AT)
    assert _rating(zero) == ("", None, "")
    assert _rating(missing) == ("4.0/5 (5 reviews)", 5, "")
    assert refreshed == [1]
    assert get_setting("catalog_rating_fill_sha") == "sha-1"


def test_catalog_fill_without_rating_count_column(rating_catalog, tmp_path):
    import sqlite3

    add_thread, _sha = rating_catalog
    game_id = _rated_game(1)
    add_thread(1, 4.3)
    with sqlite3.connect(tmp_path / "catalog.db") as catalog:
        catalog.execute("ALTER TABLE f95_thread_details DROP COLUMN rating_count")
    assert Api()._fill_ratings_from_catalog() == 1
    assert _rating(game_id) == ("4.3/5", None, BUILT_AT)


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
    assert _rating(game_id) == ("", None, "")
    sha["value"] = "sha-2"
    assert api._fill_ratings_from_catalog() == 1
    assert _rating(game_id) == ("4.3/5", None, BUILT_AT)


def test_catalog_fill_does_nothing_when_the_catalog_is_off(rating_catalog):
    add_thread, _sha = rating_catalog
    game_id = _rated_game(1)
    add_thread(1, 4.3)
    update_setting("catalog_enabled", "0")
    assert Api()._fill_ratings_from_catalog() == 0
    assert _rating(game_id) == ("", None, "")
    assert get_setting("catalog_rating_fill_sha") in (None, "")


def test_catalog_fill_is_the_install_callback(started):
    api = Api()
    assert api.catalog.on_installed == api._on_catalog_installed


@pytest.fixture
def f95_metadata_catalog(started, tmp_path, monkeypatch):
    """A fake installed catalog with rich F95 rows, prefixes, and tags."""
    import sqlite3

    from catalog_server.store import CATALOG_SCHEMA

    catalog_path = tmp_path / "catalog.db"
    conn = sqlite3.connect(catalog_path)
    conn.executescript(CATALOG_SCHEMA)
    conn.execute("INSERT INTO meta (key, value) VALUES ('built_at', ?)", (BUILT_AT,))
    conn.executemany(
        "INSERT INTO f95_prefix_names (id, name) VALUES (?, ?)",
        [(1, "Ren'Py"), (2, "Completed"), (3, "VN"), (4, "RPGM")],
    )
    conn.executemany(
        "INSERT INTO f95_tag_names (id, name) VALUES (?, ?)",
        [(10, "3dcg"), (11, "rpg"), (12, "sandbox")],
    )
    conn.commit()
    conn.close()
    sha = {"value": "sha-meta-1"}
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: sha["value"])

    def add_f95(
        thread_id: int,
        *,
        title: str = "Catalog Title",
        creator: str = "Catalog Dev",
        version: str = "v1.07",
        cover_url: str = "https://attachments.f95zone.to/cover.jpg",
        rating: float | None = 4.4,
        prefixes: list[int] | None = None,
        tags: list[int] | None = None,
    ) -> None:
        with sqlite3.connect(catalog_path) as catalog:
            catalog.execute(
                "INSERT OR REPLACE INTO f95_threads "
                "(thread_id, title, creator, version, rating, prefixes, tags, cover_url, url, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '')",
                (
                    thread_id,
                    title,
                    creator,
                    version,
                    rating,
                    json.dumps([1, 2] if prefixes is None else prefixes),
                    json.dumps([10, 11] if tags is None else tags),
                    cover_url,
                    f"https://f95zone.to/threads/{thread_id}/",
                ),
            )

    return add_f95, catalog_path, sha


def test_fill_missing_f95_from_catalog_all_fields_and_rules(
    f95_metadata_catalog, monkeypatch
):
    from core import database

    add_f95, _catalog_path, _sha = f95_metadata_catalog
    add_f95(100, creator="Studio A", cover_url="https://f95/a.jpg", rating=4.44)
    add_f95(101, creator="Studio B", cover_url="https://f95/b.jpg", rating=4.8)
    add_f95(102, prefixes=[2], tags=[10, 11], rating=0.0)
    add_f95(103, prefixes=[4], tags=[12], rating=5.5)
    add_f95(104, prefixes=[1], tags=[10, 11], rating=None)
    add_f95(105, prefixes=[1], tags=[10, 11], rating=4.2)
    add_f95(106, prefixes=[1], tags=[10, 11], rating=4.2)
    add_f95(107, prefixes=[1], tags=[10, 11], rating=4.2)

    # Empty game with NULL f95_tags baseline -> fills all 5 fields, never version.
    empty_id = database.add_game(
        "Empty", "", "https://f95zone.to/threads/100/", version="1.05"
    )
    # Already populated game (including non-engine "VN") -> untouched.
    filled_id = database.add_game(
        "Filled",
        "",
        "https://f95zone.to/threads/101/",
        version="2.0",
        cover_image="https://mine/cover.jpg",
        tags="mine",
        rating="3.9/5",
        developer="My Dev",
        engine="VN",
    )
    # Status-prefix-only and rating 0 -> engine and rating stay empty.
    status_zero_id = database.add_game(
        "StatusZero", "", "https://f95zone.to/threads/102/"
    )
    # Rating > 5 -> rating stays empty, engine still fills.
    over_five_id = database.add_game("OverFive", "", "https://f95zone.to/threads/103/")
    # Missing rating -> rating stays empty.
    no_rating_id = database.add_game("NoRating", "", "https://f95zone.to/threads/104/")
    # Empty-string f95_tags baseline -> tags untouched by this helper (left for details fill).
    empty_baseline_id = database.add_game(
        "EmptyBase", "", "https://f95zone.to/threads/105/"
    )
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE games SET f95_tags = '' WHERE id = ?", (empty_baseline_id,)
        )
        conn.commit()
    # NULL f95_tags baseline, empty tags, user-edited -> tags untouched.
    user_edited_id = database.add_game(
        "UserEdited", "", "https://f95zone.to/threads/106/"
    )
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE games SET tags_user_edited = 1 WHERE id = ?", (user_edited_id,)
        )
        conn.commit()
    # NULL f95_tags baseline, existing tags -> tags and baseline untouched.
    has_tags_id = database.add_game(
        "HasTags", "", "https://f95zone.to/threads/107/", tags="rpg"
    )

    api = Api()
    refreshes: list[int] = []
    wakes: list[int] = []
    monkeypatch.setattr(api.cover_cache, "wake", lambda: wakes.append(1))
    real_refresh = api._refresh_synced_library

    def track_refresh():
        refreshes.append(1)
        real_refresh()

    monkeypatch.setattr(api, "_refresh_synced_library", track_refresh)

    changed = api._fill_missing_f95_from_catalog(None)
    assert set(changed) == {
        empty_id,
        status_zero_id,
        over_five_id,
        no_rating_id,
        empty_baseline_id,
        user_edited_id,
        has_tags_id,
    }
    assert filled_id not in changed
    assert refreshes == [1]
    assert wakes == [1]

    empty = database.get_game(empty_id)
    assert empty["developer"] == "Studio A"
    assert empty["cover_image_path"] == "https://f95/a.jpg"
    assert empty["engine"] == "Ren'Py"
    assert empty["rating"] == "4.4/5"
    assert empty["rating_updated_at"] == BUILT_AT
    assert empty["tags"] == "3dcg, rpg"
    assert empty["f95_tags"] == "3dcg, rpg"
    assert empty["version"] == "1.05"

    filled = database.get_game(filled_id)
    assert filled["developer"] == "My Dev"
    assert filled["cover_image_path"] == "https://mine/cover.jpg"
    assert filled["engine"] == "VN"
    assert filled["rating"] == "3.9/5"
    assert filled["tags"] == "mine"
    assert filled["version"] == "2.0"

    status_zero = database.get_game(status_zero_id)
    assert status_zero["engine"] == ""
    assert status_zero["rating"] == ""
    assert status_zero["rating_updated_at"] == ""
    assert status_zero["tags"] == "3dcg, rpg"

    over_five = database.get_game(over_five_id)
    assert over_five["engine"] == "RPGM"
    assert over_five["rating"] == ""
    assert over_five["rating_updated_at"] == ""

    no_rating = database.get_game(no_rating_id)
    assert no_rating["rating"] == ""
    assert no_rating["rating_updated_at"] == ""

    empty_baseline = database.get_game(empty_baseline_id)
    assert empty_baseline["tags"] == ""
    assert empty_baseline["f95_tags"] == ""

    user_edited = database.get_game(user_edited_id)
    assert user_edited["tags"] == ""
    assert user_edited["f95_tags"] is None

    has_tags = database.get_game(has_tags_id)
    assert has_tags["tags"] == "rpg"
    assert has_tags["f95_tags"] is None

    # Second run changes nothing and triggers no refresh.
    assert api._fill_missing_f95_from_catalog([filled_id]) == []
    assert refreshes == [1]


def test_fill_missing_f95_from_catalog_noop_when_off_or_missing(
    f95_metadata_catalog, monkeypatch
):
    from core import database

    add_f95, catalog_path, _sha = f95_metadata_catalog
    add_f95(200)
    game_id = database.add_game("Game", "", "https://f95zone.to/threads/200/")
    api = Api()
    refreshes: list[int] = []
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: refreshes.append(1))

    # Catalog disabled -> no writes.
    update_setting("catalog_enabled", "0")
    assert api._fill_missing_f95_from_catalog([game_id]) == []
    assert database.get_game(game_id)["developer"] == ""
    assert refreshes == []

    # Catalog enabled, file missing -> no writes.
    update_setting("catalog_enabled", "1")
    catalog_path.unlink()
    assert api._fill_missing_f95_from_catalog([game_id]) == []
    assert database.get_game(game_id)["developer"] == ""
    assert refreshes == []


def test_fetch_added_game_metadata_fills_from_catalog(
    f95_metadata_catalog, monkeypatch
):
    from core import database

    add_f95, _catalog_path, _sha = f95_metadata_catalog
    add_f95(
        300,
        creator="Cat Dev",
        version="v9.9",
        cover_url="https://f95/cat300.jpg",
        rating=4.4,
        prefixes=[1],
        tags=[10, 11],
    )
    add_f95(
        301,
        creator="Cat Dev 301",
        cover_url="https://f95/cat301.jpg",
        rating=4.6,
        prefixes=[1],
        tags=[10],
    )

    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    monkeypatch.setattr(api.cover_cache, "wake", lambda: None)

    # 1) Blocked scrape still fills from catalog and keeps saved version.
    blocked_id = database.add_game(
        "Blocked", "", "https://f95zone.to/threads/300/", version="1.05"
    )
    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda _url: {"success": False, "code": "blocked", "error": "Cloudflare"},
    )
    api._fetch_added_game_metadata(blocked_id, "https://f95zone.to/threads/300/", True)
    blocked = database.get_game(blocked_id)
    assert blocked["developer"] == "Cat Dev"
    assert blocked["cover_image_path"] == "https://f95/cat300.jpg"
    assert blocked["engine"] == "Ren'Py"
    assert blocked["tags"] == "3dcg, rpg"
    assert blocked["f95_tags"] == "3dcg, rpg"
    assert blocked["rating"] == "4.4/5"
    assert blocked["version"] == "1.05"

    # 2) Successful scrape keeps page cover and gets only missing developer from catalog.
    scraped_id = database.add_game("Scraped", "", "https://f95zone.to/threads/301/")
    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda _url: {
            "success": True,
            "title": "Scraped [v1.0]",
            "cover_image": "https://page/cover301.jpg",
            "engine": "Ren'Py",
            "tags": ["3dcg"],
        },
    )
    api._fetch_added_game_metadata(scraped_id, "https://f95zone.to/threads/301/", True)
    scraped = database.get_game(scraped_id)
    assert scraped["cover_image_path"] == "https://page/cover301.jpg"
    assert scraped["developer"] == "Cat Dev 301"

    # 3) Thread missing from catalog with blocked scrape leaves saved values unchanged.
    missing_id = database.add_game(
        "Unknown", "", "https://f95zone.to/threads/999999/", version="0.1"
    )
    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda _url: {"success": False, "code": "blocked", "error": "Cloudflare"},
    )
    api._fetch_added_game_metadata(
        missing_id, "https://f95zone.to/threads/999999/", True
    )
    missing = database.get_game(missing_id)
    assert missing["developer"] == ""
    assert missing["cover_image_path"] == ""
    assert missing["engine"] == ""
    assert missing["tags"] == ""
    assert missing["rating"] == ""
    assert missing["version"] == "0.1"


def test_on_catalog_installed_runs_one_time_f95_fill(f95_metadata_catalog, monkeypatch):
    from core import database, lewdcorner_plus
    from core.library_backup import SUPPORTED_SECTIONS, build_library_backup

    add_f95, catalog_path, _sha = f95_metadata_catalog
    add_f95(
        400,
        creator="OneTime Dev",
        cover_url="https://f95/onetime.jpg",
        rating=4.5,
        prefixes=[1],
        tags=[10],
    )
    game_id = database.add_game("Existing", "", "https://f95zone.to/threads/400/")
    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    monkeypatch.setattr(lewdcorner_plus, "start", lambda _trigger: None)

    # First callback fills existing game and records the device-local setting.
    api._on_catalog_installed()
    game = database.get_game(game_id)
    assert game["developer"] == "OneTime Dev"
    assert game["cover_image_path"] == "https://f95/onetime.jpg"
    assert game["engine"] == "Ren'Py"
    assert game["tags"] == "3dcg"
    assert game["rating"] == "4.5/5"
    assert get_setting("catalog_f95_fill_done") == "1"

    # Second callback after user clears cover leaves it empty.
    database.update_game(game_id, {"cover_image_path": ""})
    api._on_catalog_installed()
    assert database.get_game(game_id)["cover_image_path"] == ""

    # Device-local setting is absent from a JSON library export.
    backup = build_library_backup({"sections": list(SUPPORTED_SECTIONS)})
    assert "catalog_f95_fill_done" not in json.dumps(backup)

    # No catalog on first callback leaves setting unset; runs on next install.
    update_setting("catalog_f95_fill_done", "")
    database.update_game(game_id, {"cover_image_path": "", "developer": ""})
    backup_bytes = catalog_path.read_bytes()
    catalog_path.unlink()
    api._on_catalog_installed()
    assert get_setting("catalog_f95_fill_done") in (None, "")
    assert database.get_game(game_id)["cover_image_path"] == ""

    catalog_path.write_bytes(backup_bytes)
    api._on_catalog_installed()
    assert get_setting("catalog_f95_fill_done") == "1"
    assert database.get_game(game_id)["cover_image_path"] == "https://f95/onetime.jpg"
    assert database.get_game(game_id)["developer"] == "OneTime Dev"


def test_api_lookup_lc_partner_returns_f95_catalog_entry(
    f95_metadata_catalog,
):
    import sqlite3

    add_f95, catalog_path, _sha = f95_metadata_catalog
    add_f95(
        500,
        title="Ambrosia",
        creator="Shimobashira Workshop / Kagura Games",
        version="v1.07",
        cover_url="https://f95/ambrosia.jpg",
        rating=4.5,
        prefixes=[1, 2],
        tags=[10, 11],
    )
    with sqlite3.connect(catalog_path) as catalog:
        catalog.execute(
            "INSERT INTO lc_prefix_names (id, name) VALUES (1, 'Completed'), (2, 'Ren''Py')"
        )
        catalog.execute(
            "INSERT INTO lc_threads "
            "(thread_id, title, creator, version, tier, prefixes, tags, cover_url, url, first_seen, last_seen) "
            "VALUES (77, 'Ambrosia LC', 'LC Dev', 'v1.08', 'Free', '[1, 2]', '[\"lc-tag\"]', 'https://lc/cover.jpg', 'https://lewdcorner.com/threads/77/', '', '')"
        )
        catalog.execute(
            "INSERT INTO lc_thread_details "
            "(thread_id, fetched_at, status, overview, language) "
            "VALUES (77, ?, 'ok', 'LC overview fallback', 'English')",
            (BUILT_AT,),
        )
        catalog.execute(
            "INSERT INTO links (src_a, id_a, src_b, id_b, origin) "
            "VALUES ('f95', '500', 'lc', '77', 'title')"
        )
        catalog.commit()

    api = Api()
    f95_url = "https://f95zone.to/threads/500/"
    lc_url = "https://lewdcorner.com/threads/77/"

    # 1) F95 URL with "Use LewdCorner" off -> F95 entry, no lc_url.
    update_setting("lc_enabled", "0")
    off_result = api.lookup_lc_partner(f95_url)
    assert "lc_url" not in off_result
    entry = off_result["catalog_entry"]
    assert isinstance(entry, dict)
    assert entry["title"] == "Ambrosia"
    assert entry["developer"] == "Shimobashira Workshop / Kagura Games"
    assert entry["version"] == "v1.07"
    assert entry["coverImage"] == "https://f95/ambrosia.jpg"
    assert entry["tags"] == "3dcg, rpg"
    assert entry["rating"] == 4.5
    assert entry["engine"] == "Ren'Py"
    assert entry["overview"] == ""

    # 2) F95 URL with "Use LewdCorner" on and a partner -> combined entry + lc_url.
    update_setting("lc_enabled", "1")
    on_result = api.lookup_lc_partner(f95_url)
    assert on_result["lc_url"] == lc_url
    combined = on_result["catalog_entry"]
    assert isinstance(combined, dict)
    assert combined["title"] == "Ambrosia"
    assert combined["developer"] == "Shimobashira Workshop / Kagura Games"
    assert combined["tags"] == "3dcg, rpg"
    assert combined["overview"] == "LC overview fallback"
    assert combined["language"] == "English"

    # 3) Unknown F95 thread -> no catalog_entry.
    assert api.lookup_lc_partner("https://f95zone.to/threads/999999/") == {}

    # 4) LC URL result stays unchanged (returns f95_url and combined entry).
    lc_result = api.lookup_lc_partner(lc_url)
    assert lc_result["f95_url"] == f95_url
    lc_combined = lc_result["catalog_entry"]
    assert isinstance(lc_combined, dict)
    assert lc_combined["title"] == "Ambrosia"
    assert lc_combined["overview"] == "LC overview fallback"
