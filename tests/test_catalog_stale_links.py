# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
"""Removed catalog rows and reused Ryuugames permalinks must not resolve games."""

import sqlite3

import pytest

from catalog_server.links import rebuild_links
from catalog_server.store import CATALOG_SCHEMA, CatalogStore
from core import database
from core.game_lookup import resolve_page

NOW = "2026-10-26T00:00:00Z"
GONE = "2026-10-20T00:00:00Z"


def test_builder_skips_links_of_removed_rows(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert(
        "dlsite_works",
        "product_id",
        [
            {"product_id": "RJ1", "title": "a", "url": "d"},
            {"product_id": "RJ2", "title": "b", "url": "d"},
        ],
        NOW,
    )
    catalog.upsert(
        "vndb_vns",
        "vn_id",
        [
            {"vn_id": "v1", "title": "a", "url": "v"},
            {"vn_id": "v2", "title": "b", "url": "v"},
        ],
        NOW,
    )
    catalog.upsert(
        "vndb_releases",
        "release_id",
        [
            {
                "release_id": "r1",
                "title": "r",
                "vn_ids": ["v1"],
                "dlsite_ids": ["RJ1"],
                "steam_ids": ["10"],
                "url": "r",
            },
            {
                "release_id": "r2",
                "title": "r",
                "vn_ids": ["v2"],
                "dlsite_ids": ["RJ2"],
                "steam_ids": ["20"],
                "url": "r",
            },
        ],
        NOW,
    )
    catalog.upsert(
        "ryuu_posts",
        "post_id",
        [
            {
                "post_id": 1,
                "title": "x",
                "raw_title": "x",
                "rj_codes": ["RJ1"],
                "steam_ids": ["30"],
                "vndb_ids": ["v1"],
                "url": "p1",
            },
            {
                "post_id": 2,
                "title": "y",
                "raw_title": "y",
                "rj_codes": ["RJ2"],
                "steam_ids": [],
                "vndb_ids": ["v2"],
                "url": "p2",
            },
        ],
        NOW,
    )
    with catalog.conn:
        catalog.conn.execute(
            "UPDATE ryuu_posts SET removed_at = ? WHERE post_id = 1", (GONE,)
        )
        catalog.conn.execute(
            "UPDATE vndb_releases SET removed_at = ? WHERE release_id = 'r1'", (GONE,)
        )
        catalog.conn.execute(
            "UPDATE dlsite_works SET removed_at = ? WHERE product_id = 'RJ2'", (GONE,)
        )
    rebuild_links(catalog)
    links = set(catalog.conn.execute("SELECT src_a, id_a, src_b, id_b FROM links"))
    assert links == {
        ("ryuugames", "2", "vndb", "v2"),
        ("vndb", "v2", "steam", "20"),
    }


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()
    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.executescript(CATALOG_SCHEMA)
    conn.execute(
        "INSERT INTO vndb_vns (vn_id,title,url,first_seen,last_seen) VALUES ('v1','Novel','https://vndb.org/v1','','')"
    )
    conn.executemany(
        "INSERT INTO ryuu_posts (post_id,title,raw_title,version,rj_codes,url,first_seen,last_seen,removed_at) VALUES (?,?,?,?,?,?,'','',?)",
        [
            (
                43,
                "Old",
                "Old",
                "0.1",
                '["RJ43"]',
                "https://www.ryuugames.com/reused/",
                None,
            ),
            (
                44,
                "New",
                "New",
                "2.0",
                '["RJ44"]',
                "https://www.ryuugames.com/new-slug/",
                None,
            ),
            (50, "Gone", "Gone", "", "[]", "https://www.ryuugames.com/gone/", GONE),
        ],
    )
    # A snapshot built before removed rows were left out of `links`.
    conn.execute("INSERT INTO links VALUES ('ryuugames','50','vndb','v1','ryuugames')")
    conn.commit()
    yield conn
    conn.close()


def _game_with(url: str) -> int:
    game_id = database.add_game("Game", "")
    database.replace_game_links(game_id, "page", [{"url": url}])
    return game_id


def test_link_to_a_removed_row_does_not_match(catalog):
    _ = _game_with("https://www.ryuugames.com/?p=50")
    result = resolve_page("https://vndb.org/v1")
    assert result["matches"] == []
    assert {(link["source"], link["id"]) for link in result["ids"]} == {("vndb", "v1")}


def test_live_post_id_beats_an_old_permalink(catalog):
    old = _game_with("https://www.dlsite.com/maniax/work/=/product_id/RJ43.html")
    new = _game_with("https://www.dlsite.com/maniax/work/=/product_id/RJ44.html")
    result = resolve_page("https://www.ryuugames.com/reused/", ["ryuugames:44"])
    assert result["matches"] == [new]
    assert result["catalog_entry"]["title"] == "New"
    assert ("ryuugames", "43") not in {
        (link["source"], link["id"]) for link in result["ids"]
    }
    # Without a live ID the stored permalink is still used.
    assert resolve_page("https://www.ryuugames.com/reused/")["matches"] == [old]
