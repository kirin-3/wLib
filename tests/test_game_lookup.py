# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3

import pytest

from catalog_server.store import CATALOG_SCHEMA
from core import database
from core.game_lookup import resolve_page


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()
    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.executescript(CATALOG_SCHEMA)
    conn.execute(
        "INSERT INTO dlsite_works (product_id,title,circle,genres,url,first_seen,last_seen) VALUES ('RJ123','Work','Circle','[\"RPG\"]','https://www.dlsite.com/maniax/work/=/product_id/RJ123.html','','')"
    )
    conn.execute(
        "INSERT INTO vndb_vns (vn_id,title,developers,tags,url,first_seen,last_seen) VALUES ('v1','Novel','[\"Developer\"]','[\"Fantasy\"]','https://vndb.org/v1','','')"
    )
    conn.execute(
        "INSERT INTO ryuu_posts (post_id,title,raw_title,version,rj_codes,url,first_seen,last_seen) VALUES (42,'Post','Post','1.2','[\"RJ123\"]','https://www.ryuugames.com/post/','','')"
    )
    conn.executemany(
        "INSERT INTO links VALUES (?,?,?,?,?)",
        [
            ("vndb", "v1", "dlsite", "RJ123", "vndb"),
            ("vndb", "v1", "steam", "321", "vndb"),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


def test_vndb_one_hop_matches_dlsite_and_metadata(catalog):
    game_id = database.add_game("Different title", "")
    database.replace_game_links(
        game_id,
        "page",
        [{"url": "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html"}],
    )
    result = resolve_page("https://vndb.org/v1")
    assert result["matches"] == [game_id]
    assert result["catalog_entry"]["developer"] == "Developer"
    assert result["catalog_entry"]["tags"] == "Fantasy"
    assert {link["id"] for link in result["ids"]} == {"v1", "RJ123", "321"}
    # RJ123 -> v1 -> Steam must not make a second hop.
    assert {
        link["id"]
        for link in resolve_page(
            "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html"
        )["ids"]
    } == {"v1", "RJ123"}


def test_ryuu_permalink_and_page_post_id_match_rj_codes(catalog):
    game_id = database.add_game("Work", "")
    database.replace_game_links(
        game_id,
        "page",
        [{"url": "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html"}],
    )
    assert resolve_page("https://www.ryuugames.com/post/")["matches"] == [game_id]
    result = resolve_page("https://www.ryuugames.com/changed-slug/", ["ryuugames:42"])
    assert result["matches"] == [game_id]
    assert result["catalog_entry"]["version"] == "1.2"


def test_off_missing_catalog_and_no_title_matching(catalog, tmp_path):
    database.add_game("Novel", "")
    assert resolve_page("https://vndb.org/v1")["matches"] == []
    database.update_setting("catalog_enabled", "0")
    assert [link["id"] for link in resolve_page("https://vndb.org/v1")["ids"]] == ["v1"]
    assert resolve_page("https://vndb.org/v1")["catalog_entry"] == {}
    database.update_setting("catalog_enabled", "1")
    catalog.close()  # Windows cannot delete a file that is still open.
    (tmp_path / "catalog.db").unlink()
    assert resolve_page("https://vndb.org/v1")["matches"] == []
    assert not (tmp_path / "catalog.db").exists()


def test_ambiguous_and_f95_priority_never_changes_games(catalog):
    for title in ("One", "Two"):
        game_id = database.add_game(title, "")
        database.replace_game_links(game_id, "page", [{"url": "https://vndb.org/v1"}])
    before = database.get_all_games()
    assert len(resolve_page("https://vndb.org/v1")["matches"]) == 2
    assert database.get_all_games() == before
    f95 = database.add_game("Thread", "", "https://f95zone.to/threads/123/")
    assert resolve_page("https://f95zone.to/threads/123/", ["vndb:v1"])["matches"] == [
        f95
    ]


@pytest.mark.parametrize(
    "source,identity,url",
    [
        ("itch", "creator/game", "https://creator.itch.io/game"),
        ("fanza", "d_123", "https://www.dmm.co.jp/dc/doujin/-/detail/=/cid=d_123/"),
    ],
)
def test_f95_page_ids_match_other_supported_work_sites(catalog, source, identity, url):
    game_id = database.add_game("Work", "")
    database.replace_game_links(game_id, "page", [{"url": url}])
    assert resolve_page("https://f95zone.to/threads/123/", [f"{source}:{identity}"])[
        "matches"
    ] == [game_id]


def test_ryuu_store_code_is_never_a_version(catalog):
    catalog.execute(
        "INSERT INTO ryuu_posts (post_id,title,raw_title,version,url,first_seen,last_seen) VALUES (43,'Work','Work (VJ01002857)','VJ01002857','https://www.ryuugames.com/work/','','')"
    )
    catalog.commit()
    result = resolve_page("https://www.ryuugames.com/?p=43")
    assert result["catalog_entry"]["title"] == "Work"
    assert result["catalog_entry"]["version"] == ""
