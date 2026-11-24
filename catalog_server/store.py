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
# `origin` of rows and details copied from an Atlas database.
IMPORT_ORIGIN = "import"
# Version of the developer-ID kinds kept from first posts. Details stored with
# an older version are read again (2 added DLsite and FANZA work IDs).
IDS_VERSION = 2

# `origin` is set on rows imported from elsewhere (NULL when the source
# itself listed it). Imported rows are never marked removed; the first time
# the source lists one, it becomes an ordinary row.
_SEEN_COLUMNS = """
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    removed_at TEXT,
    origin TEXT
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
    screens TEXT,
    url TEXT NOT NULL,
    {_SEEN_COLUMNS}
);
CREATE TABLE IF NOT EXISTS dlsite_works (
    product_id TEXT PRIMARY KEY,
    site TEXT,
    title TEXT NOT NULL,
    title_en TEXT,
    circle_id TEXT,
    circle TEXT,
    circle_en TEXT,
    work_type TEXT,
    age_category INTEGER,
    genres TEXT NOT NULL DEFAULT '[]',
    genres_en TEXT,
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
-- First-post details of F95 threads; `thread_ts` is the feed `ts` when the
-- page was read, so a newer `ts` in f95_threads means the thread changed.
-- `kept_after_failure` means this row's details came from an earlier read.
CREATE TABLE IF NOT EXISTS f95_thread_details (
    thread_id INTEGER PRIMARY KEY,
    thread_ts INTEGER,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL,
    overview TEXT,
    rating_count INTEGER,
    rating_count_attempted INTEGER NOT NULL DEFAULT 0,
    original_title TEXT,
    aliases TEXT NOT NULL DEFAULT '[]',
    language TEXT,
    languages TEXT NOT NULL DEFAULT '[]',
    external_ids TEXT NOT NULL DEFAULT '{{}}',
    kept_after_failure INTEGER NOT NULL DEFAULT 0,
    ids_version INTEGER NOT NULL DEFAULT 0
);
-- LewdCorner threads from the latest-updates feed (or an Atlas import).
-- `tier` is 'Premium' for a LewdCorner Plus thread and 'Free' otherwise.
CREATE TABLE IF NOT EXISTS lc_threads (
    thread_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    creator TEXT,
    version TEXT,
    updated_at TEXT,
    ts INTEGER,
    node_id INTEGER,
    tier TEXT NOT NULL DEFAULT 'Free',
    prefixes TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    platforms TEXT NOT NULL DEFAULT '[]',
    cover_url TEXT,
    screens TEXT NOT NULL DEFAULT '[]',
    views INTEGER,
    likes INTEGER,
    rating REAL,
    url TEXT NOT NULL,
    {_SEEN_COLUMNS}
);
-- LC prefix names by ID. Never cleared: the feed names prefixes inline, and
-- LC uses some names on two IDs.
CREATE TABLE IF NOT EXISTS lc_prefix_names (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
-- First-post details of LC threads, like f95_thread_details. `release_text`
-- is the release date as shown; `released_at` is its ISO date when it parses.
-- `origin = 'import'` marks values copied from Atlas, which the crawler
-- replaces; `thread_ts` NULL means LC's own page was never read.
CREATE TABLE IF NOT EXISTS lc_thread_details (
    thread_id INTEGER PRIMARY KEY,
    thread_ts INTEGER,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL,
    overview TEXT,
    developer TEXT,
    release_text TEXT,
    released_at TEXT,
    language TEXT,
    languages TEXT NOT NULL DEFAULT '[]',
    os TEXT NOT NULL DEFAULT '[]',
    external_ids TEXT NOT NULL DEFAULT '{{}}',
    origin TEXT,
    kept_after_failure INTEGER NOT NULL DEFAULT 0,
    ids_version INTEGER NOT NULL DEFAULT 0
);
-- One row per current catalog entry: the group of entries that are the
-- same game across sites (see catalog_server/groups.py). Rebuilt each run.
CREATE TABLE IF NOT EXISTS game_groups (
    src TEXT NOT NULL,
    id TEXT NOT NULL,
    group_key TEXT NOT NULL,
    PRIMARY KEY (src, id)
);
CREATE INDEX IF NOT EXISTS game_groups_key ON game_groups (group_key);
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
-- Server-side map of DLsite genre IDs to English names (never published).
CREATE TABLE IF NOT EXISTS dlsite_genres (
    genre_id TEXT PRIMARY KEY,
    name_en TEXT,
    checked_at TEXT
);
-- Link pairs from the last `tools.import_links` run (never published; only
-- the links made from them are). Each import replaces the whole list.
CREATE TABLE IF NOT EXISTS imported_links (
    src_a TEXT NOT NULL,
    id_a TEXT NOT NULL,
    src_b TEXT NOT NULL,
    id_b TEXT NOT NULL,
    PRIMARY KEY (src_a, id_a, src_b, id_b)
);
CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_error TEXT,
    last_error_at TEXT,
    sweep_completed_at TEXT
);
"""

