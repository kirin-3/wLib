# SPDX-License-Identifier: GPL-3.0-or-later
import json
import sqlite3
import subprocess
import sys

from catalog_server.store import CatalogStore, StateStore, open_stores


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
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
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() == ("1",)
    for table in ("f95_threads", "dlsite_works", "ryuu_posts", "vndb_vns"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert {"first_seen", "last_seen", "removed_at"} <= columns
    conn.close()


def test_upsert_keeps_first_seen_and_clears_removed(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    row = {"thread_id": 1, "title": "A", "tags": [1, 2], "url": "u"}
    catalog.upsert("f95_threads", "thread_id", [row], "2026-01-01T00:00:00Z")
    catalog.conn.execute("UPDATE f95_threads SET removed_at = 'x'")
    catalog.upsert("f95_threads", "thread_id", [{**row, "title": "B"}], "2026-02-01T00:00:00Z")
    assert catalog.conn.execute(
        "SELECT title, tags, first_seen, last_seen, removed_at FROM f95_threads"
    ).fetchone() == ("B", json.dumps([1, 2]), "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", None)


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
    state.record_source(run_id, "f95", "error", 0, 1, "timed out", "2026-01-01T00:01:00Z")
    assert state.source_status("f95")["last_error"] == "timed out"
    state.record_source(run_id + 1, "f95", "ok", 5, 2, None, "2026-01-01T12:00:00Z")
    status = state.source_status("f95")
    assert status["last_error"] is None and status["last_success_at"] == "2026-01-01T12:00:00Z"


def test_imported_rows_survive_sweeps_until_the_source_lists_them(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    row = {"thread_id": 1, "title": "A", "url": "u"}
    catalog.upsert("f95_threads", "thread_id", [row], "2026-01-01T00:00:00Z", origin="import")
    assert catalog.mark_removed("f95", "2026-02-01T00:00:00Z", "2026-02-01T00:00:00Z") == 0
    catalog.upsert("f95_threads", "thread_id", [row], "2026-02-01T00:00:00Z")
    assert catalog.conn.execute("SELECT origin FROM f95_threads").fetchone() == (None,)
    assert catalog.mark_removed("f95", "2026-03-01T00:00:00Z", "2026-03-01T00:00:00Z") == 1


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
    conn.execute("CREATE TABLE f95_threads (thread_id INTEGER PRIMARY KEY, title TEXT, url TEXT, first_seen TEXT, last_seen TEXT)")
    conn.execute("INSERT INTO f95_threads VALUES (1, 'A', 'u', 'x', 'x')")
    conn.commit()
    conn.close()
    catalog = CatalogStore(str(path))
    assert catalog.conn.execute("SELECT screens FROM f95_threads").fetchone() == (None,)
    assert catalog.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() == ("1",)
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
    catalog.upsert("lc_threads", "thread_id", [_lc_row(1, 10), _lc_row(2, 20)], "2026-01-01T00:00:00Z")
    catalog.upsert(
        "lc_threads", "thread_id", [_lc_row(3, 30, tier="Premium")], "2026-01-01T00:00:00Z",
        origin="import",
    )
    catalog.upsert("lc_threads", "thread_id", [_lc_row(1, 10)], "2026-02-01T00:00:00Z")
    assert catalog.count("lc") == 3
    # Thread 2 was not seen after the sweep start; thread 3 is imported.
    assert catalog.mark_removed("lc", "2026-01-15T00:00:00Z", "2026-02-01T00:00:00Z") == 1
    assert catalog.count("lc") == 2
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
        [_lc_row(1, 100, views=5), _lc_row(2, 200, views=999), _lc_row(3, 300, views=1),
         _lc_row(4, 50, views=10)],
        now,
    )
    # Thread 1 was read at ts 90 and changed since; thread 4 is up to date.
    catalog.upsert_lc_details(1, 90, now, {"status": "ok"})
    catalog.upsert_lc_details(4, 50, now, {"status": "missing"})
    # since=250: thread 3 is new after the first run; 2 is an old never-read thread.
    assert catalog.lc_details_queue(250, 10) == [(3, 300), (1, 100), (2, 200)]
    assert catalog.lc_newest_ts() == 300
    assert catalog.count("lc_threads") == 2


def test_lc_prefix_names_are_kept_by_id(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert_lc_prefix_names({22: "Other", 29: "Other", 8: "RPGM"})
    catalog.upsert_lc_prefix_names({8: "RPGM"})
    assert catalog.lc_prefix_ids_by_name() == {"other": [22, 29], "rpgm": [8]}
