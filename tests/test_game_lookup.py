# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3

import pytest

from catalog_server.store import CATALOG_SCHEMA
from core import database
from core.game_lookup import f95_thread_cover, resolve_page


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


def test_removed_catalog_rows_do_not_expand_stale_links(catalog):
    game_id = database.add_game("Steam work", "")
    database.replace_game_links(
        game_id,
        "page",
        [{"url": "https://store.steampowered.com/app/999/"}],
    )
    catalog.execute(
        "INSERT INTO links VALUES ('dlsite', 'RJ123', 'steam', '999', 'dlsite')"
    )
    catalog.execute(
        "UPDATE dlsite_works SET removed_at = '2026-10-27T00:00:00Z' "
        "WHERE product_id = 'RJ123'"
    )
    catalog.commit()

    result = resolve_page(
        "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html"
    )
    # The live page keeps its own identity, but the removed row's stale
    # catalog link must not report a different library game as a match.
    assert [(link["source"], link["id"]) for link in result["ids"]] == [
        ("dlsite", "RJ123")
    ]
    assert result["catalog_entry"] == {}
    assert result["matches"] == []


def test_ryuu_store_code_is_never_a_version(catalog):
    catalog.execute(
        "INSERT INTO ryuu_posts (post_id,title,raw_title,version,url,first_seen,last_seen) VALUES (43,'Work','Work (VJ01002857)','VJ01002857','https://www.ryuugames.com/work/','','')"
    )
    catalog.commit()
    result = resolve_page("https://www.ryuugames.com/?p=43")
    assert result["catalog_entry"]["title"] == "Work"
    assert result["catalog_entry"]["version"] == ""


def test_f95_thread_cover_reads_catalog(catalog):
    catalog.executemany(
        "INSERT INTO f95_threads (thread_id,title,cover_url,url,first_seen,last_seen) VALUES (?,?,?,?,'','')",
        [
            (7, "Game", "https://preview.f95zone.to/2026/01/cover.png", "https://f95zone.to/threads/7/"),
            (8, "No cover", None, "https://f95zone.to/threads/8/"),
        ],
    )
    catalog.commit()
    assert f95_thread_cover("7") == "https://preview.f95zone.to/2026/01/cover.png"
    assert f95_thread_cover("8") == ""
    assert f95_thread_cover("9") == ""
    assert f95_thread_cover("") == ""
    database.update_setting("catalog_enabled", "0")
    assert f95_thread_cover("7") == ""


def test_f95_thread_cover_without_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    assert f95_thread_cover("7") == ""


def test_catalog_screenshots_and_mapping(catalog, tmp_path):
    from core.game_lookup import f95_thread_screenshots, screenshot_urls

    catalog.execute("INSERT INTO f95_threads (thread_id,title,url,first_seen,last_seen,screens) VALUES (123,'A','u','','',?)", ('["https://preview.f95zone.to/a", "http://bad", 1]',))
    catalog.commit()
    assert f95_thread_screenshots("123") == ["https://preview.f95zone.to/a"]
    assert f95_thread_screenshots("124") is None
    catalog.execute("UPDATE f95_threads SET screens = 'invalid'")
    catalog.commit()
    assert f95_thread_screenshots("123") == []
    catalog.execute("UPDATE f95_threads SET screens = NULL")
    catalog.commit()
    assert f95_thread_screenshots("123") == []
    database.update_setting("catalog_enabled", "0")
    assert f95_thread_screenshots("123") is None
    database.update_setting("catalog_enabled", "1")
    assert screenshot_urls("https://preview.f95zone.to/a") == {"thumb": "https://preview.f95zone.to/a", "full": "https://attachments.f95zone.to/a"}
    assert screenshot_urls("https://attachments.f95zone.to/a")["thumb"] == "https://preview.f95zone.to/a"
    for url in ("https://i.imgur.com/a", "http://[bad"):
        assert screenshot_urls(url) == {"thumb": url, "full": url}
    catalog.execute("ALTER TABLE f95_threads DROP COLUMN screens")
    assert f95_thread_screenshots("123") is None


