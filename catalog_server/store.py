# SPDX-License-Identifier: GPL-3.0-or-later
"""The two builder databases: `catalog.sqlite` (published) and `state.sqlite` (private)."""

import json
import os
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import cast

from core.catalog_format import META_SCHEMA_VERSION, SCHEMA_VERSION

CATALOG_FILE = "catalog.sqlite"
STATE_FILE = "state.sqlite"

_SEEN_COLUMNS = """
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    removed_at TEXT
"""

CATALOG_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS f95_threads (
    thread_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    creator TEXT,
    version TEXT,
    updated_at TEXT,
    ts INTEGER,
    views INTEGER,
    likes INTEGER,
    rating REAL,
    prefixes TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    cover_url TEXT,
    url TEXT NOT NULL,
    {_SEEN_COLUMNS}
);
CREATE TABLE IF NOT EXISTS dlsite_works (
    product_id TEXT PRIMARY KEY,
    site TEXT,
    title TEXT NOT NULL,
    circle_id TEXT,
    circle TEXT,
    work_type TEXT,
    age_category INTEGER,
    genres TEXT NOT NULL DEFAULT '[]',
    released_at TEXT,
    cover_url TEXT,
    url TEXT NOT NULL,
    {_SEEN_COLUMNS}
);
CREATE TABLE IF NOT EXISTS ryuu_posts (
    post_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    raw_title TEXT NOT NULL,
    developer TEXT,
    version TEXT,
    categories TEXT NOT NULL DEFAULT '[]',
    rj_codes TEXT NOT NULL DEFAULT '[]',
    steam_ids TEXT NOT NULL DEFAULT '[]',
    vndb_ids TEXT NOT NULL DEFAULT '[]',
    published_at TEXT,
    updated_at TEXT,
    cover_url TEXT,
    url TEXT NOT NULL,
    {_SEEN_COLUMNS}
);
CREATE TABLE IF NOT EXISTS vndb_vns (
    vn_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    alttitle TEXT,
    developers TEXT NOT NULL DEFAULT '[]',
    released TEXT,
    olang TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    cover_url TEXT,
    url TEXT NOT NULL,
    {_SEEN_COLUMNS}
);
CREATE TABLE IF NOT EXISTS vndb_releases (
    release_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    released TEXT,
    vn_ids TEXT NOT NULL DEFAULT '[]',
    dlsite_ids TEXT NOT NULL DEFAULT '[]',
    steam_ids TEXT NOT NULL DEFAULT '[]',
    url TEXT NOT NULL,
    {_SEEN_COLUMNS}
);
CREATE TABLE IF NOT EXISTS links (
    src_a TEXT NOT NULL,
    id_a TEXT NOT NULL,
    src_b TEXT NOT NULL,
    id_b TEXT NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY (src_a, id_a, src_b, id_b, origin)
);
CREATE INDEX IF NOT EXISTS links_b ON links (src_b, id_b);
CREATE TABLE IF NOT EXISTS f95_tag_names (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS f95_prefix_names (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    source TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (source, key)
);
CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY,
    site TEXT NOT NULL,
    at REAL NOT NULL,
    status INTEGER
);
CREATE INDEX IF NOT EXISTS request_log_site_at ON request_log (site, at);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    published TEXT
);
CREATE TABLE IF NOT EXISTS run_sources (
    run_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    rows INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    PRIMARY KEY (run_id, source)
);
-- DLsite IDs a listing showed that are not adult, so incremental runs treat
-- them as known instead of fetching their details again.
CREATE TABLE IF NOT EXISTS dlsite_skipped (
    product_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_error TEXT,
    last_error_at TEXT,
    sweep_completed_at TEXT
);
"""

# Tables each source owns, for removal marking and manifest counts.
SOURCE_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "f95": (("f95_threads", "thread_id"),),
    "dlsite": (("dlsite_works", "product_id"),),
    "ryuugames": (("ryuu_posts", "post_id"),),
    "vndb": (("vndb_vns", "vn_id"), ("vndb_releases", "release_id")),
}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: object) -> object:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value


def _rows(cursor: sqlite3.Cursor) -> list[tuple[object, ...]]:
    return cast(list[tuple[object, ...]], cursor.fetchall())


class CatalogStore:
    def __init__(self, path: str) -> None:
        self.path: str = path
        self.conn: sqlite3.Connection = sqlite3.connect(path)
        _ = self.conn.execute("PRAGMA journal_mode=WAL")
        _ = self.conn.executescript(CATALOG_SCHEMA)
        self.set_meta(META_SCHEMA_VERSION, str(SCHEMA_VERSION))

    def close(self) -> None:
        self.conn.close()

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            _ = self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                + "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def upsert(
        self,
        table: str,
        key: str,
        rows: Sequence[Mapping[str, object]],
        now: str,
    ) -> int:
        """Insert or update rows; keeps `first_seen` and clears `removed_at`."""
        if not rows:
            return 0
        columns = list(rows[0])
        names = ", ".join([*columns, "first_seen", "last_seen", "removed_at"])
        marks = ", ".join(["?"] * (len(columns) + 3))
        updates = ", ".join(
            f"{column} = excluded.{column}" for column in columns if column != key
        )
        sql = (
            f"INSERT INTO {table} ({names}) VALUES ({marks}) "
            + f"ON CONFLICT({key}) DO UPDATE SET {updates}, "
            + "last_seen = excluded.last_seen, removed_at = NULL"
        )
        with self.conn:
            _ = self.conn.executemany(
                sql,
                [
                    [_json(row[column]) for column in columns] + [now, now, None]
                    for row in rows
                ],
            )
        return len(rows)

    def existing_keys(self, table: str, key: str, values: Iterable[object]) -> set[str]:
        wanted = [str(value) for value in values]
        if not wanted:
            return set()
        marks = ", ".join(["?"] * len(wanted))
        rows = self.conn.execute(
            f"SELECT {key} FROM {table} WHERE CAST({key} AS TEXT) IN ({marks})",
            wanted,
        )
        return {str(row[0]) for row in _rows(rows)}

    def touch(self, table: str, key: str, values: Iterable[object], now: str) -> None:
        """Record that a listing still shows these rows without refetching them."""
        wanted = [str(value) for value in values]
        if not wanted:
            return
        marks = ", ".join(["?"] * len(wanted))
        with self.conn:
            _ = self.conn.execute(
                f"UPDATE {table} SET last_seen = ?, removed_at = NULL "
                + f"WHERE CAST({key} AS TEXT) IN ({marks})",
                [now, *wanted],
            )

    def mark_removed(self, source: str, sweep_started_at: str, now: str) -> int:
        """Mark rows a completed sweep did not see; reappearing rows are cleared by upsert."""
        marked = 0
        with self.conn:
            for table, _key in SOURCE_TABLES[source]:
                marked += self.conn.execute(
                    f"UPDATE {table} SET removed_at = ? "
                    + "WHERE removed_at IS NULL AND last_seen < ?",
                    (now, sweep_started_at),
                ).rowcount
        return marked

    def count(self, source: str) -> int:
        table, _key = SOURCE_TABLES[source][0]
        row = cast(
            tuple[int],
            self.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE removed_at IS NULL"
            ).fetchone(),
        )
        return row[0]

    def replace_names(
        self,
        tags: Mapping[int, str],
        prefixes: Mapping[int, tuple[str, str | None]],
    ) -> None:
        with self.conn:
            _ = self.conn.execute("DELETE FROM f95_tag_names")
            _ = self.conn.execute("DELETE FROM f95_prefix_names")
            _ = self.conn.executemany(
                "INSERT INTO f95_tag_names (id, name) VALUES (?, ?)", tags.items()
            )
            _ = self.conn.executemany(
                "INSERT INTO f95_prefix_names (id, name, category) VALUES (?, ?, ?)",
                [(key, name, category) for key, (name, category) in prefixes.items()],
            )


class StateStore:
    def __init__(self, path: str) -> None:
        self.conn: sqlite3.Connection = sqlite3.connect(path)
        _ = self.conn.execute("PRAGMA journal_mode=WAL")
        _ = self.conn.executescript(STATE_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def get(self, source: str, key: str, default: object = None) -> object:
        row = cast(
            tuple[str] | None,
            self.conn.execute(
                "SELECT value FROM kv WHERE source = ? AND key = ?", (source, key)
            ).fetchone(),
        )
        return default if row is None else cast(object, json.loads(row[0]))

    def put(self, source: str, key: str, value: object) -> None:
        with self.conn:
            if value is None:
                _ = self.conn.execute(
                    "DELETE FROM kv WHERE source = ? AND key = ?", (source, key)
                )
                return
            _ = self.conn.execute(
                "INSERT INTO kv (source, key, value) VALUES (?, ?, ?) "
                + "ON CONFLICT(source, key) DO UPDATE SET value = excluded.value",
                (source, key, json.dumps(value)),
            )

    def items(self, source: str) -> dict[str, object]:
        rows = cast(
            list[tuple[str, str]],
            self.conn.execute(
                "SELECT key, value FROM kv WHERE source = ? ORDER BY key", (source,)
            ).fetchall(),
        )
        return {key: cast(object, json.loads(value)) for key, value in rows}

    # Request log, read by the pacer so budgets hold across runs.
    def log_request(self, site: str, at: float, status: int | None) -> None:
        with self.conn:
            _ = self.conn.execute(
                "INSERT INTO request_log (site, at, status) VALUES (?, ?, ?)",
                (site, at, status),
            )
            # Keep a day of history; budgets look back at most an hour.
            _ = self.conn.execute(
                "DELETE FROM request_log WHERE at < ?", (at - 86400.0,)
            )

    def request_times(self, site: str, since: float) -> list[float]:
        rows = cast(
            list[tuple[float]],
            self.conn.execute(
                "SELECT at FROM request_log WHERE site = ? AND at >= ? ORDER BY at",
                (site, since),
            ).fetchall(),
        )
        return [row[0] for row in rows]

    def last_request_at(self, site: str) -> float | None:
        row = cast(
            tuple[float | None],
            self.conn.execute(
                "SELECT MAX(at) FROM request_log WHERE site = ?", (site,)
            ).fetchone(),
        )
        return row[0]

    # Run history.
    def start_run(self, now: str) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO runs (started_at) VALUES (?)", (now,)
            )
        return int(cursor.lastrowid or 0)

    def finish_run(self, run_id: int, now: str, published: str | None) -> None:
        with self.conn:
            _ = self.conn.execute(
                "UPDATE runs SET finished_at = ?, published = ? WHERE id = ?",
                (now, published, run_id),
            )

    def record_source(
        self,
        run_id: int,
        source: str,
        status: str,
        rows: int,
        requests: int,
        detail: str | None,
        now: str,
    ) -> None:
        with self.conn:
            _ = self.conn.execute(
                "INSERT INTO run_sources (run_id, source, status, rows, requests, detail) "
                + "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, source) DO UPDATE SET "
                + "status = excluded.status, rows = rows + excluded.rows, "
                + "requests = requests + excluded.requests, detail = excluded.detail",
                (run_id, source, status, rows, requests, detail),
            )
            _ = self.conn.execute(
                "INSERT INTO source_status (source) VALUES (?) ON CONFLICT DO NOTHING",
                (source,),
            )
            if status == "error":
                _ = self.conn.execute(
                    "UPDATE source_status SET last_error = ?, last_error_at = ? "
                    + "WHERE source = ?",
                    (detail, now, source),
                )
            else:
                # A rate-limited run still collected data up to the limit.
                _ = self.conn.execute(
                    "UPDATE source_status SET last_success_at = ?, last_error = NULL, "
                    + "last_error_at = NULL WHERE source = ?",
                    (now, source),
                )

    def skipped_dlsite(self, ids: Iterable[str]) -> set[str]:
        wanted = list(ids)
        if not wanted:
            return set()
        marks = ", ".join(["?"] * len(wanted))
        rows = self.conn.execute(
            f"SELECT product_id FROM dlsite_skipped WHERE product_id IN ({marks})",
            wanted,
        )
        return {str(row[0]) for row in _rows(rows)}

    def add_skipped_dlsite(self, ids: Iterable[str]) -> None:
        with self.conn:
            _ = self.conn.executemany(
                "INSERT OR IGNORE INTO dlsite_skipped (product_id) VALUES (?)",
                [(value,) for value in ids],
            )

    def mark_sweep_completed(self, source: str, now: str) -> None:
        with self.conn:
            _ = self.conn.execute(
                "INSERT INTO source_status (source, sweep_completed_at) VALUES (?, ?) "
                + "ON CONFLICT(source) DO UPDATE SET sweep_completed_at = excluded.sweep_completed_at",
                (source, now),
            )

    def source_status(self, source: str) -> dict[str, str | None]:
        row = cast(
            tuple[str | None, str | None, str | None, str | None] | None,
            self.conn.execute(
                "SELECT last_success_at, last_error, last_error_at, sweep_completed_at "
                + "FROM source_status WHERE source = ?",
                (source,),
            ).fetchone(),
        )
        keys = ("last_success_at", "last_error", "last_error_at", "sweep_completed_at")
        if row is None:
            return dict.fromkeys(keys)
        return dict(zip(keys, row))

    def recent_runs(self, limit: int = 5) -> list[dict[str, object]]:
        runs = _rows(self.conn.execute(
            "SELECT id, started_at, finished_at, published FROM runs "
            + "ORDER BY id DESC LIMIT ?",
            (limit,),
        ))
        result: list[dict[str, object]] = []
        for run_id, started_at, finished_at, published in runs:
            sources = _rows(self.conn.execute(
                "SELECT source, status, rows, requests, detail FROM run_sources "
                + "WHERE run_id = ? ORDER BY source",
                (run_id,),
            ))
            result.append(
                {
                    "id": run_id,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "published": published,
                    "sources": [
                        {
                            "source": source,
                            "status": status,
                            "rows": rows,
                            "requests": requests,
                            "detail": detail,
                        }
                        for source, status, rows, requests, detail in sources
                    ],
                }
            )
        return result


def open_stores(data_dir: str) -> tuple[CatalogStore, StateStore]:
    os.makedirs(data_dir, exist_ok=True)
    return (
        CatalogStore(os.path.join(data_dir, CATALOG_FILE)),
        StateStore(os.path.join(data_dir, STATE_FILE)),
    )