# Builder-only detail steps, counted by their details table.
DETAIL_TABLES: dict[str, str] = {
    "f95_threads": "f95_thread_details",
    "lc_threads": "lc_thread_details",
}

# Tables each source owns, for removal marking and manifest counts.
SOURCE_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "f95": (("f95_threads", "thread_id"),),
    "lc": (("lc_threads", "thread_id"),),
    # The guest Plus listings share lc_threads with the feed; see SOURCE_ROWS.
    "lc_plus": (("lc_threads", "thread_id"),),
    "dlsite": (("dlsite_works", "product_id"),),
    "ryuugames": (("ryuu_posts", "post_id"),),
    "vndb": (("vndb_vns", "vn_id"), ("vndb_releases", "release_id")),
}

# The rows of a shared table that each source owns. A Standard feed never
# lists Plus threads and the Plus listings never list Free ones, so each
# sweep marks, counts and reports only its own tier.
SOURCE_ROWS: dict[str, str] = {
    "lc": "tier = 'Free'",
    "lc_plus": "tier = 'Premium'",
}


def _owned(source: str) -> str:
    """An SQL condition (starting with AND) for the rows `source` owns, or ''."""
    condition = SOURCE_ROWS.get(source)
    return f" AND {condition}" if condition else ""


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
        self._add_origin_columns()
        columns = {
            str(row[1])
            for row in _rows(self.conn.execute("PRAGMA table_info(f95_threads)"))
        }
        if "screens" not in columns:
            with self.conn:
                _ = self.conn.execute("ALTER TABLE f95_threads ADD COLUMN screens TEXT")
        details = {
            str(row[1])
            for row in _rows(self.conn.execute("PRAGMA table_info(f95_thread_details)"))
        }
        if "rating_count" not in details:
            with self.conn:
                _ = self.conn.execute(
                    "ALTER TABLE f95_thread_details ADD COLUMN rating_count INTEGER"
                )
        if "languages" not in details:
            with self.conn:
                _ = self.conn.execute(
                    "ALTER TABLE f95_thread_details ADD COLUMN languages TEXT NOT NULL DEFAULT '[]'"
                )
                # Rows from before the column have the older external_ids form; read them again.
                _ = self.conn.execute("DELETE FROM f95_thread_details")
        lc_details = {
            str(row[1])
            for row in _rows(self.conn.execute("PRAGMA table_info(lc_thread_details)"))
        }
        if "origin" not in lc_details:
            with self.conn:
                _ = self.conn.execute(
                    "ALTER TABLE lc_thread_details ADD COLUMN origin TEXT"
                )
        with self.conn:
            for table, columns in (
                ("f95_thread_details", details),
                ("lc_thread_details", lc_details),
            ):
                if "kept_after_failure" not in columns:
                    _ = self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN kept_after_failure INTEGER NOT NULL DEFAULT 0"
                    )
        with self.conn:
            for table, columns in (
                ("f95_thread_details", details),
                ("lc_thread_details", lc_details),
            ):
                if "ids_version" not in columns:
                    # Rows read before DLsite and FANZA IDs were kept start at 0,
                    # so the details steps read them again.
                    _ = self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN ids_version INTEGER NOT NULL DEFAULT 0"
                    )
        if "rating_count_attempted" not in details:
            # Rows read before review counts existed start at 0, so the queue backfills them.
            with self.conn:
                _ = self.conn.execute(
                    "ALTER TABLE f95_thread_details ADD COLUMN "
                    + "rating_count_attempted INTEGER NOT NULL DEFAULT 0"
                )
        dlsite = {
            str(row[1])
            for row in _rows(self.conn.execute("PRAGMA table_info(dlsite_works)"))
        }
        with self.conn:
            for column in ("title_en", "circle_en", "genres_en"):
                if column not in dlsite:
                    _ = self.conn.execute(
                        f"ALTER TABLE dlsite_works ADD COLUMN {column} TEXT"
                    )
        self.set_meta(META_SCHEMA_VERSION, str(SCHEMA_VERSION))

    def _add_origin_columns(self) -> None:
        """Catalogs created before `origin` existed get the column added."""
        with self.conn:
            for tables in SOURCE_TABLES.values():
                for table, _key in tables:
                    columns = {
                        str(row[1])
                        for row in _rows(
                            self.conn.execute(f"PRAGMA table_info({table})")
                        )
                    }
                    if "origin" not in columns:
                        _ = self.conn.execute(
                            f"ALTER TABLE {table} ADD COLUMN origin TEXT"
                        )

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
        origin: str | None = None,
    ) -> int:
        """Insert or update rows; keeps `first_seen` and clears `removed_at`.

        Sources upsert with no origin, which turns imported rows into ordinary ones.
        """
        if not rows:
            return 0
        columns = list(rows[0])
        names = ", ".join([*columns, "first_seen", "last_seen", "removed_at", "origin"])
        marks = ", ".join(["?"] * (len(columns) + 4))
        updates = ", ".join(
            f"{column} = excluded.{column}" for column in columns if column != key
        )
        sql = (
            f"INSERT INTO {table} ({names}) VALUES ({marks}) "
            + f"ON CONFLICT({key}) DO UPDATE SET {updates}, "
            + "last_seen = excluded.last_seen, removed_at = NULL, origin = excluded.origin"
        )
        with self.conn:
            _ = self.conn.executemany(
                sql,
                [
                    [_json(row[column]) for column in columns]
                    + [now, now, None, origin]
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
                f"UPDATE {table} SET last_seen = ?, removed_at = NULL, origin = NULL "
                + f"WHERE CAST({key} AS TEXT) IN ({marks})",
                [now, *wanted],
            )

    def mark_removed(self, source: str, sweep_started_at: str, now: str) -> int:
        """Mark rows a completed sweep did not see; reappearing rows are cleared by upsert.

        Imported rows are skipped: the source never listing them is expected.
        """
        marked = 0
        with self.conn:
            for table, _key in SOURCE_TABLES[source]:
                marked += self.conn.execute(
                    f"UPDATE {table} SET removed_at = ? "
                    + "WHERE removed_at IS NULL AND origin IS NULL AND last_seen < ?"
                    + _owned(source),
                    (now, sweep_started_at),
                ).rowcount
        return marked

    def f95_newest_ts(self) -> int | None:
        row = cast(
            tuple[int | None],
            self.conn.execute("SELECT MAX(ts) FROM f95_threads").fetchone(),
        )
        return row[0]

    def f95_details_queue(self, since_ts: int, limit: int) -> list[tuple[int, int]]:
        """Threads to read: changed ones newest first, then never-read ones most viewed first.

        The backfill also visits successful details with no count attempt.
        Details read before the current `IDS_VERSION` come last.
        Changed means a feed `ts` newer than the stored details, or a thread
        never read whose `ts` is newer than `since_ts`.
        """
        changed = "(t.ts > d.thread_ts OR (d.thread_id IS NULL AND t.ts > :since))"
        # Read before DLsite and FANZA IDs were kept, and not due for any other reason.
        old_ids = (
            "(d.status = 'ok' AND d.ids_version < :ids AND d.rating_count_attempted != 0 "
            + "AND t.ts <= d.thread_ts)"
        )
        rows = self.conn.execute(
            "SELECT t.thread_id, t.ts FROM f95_threads t "
            + "LEFT JOIN f95_thread_details d ON d.thread_id = t.thread_id "
            + "WHERE t.removed_at IS NULL AND t.ts IS NOT NULL "
            + "AND (d.thread_id IS NULL OR t.ts > d.thread_ts "
            + "OR (d.status = 'ok' AND d.rating_count_attempted = 0) "
            + "OR (d.status = 'ok' AND d.ids_version < :ids)) "
            + f"ORDER BY CASE WHEN {changed} THEN 0 WHEN {old_ids} THEN 2 ELSE 1 END, "
            + f"CASE WHEN {changed} THEN t.ts ELSE COALESCE(t.views, 0) END DESC, "
            + "t.thread_id LIMIT :limit",
            {"since": since_ts, "limit": limit, "ids": IDS_VERSION},
        )
        return cast(list[tuple[int, int]], rows.fetchall())

    def upsert_f95_details(
        self, thread_id: int, thread_ts: int, now: str, details: Mapping[str, object]
    ) -> int:
        """Store what F95's page gave. A page without details (`missing`,
        `unreadable`) keeps stored `ok` details, including the review count,
        and only records the read."""
        with self.conn:
            if details["status"] != "ok":
                kept = self.conn.execute(
                    "UPDATE f95_thread_details SET thread_ts = ?, fetched_at = ?, kept_after_failure = 1, "
                    + "rating_count_attempted = 1, ids_version = ? "
                    + "WHERE thread_id = ? AND status = 'ok'",
                    (thread_ts, now, IDS_VERSION, thread_id),
                )
                if kept.rowcount:
                    return 1
            _ = self.conn.execute(
                "INSERT OR REPLACE INTO f95_thread_details (thread_id, thread_ts, fetched_at, "
                + "status, overview, original_title, aliases, language, languages, external_ids, "
                + "rating_count, rating_count_attempted, ids_version) "
                + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    thread_id,
                    thread_ts,
                    now,
                    details["status"],
                    details.get("overview"),
                    details.get("original_title"),
                    json.dumps(details.get("aliases") or [], ensure_ascii=False),
                    details.get("language"),
                    json.dumps(details.get("languages") or [], ensure_ascii=False),
                    json.dumps(details.get("external_ids") or {}, ensure_ascii=False),
                    details.get("rating_count") if details["status"] == "ok" else None,
                    IDS_VERSION,
                ),
            )
        return 1

    def lc_newest_ts(self) -> int | None:
        row = cast(
            tuple[int | None],
            self.conn.execute("SELECT MAX(ts) FROM lc_threads").fetchone(),
        )
        return row[0]

    def lc_details_queue(self, since_ts: int, limit: int) -> list[tuple[int, int]]:
        """Like `f95_details_queue`: changed threads newest first, then never-read ones.

        Details imported from Atlas and never checked on LC (`thread_ts` NULL)
        count as never read. Details read before the current `IDS_VERSION`
        come last."""
        read = "d.thread_ts IS NOT NULL"
        changed = f"({read} OR t.ts > :since)"
        # Read on LC before DLsite and FANZA IDs were kept, and not changed since.
        old_ids = (
            f"({read} AND t.ts <= d.thread_ts AND d.status = 'ok' "
            + "AND d.origin IS NULL AND d.ids_version < :ids)"
        )
        rows = self.conn.execute(
            "SELECT t.thread_id, t.ts FROM lc_threads t "
            + "LEFT JOIN lc_thread_details d ON d.thread_id = t.thread_id "
            + "WHERE t.removed_at IS NULL AND t.ts IS NOT NULL "
            + f"AND (NOT ({read}) OR t.ts > d.thread_ts OR {old_ids}) "
            + f"ORDER BY CASE WHEN {old_ids} THEN 2 WHEN {changed} THEN 0 ELSE 1 END, "
            + f"CASE WHEN {changed} THEN t.ts ELSE COALESCE(t.views, 0) END DESC, "
            + "t.thread_id LIMIT :limit",
            {"since": since_ts, "limit": limit, "ids": IDS_VERSION},
        )
        return cast(list[tuple[int, int]], rows.fetchall())

    def upsert_lc_details(
        self, thread_id: int, thread_ts: int, now: str, details: Mapping[str, object]
    ) -> int:
        """Store what LC's page gave. A page without details (`missing`,
        `unreadable`) keeps stored `ok` or imported Atlas values and only
        records the read."""
        with self.conn:
            self._write_lc_details(thread_id, thread_ts, now, details)
        return 1

    def upsert_lc_premium_details(
        self,
        thread_id: int,
        thread_ts: int,
        now: str,
        details: Mapping[str, object],
        version: str | None,
        tags: Sequence[str],
    ) -> int:
        """Like `upsert_lc_details`, and in the same transaction the page's
        version and tags for a Premium thread, whose feed is not readable.

        The version is written only while the thread still has the `ts` the
        page was read for, so a version always belongs to its update date.
        Free threads keep the feed's version and tags."""
        with self.conn:
            self._write_lc_details(thread_id, thread_ts, now, details)
            if details["status"] == "ok":
                _ = self.conn.execute(
                    "UPDATE lc_threads SET version = ?, tags = ? "
                    + "WHERE thread_id = ? AND tier = 'Premium' AND ts = ?",
                    (
                        version,
                        json.dumps(list(tags), ensure_ascii=False),
                        thread_id,
                        thread_ts,
                    ),
                )
        return 1

    def mark_lc_pages_unread(
        self, thread_ids: Iterable[int], *, any_status: bool = False
    ) -> int:
        """Make the details step read these threads again as never read, when
        their details are imported from Atlas or LC refused the page before
        (or always, with `any_status`)."""
        wanted = list(thread_ids)
        if not wanted:
            return 0
        marks = ", ".join(["?"] * len(wanted))
        condition = (
            ""
            if any_status
            else " AND (origin = ? OR status != 'ok' OR kept_after_failure = 1)"
        )
        with self.conn:
            return self.conn.execute(
                f"UPDATE lc_thread_details SET thread_ts = NULL WHERE thread_id IN ({marks})"
                + condition,
                wanted if any_status else [*wanted, IMPORT_ORIGIN],
            ).rowcount

    def lc_listing_state(
        self, thread_ids: Iterable[int]
    ) -> dict[int, tuple[int | None, str, str | None]]:
        """`ts`, tier and origin of the stored LC threads among `thread_ids`."""
        wanted = list(thread_ids)
        if not wanted:
            return {}
        marks = ", ".join(["?"] * len(wanted))
        found = cast(
            list[tuple[int, int | None, str, str | None]],
            self.conn.execute(
                f"SELECT thread_id, ts, tier, origin FROM lc_threads WHERE thread_id IN ({marks})",
                wanted,
            ).fetchall(),
        )
        return {thread_id: (ts, tier, origin) for thread_id, ts, tier, origin in found}

    def lc_premium_ids(self, thread_ids: Iterable[int]) -> set[int]:
        """The `Premium` threads among `thread_ids`."""
        return {
            thread_id
            for thread_id, (_ts, tier, _origin) in self.lc_listing_state(
                thread_ids
            ).items()
            if tier == "Premium"
        }

    def _write_lc_details(
        self, thread_id: int, thread_ts: int, now: str, details: Mapping[str, object]
    ) -> None:
        """The writes of `upsert_lc_details`; the caller holds the transaction."""
        if details["status"] != "ok":
            kept = self.conn.execute(
                "UPDATE lc_thread_details SET thread_ts = ?, fetched_at = ?, kept_after_failure = 1, "
                + "ids_version = ? WHERE thread_id = ? AND (origin = ? OR status = 'ok')",
                (thread_ts, now, IDS_VERSION, thread_id, IMPORT_ORIGIN),
            )
            if kept.rowcount:
                return
        _ = self.conn.execute(
            "INSERT OR REPLACE INTO lc_thread_details (thread_id, thread_ts, fetched_at, "
            + "status, overview, developer, release_text, released_at, language, "
            + "languages, os, external_ids, origin, ids_version) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                thread_id,
                thread_ts,
                now,
                details["status"],
                details.get("overview"),
                details.get("developer"),
                details.get("release_text"),
                details.get("released_at"),
                details.get("language"),
                json.dumps(details.get("languages") or [], ensure_ascii=False),
                json.dumps(details.get("os") or [], ensure_ascii=False),
                json.dumps(details.get("external_ids") or {}, ensure_ascii=False),
                IDS_VERSION,
            ),
        )

    def import_lc_details(
        self, rows: Mapping[int, Mapping[str, object]], now: str
    ) -> tuple[int, int]:
        """Add Atlas details for LC threads whose page was never stored, with
        `origin = 'import'`. Details LC's page gave are never replaced, and an
        imported row keeps its `thread_ts`, so a page read once (for example a
        Plus thread LC refused) is not read again until the thread changes.
        Returns (added, refreshed)."""
        existing = dict(
            cast(
                list[tuple[int, str | None]],
                self.conn.execute(
                    "SELECT thread_id, origin FROM lc_thread_details"
                ).fetchall(),
            )
        )
        threads = {
            row[0]
            for row in cast(
                list[tuple[int]],
                self.conn.execute("SELECT thread_id FROM lc_threads").fetchall(),
            )
        }
        added = refreshed = 0
        with self.conn:
            for thread_id, details in rows.items():
                if thread_id not in threads:
                    continue
                if thread_id in existing and existing[thread_id] != IMPORT_ORIGIN:
                    continue
                values = (
                    now,
                    details.get("overview"),
                    details.get("developer"),
                    details.get("released_at"),
                    details.get("language"),
                    json.dumps(details.get("languages") or [], ensure_ascii=False),
                    json.dumps(details.get("os") or [], ensure_ascii=False),
                    json.dumps(details.get("external_ids") or {}, ensure_ascii=False),
                )
                if thread_id in existing:
                    _ = self.conn.execute(
                        "UPDATE lc_thread_details SET fetched_at = ?, status = 'ok', "
                        + "overview = ?, developer = ?, release_text = NULL, released_at = ?, "
                        + "language = ?, languages = ?, os = ?, external_ids = ? "
                        + "WHERE thread_id = ?",
                        (*values, thread_id),
                    )
                    refreshed += 1
                else:
                    _ = self.conn.execute(
                        "INSERT INTO lc_thread_details (thread_id, thread_ts, fetched_at, "
                        + "status, overview, developer, release_text, released_at, language, "
                        + "languages, os, external_ids, origin) "
                        + "VALUES (?, NULL, ?, 'ok', ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                        (thread_id, *values, IMPORT_ORIGIN),
                    )
                    added += 1
        return added, refreshed

    def upsert_lc_prefix_names(self, names: Mapping[int, str]) -> None:
        """Add or rename LC prefixes; IDs the feed no longer shows keep their name."""
        if not names:
            return
        with self.conn:
            _ = self.conn.executemany(
                "INSERT INTO lc_prefix_names (id, name) VALUES (?, ?) "
                + "ON CONFLICT(id) DO UPDATE SET name = excluded.name",
                names.items(),
            )

    def lc_prefix_ids_by_name(self) -> dict[str, list[int]]:
        """Casefolded prefix name to every ID that has it; LC reuses some names."""
        found: dict[str, list[int]] = {}
        for prefix_id, name in _rows(
            self.conn.execute("SELECT id, name FROM lc_prefix_names ORDER BY id")
        ):
            found.setdefault(str(name).casefold(), []).append(cast(int, prefix_id))
        return found

    def count(self, source: str, *, whole_table: bool = False) -> int:
        """Current rows the source owns; `whole_table` counts every current row
        of its main table, e.g. all LC threads for the manifest."""
        if source in DETAIL_TABLES:
            row = cast(
                tuple[int],
                self.conn.execute(
                    f"SELECT COUNT(*) FROM {DETAIL_TABLES[source]}"
                ).fetchone(),
            )
            return row[0]
        table, _key = SOURCE_TABLES[source][0]
        owned = "" if whole_table else _owned(source)
        row = cast(
            tuple[int],
            self.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE removed_at IS NULL{owned}"
            ).fetchone(),
        )
        return row[0]

    def changes_since(self, source: str, since: str) -> tuple[int, int]:
        """Rows first seen and rows marked removed at or after `since`, in the
        source's main table and of its own rows (the ones `count` uses)."""
        table, _key = SOURCE_TABLES[source][0]
        row = cast(
            tuple[int | None, int | None],
            self.conn.execute(
                f"SELECT SUM(first_seen >= ?), SUM(removed_at >= ?) FROM {table} "
                + f"WHERE 1{_owned(source)}",
                (since, since),
            ).fetchone(),
        )
        return row[0] or 0, row[1] or 0

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

    def replace_imported_links(self, pairs: Iterable[tuple[str, str, str, str]]) -> int:
        """Replace the imported link pairs; returns how many are stored."""
        with self.conn:
            _ = self.conn.execute("DELETE FROM imported_links")
            _ = self.conn.executemany(
                "INSERT OR IGNORE INTO imported_links (src_a, id_a, src_b, id_b) "
                + "VALUES (?, ?, ?, ?)",
                pairs,
            )
        row = cast(
            tuple[int],
            self.conn.execute("SELECT COUNT(*) FROM imported_links").fetchone(),
        )
        return row[0]

    def imported_links(self) -> list[tuple[str, str, str, str]]:
        return cast(
            list[tuple[str, str, str, str]],
            self.conn.execute(
                "SELECT src_a, id_a, src_b, id_b FROM imported_links ORDER BY 1, 2, 3, 4"
            ).fetchall(),
        )

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

    def has_dlsite_genres(self) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM dlsite_genres LIMIT 1").fetchone()
            is not None
        )

    def dlsite_genre_names(
        self, genre_ids: Iterable[str] | None = None
    ) -> dict[str, str]:
        if genre_ids is None:
            found = _rows(
                self.conn.execute("SELECT genre_id, name_en FROM dlsite_genres")
            )
        else:
            wanted = list(dict.fromkeys(str(gid) for gid in genre_ids))
            if not wanted:
                return {}
            marks = ", ".join(["?"] * len(wanted))
            found = _rows(
                self.conn.execute(
                    f"SELECT genre_id, name_en FROM dlsite_genres WHERE genre_id IN ({marks})",
                    wanted,
                )
            )
        return {
            str(genre_id): str(name_en)
            for genre_id, name_en in found
            if name_en is not None
        }

    def upsert_dlsite_genres(self, genres: Mapping[str, str], now: str) -> None:
        if not genres:
            return
        with self.conn:
            _ = self.conn.executemany(
                "INSERT INTO dlsite_genres (genre_id, name_en, checked_at) "
                + "VALUES (?, ?, ?) ON CONFLICT(genre_id) DO UPDATE SET "
                + "name_en = excluded.name_en, checked_at = excluded.checked_at",
                [(str(genre_id), name_en, now) for genre_id, name_en in genres.items()],
            )

    def unknown_dlsite_genres(self, genre_ids: Iterable[str]) -> list[str]:
        wanted = list(dict.fromkeys(str(gid) for gid in genre_ids))
        if not wanted:
            return []
        known = set(self.dlsite_genre_names(wanted))
        return [gid for gid in wanted if gid not in known]

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
        return dict(zip(keys, row, strict=True))

    def recent_runs(self, limit: int = 5) -> list[dict[str, object]]:
        runs = _rows(
            self.conn.execute(
                "SELECT id, started_at, finished_at, published FROM runs "
                + "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        )
        result: list[dict[str, object]] = []
        for run_id, started_at, finished_at, published in runs:
            sources = _rows(
                self.conn.execute(
                    "SELECT source, status, rows, requests, detail FROM run_sources "
                    + "WHERE run_id = ? ORDER BY source",
                    (run_id,),
                )
            )
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
