# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlite3
import shlex
import re
import os
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from datetime import datetime
from typing import TypedDict, cast

from core.f95zone import normalize_thread_url, thread_urls_match
from core.host_platform import get_data_dir
from core.store_links import (
    LINK_ORIGINS,
    MATCHABLE_SOURCES,
    StoreLink,
    normalize_store_links,
)
from core.versions import normalize_version

DATA_DIR = get_data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "wlib.db")

DEFAULT_PLAY_STATUS = "Not Started"
DEFAULT_LAUNCH_MODE = "auto"
RPGMAKER_LINUX_LAUNCH_MODE = "rpgmaker_linux"
RPGMAKER_LINUX_RUNNER_SETTING = "rpgmaker_linux_runner_path"
DEFAULT_LIBRARY_FOLDER_PATTERN = "{creator}/{title}/{version}"
GENERAL_SETTINGS_KEYS = (
    "enable_logging",
    "auto_update_check",
    "update_notifications",
    "custom_play_statuses",
    "collections",
    "library_filter_presets",
    "tag_colors",
    "save_backup_keep",
    "sandbox_new_games",
)
SAVE_BACKUP_KEEP_MAX = 99
TAG_COLOR_SWATCHES = (
    "red",
    "orange",
    "yellow",
    "green",
    "teal",
    "blue",
    "purple",
    "pink",
)
SYNC_GAME_COLUMNS = (
    "title",
    "developer",
    "engine",
    "tags",
    "f95_url",
    "version",
    "latest_version",
    "thread_completed",
    "cover_image_path",
    "play_status",
    "finished_version",
    "last_played_version",
    "skipped_version",
    "is_favorite",
    "rating",
    "rating_graphics",
    "rating_story",
    "rating_fappability",
    "rating_gameplay",
    "progress",
    "playtime_seconds",
    "last_played",
    "date_added",
    "collections",
)
CANONICAL_LAUNCH_MODES = (
    DEFAULT_LAUNCH_MODE,
    "native",
    "wine_proton",
    "custom",
    RPGMAKER_LINUX_LAUNCH_MODE,
)
CANONICAL_PLAY_STATUSES = (
    DEFAULT_PLAY_STATUS,
    "Plan to Play",
    "Playing",
    "Waiting For Update",
    "On Hold",
    "Completed",
    "Abandoned",
)

_CANONICAL_PLAY_STATUS_MAP = {
    status.lower(): status for status in CANONICAL_PLAY_STATUSES
}
_LEGACY_PLAY_STATUS_MAP = {
    "completed": "Completed",
    "in_progress": "Playing",
    "replaying": "Playing",
    "waiting_update": "Waiting For Update",
    "abandoned": "Abandoned",
}
_LEGACY_RECOVERABLE_PLAY_STATUSES = {
    "",
    DEFAULT_PLAY_STATUS.lower(),
    "on hold",
    "waiting_update",
    "abandoned",
}

_CANONICAL_LAUNCH_MODE_SET = set(CANONICAL_LAUNCH_MODES)


class LaunchTarget(TypedDict):
    id: int
    game_id: int
    label: str
    exe_path: str
    sort_order: int
    created_at: str
    updated_at: str


def _normalize_status_key(value: object) -> str:
    return str(value or "").strip().lower()


