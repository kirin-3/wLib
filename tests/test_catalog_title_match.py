# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict LewdCorner↔F95 title matching."""

from catalog_server.links import rebuild_links
from catalog_server.store import CatalogStore
from catalog_server.title_match import devkey, match_lc_f95, norm

NOW = "2026-11-18T00:00:00Z"


def _catalog(tmp_path):
    return CatalogStore(str(tmp_path / "catalog.sqlite"))


def _lc(catalog, thread_id, title, creator, ids=None):
    catalog.upsert("lc_threads", "thread_id", [{
        "thread_id": thread_id, "title": title, "creator": creator, "ts": 1,
        "url": f"https://lewdcorner.com/threads/{thread_id}/",
    }], NOW)
    if ids is not None:
        catalog.upsert_lc_details(thread_id, 1, NOW, {"status": "ok", "external_ids": ids})


def _f95(catalog, thread_id, title, creator, ids=None):
    catalog.upsert("f95_threads", "thread_id", [{
        "thread_id": thread_id, "title": title, "creator": creator, "ts": 1,
        "url": f"https://f95zone.to/threads/{thread_id}/",
    }], NOW)
    if ids is not None:
        catalog.upsert_f95_details(thread_id, 1, NOW, {"status": "ok", "external_ids": ids})


def _patreon(value):
    return {"patreon": [{"id": value, "url": "https://www.patreon.com/x"}]}


def test_norm_folds_case_accents_and_symbols():
    assert norm("Summoner's Legacy") == norm("Summoner’s Legacy") == "summoner s legacy"
    assert norm("  Café & Crème!! ") == "cafe and creme"
    assert norm(None) == ""
    assert devkey("Nun Ya") == devkey("nun-ya") == "nunya"


def test_same_title_and_developer_links(tmp_path):
    catalog = _catalog(tmp_path)
    _lc(catalog, 6, "Summoner's Legacy", "Nun Ya")
    _f95(catalog, 100, "Summoner’s Legacy", "Nun Ya")
    assert match_lc_f95(catalog) == [(6, 100)]
    assert rebuild_links(catalog) == 1
    assert catalog.conn.execute("SELECT * FROM links").fetchall() == [
        ("lc", "6", "f95", "100", "title")
    ]


def test_same_short_title_with_different_developers_does_not_link(tmp_path):
    catalog = _catalog(tmp_path)
    _lc(catalog, 1, "Lisa", "Dev A", _patreon("deva"))
    _f95(catalog, 2, "Lisa", "Dev B", _patreon("devb"))
    assert match_lc_f95(catalog) == []


def test_a_duplicate_title_on_either_side_links_nothing(tmp_path):
    catalog = _catalog(tmp_path)
    _lc(catalog, 1, "Corruption", "Dev")
    _f95(catalog, 10, "Corruption", "Dev")
    _f95(catalog, 11, "corruption!", "Dev")
    assert match_lc_f95(catalog) == []
    (tmp_path / "other").mkdir()
    other = _catalog(tmp_path / "other")
    _lc(other, 1, "Corruption", "Dev")
    _lc(other, 2, "Corruption", "Dev")
    _f95(other, 10, "Corruption", "Dev")
    assert match_lc_f95(other) == []


def test_a_shared_patreon_id_links_different_developer_spellings(tmp_path):
    catalog = _catalog(tmp_path)
    _lc(catalog, 1, "Dark Coven", "Nun Ya and Fallen Angel Productions", _patreon("u:5648613"))
    _f95(catalog, 2, "Dark Coven", "Nun Ya", _patreon("u:5648613"))
    assert match_lc_f95(catalog) == [(1, 2)]


def test_removed_threads_are_not_matched(tmp_path):
    catalog = _catalog(tmp_path)
    _lc(catalog, 1, "Game", "Dev")
    _f95(catalog, 2, "Game", "Dev")
    catalog.conn.execute("UPDATE f95_threads SET removed_at = ?", (NOW,))
    assert match_lc_f95(catalog) == []


def test_a_discord_only_overlap_does_not_link(tmp_path):
    catalog = _catalog(tmp_path)
    invite = {"discord": [{"id": "abc", "url": "https://discord.gg/abc"}]}
    _lc(catalog, 1, "Game", "Dev A", invite)
    _f95(catalog, 2, "Game", "Dev B", invite)
    assert match_lc_f95(catalog) == []


def test_empty_developers_do_not_count_as_equal(tmp_path):
    catalog = _catalog(tmp_path)
    _lc(catalog, 1, "Game", None)
    _f95(catalog, 2, "Game", "")
    assert match_lc_f95(catalog) == []


def _import_lc(catalog, thread_id, title, creator):
    catalog.upsert("lc_threads", "thread_id", [{
        "thread_id": thread_id, "title": title, "creator": creator, "ts": 1,
        "url": f"https://lewdcorner.com/threads/{thread_id}/",
    }], NOW, origin="import")


def test_an_atlas_imported_lc_thread_is_never_linked(tmp_path):
    # Its title and creator are Atlas's merged values, often F95's own.
    catalog = _catalog(tmp_path)
    _import_lc(catalog, 1, "Plus Game", "Dev")
    _f95(catalog, 2, "Plus Game", "Dev")
    assert match_lc_f95(catalog) == []


def test_an_atlas_imported_lc_thread_still_makes_a_title_ambiguous(tmp_path):
    catalog = _catalog(tmp_path)
    _lc(catalog, 1, "Game", "Dev")
    _import_lc(catalog, 3, "Game", "Other Dev")
    _f95(catalog, 2, "Game", "Dev")
    assert match_lc_f95(catalog) == []


def test_an_imported_thread_matches_once_the_feed_lists_it(tmp_path):
    catalog = _catalog(tmp_path)
    _import_lc(catalog, 1, "Plus Game", "Dev")
    _f95(catalog, 2, "Plus Game", "Dev")
    _lc(catalog, 1, "Plus Game", "Dev")  # a feed upsert clears the import origin
    assert match_lc_f95(catalog) == [(1, 2)]
