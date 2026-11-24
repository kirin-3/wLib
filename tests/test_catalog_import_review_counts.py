# SPDX-License-Identifier: GPL-3.0-or-later
"""Imported F95 review counts (catalog_server.tools.import_review_counts)."""

import os

from catalog_server.__main__ import _run_lock  # pyright: ignore[reportPrivateUsage]
from catalog_server.store import CATALOG_FILE, CatalogStore
from catalog_server.tools import import_review_counts

NOW = "2026-11-24T00:00:00Z"


def _catalog(folder):
    catalog = CatalogStore(os.path.join(folder, CATALOG_FILE))
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [
            {"thread_id": thread, "title": f"Game {thread}", "url": "u", "ts": 10}
            for thread in (1, 2, 3, 4)
        ],
        NOW,
    )
    catalog.upsert_f95_details(1, 10, NOW, {"status": "ok", "overview": "A"})
    catalog.upsert_f95_details(
        2, 10, NOW, {"status": "ok", "overview": "B", "rating_count": 40}
    )
    catalog.upsert_f95_details(3, 10, NOW, {"status": "missing"})
    return catalog


def _counts(folder):
    catalog = CatalogStore(os.path.join(folder, CATALOG_FILE))
    try:
        return dict(
            catalog.conn.execute(
                "SELECT thread_id, rating_count FROM f95_thread_details"
            ).fetchall()
        )
    finally:
        catalog.close()


def test_import_fills_only_missing_counts(tmp_path, capsys):
    _catalog(str(tmp_path)).close()
    counts = tmp_path / "counts.tsv"
    counts.write_text(
        "# comment\n1\t120\n2\t999\n3\t50\n4\t60\n\n5\t0\nx\t1\n7\t-2\n",
        encoding="utf-8",
    )
    assert import_review_counts.main([str(counts), "--data-dir", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert "filled 1 of 4 counts, skipped 2 lines" in captured.out
    assert "line 8" in captured.err and "line 9" in captured.err
    # F95's own count stays; a thread without read details gets nothing.
    assert _counts(str(tmp_path)) == {1: 120, 2: 40, 3: None}


def test_next_read_replaces_an_imported_count(tmp_path):
    catalog = _catalog(str(tmp_path))
    try:
        assert catalog.fill_f95_rating_counts([(1, 120)]) == 1
        catalog.upsert_f95_details(
            1, 10, NOW, {"status": "ok", "overview": "A", "rating_count": 131}
        )
        assert catalog.conn.execute(
            "SELECT rating_count FROM f95_thread_details WHERE thread_id = 1"
        ).fetchone() == (131,)
    finally:
        catalog.close()


def test_backfill_reads_threads_without_a_count_first(tmp_path):
    catalog = CatalogStore(os.path.join(str(tmp_path), CATALOG_FILE))
    try:
        catalog.upsert(
            "f95_threads",
            "thread_id",
            [
                {"thread_id": 1, "title": "A", "url": "u", "ts": 10, "views": 900},
                {"thread_id": 2, "title": "B", "url": "u", "ts": 10, "views": 100},
            ],
            NOW,
        )
        for thread in (1, 2):
            catalog.upsert_f95_details(thread, 10, NOW, {"status": "ok"})
        catalog.conn.execute("UPDATE f95_thread_details SET rating_count_attempted = 0")
        assert catalog.fill_f95_rating_counts([(1, 75)]) == 1
        # Thread 1 has more views but already has an imported count.
        assert catalog.f95_details_queue(since_ts=20, limit=10) == [(2, 10), (1, 10)]
    finally:
        catalog.close()


def test_import_refuses_during_a_run(tmp_path, capsys):
    _catalog(str(tmp_path)).close()
    counts = tmp_path / "counts.tsv"
    counts.write_text("1\t120\n", encoding="utf-8")
    with _run_lock(str(tmp_path)) as acquired:
        assert acquired
        assert (
            import_review_counts.main([str(counts), "--data-dir", str(tmp_path)]) == 1
        )
    assert "in progress" in capsys.readouterr().err
    assert _counts(str(tmp_path))[1] is None
