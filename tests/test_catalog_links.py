# SPDX-License-Identifier: GPL-3.0-or-later
from catalog_server.links import rebuild_links
from catalog_server.store import CatalogStore

NOW = "2026-10-26T00:00:00Z"


def _catalog(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert("dlsite_works", "product_id", [
        {"product_id": "RJ01714523", "title": "Same Title", "url": "d1"},
        {"product_id": "VJ002520", "title": "Kusarihime", "url": "d2"},
    ], NOW)
    catalog.upsert("vndb_vns", "vn_id", [
        {"vn_id": "v37", "title": "Kusarihime", "url": "v"},
        {"vn_id": "v2480", "title": "Aniyome", "url": "v"},
    ], NOW)
    catalog.upsert("vndb_releases", "release_id", [
        {"release_id": "r80", "title": "r", "vn_ids": ["v37"], "dlsite_ids": ["VJ002520"], "steam_ids": ["111"], "url": "r"},
    ], NOW)
    catalog.upsert("ryuu_posts", "post_id", [
        {"post_id": 1, "title": "x", "raw_title": "x", "rj_codes": ["RJ01714523", "RJ09999999"],
         "steam_ids": ["3771570"], "vndb_ids": ["v2480"], "url": "p"},
    ], NOW)
    catalog.upsert("f95_threads", "thread_id", [
        {"thread_id": 5, "title": "Same Title", "url": "f"},
    ], NOW)
    return catalog


def test_links_come_only_from_stated_ids(tmp_path):
    catalog = _catalog(tmp_path)
    assert rebuild_links(catalog) == 5
    links = set(catalog.conn.execute("SELECT src_a, id_a, src_b, id_b, origin FROM links"))
    assert links == {
        ("ryuugames", "1", "dlsite", "RJ01714523", "ryuugames"),
        ("ryuugames", "1", "steam", "3771570", "ryuugames"),
        ("ryuugames", "1", "vndb", "v2480", "ryuugames"),
        ("vndb", "v37", "dlsite", "VJ002520", "vndb"),
        ("vndb", "v37", "steam", "111", "vndb"),
    }


def test_same_title_without_a_stated_link_stays_unlinked(tmp_path):
    catalog = _catalog(tmp_path)
    rebuild_links(catalog)
    assert catalog.conn.execute(
        "SELECT COUNT(*) FROM links WHERE src_a = 'f95' OR src_b = 'f95'"
    ).fetchone() == (0,)


def test_rebuild_replaces_old_links(tmp_path):
    catalog = _catalog(tmp_path)
    rebuild_links(catalog)
    catalog.conn.execute("DELETE FROM vndb_releases")
    catalog.conn.commit()
    assert rebuild_links(catalog) == 3