def default_downloads_folder() -> str:
    """OS Downloads folder: `xdg-user-dir DOWNLOAD` with `~/Downloads` fallback."""
    fallback = os.path.abspath(os.path.expanduser(os.path.join("~", "Downloads")))
    try:
        import shutil
        import subprocess

        xdg_user_dir = shutil.which("xdg-user-dir")
        if xdg_user_dir:
            completed = subprocess.run(
                [xdg_user_dir, "DOWNLOAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            candidate = (completed.stdout or "").strip()
            if completed.returncode == 0 and candidate:
                resolved = os.path.abspath(os.path.expanduser(candidate))
                # xdg-user-dir falls back to $HOME when DOWNLOAD is unset.
                if resolved != os.path.abspath(os.path.expanduser("~")):
                    return resolved
    except Exception:
        pass
    return fallback


def normalize_launch_mode(launch_mode: object) -> str:
    normalized = str(launch_mode or "").strip().lower()
    if normalized in _CANONICAL_LAUNCH_MODE_SET:
        return normalized
    return DEFAULT_LAUNCH_MODE


def normalize_play_status(
    play_status: object, legacy_status: object | None = None
) -> str:
    normalized_play_status = _normalize_status_key(play_status)
    normalized_legacy_status = _normalize_status_key(legacy_status)

    recovered_status = _LEGACY_PLAY_STATUS_MAP.get(normalized_legacy_status)
    if (
        recovered_status is not None
        and normalized_play_status in _LEGACY_RECOVERABLE_PLAY_STATUSES
    ):
        return recovered_status

    canonical_status = _CANONICAL_PLAY_STATUS_MAP.get(normalized_play_status)
    if canonical_status is not None:
        return canonical_status

    legacy_status_value = _LEGACY_PLAY_STATUS_MAP.get(normalized_play_status)
    if legacy_status_value is not None:
        return legacy_status_value

    if normalized_play_status:
        return str(play_status).strip()[:40]

    legacy_fallback = _LEGACY_PLAY_STATUS_MAP.get(normalized_legacy_status)
    if legacy_fallback is not None:
        return legacy_fallback

    return DEFAULT_PLAY_STATUS


def _normalize_game_play_statuses(
    cursor: sqlite3.Cursor, existing_columns: set[str]
) -> None:
    select_columns = ["id", "play_status"]
    has_legacy_status = "status" in existing_columns
    if has_legacy_status:
        select_columns.append("status")

    _ = cursor.execute(f"SELECT {', '.join(select_columns)} FROM games")
    rows = cast(list[sqlite3.Row], cursor.fetchall())

    for row in rows:
        current_play_status = cast(object, row["play_status"])
        legacy_status = (
            cast(object | None, row["status"]) if has_legacy_status else None
        )
        normalized_status = normalize_play_status(current_play_status, legacy_status)
        if normalized_status != current_play_status:
            _ = cursor.execute(
                "UPDATE games SET play_status = ? WHERE id = ?",
                (normalized_status, row["id"]),
            )

    if has_legacy_status:
        # Recover once, then drop the legacy value so it can't override
        # statuses the user picks later.
        _ = cursor.execute("UPDATE games SET status = '' WHERE status != ''")


def _normalize_game_f95_urls(cursor: sqlite3.Cursor) -> None:
    # Rows saved before URL normalization existed never match the exact-URL
    # lookups used by metadata refreshes.
    _ = cursor.execute(
        "SELECT id, f95_url FROM games WHERE f95_url IS NOT NULL AND TRIM(f95_url) != ''"
    )
    for row in cast(list[sqlite3.Row], cursor.fetchall()):
        stored = cast(object, row["f95_url"])
        normalized = normalize_thread_url(stored)
        game_id = cast(int, row["id"])
        if not normalized or normalized == stored:
            continue
        # Leave true duplicates alone rather than merging two library entries.
        if find_matching_game_row(cursor, normalized, exclude_id=game_id) is not None:
            continue
        try:
            _ = cursor.execute(
                "UPDATE games SET f95_url = ? WHERE id = ?", (normalized, game_id)
            )
        except sqlite3.IntegrityError:
            continue


def find_matching_game_row(
    cursor: sqlite3.Cursor, url: object, exclude_id: int | None = None
) -> sqlite3.Row | None:
    lookup_url = normalize_thread_url(url)
    if not lookup_url:
        return None

    query = "SELECT id, title, f95_url, play_status, exe_path FROM games WHERE f95_url IS NOT NULL AND TRIM(f95_url) != ''"
    params: list[int] = []
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    query += " ORDER BY id ASC"

    _ = cursor.execute(query, params)
    for row in cast(list[sqlite3.Row], cursor.fetchall()):
        if thread_urls_match(cast(object, row["f95_url"]), lookup_url):
            return row
    return None


def _row_to_launch_target(row: sqlite3.Row) -> LaunchTarget:
    return {
        "id": int(str(cast(object, row["id"]))),
        "game_id": int(str(cast(object, row["game_id"]))),
        "label": str(cast(object, row["label"])),
        "exe_path": str(cast(object, row["exe_path"])),
        "sort_order": int(str(cast(object, row["sort_order"]))),
        "created_at": str(cast(object, row["created_at"] or "")),
        "updated_at": str(cast(object, row["updated_at"] or "")),
    }


def _normalize_launch_target_label(label: object) -> str:
    normalized = str(label or "").strip()
    if not normalized:
        raise ValueError("Launch target label is required")
    return normalized


def _normalize_launch_target_path(exe_path: object) -> str:
    normalized = str(exe_path or "").strip()
    if not normalized:
        raise ValueError("Launch target executable path is required")
    return normalized


def _coerce_sort_order(sort_order: object) -> int:
    try:
        if isinstance(sort_order, bool):
            return 1 if sort_order else 0
        if isinstance(sort_order, int):
            return max(0, sort_order)
        if isinstance(sort_order, float):
            return max(0, int(sort_order))
        text = str(sort_order or "").strip()
        if not text:
            return 0
        return max(0, int(float(text)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _coerce_bool_int(value: object) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int | float):
        return 1 if value != 0 else 0
    return 1 if str(value or "").strip().lower() in {"1", "true", "yes", "on"} else 0


def find_game_by_f95_url(
    url: object, exclude_id: int | None = None
) -> dict[str, object] | None:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        row = find_matching_game_row(cursor, url, exclude_id=exclude_id)
    if row is None:
        return None
    return {str(key): cast(object, row[key]) for key in row.keys()}


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    _ = conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        # WAL is stored in the database file, so setting it once covers every connection.
        _ = conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()

        # Create the Games table
        # f95_url: the F95Zone thread URL to scrape
        # exe_path: the path to the main game executable
        # version: the last known version string from F95Zone
        # progress: optional user notes or completion status
        _ = cursor.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                f95_url TEXT,
                exe_path TEXT NOT NULL,
                version TEXT,
                progress TEXT DEFAULT '',
                developer TEXT DEFAULT '',
                last_played TIMESTAMP,
                cover_image_path TEXT
            )
        """)

        # Create a table for Settings (like Proton Path, Default Prefix Path)
        _ = cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        _ = cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_launch_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                exe_path TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            )
        """)
        _ = cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_launch_targets_game_order ON game_launch_targets(game_id, sort_order, id)"
        )
        _ = cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_links (
                id INTEGER PRIMARY KEY,
                game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                url TEXT NOT NULL,
                origin TEXT NOT NULL,
                created_at TIMESTAMP,
                UNIQUE(game_id, source, external_id)
            )
        """)
        _ = cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_links_identity ON game_links(source, external_id)"
        )
        _ = cursor.execute("""
            CREATE TABLE IF NOT EXISTS companion_tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL DEFAULT '',
                arguments TEXT NOT NULL DEFAULT '',
                show_on_all_games INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """)
        _ = cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_companion_tools_name ON companion_tools(name COLLATE NOCASE)"
        )
        _ = cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_companion_tools (
                game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                tool_id INTEGER NOT NULL REFERENCES companion_tools(id) ON DELETE CASCADE,
                visible INTEGER NOT NULL,
                PRIMARY KEY(game_id, tool_id)
            )
        """)
        _ = cursor.execute("""
            CREATE TABLE IF NOT EXISTS play_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                version TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_seconds INTEGER NOT NULL DEFAULT 0
            )
        """)
        _ = cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_play_sessions_game_started ON play_sessions(game_id, started_at)"
        )

        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('proton_path', '')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('wine_prefix_path', '')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('enable_logging', 'false')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('sandbox_new_games', 'false')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('sandboxie_custom_path', '')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_update_check', 'weekly')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('update_notifications', 'on')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('last_update_check', '')"
        )
        _ = cursor.execute(
            "DELETE FROM settings WHERE key = 'playwright_browsers_path'"
        )
        _ = cursor.execute(
            f"INSERT OR IGNORE INTO settings (key, value) VALUES ('{RPGMAKER_LINUX_RUNNER_SETTING}', '')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('library_folder', '')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('library_folder_pattern', ?)",
            (DEFAULT_LIBRARY_FOLDER_PATTERN,),
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads_folder', ?)",
            (default_downloads_folder(),),
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('download_detection_enabled', 'true')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('dismissed_download_archives', '[]')"
        )

        # Safely migrate existing DBs by adding new columns
        _ = cursor.execute("PRAGMA table_info(games)")
        table_info_rows = cast(list[tuple[object, ...]], cursor.fetchall())
        existing_columns = {
            str(row[1]) for row in table_info_rows if len(row) > 1 and row[1]
        }

        new_columns = [
            ("cover_local", "TEXT"),
            ("tags", "TEXT DEFAULT ''"),
            ("rating", "TEXT DEFAULT ''"),
            ("command_line_args", "TEXT DEFAULT ''"),
            ("status", "TEXT DEFAULT ''"),
            ("rating_graphics", "REAL DEFAULT 0"),
            ("rating_story", "REAL DEFAULT 0"),
            ("rating_fappability", "REAL DEFAULT 0"),
            ("rating_gameplay", "REAL DEFAULT 0"),
            ("engine", "TEXT DEFAULT ''"),
            ("latest_version", "TEXT DEFAULT ''"),
            ("run_japanese_locale", "BOOLEAN DEFAULT 0"),
            ("run_wayland", "BOOLEAN DEFAULT 0"),
            ("auto_inject_ce", "BOOLEAN DEFAULT 0"),
            ("sandbox_enabled", "BOOLEAN DEFAULT 0"),
            ("custom_prefix", "TEXT DEFAULT ''"),
            ("proton_version", "TEXT DEFAULT ''"),
            ("playtime_seconds", "INTEGER DEFAULT 0"),
            ("last_played", "TIMESTAMP"),
            ("date_added", "TIMESTAMP"),
            ("play_status", f"TEXT DEFAULT '{DEFAULT_PLAY_STATUS}'"),
            ("is_favorite", "BOOLEAN DEFAULT 0"),
            ("thread_main_post_last_edit_at", "TIMESTAMP"),
            ("thread_main_post_checked_at", "TIMESTAMP"),
            ("thread_completed", "BOOLEAN DEFAULT 0"),
            ("launch_mode", f"TEXT DEFAULT '{DEFAULT_LAUNCH_MODE}'"),
            ("finished_version", "TEXT DEFAULT ''"),
            ("last_played_version", "TEXT DEFAULT ''"),
            ("skipped_version", "TEXT DEFAULT ''"),
            ("collections", "TEXT DEFAULT '[]'"),
            ("screenshots", "TEXT DEFAULT '[]'"),
            ("save_path_override", "TEXT DEFAULT ''"),
        ]

        for col_name, col_type in new_columns:
            if col_name not in existing_columns:
                _ = cursor.execute(
                    f"ALTER TABLE games ADD COLUMN {col_name} {col_type}"
                )

        try:
            _ = cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_games_f95_url_unique ON games(f95_url) WHERE f95_url IS NOT NULL AND f95_url != ''"
            )
        except sqlite3.IntegrityError:
            print(
                "[wLib] Warning: duplicate f95_url values exist; unique index not applied"
            )

        _normalize_game_play_statuses(cursor, existing_columns | {"play_status"})
        _normalize_game_f95_urls(cursor)
        launch_mode_placeholders = ", ".join("?" for _ in CANONICAL_LAUNCH_MODES)
        _ = cursor.execute(
            f"UPDATE games SET launch_mode = ? WHERE launch_mode IS NULL OR TRIM(launch_mode) = '' OR launch_mode NOT IN ({launch_mode_placeholders})",
            (DEFAULT_LAUNCH_MODE, *CANONICAL_LAUNCH_MODES),
        )

        bump_sync_time = """
            INSERT INTO settings (key, value)
            VALUES ('sync_local_modified_at', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(key) DO UPDATE SET value = CASE
                WHEN settings.value >= excluded.value
                THEN COALESCE(
                    strftime('%Y-%m-%dT%H:%M:%fZ', settings.value, '+0.001 seconds'),
                    excluded.value
                )
                ELSE excluded.value END;
        """
        for action in ("INSERT", "DELETE"):
            for table, prefix in (
                ("game_links", "sync_game_links"),
                ("games", "sync_games"),
            ):
                trigger = f"{prefix}_{action.lower()}"
                # Recreate on every start so upgraded databases pick up
                # changes to the bump statement.
                _ = cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                _ = cursor.execute(
                    f"CREATE TRIGGER {trigger} AFTER {action} ON {table} BEGIN {bump_sync_time} END"
                )
        columns = ", ".join(SYNC_GAME_COLUMNS)
        changed = " OR ".join(
            f"OLD.{column} IS NOT NEW.{column}" for column in SYNC_GAME_COLUMNS
        )
        # Recreate on every start so upgraded databases watch columns added
        # after the trigger was first created.
        _ = cursor.execute("DROP TRIGGER IF EXISTS sync_games_update")
        _ = cursor.execute(
            f"CREATE TRIGGER sync_games_update AFTER UPDATE OF {columns} ON games WHEN {changed} BEGIN {bump_sync_time} END"
        )
        keys = ", ".join(f"'{key}'" for key in GENERAL_SETTINGS_KEYS)
        for action in ("INSERT", "UPDATE", "DELETE"):
            trigger = f"sync_play_sessions_{action.lower()}"
            _ = cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            _ = cursor.execute(
                f"CREATE TRIGGER {trigger} AFTER {action} ON play_sessions BEGIN {bump_sync_time} END"
            )
            row = "OLD" if action == "DELETE" else "NEW"
            condition = f"{row}.key IN ({keys})"
            if action == "UPDATE":
                condition += " AND OLD.value IS NOT NEW.value"
            _ = cursor.execute(f"DROP TRIGGER IF EXISTS sync_settings_{action.lower()}")
            _ = cursor.execute(
                f"CREATE TRIGGER sync_settings_{action.lower()} AFTER {action} ON settings WHEN {condition} BEGIN {bump_sync_time} END"
            )

        conn.commit()
    finally:
        conn.close()


