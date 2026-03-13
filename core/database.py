from __future__ import annotations

import sqlite3
import os
from collections.abc import Mapping
from contextlib import closing
from datetime import datetime
from typing import cast

from core.f95zone import normalize_thread_url, thread_urls_match

DATA_DIR = os.path.expanduser("~/.local/share/wLib")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "wlib.db")

DEFAULT_PLAY_STATUS = "Not Started"
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


def _normalize_status_key(value: object) -> str:
    return str(value or "").strip().lower()


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


def _find_matching_game_row(
    cursor: sqlite3.Cursor, url: object, exclude_id: int | None = None
) -> sqlite3.Row | None:
    lookup_url = normalize_thread_url(url)
    if not lookup_url:
        return None

    query = "SELECT id, title, f95_url, play_status FROM games WHERE f95_url IS NOT NULL AND TRIM(f95_url) != ''"
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


def find_game_by_f95_url(
    url: object, exclude_id: int | None = None
) -> dict[str, object] | None:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        row = _find_matching_game_row(cursor, url, exclude_id=exclude_id)
    if row is None:
        return None
    return {str(key): cast(object, row[key]) for key in row.keys()}


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    _ = conn.execute("PRAGMA journal_mode=WAL;")
    _ = conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
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
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_update_check', 'weekly')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('last_update_check', '')"
        )
        _ = cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('playwright_browsers_path', ?)",
            (os.path.expanduser("~/.cache/ms-playwright"),),
        )

        # Safely migrate existing DBs by adding new columns
        _ = cursor.execute("PRAGMA table_info(games)")
        table_info_rows = cast(list[tuple[object, ...]], cursor.fetchall())
        existing_columns = {
            str(row[1]) for row in table_info_rows if len(row) > 1 and row[1]
        }

        new_columns = [
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
            ("custom_prefix", "TEXT DEFAULT ''"),
            ("proton_version", "TEXT DEFAULT ''"),
            ("playtime_seconds", "INTEGER DEFAULT 0"),
            ("last_played", "TIMESTAMP"),
            ("date_added", "TIMESTAMP"),
            ("play_status", f"TEXT DEFAULT '{DEFAULT_PLAY_STATUS}'"),
            ("is_favorite", "BOOLEAN DEFAULT 0"),
            ("thread_main_post_last_edit_at", "TIMESTAMP"),
            ("thread_main_post_checked_at", "TIMESTAMP"),
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
) -> int | None:
    # tags might be a list, so convert to comma-separated string if needed
    if isinstance(tags, list):
        tags = ", ".join(tags)

    normalized_url = normalize_thread_url(f95_url)

    now_iso = datetime.now().isoformat()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if (
            normalized_url
            and _find_matching_game_row(cursor, normalized_url) is not None
        ):
            raise sqlite3.IntegrityError("duplicate f95_url")

        _ = cursor.execute(
            "INSERT INTO games (title, exe_path, f95_url, version, cover_image_path, tags, rating, developer, engine, run_japanese_locale, run_wayland, auto_inject_ce, custom_prefix, proton_version, date_added, play_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                run_japanese_locale,
                run_wayland,
                auto_inject_ce,
                custom_prefix,
                proton_version,
                now_iso,
                DEFAULT_PLAY_STATUS,
            ),
        )
        game_id = cursor.lastrowid
        conn.commit()
    return game_id


def get_all_games() -> list[dict[str, object]]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = cursor.execute("SELECT * FROM games ORDER BY title ASC")
        games = cast(list[sqlite3.Row], cursor.fetchall())
    return [
        {str(key): cast(object, game[key]) for key in game.keys()} for game in games
    ]


def update_game_version(game_id: int, version: str) -> None:
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _ = cursor.execute(
            "UPDATE games SET version = ? WHERE id = ?", (version, game_id)
        )
        conn.commit()


def delete_game(game_id: int) -> None:
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _ = cursor.execute("DELETE FROM games WHERE id = ?", (game_id,))
        conn.commit()


def update_game(game_id: int, fields: Mapping[str, object]) -> None:
    """Update arbitrary fields on a game row. `fields` is a dict of column->value."""
    if not fields:
        return
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
        "status",
        "rating_graphics",
        "rating_story",
        "rating_fappability",
        "rating_gameplay",
        "engine",
        "latest_version",
        "run_japanese_locale",
        "run_wayland",
        "auto_inject_ce",
        "custom_prefix",
        "proton_version",
        "play_status",
        "is_favorite",
    }
    # Only allow known columns
    safe_fields: dict[str, object] = {
        k: v for k, v in fields.items() if k in allowed and v is not None
    }
    if not safe_fields:
        return

    if "f95_url" in safe_fields:
        safe_fields["f95_url"] = normalize_thread_url(safe_fields["f95_url"])

    if "play_status" in safe_fields:
        safe_fields["play_status"] = normalize_play_status(safe_fields["play_status"])

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        updated_url = safe_fields.get("f95_url")
        if updated_url and _find_matching_game_row(
            cursor,
            str(updated_url),
            exclude_id=game_id,
        ):
            raise sqlite3.IntegrityError("duplicate f95_url")

        set_clause = ", ".join([f"{k} = ?" for k in safe_fields.keys()])
        values: tuple[object, ...] = tuple(safe_fields.values()) + (game_id,)
        _ = cursor.execute(f"UPDATE games SET {set_clause} WHERE id = ?", values)
        conn.commit()


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
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _ = cursor.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        conn.commit()
