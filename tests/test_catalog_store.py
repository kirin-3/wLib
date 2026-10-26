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