# CRUD Operations for Games
def add_game(
    title: str,
    exe_path: str,
    f95_url: str = "",
    version: str = "",
    cover_image: str = "",
    tags: str | list[str] = "",
    rating: str = "",
    developer: str = "",
    engine: str = "",
    run_japanese_locale: bool = False,
    run_wayland: bool = False,
    auto_inject_ce: bool = False,
    custom_prefix: str = "",
    proton_version: str = "",
    launch_mode: str = DEFAULT_LAUNCH_MODE,
    command_line_args: str = "",
    sandbox_enabled: bool | None = None,
) -> int | None:
    # tags might be a list, so convert to comma-separated string if needed
    if isinstance(tags, list):
        tags = ", ".join(str(tag) for tag in tags)

    normalized_url = normalize_thread_url(f95_url)
    if sandbox_enabled is None:
        sandbox_enabled = bool(_coerce_bool_int(get_setting("sandbox_new_games")))

    now_iso = datetime.now().isoformat()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if (
            normalized_url
            and find_matching_game_row(cursor, normalized_url) is not None
        ):
            raise sqlite3.IntegrityError("duplicate f95_url")

        _ = cursor.execute(
            "INSERT INTO games (title, exe_path, f95_url, version, cover_image_path, tags, rating, developer, engine, run_japanese_locale, run_wayland, auto_inject_ce, custom_prefix, proton_version, launch_mode, date_added, play_status, command_line_args, sandbox_enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title,
                exe_path,
                normalized_url,
                version,
                cover_image,
                tags,
                rating,
                developer,
                engine,
                _coerce_bool_int(run_japanese_locale),
                _coerce_bool_int(run_wayland),
                _coerce_bool_int(auto_inject_ce),
                custom_prefix,
                proton_version,
                normalize_launch_mode(launch_mode),
                now_iso,
                DEFAULT_PLAY_STATUS,
                command_line_args,
                _coerce_bool_int(sandbox_enabled),
            ),
        )
        game_id = cursor.lastrowid
        conn.commit()
    return game_id


def get_game_links(game_id: int) -> list[StoreLink]:
    """Return portable link metadata in stable insertion order."""
    with closing(get_connection()) as conn:
        rows = cast(
            list[tuple[str, str, str]],
            conn.execute(
                "SELECT source, external_id, url FROM game_links WHERE game_id = ? ORDER BY id",
                (game_id,),
            ).fetchall(),
        )
    return [
        {"source": source, "id": identity, "url": url} for source, identity, url in rows
    ]


