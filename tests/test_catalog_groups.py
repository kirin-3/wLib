# SPDX-License-Identifier: GPL-3.0-or-later
"""Game groups across catalog sites (catalog_server.groups)."""

from catalog_server.groups import build_groups, rebuild_groups
from catalog_server.links import rebuild_links
from catalog_server.store import CatalogStore

NOW = "2026-11-24T00:00:00Z"


def _catalog(tmp_path):
    return CatalogStore(str(tmp_path / "catalog.sqlite"))


def _f95(catalog, thread_id, *, dlsite=(), steam=(), removed=False):
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [{"thread_id": thread_id, "title": f"F{thread_id}", "url": "u", "ts": 1}],
        NOW,
    )
    ids = {}
    if dlsite:
        ids["dlsite"] = [{"id": value, "url": "x"} for value in dlsite]
    if steam:
        ids["steam"] = [{"id": value, "url": "x"} for value in steam]
    catalog.upsert_f95_details(thread_id, 1, NOW, {"status": "ok", "external_ids": ids})
    if removed:
        catalog.conn.execute(
            "UPDATE f95_threads SET removed_at = ? WHERE thread_id = ?",
            (NOW, thread_id),
        )


def _works(catalog, *ids, site="maniax"):
    catalog.upsert(
        "dlsite_works",
        "product_id",
        [
            {"product_id": value, "title": value, "url": "d", "site": site}
            for value in ids
        ],
        NOW,
    )


def _ryuu(catalog, post_id, *, rj=(), steam=()):
    catalog.upsert(
        "ryuu_posts",
        "post_id",
        [
            {
                "post_id": post_id,
                "title": "x",
                "raw_title": "x",
                "rj_codes": list(rj),
                "steam_ids": list(steam),
                "url": "p",
            }
        ],
        NOW,
    )


def _vndb(catalog, vn_id):
    catalog.upsert(
        "vndb_vns", "vn_id", [{"vn_id": vn_id, "title": vn_id, "url": "v"}], NOW
    )


def _groups(catalog, imported=()):
    rebuild_links(catalog, imported)
    rebuild_groups(catalog)
    return dict(
        ((src, identity), key)
        for src, identity, key in catalog.conn.execute(
            "SELECT src, id, group_key FROM game_groups"
        )
    )


def test_entries_without_links_removed_and_appx(tmp_path):
    catalog = _catalog(tmp_path)
    _f95(catalog, 1)
    _f95(catalog, 2, removed=True)
    _works(catalog, "RJ01000001", site="appx")
    _f95(catalog, 3, dlsite=["RJ01000001"])
    groups = _groups(catalog)
    assert groups == {("f95", "1"): "f95:1", ("f95", "3"): "f95:3"}


def test_one_stated_partner_joins_and_f95_is_primary(tmp_path):
    catalog = _catalog(tmp_path)
    _works(catalog, "RJ01234567")
    _vndb(catalog, "v123")
    catalog.upsert(
        "vndb_releases",
        "release_id",
        [
            {
                "release_id": "r1",
                "title": "r",
                "vn_ids": ["v123"],
                "dlsite_ids": ["RJ01234567"],
                "url": "r",
            }
        ],
        NOW,
    )
    _f95(catalog, 223393, dlsite=["RJ01234567"])
    groups = _groups(catalog)
    assert set(groups.values()) == {"f95:223393"}
    assert len(groups) == 3


def test_dlsite_and_vndb_group_has_the_dlsite_primary(tmp_path):
    catalog = _catalog(tmp_path)
    _works(catalog, "RJ01234567")
    _vndb(catalog, "v123")
    catalog.upsert(
        "vndb_releases",
        "release_id",
        [
            {
                "release_id": "r1",
                "title": "r",
                "vn_ids": ["v123"],
                "dlsite_ids": ["RJ01234567"],
                "url": "r",
            }
        ],
        NOW,
    )
    assert _groups(catalog)[("vndb", "v123")] == "dlsite:RJ01234567"


def test_bundle_post_joins_none_of_its_works(tmp_path):
    catalog = _catalog(tmp_path)
    codes = [f"RJ0100{index:04d}" for index in range(61)]
    _works(catalog, *codes)
    _ryuu(catalog, 9, rj=codes)
    groups = _groups(catalog)
    assert len(set(groups.values())) == 62


def test_steam_id_stated_by_two_f95_threads_joins_nothing(tmp_path):
    catalog = _catalog(tmp_path)
    _f95(catalog, 1, steam=["1234"])
    _f95(catalog, 2, steam=["1234"])
    _ryuu(catalog, 7, steam=["1234"])
    groups = _groups(catalog)
    assert len(set(groups.values())) == 3


def test_steam_id_joins_one_entry_per_site(tmp_path):
    catalog = _catalog(tmp_path)
    _f95(catalog, 1, steam=["1234"])
    _ryuu(catalog, 7, steam=["1234"])
    groups = _groups(catalog)
    assert groups[("ryuugames", "7")] == "f95:1"


def test_join_that_brings_a_second_dlsite_work_is_skipped(tmp_path):
    catalog = _catalog(tmp_path)
    _works(catalog, "RJ01000001", "RJ01000002")
    _f95(catalog, 100, dlsite=["RJ01000001"], steam=["555"])
    _ryuu(catalog, 7, rj=["RJ01000002"], steam=["555"])
    groups = _groups(catalog)
    assert groups[("f95", "100")] == groups[("dlsite", "RJ01000001")] == "f95:100"
    assert groups[("ryuugames", "7")] != "f95:100"


def test_stated_link_wins_over_an_imported_pair(tmp_path):
    catalog = _catalog(tmp_path)
    _works(catalog, "RJ153359")
    _works(catalog, "VJ008354", site="pro")
    _f95(catalog, 5027, dlsite=["RJ153359"])
    groups = _groups(catalog, [("f95", "5027", "dlsite", "VJ008354")])
    assert groups[("dlsite", "RJ153359")] == "f95:5027"
    assert groups[("dlsite", "VJ008354")] == "dlsite:VJ008354"


def test_title_link_and_imported_pair_join(tmp_path):
    catalog = _catalog(tmp_path)
    _works(catalog, "RJ01000001", "RJ01000002")
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [{"thread_id": 10, "title": "A", "url": "u", "ts": 1}],
        NOW,
    )
    catalog.upsert_f95_details(
        10, 1, NOW, {"status": "ok", "original_title": "RJ01000001"}
    )
    _f95(catalog, 11)
    groups = _groups(catalog, [("f95", "11", "dlsite", "RJ01000002")])
    assert groups[("dlsite", "RJ01000001")] == "f95:10"
    assert groups[("dlsite", "RJ01000002")] == "f95:11"


def test_build_groups_covers_every_current_entry(tmp_path):
    catalog = _catalog(tmp_path)
    _f95(catalog, 1)
    _works(catalog, "RJ01000001")
    _vndb(catalog, "v1")
    _ryuu(catalog, 3)
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [{"thread_id": 4, "title": "L", "url": "u", "ts": 1}],
        NOW,
    )
    rebuild_links(catalog)
    assert set(build_groups(catalog)) == {
        ("f95", "1"),
        ("dlsite", "RJ01000001"),
        ("vndb", "v1"),
        ("ryuugames", "3"),
        ("lc", "4"),
    }
