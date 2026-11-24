# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict LewdCorner↔F95 title matching."""

import sqlite3
from contextlib import closing

import pytest

from catalog_server.links import rebuild_links
from catalog_server.store import CatalogStore
from catalog_server.title_match import devkey, match_lc_f95, norm

NOW = "2026-11-18T00:00:00Z"


def _catalog(tmp_path):
    return CatalogStore(str(tmp_path / "catalog.sqlite"))


def _lc(catalog, thread_id, title, creator, ids=None, *, ts=1):
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [
            {
                "thread_id": thread_id,
                "title": title,
                "creator": creator,
                "ts": ts,
                "url": f"https://lewdcorner.com/threads/{thread_id}/",
            }
        ],
        NOW,
    )
    if ids is not None:
        catalog.upsert_lc_details(
            thread_id, ts, NOW, {"status": "ok", "external_ids": ids}
        )


def _f95(catalog, thread_id, title, creator, ids=None, *, ts=1):
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [
            {
                "thread_id": thread_id,
                "title": title,
                "creator": creator,
                "ts": ts,
                "url": f"https://f95zone.to/threads/{thread_id}/",
            }
        ],
        NOW,
    )
    if ids is not None:
        catalog.upsert_f95_details(
            thread_id, ts, NOW, {"status": "ok", "external_ids": ids}
        )


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
    _lc(
        catalog,
        1,
        "Dark Coven",
        "Nun Ya and Fallen Angel Productions",
        _patreon("u:5648613"),
    )
    _f95(catalog, 2, "Dark Coven", "Nun Ya", _patreon("u:5648613"))
    assert match_lc_f95(catalog) == [(1, 2)]


@pytest.mark.parametrize("source", ["lc", "f95"])
@pytest.mark.parametrize("status", ["missing", "unreadable"])
def test_a_failed_reread_cannot_link_a_renamed_thread_using_old_ids(
    tmp_path, source, status
):
    catalog = _catalog(tmp_path)
    ids = _patreon("same")
    _lc(catalog, 1, "New Title" if source == "f95" else "Old Title", "Dev A", ids)
    _f95(catalog, 2, "New Title" if source == "lc" else "Old Title", "Dev B", ids)
    assert rebuild_links(catalog) == 0

    if source == "lc":
        _lc(catalog, 1, "New Title", "Dev C", ts=2)
        catalog.upsert_lc_details(1, 2, NOW, {"status": status})
    else:
        _f95(catalog, 2, "New Title", "Dev C", ts=2)
        catalog.upsert_f95_details(2, 2, NOW, {"status": status})
    assert rebuild_links(catalog) == 0

    if source == "lc":
        _lc(catalog, 1, "New Title", "Dev C", ids, ts=3)
    else:
        _f95(catalog, 2, "New Title", "Dev C", ids, ts=3)
    assert rebuild_links(catalog) == 1


def test_legacy_details_schema_supports_matching_and_migrates_on_open(tmp_path):
    path = tmp_path / "catalog.sqlite"
    ids = _patreon("same")
    catalog = _catalog(tmp_path)
    _lc(catalog, 1, "Game", "Dev A", ids)
    _f95(catalog, 2, "Game", "Dev B", ids)
    with catalog.conn:
        catalog.conn.execute(
            "ALTER TABLE lc_thread_details DROP COLUMN kept_after_failure"
        )
        catalog.conn.execute(
            "ALTER TABLE f95_thread_details DROP COLUMN kept_after_failure"
        )
    catalog.conn.close()

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        assert match_lc_f95(conn) == [(1, 2)]
    catalog = CatalogStore(str(path))
    assert match_lc_f95(catalog) == [(1, 2)]
    catalog.upsert_f95_details(2, 2, NOW, {"status": "missing"})
    assert match_lc_f95(catalog) == []


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
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [
            {
                "thread_id": thread_id,
                "title": title,
                "creator": creator,
                "ts": 1,
                "url": f"https://lewdcorner.com/threads/{thread_id}/",
            }
        ],
        NOW,
        origin="import",
    )


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


