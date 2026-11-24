# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest
from discover_catalog import make_catalog

from core import database
from core.api import Api
from core.catalog import CatalogClient
from core.database import get_game, init_db, update_setting
from core.downloads.queue import QueueJob


@pytest.fixture(autouse=True)
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path))
    init_db()
    monkeypatch.setattr(
        CatalogClient, "start_background", lambda self, force=False: True
    )


def _api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, sql: str = "") -> Api:
    catalog = make_catalog(tmp_path)
    if sql:
        with closing(sqlite3.connect(catalog)) as conn, conn:
            _ = conn.executescript(sql)
    api = Api()
    monkeypatch.setattr(api.catalog, "installed_sha256", lambda: "sha")
    assert api.catalog.catalog_path == str(catalog)
    assert api.discover_index.ensure(catalog, "sha") is True

    def no_f95(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Discover sent an F95 request")

    monkeypatch.setattr(api.scraper, "get_thread_metadata", no_f95)
    return api


def _links(game_id: int) -> set[tuple[str, str]]:
    with closing(database.get_connection()) as conn:
        return set(
            cast(
                list[tuple[str, str]],
                conn.execute(
                    "SELECT source, external_id FROM game_links WHERE game_id = ?",
                    (game_id,),
                ).fetchall(),
            )
        )


def _game_count() -> int:
    with closing(database.get_connection()) as conn:
        return cast(tuple[int], conn.execute("SELECT COUNT(*) FROM games").fetchone())[
            0
        ]


def test_status_reports_the_catalog_and_index(tmp_path, monkeypatch) -> None:
    api = _api(tmp_path, monkeypatch)
    status = api.get_discover_status()
    assert status["enabled"] and status["installed"] and status["ready"]
    assert not status["building"]
    update_setting("catalog_enabled", "0")
    off = api.get_discover_status()
    assert off["enabled"] is False and off["ready"] is False
    assert api.search_discover({})["error_code"] == "catalog_off"


def test_index_not_ready_gives_a_loading_error(tmp_path, monkeypatch) -> None:
    api = Api()
    result = api.search_discover({})
    assert result == {
        "success": False,
        "error": "The game catalog is still loading",
        "error_code": "not_ready",
    }


def test_search_filters_and_markers(tmp_path, monkeypatch) -> None:
    api = _api(tmp_path, monkeypatch)
    result = api.search_discover({"search": "echo", "include_sites": ["f95"]})
    assert result["success"] and result["total"] == 1
    game = cast(list[dict[str, object]], result["games"])[0]
    assert game["key"] == "f95:100" and game["library"] is None
    filters = api.get_discover_filters()
    assert filters["success"] and filters["tags"]
    added = api.add_discover_game("f95:100")
    after = api.search_discover({"search": "echo"})
    assert cast(list[dict[str, object]], after["games"])[0]["library"] == {
        "game_id": added["game_id"],
        "state": "wishlisted",
    }
    hidden = api.search_discover({"search": "echo", "hide_library": True})
    assert hidden["total"] == 0


def test_game_details_prefer_the_f95_overview(tmp_path, monkeypatch) -> None:
    api = _api(
        tmp_path,
        monkeypatch,
        sql="""
        UPDATE f95_thread_details SET overview = 'F95 story', languages = '["English"]';
        INSERT INTO lc_thread_details (thread_id, fetched_at, status, overview)
          VALUES (300, '2026-01-01', 'ok', 'LC story');
        UPDATE dlsite_works SET genres = '["巨乳"]', genres_en = '["Big Breasts"]';
        """,
    )
    game = cast(dict[str, object], api.get_discover_game("f95:100")["game"])
    assert game["overview"] == "F95 story"
    assert game["original_title"] == "エコーパーク"
    assert game["languages"] == ["English"]
    assert game["tags"] == ["big tits", "lc only tag", "sandbox"]
    entries = cast(list[dict[str, object]], game["entries"])
    assert [(entry["site"], entry.get("tier")) for entry in entries] == [
        ("f95", None),
        ("lc", "Premium"),
    ]
    assert entries[0]["url"] == "https://f95zone.to/threads/100/"
    assert game["download"] == {"available": True, "reason": ""}
    dlsite = cast(dict[str, object], api.get_discover_game("dlsite:RJ01234567")["game"])
    assert "overview" not in dlsite
    assert dlsite["dlsite_genres"] == ["Big Breasts"]
    assert dlsite["title"] == "English Title"
    assert dlsite["download"] == {"available": False, "reason": ""}
    assert api.get_discover_game("f95:999")["error_code"] == "unknown_game"
    assert api.get_discover_game(5)["error_code"] == "unknown_game"


def test_lc_plus_thread_needs_lc_plus(tmp_path, monkeypatch) -> None:
    api = _api(
        tmp_path,
        monkeypatch,
        sql="UPDATE lc_threads SET tier = 'Premium' WHERE thread_id = 400;",
    )
    game = cast(dict[str, object], api.get_discover_game("lc:400")["game"])
    assert game["download"] == {"available": False, "reason": "Needs LC Plus"}
    update_setting("lc_plus_mode", "yes")
    game = cast(dict[str, object], api.get_discover_game("lc:400")["game"])
    assert game["download"] == {"available": True, "reason": ""}


def test_screenshots_use_the_preview_host_and_local_lc_addresses(
    tmp_path, monkeypatch
) -> None:
    api = _api(
        tmp_path,
        monkeypatch,
        sql="""
        UPDATE f95_threads SET screens = '["https://attachments.f95zone.to/2023/01/s.jpg"]'
          WHERE thread_id = 100;
        UPDATE lc_threads SET screens = '["https://lewdcorner.com/attachments/shot-jpg.123/"]'
          WHERE thread_id = 400;
        """,
    )
    f95 = api.get_discover_screenshots("f95:100")
    assert f95["screenshots"] == [
        {
            "thumb": "https://preview.f95zone.to/2023/01/s.jpg",
            "full": "https://attachments.f95zone.to/2023/01/s.jpg",
        }
    ]
    lc = cast(
        list[dict[str, str]], api.get_discover_screenshots("lc:400")["screenshots"]
    )
    assert len(lc) == 1
    assert lc[0]["thumb"].startswith("http://127.0.0.1:8183/lc-images/")
    key = lc[0]["thumb"].rsplit("/", 1)[1]
    assert (
        api.lc_image_keys.get(key) == "https://lewdcorner.com/attachments/shot-jpg.123/"
    )


def test_wishlist_an_f95_game_from_catalog_data(tmp_path, monkeypatch) -> None:
    api = _api(tmp_path, monkeypatch)
    started: list[object] = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))
    result = api.add_discover_game("f95:100")
    assert result["success"] and result["created"] is True
    game_id = cast(int, result["game_id"])
    game = get_game(game_id)
    assert game is not None
    assert game["exe_path"] == ""
    assert game["f95_url"] == "https://f95zone.to/threads/100/"
    assert game["title"] == "Echo Park"
    assert game["version"] == "v0.5"
    assert game["developer"] == "EchoDev"
    assert game["engine"] == "Ren'Py"
    assert game["rating_count"] == 1200
    assert game["thread_completed"]
    # "Use LewdCorner" is off by default: no LC thread.
    assert not game.get("lc_url")
    assert {("dlsite", "RJ01234567"), ("steam", "943700")} <= _links(game_id)
    # No metadata scrape thread.
    assert started == []
    # A second add returns the same game.
    again = api.add_discover_game("f95:100")
    assert again == {"success": True, "game_id": game_id, "created": False}