def test_f95_thread_ratings_reads_built_at_and_chunks(catalog, tmp_path):
    from core.game_lookup import f95_thread_ratings

    catalog.execute("INSERT INTO meta (key, value) VALUES ('built_at', '2026-11-01T12:00:00Z')")
    rows = [
        (thread_id, f"T{thread_id}", 4.0 + (thread_id % 10) / 10, f"https://f95zone.to/threads/{thread_id}/")
        for thread_id in range(1, 1201)
    ]
    catalog.executemany(
        "INSERT INTO f95_threads (thread_id, title, rating, url, first_seen, last_seen) VALUES (?,?,?,?,'','')",
        rows,
    )
    catalog.execute(
        "INSERT INTO f95_threads (thread_id, title, rating, url, first_seen, last_seen) VALUES (5000,'None',NULL,'u','','')"
    )
    catalog.commit()

    built_at, ratings = f95_thread_ratings([*range(1, 1201), 5000, 9999])
    assert built_at == "2026-11-01T12:00:00Z"
    assert len(ratings) == 1200
    assert ratings[3] == pytest.approx(4.3)
    assert 5000 not in ratings and 9999 not in ratings

    database.update_setting("catalog_enabled", "0")
    assert f95_thread_ratings([3]) == ("", {})
    database.update_setting("catalog_enabled", "1")
    catalog.close()
    (tmp_path / "catalog.db").unlink()
    assert f95_thread_ratings([3]) == ("", {})
    assert not (tmp_path / "catalog.db").exists()


@pytest.fixture
def lc_catalog(catalog):
    catalog.executemany(
        "INSERT INTO f95_threads(thread_id,title,url,first_seen,last_seen) VALUES (?, 'F95', '', '', '')",
        [(101,), (102,), (103,)],
    )
    catalog.executemany(
        "INSERT INTO lc_threads(thread_id,title,creator,version,tier,prefixes,tags,cover_url,rating,url,first_seen,last_seen) "
        "VALUES (?, ?, 'Creator', '1.1', ?, '[1,2,3]', '[\"Tag\"]', 'https://example.org/cover.webp', ?, '', '', '')",
        [(21, "Free game", "Free", 0), (22, "Plus game", "Premium", 4.1), (23, "Atlas game", "Free", 3.8)],
    )
    catalog.executemany("INSERT INTO lc_prefix_names VALUES (?,?)", [(1, "DAZ"), (2, "Ren'Py"), (3, "Completed")])
    catalog.execute(
        "INSERT INTO lc_thread_details(thread_id,fetched_at,status,overview,language,external_ids,origin) "
        """VALUES (23,'today','ok','Atlas overview','English','{"steam":[{"id":"987"}]}','import')"""
    )
    catalog.executemany("INSERT INTO links VALUES (?,?,?,?,?)", [
        ("lc", "21", "f95", "101", "title"), ("f95", "102", "lc", "22", "title"),
        ("lc", "23", "f95", "103", "import"),
    ])
    catalog.commit()
    return catalog


def test_lc_partner_cardinality_and_availability(lc_catalog, tmp_path):
    from core.game_lookup import f95_for_lc, lc_for_f95

    assert lc_for_f95("101") == ("21", "Free")
    assert lc_for_f95("102") == ("22", "Premium")
    assert f95_for_lc("22") == "102"
    assert f95_for_lc("23") is None
    lc_catalog.execute("INSERT INTO links VALUES ('lc','21','f95','102','title')")
    lc_catalog.commit()
    assert lc_for_f95("101") is None
    assert lc_for_f95("102") is None
    assert f95_for_lc("22") is None
    lc_catalog.execute("DELETE FROM links WHERE src_a='lc' AND id_a='21' AND id_b='102'")
    lc_catalog.execute("UPDATE lc_threads SET removed_at = 'yesterday' WHERE thread_id = 21")
    lc_catalog.execute("UPDATE f95_threads SET removed_at = 'yesterday' WHERE thread_id = 102")
    lc_catalog.commit()
    assert lc_for_f95("101") is None
    assert f95_for_lc("22") is None
    database.update_setting("catalog_enabled", "0")
    assert f95_for_lc("22") is None
    database.update_setting("catalog_enabled", "1")
    lc_catalog.close()
    (tmp_path / "catalog.db").unlink()
    assert lc_for_f95("101") is None