def replace_game_links(
    game_id: int,
    origin: str,
    links: Sequence[Mapping[str, object]],
    *,
    connection: sqlite3.Connection | None = None,
    replace_all: bool = False,
) -> None:
    """Replace only this origin's links, leaving identical rows untouched.

    An identity already owned by another origin stays with that origin; the
    per-game uniqueness constraint prevents duplicate links from two sources.
    URL changes use delete/insert so both are covered by sync triggers.
    """
    if origin not in LINK_ORIGINS:
        raise ValueError("Invalid store link origin")
    incoming = {
        (link["source"], link["id"]): link["url"]
        for link in normalize_store_links(links)
    }
    if connection is None:
        with closing(get_connection()) as conn:
            # Read the current rows under the write lock. Without it, two saves
            # can both see no row for an identity and then race the unique
            # constraint, failing one of them with IntegrityError.
            _ = conn.execute("BEGIN IMMEDIATE")
            replace_game_links(
                game_id, origin, links, connection=conn, replace_all=replace_all
            )
            conn.commit()
        return
    conn = connection
    rows = cast(
        list[tuple[int, str, str, str, str]],
        conn.execute(
            "SELECT id, source, external_id, url, origin FROM game_links WHERE game_id = ?",
            (game_id,),
        ).fetchall(),
    )
    keep: set[tuple[str, str]] = set()
    for row_id, source, identity, url, stored_origin in rows:
        key = (source, identity)
        # A full replace keeps identical rows whatever their origin, so applying
        # unchanged links from a backup or sync file does not bump the sync time.
        if (not replace_all and stored_origin != origin) or (
            (replace_all or stored_origin == origin) and incoming.get(key) == url
        ):
            keep.add(key)
        else:
            _ = conn.execute("DELETE FROM game_links WHERE id = ?", (row_id,))
    for (source, identity), url in incoming.items():
        if (source, identity) not in keep:
            _ = conn.execute(
                "INSERT INTO game_links (game_id, source, external_id, url, origin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (game_id, source, identity, url, origin, datetime.now().isoformat()),
            )


def find_games_by_links(links: Sequence[Mapping[str, object]]) -> list[int]:
    """Return every exact match, including ambiguous matches, never creators."""
    identities = {
        (link["source"], link["id"])
        for link in normalize_store_links(links)
        if link["source"] in MATCHABLE_SOURCES
    }
    matches: set[int] = set()
    with closing(get_connection()) as conn:
        for source, identity in identities:
            rows = cast(
                list[tuple[int]],
                conn.execute(
                    "SELECT game_id FROM game_links WHERE source = ? AND external_id = ?",
                    (source, identity),
                ).fetchall(),
            )
            matches.update(row[0] for row in rows)
    return sorted(matches)


def get_game_launch_target(target_id: int) -> LaunchTarget | None:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = cursor.execute(
            "SELECT * FROM game_launch_targets WHERE id = ?",
            (target_id,),
        )
        row = cast(sqlite3.Row | None, cursor.fetchone())
    return _row_to_launch_target(row) if row is not None else None


def list_game_launch_targets(game_id: int) -> list[LaunchTarget]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = cursor.execute(
            "SELECT * FROM game_launch_targets WHERE game_id = ? ORDER BY sort_order ASC, id ASC",
            (game_id,),
        )
        rows = cast(list[sqlite3.Row], cursor.fetchall())
    return [_row_to_launch_target(row) for row in rows]


def list_launch_targets_for_games(
    game_ids: Sequence[int],
) -> dict[int, list[LaunchTarget]]:
    normalized_ids = [int(game_id) for game_id in game_ids]
    targets_by_game: dict[int, list[LaunchTarget]] = {
        game_id: [] for game_id in normalized_ids
    }
    if not normalized_ids:
        return targets_by_game

    rows: list[sqlite3.Row] = []
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        unique_ids = list(dict.fromkeys(normalized_ids))
        # SQLite before 3.32 allows only 999 parameters per statement.
        for start in range(0, len(unique_ids), 500):
            chunk = unique_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            _ = cursor.execute(
                f"SELECT * FROM game_launch_targets WHERE game_id IN ({placeholders}) ORDER BY game_id ASC, sort_order ASC, id ASC",
                tuple(chunk),
            )
            rows.extend(cast(list[sqlite3.Row], cursor.fetchall()))

    for row in rows:
        target = _row_to_launch_target(row)
        targets_by_game.setdefault(target["game_id"], []).append(target)
    return targets_by_game


def add_game_launch_target(
    game_id: int,
    label: object,
    exe_path: object,
    sort_order: object | None = None,
) -> LaunchTarget:
    target_label = _normalize_launch_target_label(label)
    target_path = _normalize_launch_target_path(exe_path)
    now_iso = datetime.now().isoformat()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if sort_order is None:
            _ = cursor.execute(
                "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM game_launch_targets WHERE game_id = ?",
                (game_id,),
            )
            row = cast(tuple[object, ...] | None, cursor.fetchone())
            target_order = int(str(row[0] if row is not None else 0))
        else:
            target_order = _coerce_sort_order(sort_order)

        _ = cursor.execute(
            "INSERT INTO game_launch_targets (game_id, label, exe_path, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (game_id, target_label, target_path, target_order, now_iso, now_iso),
        )
        target_id = cursor.lastrowid
        _ = cursor.execute(
            "SELECT * FROM game_launch_targets WHERE id = ?",
            (target_id,),
        )
        target = _row_to_launch_target(cast(sqlite3.Row, cursor.fetchone()))
        conn.commit()
    return target