def test_lc_partner_follows_the_lc_rules(tmp_path, monkeypatch) -> None:
    api = _api(tmp_path, monkeypatch)
    update_setting("lc_enabled", "1")
    # Premium thread without LC Plus: not attached.
    first = api.add_discover_game("f95:100")
    assert not cast(dict[str, object], get_game(cast(int, first["game_id"])))["lc_url"]
    assert api.delete_game(first["game_id"])["success"]
    update_setting("lc_plus_mode", "yes")
    second = api.add_discover_game("f95:100")
    game = cast(dict[str, object], get_game(cast(int, second["game_id"])))
    assert game["lc_url"] == "https://lewdcorner.com/threads/300/"


def test_lc_only_game_keeps_its_thread(tmp_path, monkeypatch) -> None:
    api = _api(tmp_path, monkeypatch)
    result = api.add_discover_game("lc:400")
    game = cast(dict[str, object], get_game(cast(int, result["game_id"])))
    assert game["lc_url"] == "https://lewdcorner.com/threads/400/"
    assert game["title"] == "Only On LC"
    assert not game["f95_url"]


def test_wishlist_a_dlsite_only_game(tmp_path, monkeypatch) -> None:
    api = _api(tmp_path, monkeypatch)
    result = api.add_discover_game("dlsite:RJ01234567")
    game_id = cast(int, result["game_id"])
    game = cast(dict[str, object], get_game(game_id))
    assert game["title"] == "English Title" and game["exe_path"] == ""
    assert ("dlsite", "RJ01234567") in _links(game_id)
    assert ("vndb", "v77") in _links(game_id)