def test_lc_metadata_and_matching(lc_catalog):
    from core.game_lookup import lc_thread_entries

    result = lc_thread_entries(["21", "22", "23"])
    assert result["21"]["engine"] == "Ren'Py"
    assert result["21"]["thread_completed"] is True
    assert result["21"]["rating"] == ""
    assert result["22"]["rating"] == 4.1
    assert result["22"]["coverImage"] == "https://example.org/cover.webp"
    assert result["23"]["overview"] == "Atlas overview"
    assert result["23"]["language"] == "English"
    assert result["23"]["links"] == [
        {"source": "steam", "id": "987", "url": "https://store.steampowered.com/app/987/"}
    ]

    lc_url = "https://lewdcorner.com/threads/21/"
    f95_game = database.add_game("F95 game", "", "https://f95zone.to/threads/101/")
    by_partner = resolve_page(lc_url)
    assert by_partner["matches"] == [f95_game]
    assert by_partner["attach_lc_available"]
    exact = database.add_game("Exact thread", "", lc_url=lc_url, lc_mode="off")
    by_thread = resolve_page(lc_url)
    assert by_thread["matches"] == [exact]
    assert not by_thread["attach_lc_available"]
    assert exact is not None
    database.replace_game_links(exact, "user", [{"url": "https://store.steampowered.com/app/987/"}])
    by_store = resolve_page("https://lewdcorner.com/threads/23/")
    assert by_store["matches"] == [exact]
    assert not by_store["attach_lc_available"]


def test_lc_prefill(lc_catalog):
    from core.game_lookup import lookup_lc_partner

    f95 = "https://f95zone.to/threads/101/"
    lc = "https://lewdcorner.com/threads/21/"
    premium_f95 = "https://f95zone.to/threads/102/"
    assert lookup_lc_partner(lc) == {"f95_url": f95}
    assert lookup_lc_partner(f95) == {}
    database.update_setting("lc_enabled", "1")
    assert lookup_lc_partner(f95) == {"lc_url": lc}
    assert lookup_lc_partner(premium_f95) == {}
    database.update_setting("lc_plus", "1")
    assert lookup_lc_partner(premium_f95) == {"lc_url": "https://lewdcorner.com/threads/22/"}
    database.add_game("F95", "", f95)
    database.add_game("LC", "", lc_url=lc)
    assert lookup_lc_partner(lc) == lookup_lc_partner(f95) == {}
    database.update_setting("catalog_enabled", "0")
    assert lookup_lc_partner(premium_f95) == {}


def test_lc_automatic_fill_and_dismissal(lc_catalog, monkeypatch):
    from core.api import Api

    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    free = database.add_game("Free", "", "https://f95zone.to/threads/101/")
    plus = database.add_game("Plus", "", "https://f95zone.to/threads/102/")
    existing = database.add_game("Existing", "", "https://f95zone.to/threads/103/",
                                 lc_url="https://lewdcorner.com/threads/99/")
    claimed = database.add_game("Claimed", "", lc_url="https://lewdcorner.com/threads/21/")
    assert api._fill_lc_threads_from_catalog() == 0
    database.update_setting("lc_enabled", "1")
    assert api._fill_lc_threads_from_catalog() == 0
    database.delete_game(claimed)
    assert api._fill_lc_threads_from_catalog() == 1
    assert database.get_game(free)["lc_url"].endswith("/21/")
    assert database.get_game(existing)["lc_url"].endswith("/99/")
    assert database.get_game(plus)["lc_url"] == ""
    database.update_game(free, {"lc_url": ""})
    database.update_setting("lc_plus", "1")
    assert api._fill_lc_threads_from_catalog() == 1
    assert database.get_game(plus)["lc_url"].endswith("/22/")
    assert database.get_game(free)["lc_url"] == ""