def update_game_launch_target(
    target_id: int, fields: Mapping[str, object]
) -> LaunchTarget | None:
    safe_fields: dict[str, object] = {}
    if "label" in fields:
        safe_fields["label"] = _normalize_launch_target_label(fields["label"])
    if "exe_path" in fields:
        safe_fields["exe_path"] = _normalize_launch_target_path(fields["exe_path"])
    if "sort_order" in fields:
        safe_fields["sort_order"] = _coerce_sort_order(fields["sort_order"])

    if not safe_fields:
        return get_game_launch_target(target_id)

    safe_fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join([f"{key} = ?" for key in safe_fields.keys()])
    values = tuple(safe_fields.values()) + (target_id,)

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = cursor.execute(
            f"UPDATE game_launch_targets SET {set_clause} WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            return None
        _ = cursor.execute(
            "SELECT * FROM game_launch_targets WHERE id = ?",
            (target_id,),
        )
        target = _row_to_launch_target(cast(sqlite3.Row, cursor.fetchone()))
        conn.commit()
    return target


def delete_game_launch_target(target_id: int) -> bool:
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _ = cursor.execute("DELETE FROM game_launch_targets WHERE id = ?", (target_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
    return deleted


def reorder_game_launch_targets(
    game_id: int, target_ids: Sequence[int]
) -> list[LaunchTarget]:
    normalized_ids = [int(target_id) for target_id in target_ids]
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError("Launch target order contains duplicate targets")

    existing = list_game_launch_targets(game_id)
    existing_ids = {target["id"] for target in existing}
    if set(normalized_ids) != existing_ids:
        raise ValueError("Launch target order must include all targets for the game")

    now_iso = datetime.now().isoformat()
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        for sort_order, target_id in enumerate(normalized_ids):
            _ = cursor.execute(
                "UPDATE game_launch_targets SET sort_order = ?, updated_at = ? WHERE id = ? AND game_id = ?",
                (sort_order, now_iso, target_id, game_id),
            )
        conn.commit()
    return list_game_launch_targets(game_id)


def get_game_count() -> int:
    with closing(get_connection()) as conn:
        row = cast(tuple[int], conn.execute("SELECT COUNT(*) FROM games").fetchone())
        return row[0]


def get_all_games() -> list[dict[str, object]]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = cursor.execute("SELECT * FROM games ORDER BY title ASC")
        games = cast(list[sqlite3.Row], cursor.fetchall())
        link_rows = cast(
            list[tuple[int, str, str, str]],
            conn.execute(
                "SELECT game_id, source, external_id, url FROM game_links ORDER BY id"
            ).fetchall(),
        )
    links_by_game: dict[int, list[StoreLink]] = {}
    for game_id, source, identity, url in link_rows:
        links_by_game.setdefault(game_id, []).append(
            {"source": source, "id": identity, "url": url}
        )
    targets_by_game = list_launch_targets_for_games(
        [int(str(cast(object, game["id"]))) for game in games]
    )
    # Membership is matched by name, case-insensitively; names that no longer
    # belong to any collection are hidden rather than shown as ghost tabs.
    canonical_collections = {name.casefold(): name for name in get_collections()}
    result: list[dict[str, object]] = []
    for game in games:
        game_dict = {str(key): cast(object, game[key]) for key in game.keys()}
        game_dict["launch_mode"] = normalize_launch_mode(game_dict.get("launch_mode"))
        game_dict["collections"] = [
            canonical_collections[name.casefold()]
            for name in _decode_game_collections(game_dict.get("collections"))
            if name.casefold() in canonical_collections
        ]
        game_dict["screenshots"] = _decode_game_screenshots(game_dict.get("screenshots"))
        game_id = int(str(game_dict["id"]))
        game_dict["launch_targets"] = targets_by_game.get(game_id, [])
        game_dict["links"] = links_by_game.get(game_id, [])
        result.append(game_dict)
    return result


def get_game(game_id: int) -> dict[str, object] | None:
    """The raw games row for one id, or None."""
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = cast(
            sqlite3.Row | None,
            conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone(),
        )
    if row is None:
        return None
    return {str(key): cast(object, row[key]) for key in row.keys()}


def delete_game(game_id: int) -> None:
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _ = cursor.execute("DELETE FROM games WHERE id = ?", (game_id,))
        conn.commit()


def set_game_local_cover(
    game_id: int, name: str | None, *, connection: sqlite3.Connection | None = None
) -> str:
    """Set device-only cover state, without touching any sync columns."""
    if connection is None:
        with closing(get_connection()) as conn:
            old = set_game_local_cover(game_id, name, connection=conn)
            conn.commit()
            return old
    row = cast(
        sqlite3.Row | tuple[object, ...] | None,
        connection.execute(
            "SELECT cover_local FROM games WHERE id = ?", (game_id,)
        ).fetchone(),
    )
    if row is None:
        raise ValueError("Game was not found")
    old = str(cast(object, row[0]) or "")
    _ = connection.execute(
        "UPDATE games SET cover_local = ? WHERE id = ?", (name, game_id)
    )
    return old


def clear_game_local_cover(game_id: int) -> str:
    return set_game_local_cover(game_id, None)


def set_sandbox_for_all_games(enabled: bool) -> int:
    """Set the next-launch sandbox preference and count only changed games."""
    value = _coerce_bool_int(enabled)
    with closing(get_connection()) as conn:
        cursor = conn.execute(
            "UPDATE games SET sandbox_enabled = ? WHERE sandbox_enabled IS NOT ?",
            (value, value),
        )
        conn.commit()
        return cursor.rowcount


def update_game(game_id: int, fields: Mapping[str, object]) -> dict[str, object]:
    """Update arbitrary fields on a game row. `fields` is a dict of column->value.

    Returns the fields actually applied, including a `finished_version` the
    caller never sent when a move to `Completed` auto-filled it.
    """
    if not fields:
        return {}
    allowed = {
        "title",
        "exe_path",
        "f95_url",
        "version",
        "progress",
        "developer",
        "cover_image_path",
        "tags",
        "rating",
        "command_line_args",
        "rating_graphics",
        "rating_story",
        "rating_fappability",
        "rating_gameplay",
        "engine",
        "latest_version",
        "run_japanese_locale",
        "run_wayland",
        "auto_inject_ce",
        "sandbox_enabled",
        "custom_prefix",
        "proton_version",
        "save_path_override",
        "launch_mode",
        "play_status",
        "finished_version",
        "skipped_version",
        "is_favorite",
        "thread_completed",
    }
    # Only allow known columns
    safe_fields: dict[str, object] = {
        k: v for k, v in fields.items() if k in allowed and v is not None
    }
    if not safe_fields:
        return {}

    raw_tags = safe_fields.get("tags")
    if isinstance(raw_tags, list):
        safe_fields["tags"] = ", ".join(
            str(tag) for tag in cast(list[object], raw_tags)
        )

    for bool_column in (
        "thread_completed",
        "is_favorite",
        "run_japanese_locale",
        "run_wayland",
        "auto_inject_ce",
        "sandbox_enabled",
    ):
        if bool_column in safe_fields:
            safe_fields[bool_column] = _coerce_bool_int(safe_fields[bool_column])

    if "f95_url" in safe_fields:
        safe_fields["f95_url"] = normalize_thread_url(safe_fields["f95_url"])

    if "play_status" in safe_fields:
        safe_fields["play_status"] = normalize_play_status(safe_fields["play_status"])
    if "finished_version" in safe_fields:
        safe_fields["finished_version"] = str(safe_fields["finished_version"]).strip()
    if "launch_mode" in safe_fields:
        safe_fields["launch_mode"] = normalize_launch_mode(safe_fields["launch_mode"])

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        updated_url = safe_fields.get("f95_url")
        if updated_url and find_matching_game_row(
            cursor,
            str(updated_url),
            exclude_id=game_id,
        ):
            raise sqlite3.IntegrityError("duplicate f95_url")

        if "f95_url" in safe_fields:
            _ = cursor.execute("SELECT f95_url FROM games WHERE id = ?", (game_id,))
            stored_url_row = cast(sqlite3.Row | None, cursor.fetchone())
            stored_url = (
                str(stored_url_row[0] or "") if stored_url_row is not None else ""
            )
            # The stored thread metadata, latest/skipped versions, completion
            # flag, and screenshots describe the old thread, not a new one.
            # Keep them when the URL is only an equivalent variant of the
            # same thread (slug, page, query, or fragment).
            if normalize_thread_url(stored_url) and not thread_urls_match(
                stored_url, safe_fields["f95_url"]
            ):
                safe_fields["latest_version"] = ""
                safe_fields["skipped_version"] = ""
                safe_fields["thread_main_post_last_edit_at"] = None
                safe_fields["thread_main_post_checked_at"] = None
                safe_fields["thread_completed"] = 0
                safe_fields["screenshots"] = "[]"

        if "version" in safe_fields:
            _ = cursor.execute("SELECT version FROM games WHERE id = ?", (game_id,))
            stored_version = cast(sqlite3.Row | None, cursor.fetchone())
            if stored_version is not None and normalize_version(
                safe_fields["version"]
            ) != normalize_version(cast(object, stored_version[0])):
                safe_fields["skipped_version"] = ""

        if (
            safe_fields.get("play_status") == "Completed"
            and "finished_version" not in safe_fields
        ):
            _ = cursor.execute(
                "SELECT play_status, version FROM games WHERE id = ?", (game_id,)
            )
            stored = cast(sqlite3.Row | None, cursor.fetchone())
            if stored is not None and stored["play_status"] != "Completed":
                # An explicitly sent version wins even when it is empty: the
                # finished version must match the installed version this call
                # stores, not the one it replaced.
                if "version" in safe_fields:
                    finished_version = str(safe_fields.get("version") or "").strip()
                else:
                    finished_version = str(stored["version"] or "").strip()
                safe_fields["finished_version"] = finished_version

        set_clause = ", ".join([f"{k} = ?" for k in safe_fields.keys()])
        values: tuple[object, ...] = tuple(safe_fields.values()) + (game_id,)
        _ = cursor.execute(f"UPDATE games SET {set_clause} WHERE id = ?", values)
        conn.commit()
    return safe_fields


def update_playtime(game_id: int, delta_seconds: int) -> None:
    clamped_delta = max(0, int(delta_seconds))

    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        _ = cursor.execute(
            "UPDATE games SET playtime_seconds = COALESCE(playtime_seconds, 0) + ?, last_played = ? WHERE id = ?",
            (clamped_delta, now_iso, game_id),
        )
        conn.commit()


def start_play_session(game_id: int, version: str) -> int:
    with closing(get_connection()) as conn:
        cursor = conn.execute(
            "INSERT INTO play_sessions (game_id, version, started_at) VALUES (?, ?, ?)",
            (game_id, version, datetime.now().isoformat()),
        )
        session_id = int(str(cursor.lastrowid))
        conn.commit()
        return session_id


def record_play_tick(game_id: int, session_id: int, delta: int, is_final: bool) -> str:
    clamped_delta = max(0, int(delta))
    with closing(get_connection()) as conn, conn:
        conn.row_factory = sqlite3.Row
        # Lock before reading so concurrent ticks cannot race the final tick.
        _ = conn.execute("BEGIN IMMEDIATE")
        session = cast(
            sqlite3.Row | None,
            conn.execute(
                "SELECT version, ended_at FROM play_sessions WHERE id = ? AND game_id = ?",
                (session_id, game_id),
            ).fetchone(),
        )
        if session is None:
            # A sync pull or backup import replaced the journal mid-play; keep
            # counting playtime so the rest of the run is not lost.
            _ = conn.execute(
                "UPDATE games SET playtime_seconds = COALESCE(playtime_seconds, 0) + ?, last_played = ? WHERE id = ?",
                (clamped_delta, datetime.now().isoformat(), game_id),
            )
        elif session["ended_at"] is None or not is_final:
            now = datetime.now().isoformat()
            _ = conn.execute(
                "UPDATE games SET playtime_seconds = COALESCE(playtime_seconds, 0) + ?, last_played = ? WHERE id = ?",
                (clamped_delta, now, game_id),
            )
            # A periodic tick can land after the final tick when the app shuts
            # down mid-tick: its interval was never recorded, so keep it while
            # leaving the session's original end time untouched.
            _ = conn.execute(
                "UPDATE play_sessions SET duration_seconds = duration_seconds + ?, ended_at = COALESCE(ended_at, ?) WHERE id = ?",
                (clamped_delta, now if is_final else None, session_id),
            )
            version = normalize_version(cast(object, session["version"]))
            if version:
                rows = cast(
                    list[sqlite3.Row],
                    conn.execute(
                        "SELECT version, duration_seconds FROM play_sessions WHERE game_id = ?",
                        (game_id,),
                    ).fetchall(),
                )
                duration = sum(
                    int(str(cast(object, row["duration_seconds"])))
                    for row in rows
                    if normalize_version(cast(object, row["version"])) == version
                )
                if duration >= 300:
                    _ = conn.execute(
                        "UPDATE games SET last_played_version = ? WHERE id = ?",
                        (session["version"], game_id),
                    )
        row = cast(
            sqlite3.Row | None,
            conn.execute(
                "SELECT last_played_version FROM games WHERE id = ?", (game_id,)
            ).fetchone(),
        )
        return str(row[0] or "") if row else ""


def get_play_sessions(game_id: int) -> list[dict[str, object]]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = cast(
            list[sqlite3.Row],
            conn.execute(
                "SELECT * FROM play_sessions WHERE game_id = ? ORDER BY started_at DESC, id DESC",
                (game_id,),
            ).fetchall(),
        )
        return [dict(row) for row in rows]


def get_all_play_sessions() -> dict[int, list[dict[str, object]]]:
    """Every game's sessions in one query, newest first, keyed by game id."""
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = cast(
            list[sqlite3.Row],
            conn.execute(
                "SELECT * FROM play_sessions ORDER BY game_id, started_at DESC, id DESC"
            ).fetchall(),
        )
    sessions: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        sessions.setdefault(int(str(cast(object, row["game_id"]))), []).append(dict(row))
    return sessions


# Settings Operations
def get_setting(key: str) -> str | None:
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _ = cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None:
        return None
    return str(row[0])


def update_setting(key: str, value: str) -> None:
    update_settings({key: value})


def update_settings(values: Mapping[str, str]) -> None:
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _ = cursor.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(values.items()),
        )
        conn.commit()


def get_save_backup_keep() -> int | None:
    """How many save backups to keep per game; None keeps all of them."""
    raw = str(get_setting("save_backup_keep") or "").strip()
    if not raw.isascii() or not raw.isdigit():
        return None
    try:
        keep = int(raw)
    except (ValueError, OverflowError):
        # Imported/synced settings can hold an arbitrarily long digit string.
        return None
    return keep if 1 <= keep <= SAVE_BACKUP_KEEP_MAX else None


def get_custom_play_statuses() -> list[str]:
    try:
        statuses = cast(object, json.loads(get_setting("custom_play_statuses") or "[]"))
    except (ValueError, TypeError):
        return []
    if not isinstance(statuses, list):
        return []
    # Imports and sync store this list unvalidated. Drop names that clash with a
    # built-in status, or removing them would reset every game in that status.
    seen = {status.casefold() for status in CANONICAL_PLAY_STATUSES}
    result: list[str] = []
    for status in cast(list[object], statuses):
        if not isinstance(status, str):
            continue
        name = status.strip()
        if (
            not 1 <= len(name) <= 40
            or name.casefold() in seen
            or normalize_play_status(name) != name
        ):
            continue
        seen.add(name.casefold())
        result.append(name)
    return result


def update_custom_play_statuses(statuses: Sequence[str]) -> None:
    # Match case-insensitively like the reset below, so a case-only rename keeps its games.
    kept = {str(status).strip().casefold() for status in statuses}
    removed = [
        status for status in get_custom_play_statuses() if status.casefold() not in kept
    ]
    with closing(get_connection()) as conn:
        for status in removed:
            _ = conn.execute(
                "UPDATE games SET play_status = ? WHERE play_status = ? COLLATE NOCASE",
                (DEFAULT_PLAY_STATUS, status),
            )
        _ = conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("custom_play_statuses", json.dumps([str(status).strip() for status in statuses])),
        )
        conn.commit()


# Collection Operations
RESERVED_COLLECTION_KEYS = {"all"}


def _validated_collection_list(raw: object) -> list[str]:
    try:
        stored = cast(object, json.loads(str(raw or "[]")))
    except (ValueError, TypeError):
        return []
    if not isinstance(stored, list):
        return []
    # Imports and sync store this list unvalidated. "All" belongs to the
    # built-in tab, so it stays reserved.
    seen = set(RESERVED_COLLECTION_KEYS)
    result: list[str] = []
    for name in cast(list[object], stored):
        if not isinstance(name, str):
            continue
        normalized = name.strip()
        if not 1 <= len(normalized) <= 40 or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        result.append(normalized)
    return result


def _read_collections(cursor: sqlite3.Cursor) -> list[str]:
    _ = cursor.execute("SELECT value FROM settings WHERE key = 'collections'")
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None:
        return []
    return _validated_collection_list(row[0])


def get_collections() -> list[str]:
    return _validated_collection_list(get_setting("collections"))


def _validate_new_collection_name(name: object, collections: Sequence[str]) -> str:
    normalized = str(name or "").strip()
    if not 1 <= len(normalized) <= 40:
        raise ValueError("Collection names must contain 1–40 characters")
    if normalized.casefold() in RESERVED_COLLECTION_KEYS:
        raise ValueError("'All' is reserved for the library-wide tab")
    if any(normalized.casefold() == other.casefold() for other in collections):
        raise ValueError(f"A collection named '{normalized}' already exists")
    return normalized


def _write_collections_setting(
    cursor: sqlite3.Cursor, collections: Sequence[str]
) -> None:
    _ = cursor.execute(
        "INSERT INTO settings (key, value) VALUES ('collections', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps(list(collections)),),
    )


def _read_game_collections(cursor: sqlite3.Cursor, game_id: int) -> list[str] | None:
    _ = cursor.execute("SELECT collections FROM games WHERE id = ?", (game_id,))
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None:
        return None
    return _decode_game_collections(row[0])


def _write_game_collections(
    cursor: sqlite3.Cursor, game_id: int, names: Sequence[str]
) -> None:
    _ = cursor.execute(
        "UPDATE games SET collections = ? WHERE id = ?",
        (json.dumps(list(names)), game_id),
    )


def _decode_game_collections(raw: object) -> list[str]:
    try:
        stored = cast(object, json.loads(str(raw or "[]")))
    except (ValueError, TypeError):
        return []
    if not isinstance(stored, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for name in cast(list[object], stored):
        if not isinstance(name, str):
            continue
        normalized = name.strip()
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        names.append(normalized)
    return names


def _decode_game_screenshots(raw: object) -> list[str]:
    try:
        stored = cast(object, json.loads(str(raw or "[]")))
    except (ValueError, TypeError):
        return []
    if not isinstance(stored, list):
        return []
    urls: list[str] = []
    for url in cast(list[object], stored):
        if not isinstance(url, str):
            continue
        normalized = url.strip()
        if normalized:
            urls.append(normalized)
    return urls


def _rewrite_game_collection_memberships(
    cursor: sqlite3.Cursor, rewrite: Callable[[list[str]], list[str]]
) -> None:
    _ = cursor.execute(
        "SELECT id, collections FROM games WHERE collections IS NOT NULL AND collections != '[]'"
    )
    for row in cast(list[sqlite3.Row], cursor.fetchall()):
        game_id = int(str(cast(object, row["id"])))
        names = _decode_game_collections(cast(object, row["collections"]))
        rewritten = rewrite(names)
        if rewritten != names:
            _write_game_collections(cursor, game_id, rewritten)


def create_collection(name: object, game_id: int | None = None) -> list[str]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        # Read the list under the write lock: two creates that both read the
        # old list would otherwise commit one after the other and lose one.
        _ = conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        collections = _read_collections(cursor)
        normalized = _validate_new_collection_name(name, collections)
        collections.append(normalized)
        _write_collections_setting(cursor, collections)
        if game_id is not None:
            members = _read_game_collections(cursor, game_id)
            if members is None:
                raise ValueError("Game not found")
            if normalized.casefold() not in {m.casefold() for m in members}:
                members.append(normalized)
                _write_game_collections(cursor, game_id, members)
        conn.commit()
    return collections


def rename_collection(old_name: object, new_name: object) -> list[str]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        _ = conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        collections = _read_collections(cursor)
        old_key = str(old_name or "").strip().casefold()
        old_index = next(
            (
                index
                for index, name in enumerate(collections)
                if name.casefold() == old_key
            ),
            None,
        )
        if old_index is None:
            raise ValueError(f"Collection '{old_name}' does not exist")
        new_normalized = str(new_name or "").strip()
        if not 1 <= len(new_normalized) <= 40:
            raise ValueError("Collection names must contain 1–40 characters")
        if new_normalized.casefold() in RESERVED_COLLECTION_KEYS:
            raise ValueError("'All' is reserved for the library-wide tab")
        new_key = new_normalized.casefold()
        if any(
            new_key == name.casefold()
            for index, name in enumerate(collections)
            if index != old_index
        ):
            raise ValueError(f"A collection named '{new_normalized}' already exists")
        collections[old_index] = new_normalized
        _write_collections_setting(cursor, collections)
        # Rename keeps members: rewrite matching names in the same transaction.
        _rewrite_game_collection_memberships(
            cursor, lambda names: [new_normalized if name.casefold() == old_key else name for name in names]
        )
        conn.commit()
    return collections


def delete_collection(name: object) -> list[str]:
    key = str(name or "").strip().casefold()
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        _ = conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        collections = _read_collections(cursor)
        if not any(name.casefold() == key for name in collections):
            raise ValueError(f"Collection '{name}' does not exist")
        remaining = [name for name in collections if name.casefold() != key]
        _write_collections_setting(cursor, remaining)
        # Games keep everything but this membership.
        _rewrite_game_collection_memberships(
            cursor, lambda names: [name for name in names if name.casefold() != key]
        )
        conn.commit()
    return remaining


def reorder_collections(names: Sequence[object]) -> list[str]:
    with closing(get_connection()) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        collections = _read_collections(cursor)
        canonical_by_key = {name.casefold(): name for name in collections}
        reordered: list[str] = []
        seen: set[str] = set()
        for name in names:
            key = str(name or "").strip().casefold()
            canonical = canonical_by_key.get(key)
            if canonical is None or key in seen:
                raise ValueError("Collection order must list every collection once")
            seen.add(key)
            reordered.append(canonical)
        if len(reordered) != len(collections):
            raise ValueError("Collection order must list every collection once")
        _write_collections_setting(cursor, reordered)
        conn.commit()
    return reordered


def set_game_collections(game_id: int, names: Sequence[object]) -> list[str]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        _ = conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        if _read_game_collections(cursor, game_id) is None:
            raise ValueError("Game not found")
        canonical_by_key = {
            name.casefold(): name for name in _read_collections(cursor)
        }
        members: list[str] = []
        seen: set[str] = set()
        for name in names:
            key = str(name or "").strip().casefold()
            canonical = canonical_by_key.get(key)
            if canonical is None:
                raise ValueError(f"Collection '{name}' does not exist")
            if key in seen:
                raise ValueError(f"Collection '{canonical}' is listed twice")
            seen.add(key)
            members.append(canonical)
        _write_game_collections(cursor, game_id, members)
        conn.commit()
    return members


# Filter Preset Operations
FILTER_PRESET_INSTALL_STATES = ("", "installed", "wishlist")
FILTER_PRESET_SORT_DIRECTIONS = ("asc", "desc")


def normalize_filter_preset(entry: object) -> dict[str, object] | None:
    if not isinstance(entry, Mapping):
        return None
    source = cast(Mapping[str, object], entry)
    raw_name = source.get("name")
    if not isinstance(raw_name, str):
        return None
    name = raw_name.strip()
    if not 1 <= len(name) <= 40:
        return None

    def string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in cast(list[object], value) if isinstance(item, str)]

    install_state = str(source.get("filterInstallState") or "")
    sort_dir = str(source.get("sortDir") or "")
    return {
        "name": name,
        "filterCollection": str(source.get("filterCollection") or ""),
        "filterStatuses": string_list(source.get("filterStatuses")),
        "filterInstallState": (
            install_state if install_state in FILTER_PRESET_INSTALL_STATES else ""
        ),
        "filterEngines": string_list(source.get("filterEngines")),
        "filterTags": string_list(source.get("filterTags")),
        "sortBy": str(source.get("sortBy") or ""),
        "sortDir": sort_dir if sort_dir in FILTER_PRESET_SORT_DIRECTIONS else "",
    }


def get_tag_colors() -> dict[str, str]:
    try:
        stored = cast(object, json.loads(get_setting("tag_colors") or "{}"))
    except (ValueError, TypeError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {
        name.strip().lower(): swatch
        for name, swatch in cast(dict[object, object], stored).items()
        if isinstance(name, str)
        and name.strip()
        and isinstance(swatch, str)
        and swatch in TAG_COLOR_SWATCHES
    }


def get_library_filter_presets() -> list[dict[str, object]]:
    try:
        stored = cast(object, json.loads(get_setting("library_filter_presets") or "[]"))
    except (ValueError, TypeError):
        return []
    if not isinstance(stored, list):
        return []
    # Imports and sync store this list unvalidated: keep only readable
    # presets with unique names.
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for entry in cast(list[object], stored):
        preset = normalize_filter_preset(entry)
        if preset is None:
            continue
        key = str(preset["name"]).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(preset)
    return result


class CompanionTool(TypedDict):
    id: int
    name: str
    kind: str
    path: str
    arguments: str
    show_on_all_games: bool
    sort_order: int


class GameCompanionTool(CompanionTool):
    visible: bool
    overridden: bool


def split_companion_arguments(
    arguments: str, game_exe: str = ""
) -> tuple[list[str], dict[str, str]]:
    """Split before expansion so paths containing spaces stay a single token."""
    tokens = shlex.split(arguments, posix=True)

    def expand(token: str) -> str:
        return token.replace("%game_dir%", os.path.dirname(game_exe)).replace(
            "%game_exe%", game_exe
        )

    env: dict[str, str] = {}
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        key, value = tokens.pop(0).split("=", 1)
        env[key] = expand(value)
    return [expand(token) for token in tokens], env


def normalize_companion_tool(
    fields: Mapping[str, object], *, validate_paths: bool = True
) -> dict[str, object]:
    name = fields.get("name", "")
    kind = fields.get("kind", "")
    path = fields.get("path", "")
    arguments = fields.get("arguments", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Name must not be empty")
    if kind not in ("wine", "native", "command"):
        raise ValueError("Kind must be Wine, Native or Command")
    if not isinstance(path, str) or not isinstance(arguments, str):
        raise ValueError("Path and arguments must be text")
    path = path.strip()
    try:
        tokens, _ = split_companion_arguments(arguments)
    except ValueError as exc:
        raise ValueError(
            "Command line / arguments cannot be parsed: " + str(exc)
        ) from exc
    if kind == "command":
        path = ""
        if not tokens or not tokens[0]:
            raise ValueError("Command line must contain a command")
    else:
        if kind == "wine" and os.path.splitext(path)[1].lower() not in (".exe", ".bat"):
            raise ValueError("Path: Wine tools need a .exe or .bat file")
        if not path or (
            validate_paths and not os.path.isfile(os.path.expanduser(path))
        ):
            raise ValueError("Path must point to an existing file")
        if validate_paths:
            path = os.path.abspath(os.path.expanduser(path))
    return dict(
        name=name.strip(),
        kind=kind,
        path=path,
        arguments=arguments,
        show_on_all_games=bool(fields.get("show_on_all_games", True)),
        sort_order=_coerce_sort_order(fields.get("sort_order", 0)),
    )


def _companion_row(row: sqlite3.Row) -> CompanionTool:
    return CompanionTool(
        id=int(str(cast(object, row["id"]))),
        name=str(cast(object, row["name"])),
        kind=str(cast(object, row["kind"])),
        path=str(cast(object, row["path"])),
        arguments=str(cast(object, row["arguments"])),
        show_on_all_games=bool(cast(object, row["show_on_all_games"])),
        sort_order=int(str(cast(object, row["sort_order"]))),
    )


def list_companion_tools() -> list[CompanionTool]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = cast(
            list[sqlite3.Row],
            conn.execute(
                "SELECT * FROM companion_tools ORDER BY sort_order, id"
            ).fetchall(),
        )
    return [_companion_row(row) for row in rows]


def get_companion_tool(tool_id: int) -> CompanionTool | None:
    return next(
        (tool for tool in list_companion_tools() if tool["id"] == tool_id), None
    )


def save_companion_tool(fields: Mapping[str, object]) -> CompanionTool:
    tool = normalize_companion_tool(fields)
    tool_id = fields.get("id")
    now = datetime.now().isoformat()
    with closing(get_connection()) as conn:
        try:
            if tool_id is None:
                cursor = conn.execute(
                    "INSERT INTO companion_tools (name, kind, path, arguments, show_on_all_games, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (*tool.values(), now, now),
                )
                tool_id = cursor.lastrowid
            else:
                cursor = conn.execute(
                    "UPDATE companion_tools SET name=?, kind=?, path=?, arguments=?, show_on_all_games=?, sort_order=?, updated_at=? WHERE id=?",
                    (*tool.values(), now, tool_id),
                )
                if cursor.rowcount == 0:
                    raise ValueError("Tool was not found")
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Name is already used by another tool") from exc
    saved = get_companion_tool(int(str(tool_id)))
    assert saved is not None
    return saved


def delete_companion_tool(tool_id: int) -> bool:
    with closing(get_connection()) as conn:
        cursor = conn.execute("DELETE FROM companion_tools WHERE id=?", (tool_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_game_companion_tools(game_id: int) -> list[GameCompanionTool]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = cast(
            list[sqlite3.Row],
            conn.execute(
                "SELECT t.*, o.visible FROM companion_tools t LEFT JOIN game_companion_tools o ON o.tool_id=t.id AND o.game_id=? ORDER BY t.sort_order, t.id",
                (game_id,),
            ).fetchall(),
        )
    return [
        GameCompanionTool(
            **_companion_row(row),
            visible=bool(cast(object, row["visible"]))
            if row["visible"] is not None
            else bool(cast(object, row["show_on_all_games"])),
            overridden=row["visible"] is not None,
        )
        for row in rows
    ]


def set_game_companion_tool_override(
    game_id: int, tool_id: int, visible: object
) -> None:
    if visible is not None and not isinstance(visible, bool):
        raise ValueError("Visible must be true, false or null")
    with closing(get_connection()) as conn:
        if visible is None:
            _ = conn.execute(
                "DELETE FROM game_companion_tools WHERE game_id=? AND tool_id=?",
                (game_id, tool_id),
            )
        else:
            _ = conn.execute(
                "INSERT INTO game_companion_tools (game_id, tool_id, visible) VALUES (?, ?, ?) ON CONFLICT(game_id, tool_id) DO UPDATE SET visible=excluded.visible",
                (game_id, tool_id, visible),
            )
        conn.commit()
