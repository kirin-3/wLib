# SPDX-License-Identifier: GPL-3.0-or-later
import json
import sqlite3
import subprocess
import sys

from catalog_server.store import CatalogStore, StateStore, open_stores


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def test_both_databases_are_created(tmp_path):
    catalog, state = open_stores(str(tmp_path / "data"))
    catalog.close()
    state.close()
    assert {
        "f95_threads",
        "dlsite_works",
        "ryuu_posts",
        "vndb_vns",
        "links",
        "f95_tag_names",
        "f95_prefix_names",
        "meta",
    } <= _tables(tmp_path / "data" / "catalog.sqlite")
    assert {"kv", "request_log", "runs", "run_sources", "source_status"} <= _tables(
        tmp_path / "data" / "state.sqlite"
    )
    conn = sqlite3.connect(tmp_path / "data" / "catalog.sqlite")
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone() == ("1",)
    for table in ("f95_threads", "dlsite_works", "ryuu_posts", "vndb_vns"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert {"first_seen", "last_seen", "removed_at"} <= columns
    conn.close()


def test_upsert_keeps_first_seen_and_clears_removed(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    row = {"thread_id": 1, "title": "A", "tags": [1, 2], "url": "u"}
    catalog.upsert("f95_threads", "thread_id", [row], "2026-01-01T00:00:00Z")
    catalog.conn.execute("UPDATE f95_threads SET removed_at = 'x'")
    catalog.upsert(
        "f95_threads", "thread_id", [{**row, "title": "B"}], "2026-02-01T00:00:00Z"
    )
    assert catalog.conn.execute(
        "SELECT title, tags, first_seen, last_seen, removed_at FROM f95_threads"
    ).fetchone() == (
        "B",
        json.dumps([1, 2]),
        "2026-01-01T00:00:00Z",
        "2026-02-01T00:00:00Z",
        None,
    )


def test_f95_rating_count_migration_backfills_existing_details(tmp_path):
    path = str(tmp_path / "catalog.sqlite")
    now = "2026-10-26T00:00:00Z"
    catalog = CatalogStore(path)
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [{"thread_id": 1, "title": "Existing game", "url": "u", "ts": 10}],
        now,
    )
    catalog.upsert_f95_details(
        1, 10, now, {"status": "ok", "overview": "Existing text"}
    )
    with catalog.conn:
        catalog.conn.execute(
            "ALTER TABLE f95_thread_details DROP COLUMN rating_count_attempted"
        )
        catalog.conn.execute("ALTER TABLE f95_thread_details DROP COLUMN rating_count")
    catalog.close()

    catalog = CatalogStore(path)
    try:
        assert catalog.conn.execute(
            "SELECT overview, rating_count FROM f95_thread_details WHERE thread_id = 1"
        ).fetchone() == ("Existing text", None)
        assert catalog.f95_details_queue(since_ts=20, limit=10) == [(1, 10)]
        catalog.upsert_f95_details(1, 10, now, {"status": "unreadable"})
        assert catalog.conn.execute(
            "SELECT overview, rating_count, rating_count_attempted FROM f95_thread_details"
        ).fetchone() == ("Existing text", None, 1)
        assert catalog.f95_details_queue(since_ts=20, limit=10) == []
        catalog.conn.execute("UPDATE f95_threads SET ts = 11 WHERE thread_id = 1")
        assert catalog.f95_details_queue(since_ts=20, limit=10) == [(1, 11)]
        catalog.upsert_f95_details(
            1,
            11,
            now,
            {"status": "ok", "overview": "Existing text", "rating_count": 12},
        )
        assert catalog.conn.execute(
            "SELECT overview, rating_count FROM f95_thread_details WHERE thread_id = 1"
        ).fetchone() == ("Existing text", 12)
        assert catalog.f95_details_queue(since_ts=20, limit=10) == []
    finally:
        catalog.close()


def test_failed_f95_read_keeps_the_stored_review_count(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-10-26T00:00:00Z"
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [{"thread_id": 1, "title": "Game", "url": "u", "ts": 11}],
        now,
    )
    try:
        catalog.upsert_f95_details(
            1, 10, now, {"status": "ok", "overview": "Text", "rating_count": 12}
        )
        catalog.upsert_f95_details(1, 11, now, {"status": "unreadable"})
        assert catalog.conn.execute(
            "SELECT status, overview, rating_count, rating_count_attempted, "
            + "kept_after_failure FROM f95_thread_details WHERE thread_id = 1"
        ).fetchone() == ("ok", "Text", 12, 1, 1)
        assert catalog.f95_details_queue(since_ts=20, limit=10) == []
    finally:
        catalog.close()


def test_state_values_round_trip(tmp_path):
    state = StateStore(str(tmp_path / "state.sqlite"))
    state.put("f95", "sweep_page", 12)
    state.put("dlsite", "sweep_cursor", [1, 3])
    assert state.get("f95", "sweep_page") == 12
    assert state.get("dlsite", "sweep_cursor") == [1, 3]
    state.put("f95", "sweep_page", None)
    assert state.get("f95", "sweep_page", "gone") == "gone"


def test_format_module_imports_nothing_from_the_app():
    code = (
        "import sys, core.catalog_format; "
        "bad = [m for m in sys.modules if m == 'webview' or (m.startswith('core.') "
        "and m != 'core.catalog_format')]; print(bad); raise SystemExit(1 if bad else 0)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_a_later_success_clears_the_last_error(tmp_path):
    state = StateStore(str(tmp_path / "state.sqlite"))
    run_id = state.start_run("2026-01-01T00:00:00Z")
    state.record_source(
        run_id, "f95", "error", 0, 1, "timed out", "2026-01-01T00:01:00Z"
    )
    assert state.source_status("f95")["last_error"] == "timed out"
    state.record_source(run_id + 1, "f95", "ok", 5, 2, None, "2026-01-01T12:00:00Z")
    status = state.source_status("f95")
    assert (
        status["last_error"] is None
        and status["last_success_at"] == "2026-01-01T12:00:00Z"
    )


def test_imported_rows_survive_sweeps_until_the_source_lists_them(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    row = {"thread_id": 1, "title": "A", "url": "u"}
    catalog.upsert(
        "f95_threads", "thread_id", [row], "2026-01-01T00:00:00Z", origin="import"
    )
    assert (
        catalog.mark_removed("f95", "2026-02-01T00:00:00Z", "2026-02-01T00:00:00Z") == 0
    )
    catalog.upsert("f95_threads", "thread_id", [row], "2026-02-01T00:00:00Z")
    assert catalog.conn.execute("SELECT origin FROM f95_threads").fetchone() == (None,)
    assert (
        catalog.mark_removed("f95", "2026-03-01T00:00:00Z", "2026-03-01T00:00:00Z") == 1
    )


def test_older_catalogs_get_the_origin_column(tmp_path):
    path = tmp_path / "catalog.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE f95_threads (thread_id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
        + "url TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, removed_at TEXT)"
    )
    conn.commit()
    conn.close()
    catalog = CatalogStore(str(path))
    columns = {row[1] for row in catalog.conn.execute("PRAGMA table_info(f95_threads)")}
    catalog.close()
    assert "origin" in columns


def test_older_catalog_gets_nullable_screens(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE f95_threads (thread_id INTEGER PRIMARY KEY, title TEXT, url TEXT, first_seen TEXT, last_seen TEXT)"
    )
    conn.execute("INSERT INTO f95_threads VALUES (1, 'A', 'u', 'x', 'x')")
    conn.commit()
    conn.close()
    catalog = CatalogStore(str(path))
    assert catalog.conn.execute("SELECT screens FROM f95_threads").fetchone() == (None,)
    assert catalog.conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone() == ("1",)
    catalog.close()


LC_TABLES = {"lc_threads", "lc_prefix_names", "lc_thread_details"}


def _lc_row(thread_id, ts, views=0, **extra):
    return {
        "thread_id": thread_id,
        "title": f"T{thread_id}",
        "ts": ts,
        "views": views,
        "url": f"https://lewdcorner.com/threads/{thread_id}/",
        **extra,
    }


def test_new_and_existing_catalogs_get_the_lc_tables(tmp_path):
    CatalogStore(str(tmp_path / "new.sqlite")).close()
    assert LC_TABLES <= _tables(tmp_path / "new.sqlite")
    # A catalog from before LC: only the F95 table.
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE f95_threads (thread_id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
        + "url TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, removed_at TEXT)"
    )
    conn.commit()
    conn.close()
    CatalogStore(str(path)).close()
    assert LC_TABLES <= _tables(path)


def test_lc_count_ignores_removed_and_mark_removed_skips_imports(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [_lc_row(1, 10), _lc_row(2, 20)],
        "2026-01-01T00:00:00Z",
    )
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [_lc_row(3, 30, tier="Premium")],
        "2026-01-01T00:00:00Z",
        origin="import",
    )
    catalog.upsert("lc_threads", "thread_id", [_lc_row(1, 10)], "2026-02-01T00:00:00Z")
    # The feed owns the Free threads; the manifest counts the whole table.
    assert catalog.count("lc") == 2
    assert catalog.count("lc", whole_table=True) == 3
    # Thread 2 was not seen after the sweep start; thread 3 is imported.
    assert (
        catalog.mark_removed("lc", "2026-01-15T00:00:00Z", "2026-02-01T00:00:00Z") == 1
    )
    assert catalog.count("lc") == 1
    removed = catalog.conn.execute(
        "SELECT thread_id FROM lc_threads WHERE removed_at IS NOT NULL"
    ).fetchall()
    assert removed == [(2,)]


def test_lc_details_queue_reads_changed_threads_before_never_read_ones(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-01-01T00:00:00Z"
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [
            _lc_row(1, 100, views=5),
            _lc_row(2, 200, views=999),
            _lc_row(3, 300, views=1),
            _lc_row(4, 50, views=10),
        ],
        now,
    )
    # Thread 1 was read at ts 90 and changed since; thread 4 is up to date.
    catalog.upsert_lc_details(1, 90, now, {"status": "ok"})
    catalog.upsert_lc_details(4, 50, now, {"status": "missing"})
    # since=250: thread 3 is new after the first run; 2 is an old never-read thread.
    assert catalog.lc_details_queue(250, 10) == [(3, 300), (1, 100), (2, 200)]
    assert catalog.lc_newest_ts() == 300
    assert catalog.count("lc_threads") == 2


def _lc_details(catalog, thread_id):
    return catalog.conn.execute(
        "SELECT thread_ts, status, overview, origin FROM lc_thread_details WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()


def test_imported_lc_details_are_read_as_never_read(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-01-01T00:00:00Z"
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [_lc_row(1, 100, views=5), _lc_row(2, 200, views=9)],
        now,
    )
    assert catalog.import_lc_details(
        {
            1: {"overview": "Atlas text"},
            2: {"overview": "Atlas two"},
            99: {"overview": "x"},
        },
        now,
    ) == (2, 0)
    assert _lc_details(catalog, 1) == (None, "ok", "Atlas text", "import")
    # since=250: neither is new; both count as never read, most viewed first.
    assert catalog.lc_details_queue(250, 10) == [(2, 200), (1, 100)]


def test_lc_page_details_replace_imported_ones_and_are_never_replaced(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-01-01T00:00:00Z"
    catalog.upsert("lc_threads", "thread_id", [_lc_row(1, 100)], now)
    catalog.import_lc_details({1: {"overview": "Atlas text"}}, now)
    catalog.upsert_lc_details(1, 100, now, {"status": "ok", "overview": "LC text"})
    assert _lc_details(catalog, 1) == (100, "ok", "LC text", None)
    assert catalog.import_lc_details({1: {"overview": "Atlas again"}}, now) == (0, 0)
    assert _lc_details(catalog, 1) == (100, "ok", "LC text", None)
    assert catalog.lc_details_queue(0, 10) == []


def test_a_refused_lc_page_keeps_imported_details(tmp_path):
    # A Plus thread a Standard account cannot open keeps Atlas's values.
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-01-01T00:00:00Z"
    catalog.upsert("lc_threads", "thread_id", [_lc_row(1, 100)], now)
    catalog.import_lc_details({1: {"overview": "Atlas text"}}, now)
    catalog.upsert_lc_details(1, 100, now, {"status": "missing"})
    assert _lc_details(catalog, 1) == (100, "ok", "Atlas text", "import")
    assert catalog.lc_details_queue(0, 10) == []
    # Importing again refreshes the values but keeps the read, so LC is not asked again.
    assert catalog.import_lc_details({1: {"overview": "Atlas new"}}, now) == (0, 1)
    assert _lc_details(catalog, 1) == (100, "ok", "Atlas new", "import")
    assert catalog.lc_details_queue(0, 10) == []
    # A change in the feed reads the page again.
    catalog.upsert("lc_threads", "thread_id", [_lc_row(1, 150)], now)
    assert catalog.lc_details_queue(0, 10) == [(1, 150)]


def test_a_failed_lc_reread_keeps_stored_page_details(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-01-01T00:00:00Z"
    catalog.upsert("lc_threads", "thread_id", [_lc_row(1, 100)], now)
    catalog.upsert_lc_details(1, 100, now, {"status": "ok", "overview": "LC text"})
    for status in ("missing", "unreadable"):
        catalog.upsert("lc_threads", "thread_id", [_lc_row(1, 150)], now)
        catalog.upsert_lc_details(1, 150, now, {"status": status})
        assert _lc_details(catalog, 1) == (150, "ok", "LC text", None)
        assert catalog.lc_details_queue(0, 10) == []


def test_a_failed_f95_reread_keeps_stored_details(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-01-01T00:00:00Z"
    catalog.upsert_f95_details(1, 100, now, {"status": "ok", "overview": "F95 text"})
    for status in ("missing", "unreadable"):
        catalog.upsert_f95_details(1, 150, now, {"status": status})
        row = catalog.conn.execute(
            "SELECT thread_ts, status, overview FROM f95_thread_details WHERE thread_id = 1"
        ).fetchone()
        assert row == (150, "ok", "F95 text")
    # A thread never read well still records the failed read.
    catalog.upsert_f95_details(2, 100, now, {"status": "missing"})
    row = catalog.conn.execute(
        "SELECT thread_ts, status FROM f95_thread_details WHERE thread_id = 2"
    ).fetchone()
    assert row == (100, "missing")


def test_old_catalogs_get_the_lc_details_origin_column(tmp_path):
    path = str(tmp_path / "catalog.sqlite")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE lc_thread_details (thread_id INTEGER PRIMARY KEY, thread_ts INTEGER, "
        "fetched_at TEXT NOT NULL, status TEXT NOT NULL, overview TEXT, developer TEXT, "
        "release_text TEXT, released_at TEXT, language TEXT, "
        "languages TEXT NOT NULL DEFAULT '[]', os TEXT NOT NULL DEFAULT '[]', "
        "external_ids TEXT NOT NULL DEFAULT '{}')"
    )
    conn.execute(
        "INSERT INTO lc_thread_details (thread_id, thread_ts, fetched_at, status) "
        "VALUES (1, 5, 'x', 'ok')"
    )
    conn.commit()
    conn.close()
    catalog = CatalogStore(path)
    assert _lc_details(catalog, 1) == (5, "ok", None, None)


def test_lc_prefix_names_are_kept_by_id(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert_lc_prefix_names({22: "Other", 29: "Other", 8: "RPGM"})
    catalog.upsert_lc_prefix_names({8: "RPGM"})
    assert catalog.lc_prefix_ids_by_name() == {"other": [22, 29], "rpgm": [8]}


def _removed(catalog):
    return [
        row[0]
        for row in catalog.conn.execute(
            "SELECT thread_id FROM lc_threads WHERE removed_at IS NOT NULL ORDER BY thread_id"
        )
    ]


def test_each_lc_sweep_marks_only_its_own_tier(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    jan, feb, mar = (
        "2026-01-01T00:00:00Z",
        "2026-02-01T00:00:00Z",
        "2026-03-01T00:00:00Z",
    )
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [
            _lc_row(1, 10, tier="Free"),
            _lc_row(24724, 20, tier="Premium"),
            _lc_row(900, 30, tier="Premium"),
        ],
        jan,
    )
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [_lc_row(5, 50, tier="Premium")],
        jan,
        origin="import",
    )
    # The Standard feed never lists the Plus threads; its sweep leaves them alone.
    catalog.upsert("lc_threads", "thread_id", [_lc_row(1, 10)], mar)
    assert catalog.mark_removed("lc", feb, mar) == 0
    # The Plus listing never lists Free threads; it marks Plus thread 900,
    # which two sweeps did not see, and never the imported one.
    catalog.upsert("lc_threads", "thread_id", [_lc_row(24724, 20, tier="Premium")], mar)
    assert catalog.mark_removed("lc_plus", feb, mar) == 1
    assert _removed(catalog) == [900]
    # The imported Plus thread still counts.
    assert catalog.count("lc") == 1 and catalog.count("lc_plus") == 2
    assert catalog.changes_since("lc_plus", mar) == (0, 1)
    assert catalog.changes_since("lc", mar) == (0, 0)


def test_premium_page_values_replace_the_version_and_tags(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-01-01T00:00:00Z"
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [
            _lc_row(24724, 100, tier="Premium", version=None, tags=["old"]),
            _lc_row(6, 100, tier="Free", version="0.5", tags=["3dcg"]),
        ],
        now,
    )
    catalog.upsert_lc_premium_details(
        24724, 100, now, {"status": "ok"}, "1.20", ["2dcg", "rpg"]
    )
    catalog.upsert_lc_premium_details(6, 100, now, {"status": "ok"}, "9.9", ["other"])
    found = dict(
        (row[0], row[1:])
        for row in catalog.conn.execute(
            "SELECT thread_id, version, tags FROM lc_threads"
        )
    )
    assert found[24724] == ("1.20", '["2dcg", "rpg"]')
    # A Free thread keeps the feed's version and tags.
    assert found[6] == ("0.5", '["3dcg"]')
    status = catalog.conn.execute(
        "SELECT status FROM lc_thread_details WHERE thread_id = 24724"
    ).fetchone()
    assert status == ("ok",)


def test_a_premium_page_read_for_an_older_date_keeps_the_version_empty(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-01-01T00:00:00Z"
    catalog.upsert(
        "lc_threads", "thread_id", [_lc_row(24724, 200, tier="Premium")], now
    )
    catalog.upsert_lc_premium_details(24724, 100, now, {"status": "ok"}, "1.20", [])
    assert catalog.conn.execute(
        "SELECT version FROM lc_threads WHERE thread_id = 24724"
    ).fetchone() == (None,)


def test_imported_or_refused_reads_are_marked_unread(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    now = "2026-01-01T00:00:00Z"
    catalog.upsert(
        "lc_threads",
        "thread_id",
        [
            _lc_row(1, 100, tier="Premium"),
            _lc_row(2, 100, tier="Premium"),
            _lc_row(3, 100, tier="Premium"),
            _lc_row(4, 100, tier="Premium"),
        ],
        now,
    )
    catalog.import_lc_details({1: {"overview": "Atlas"}}, now)
    # Thread 1: imported details kept after a refused read. Thread 2: refused.
    # Thread 3: read from LC's page. Thread 4: a refused re-read.
    catalog.upsert_lc_details(1, 100, now, {"status": "missing"})
    catalog.upsert_lc_details(2, 100, now, {"status": "missing"})
    catalog.upsert_lc_details(3, 100, now, {"status": "ok"})
    catalog.upsert_lc_details(4, 90, now, {"status": "ok", "overview": "LC text"})
    catalog.upsert_lc_details(4, 100, now, {"status": "missing"})
    assert catalog.lc_details_queue(0, 10) == []
    assert catalog.mark_lc_pages_unread([1, 2, 3, 4]) == 3
    assert catalog.lc_details_queue(10_000, 10) == [(1, 100), (2, 100), (4, 100)]