def test_store_link_match_gets_the_f95_thread_attached(tmp_path, monkeypatch) -> None:
    api = _api(tmp_path, monkeypatch)
    existing = api.add_game(
        "Store game",
        "",
        links=[
            {
                "source": "dlsite",
                "id": "RJ01234567",
                "url": "https://www.dlsite.com/maniax/work/=/product_id/RJ01234567.html",
            }
        ],
    )
    assert existing["success"]
    result = api.add_discover_game("f95:100")
    assert result == {"success": True, "game_id": existing["id"], "created": False}
    game = cast(dict[str, object], get_game(cast(int, existing["id"])))
    assert game["f95_url"] == "https://f95zone.to/threads/100/"
    assert _game_count() == 1


def test_failed_add_leaves_the_library_unchanged(tmp_path, monkeypatch) -> None:
    api = _api(tmp_path, monkeypatch)

    def broken(*_args: object, **_kwargs: object) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("core.database.add_game", broken)
    result = api.add_discover_game("f95:200")
    assert result == {"success": False, "error": "database is locked"}
    assert _game_count() == 0


def test_discard_removes_only_an_unused_game_discover_created(
    tmp_path, monkeypatch
) -> None:
    api = _api(tmp_path, monkeypatch)
    created = cast(int, api.add_discover_game("f95:200")["game_id"])
    assert api.discard_discover_game(created) == {"success": True, "removed": True}
    assert get_game(created) is None
    # A game Discover did not create.
    own = cast(int, api.add_game("Mine", "")["id"])
    assert api.discard_discover_game(own) == {"success": True, "removed": False}
    assert get_game(own) is not None
    # A game that is no longer wishlisted.
    installed = cast(int, api.add_discover_game("f95:200")["game_id"])
    _ = database.update_game(installed, {"exe_path": "/games/solo/game.exe"})
    assert api.discard_discover_game(installed)["removed"] is False
    assert get_game(installed) is not None
    # A game with a download-queue job.
    queued = cast(int, api.add_discover_game("lc:400")["game_id"])

    class Queue:
        def jobs(self) -> list[QueueJob]:
            return [
                cast(QueueJob, cast(object, type("Job", (), {"game_id": queued})()))
            ]

    monkeypatch.setattr(api._update_downloads, "queue", Queue())
    assert api.discard_discover_game(queued)["removed"] is False
    assert get_game(queued) is not None
    assert api.discard_discover_game("junk") == {
        "success": False,
        "error": "Invalid game id",
    }
