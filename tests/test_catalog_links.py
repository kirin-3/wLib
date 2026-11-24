# SPDX-License-Identifier: GPL-3.0-or-later
from catalog_server.links import rebuild_links
from catalog_server.store import CatalogStore

NOW = "2026-10-26T00:00:00Z"


def _catalog(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert(
        "dlsite_works",
        "product_id",
        [
            {"product_id": "RJ01714523", "title": "Same Title", "url": "d1"},
            {"product_id": "VJ002520", "title": "Kusarihime", "url": "d2"},
        ],
        NOW,
    )
    catalog.upsert(
        "vndb_vns",
        "vn_id",
        [
            {"vn_id": "v37", "title": "Kusarihime", "url": "v"},
            {"vn_id": "v2480", "title": "Aniyome", "url": "v"},
        ],
        NOW,
    )
    catalog.upsert(
        "vndb_releases",
        "release_id",
        [
            {
                "release_id": "r80",
                "title": "r",
                "vn_ids": ["v37"],
                "dlsite_ids": ["VJ002520"],
                "steam_ids": ["111"],
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
                "rj_codes": ["RJ01714523", "RJ09999999"],
                "steam_ids": ["3771570"],
                "vndb_ids": ["v2480"],
                "url": "p",
            },
        ],
        NOW,
    )
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [
            {"thread_id": 5, "title": "Same Title", "url": "f"},
        ],
        NOW,
    )
    return catalog


def test_links_come_only_from_stated_ids(tmp_path):
    catalog = _catalog(tmp_path)
    assert rebuild_links(catalog) == 5
    links = set(
        catalog.conn.execute("SELECT src_a, id_a, src_b, id_b, origin FROM links")
    )
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


def test_first_post_dlsite_ids_link_current_pc_works(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert(
        "dlsite_works",
        "product_id",
        [
            {"product_id": "RJ01000001", "title": "A", "url": "d", "site": "maniax"},
            {"product_id": "RJ01000002", "title": "A", "url": "d", "site": "appx"},
            {"product_id": "RJ01000003", "title": "C", "url": "d", "site": "maniax"},
        ],
        NOW,
    )
    catalog.conn.execute(
        "UPDATE dlsite_works SET removed_at = ? WHERE product_id = 'RJ01000003'", (NOW,)
    )
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [{"thread_id": 10, "title": "A", "url": "u", "ts": 1}],
        NOW,
    )
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [{"thread_id": 20, "title": "A", "url": "u", "ts": 1}],
        NOW,
    )

    def ids(*codes, circle=None):
        found = {"dlsite": [{"id": code, "url": "x"} for code in codes]}
        if circle:
            found["dlsite_circle"] = [{"id": circle, "url": "x"}]
        return {"status": "ok", "external_ids": found}

    catalog.upsert_f95_details(
        10, 1, NOW, ids("RJ01000001", "RJ01000002", "RJ01000003", circle="RG1")
    )
    catalog.upsert_lc_details(20, 1, NOW, ids("RJ01000001"))
    rebuild_links(catalog)
    found = catalog.conn.execute(
        "SELECT src_a, id_a, src_b, id_b, origin FROM links WHERE src_b = 'dlsite' ORDER BY src_a"
    ).fetchall()
    # The appx edition, the removed work and the circle make no link.
    assert found == [
        ("f95", "10", "dlsite", "RJ01000001", "f95"),
        ("lc", "20", "dlsite", "RJ01000001", "lc"),
    ]


def _trust_catalog(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert(
        "dlsite_works",
        "product_id",
        [
            {
                "product_id": "RJ153359",
                "title": "本当の作品",
                "url": "d",
                "site": "maniax",
            },
            {"product_id": "VJ008354", "title": "別の作品", "url": "d", "site": "pro"},
            {
                "product_id": "RJ01000009",
                "title": "三つ目の作品",
                "url": "d",
                "site": "maniax",
            },
        ],
        NOW,
    )
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [
            {"thread_id": 5027, "title": "Game", "url": "u", "ts": 1},
            {"thread_id": 6000, "title": "Other", "url": "u", "ts": 1},
        ],
        NOW,
    )
    return catalog