def test_lc_catalog_fill_keeps_f95_and_edits(lc_catalog, monkeypatch):
    from core.api import Api

    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    sha = ["first"]
    monkeypatch.setattr(api.catalog, "installed_sha256", lambda: sha[0])
    database.update_setting("lc_enabled", "1")
    lc_catalog.execute("UPDATE f95_threads SET tags = '[\"F95 tag\"]' WHERE thread_id = 101")
    lc_catalog.commit()
    both = database.add_game("My library title", "", "https://f95zone.to/threads/101/",
                             lc_url="https://lewdcorner.com/threads/21/")
    lc_only = database.add_game("LC only", "", lc_url="https://lewdcorner.com/threads/23/")
    off = database.add_game("Off", "", lc_url="https://lewdcorner.com/threads/22/", lc_mode="off")

    assert api._fill_thread_details_from_catalog() == 2
    assert database.get_game(both)["tags"] == "F95 tag"
    assert database.get_game(both)["title"] == "My library title"
    assert database.get_game(lc_only)["overview"] == "Atlas overview"
    assert database.get_game(lc_only)["thread_completed"] == 1
    assert database.get_game(lc_only)["engine"] == "Ren'Py"
    assert database.get_game_links(lc_only)[0]["id"] == "987"
    assert database.get_game(off)["tags"] == ""
    database.update_game(lc_only, {"overview": "My own text"})
    lc_catalog.execute("UPDATE lc_thread_details SET overview = 'New overview', language = 'French'")
    lc_catalog.commit()
    sha[0] = "second"
    assert api._fill_thread_details_from_catalog() == 1
    assert database.get_game(lc_only)["overview"] == "My own text"
    assert database.get_game(lc_only)["language"] == "French"


def test_add_lc_game_uses_catalog_and_attaches(lc_catalog, monkeypatch):
    from core.api import Api

    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    monkeypatch.setattr(api.cover_cache, "wake", lambda: None)
    atlas_url = "https://www.lewdcorner.com/threads/atlas.23/post-2"
    result = api.add_game(exe_path="", lc_url=atlas_url)
    assert result["success"]
    assert not result["metadata_pending"]
    game = database.get_game(result["id"])
    assert game["title"] == "Atlas game"
    assert game["developer"] == "Creator"
    assert game["overview"] == "Atlas overview"
    assert game["version"] == "1.1"
    assert game["thread_completed"] == 1
    assert api.add_game("Duplicate", "", lc_url=atlas_url)["error_code"] == "duplicate_url"

    partner = database.add_game("Existing", "", "https://f95zone.to/threads/101/")
    result = api.add_game("Page name", "", lc_url="https://lewdcorner.com/threads/21/")
    assert result["attached_lc"]
    assert result["id"] == partner
    assert database.get_game(partner)["title"] == "Existing"
    assert database.get_game(partner)["lc_url"].endswith("/21/")


def test_lc_settings_trigger_catalog_fill(lc_catalog, monkeypatch):
    from core.api import Api

    api = Api()
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    monkeypatch.setattr("core.api_mixins.settings.threading.Thread", lambda target, **_kw: type(
        "ImmediateThread", (), {"start": staticmethod(target)}
    )())
    monkeypatch.setattr(api.catalog, "installed_sha256", lambda: "installed")
    free = database.add_game("Free", "", "https://f95zone.to/threads/101/")
    premium = database.add_game("Premium", "", "https://f95zone.to/threads/102/")
    # Default mode with an F95 thread: LC only becomes active with the setting.
    both = database.add_game("Both", "", "https://f95zone.to/threads/103/",
                             lc_url="https://lewdcorner.com/threads/23/")
    assert api._fill_thread_details_from_catalog() == 0
    assert database.get_game(both)["overview"] == ""
    assert api.save_settings({"lc_enabled": True})["success"]
    assert api.get_settings()["lc_enabled"] is True
    assert database.get_game(free)["lc_url"].endswith("/21/")
    # The same change fills LC data now, not at the next catalog install.
    assert database.get_game(both)["overview"] == "Atlas overview"
    assert database.get_game(premium)["lc_url"] == ""
    assert api.save_settings({"lc_plus": True})["success"]
    assert api.get_settings()["lc_plus"] is True
    assert database.get_game(premium)["lc_url"].endswith("/22/")