def test_developer_ids_imported_from_atlas_do_not_link(tmp_path):
    catalog = _catalog(tmp_path)
    _lc(catalog, 1, "Game", "Dev A")
    _f95(catalog, 2, "Game", "Dev B", _patreon("same"))
    catalog.import_lc_details({1: {"external_ids": _patreon("same")}}, NOW)
    assert match_lc_f95(catalog) == []
    catalog.upsert_lc_details(
        1, 1, NOW, {"status": "ok", "external_ids": _patreon("same")}
    )
    assert match_lc_f95(catalog) == [(1, 2)]


def _f95_original(catalog, thread_id, original, *, removed=False):
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [{"thread_id": thread_id, "title": f"T{thread_id}", "url": "u", "ts": 1}],
        NOW,
    )
    catalog.upsert_f95_details(
        thread_id, 1, NOW, {"status": "ok", "original_title": original}
    )
    if removed:
        catalog.conn.execute(
            "UPDATE f95_threads SET removed_at = ? WHERE thread_id = ?",
            (NOW, thread_id),
        )


def _work(catalog, product_id, title, site="maniax"):
    catalog.upsert(
        "dlsite_works",
        "product_id",
        [{"product_id": product_id, "title": title, "url": "d", "site": site}],
        NOW,
    )


def test_original_title_key_rules():
    from catalog_server.title_match import _keys_equal, original_title_key

    assert original_title_key("【Live2D】コン狐との日常") == original_title_key(
        "コン狐との日常"
    )
    assert original_title_key("ＡＢＣ　ゲーム！") == "abcゲーム"
    assert _keys_equal(
        original_title_key("山野リョウと痴○列車"),
        original_title_key("山野リョウと痴漢列車"),
    )
    assert not _keys_equal(
        original_title_key("痴漢列車"), original_title_key("痴漢電車")
    )


def test_unique_original_title_links_the_pc_work(tmp_path):
    from catalog_server.title_match import match_f95_dlsite

    catalog = _catalog(tmp_path)
    _f95_original(catalog, 100, "山野リョウと痴○列車")
    _work(catalog, "RJ01000001", "山野リョウと痴漢列車")
    # PC work plus its smartphone edition: only the PC work counts.
    _f95_original(catalog, 101, "妹はルームメイト")
    _work(catalog, "RJ01000002", "妹はルームメイト")
    _work(catalog, "RJ01000003", "妹はルームメイト", site="appx")
    # Two PC works with one title: no link.
    _f95_original(catalog, 102, "ふたつの作品")
    _work(catalog, "RJ01000004", "ふたつの作品")
    _work(catalog, "RJ01000005", "ふたつの作品", site="pro")
    # Two F95 threads with one original title: no link.
    _f95_original(catalog, 103, "同じタイトル")
    _f95_original(catalog, 104, "同じタイトル")
    _work(catalog, "RJ01000006", "同じタイトル")
    # Too short to be certain, and a removed thread.
    _f95_original(catalog, 105, "リサ")
    _work(catalog, "RJ01000007", "リサ")
    _f95_original(catalog, 106, "消えたスレッド", removed=True)
    _work(catalog, "RJ01000008", "消えたスレッド")
    assert match_f95_dlsite(catalog) == [(100, "RJ01000001"), (101, "RJ01000002")]


def test_a_work_named_by_two_threads_through_the_wildcard_is_not_linked(tmp_path):
    from catalog_server.title_match import match_f95_dlsite

    catalog = _catalog(tmp_path)
    # Two distinct F95 titles both equal one censored DLsite title.
    _f95_original(catalog, 200, "Night Bus")
    _f95_original(catalog, 201, "Night Box")
    _work(catalog, "RJ01000010", "Night B○○")
    # A censored and an uncensored F95 title both equal one DLsite title.
    _f95_original(catalog, 202, "痴○列車の夜")
    _f95_original(catalog, 203, "痴漢列車の夜")
    _work(catalog, "RJ01000011", "痴漢列車の夜")
    # A work only one thread names still links.
    _f95_original(catalog, 204, "一つだけの作品")
    _work(catalog, "RJ01000012", "一つだけの作品")
    assert match_f95_dlsite(catalog) == [(204, "RJ01000012")]