def _dlsite_links(catalog):
    return catalog.conn.execute(
        "SELECT id_a, id_b, origin FROM links WHERE src_a = 'f95' AND src_b = 'dlsite' ORDER BY id_a, id_b"
    ).fetchall()


def test_stated_link_wins_over_title_and_imported_links(tmp_path):
    catalog = _trust_catalog(tmp_path)
    # Thread 5027 states RJ153359, while its original title names VJ008354.
    catalog.upsert_f95_details(
        5027,
        1,
        NOW,
        {
            "status": "ok",
            "original_title": "別の作品",
            "external_ids": {"dlsite": [{"id": "RJ153359", "url": "x"}]},
        },
    )
    # Thread 6000 has only a title match; an import disagrees with it.
    catalog.upsert_f95_details(
        6000, 1, NOW, {"status": "ok", "original_title": "三つ目の作品"}
    )
    rebuild_links(
        catalog,
        [
            ("f95", "5027", "dlsite", "VJ008354"),
            ("f95", "6000", "dlsite", "RJ153359"),
        ],
    )
    assert _dlsite_links(catalog) == [
        ("5027", "RJ153359", "f95"),
        ("6000", "RJ01000009", "title"),
    ]


def test_imported_pairs_link_only_current_entries(tmp_path):
    catalog = _trust_catalog(tmp_path)
    pairs = [
        ("f95", "5027", "dlsite", "RJ153359"),
        ("f95", "6000", "dlsite", "RJ02000000"),
    ]
    rebuild_links(catalog, pairs)
    assert _dlsite_links(catalog) == [("5027", "RJ153359", "import")]
    # The work appears later: the next rebuild makes the link.
    catalog.upsert(
        "dlsite_works",
        "product_id",
        [{"product_id": "RJ02000000", "title": "x", "url": "d", "site": "maniax"}],
        NOW,
    )
    rebuild_links(catalog, pairs)
    assert ("6000", "RJ02000000", "import") in _dlsite_links(catalog)


def test_imported_pairs_give_an_entry_one_link_per_site(tmp_path):
    catalog = _trust_catalog(tmp_path)
    rebuild_links(
        catalog,
        [
            ("f95", "5027", "dlsite", "RJ153359"),
            # Same work, other thread: the work already has an F95 link.
            ("f95", "6000", "dlsite", "RJ153359"),
            # Same thread, other work: the thread already has a DLsite link.
            ("f95", "5027", "dlsite", "VJ008354"),
            ("f95", "6000", "dlsite", "RJ01000009"),
        ],
    )
    assert _dlsite_links(catalog) == [
        ("5027", "RJ153359", "import"),
        ("6000", "RJ01000009", "import"),
    ]


def test_dlsite_ids_in_imported_lc_details_make_no_stated_link(tmp_path):
    catalog = _trust_catalog(tmp_path)
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [{"thread_id": 20, "title": "Game", "url": "u", "ts": 1}],
        NOW,
    )
    stated = {"dlsite": [{"id": "RJ153359", "url": "x"}]}
    catalog.import_lc_details({20: {"external_ids": stated}}, NOW)
    rebuild_links(catalog)
    lc_links = (
        "SELECT id_a, id_b, origin FROM links WHERE src_a = 'lc' AND src_b = 'dlsite'"
    )
    assert catalog.conn.execute(lc_links).fetchall() == []
    # LC's own first post does state it.
    catalog.upsert_lc_details(20, 1, NOW, {"status": "ok", "external_ids": stated})
    rebuild_links(catalog)
    assert catalog.conn.execute(lc_links).fetchall() == [("20", "RJ153359", "lc")]
