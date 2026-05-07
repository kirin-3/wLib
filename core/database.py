# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlite3
import os
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import datetime
from typing import TypedDict, cast

from core.f95zone import normalize_thread_url, thread_urls_match

DATA_DIR = os.path.expanduser("~/.local/share/wLib")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "wlib.db")

DEFAULT_PLAY_STATUS = "Not Started"
DEFAULT_LAUNCH_MODE = "auto"
RPGMAKER_LINUX_LAUNCH_MODE = "rpgmaker_linux"
RPGMAKER_LINUX_RUNNER_SETTING = "rpgmaker_linux_runner_path"
CANONICAL_LAUNCH_MODES = (
    DEFAULT_LAUNCH_MODE,
    "native",
    "wine_proton",
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
        return max(0, int(str(sort_order)))
    except (TypeError, ValueError):
        return 0


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
        _ = cursor.execute(
            f"INSERT OR IGNORE INTO settings (key, value) VALUES ('{RPGMAKER_LINUX_RUNNER_SETTING}', '')"
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
            ("launch_mode", f"TEXT DEFAULT '{DEFAULT_LAUNCH_MODE}'"),
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
        launch_mode_placeholders = ", ".join("?" for _ in CANONICAL_LAUNCH_MODES)
        _ = cursor.execute(
            f"UPDATE games SET launch_mode = ? WHERE launch_mode IS NULL OR TRIM(launch_mode) = '' OR launch_mode NOT IN ({launch_mode_placeholders})",
            (DEFAULT_LAUNCH_MODE, *CANONICAL_LAUNCH_MODES),
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
            "INSERT INTO games (title, exe_path, f95_url, version, cover_image_path, tags, rating, developer, engine, run_japanese_locale, run_wayland, auto_inject_ce, custom_prefix, proton_version, launch_mode, date_added, play_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                normalize_launch_mode(launch_mode),
                now_iso,
                DEFAULT_PLAY_STATUS,
            ),
        )
        game_id = cursor.lastrowid
        conn.commit()
    return game_id


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

    placeholders = ",".join("?" for _ in normalized_ids)
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = cursor.execute(
            f"SELECT * FROM game_launch_targets WHERE game_id IN ({placeholders}) ORDER BY game_id ASC, sort_order ASC, id ASC",
            tuple(normalized_ids),
        )
        rows = cast(list[sqlite3.Row], cursor.fetchall())

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


def get_all_games() -> list[dict[str, object]]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = cursor.execute("SELECT * FROM games ORDER BY title ASC")
        games = cast(list[sqlite3.Row], cursor.fetchall())
    targets_by_game = list_launch_targets_for_games(
        [int(str(cast(object, game["id"]))) for game in games]
    )
    result: list[dict[str, object]] = []
    for game in games:
        game_dict = {str(key): cast(object, game[key]) for key in game.keys()}
        game_dict["launch_mode"] = normalize_launch_mode(game_dict.get("launch_mode"))
        game_id = int(str(game_dict["id"]))
        game_dict["launch_targets"] = targets_by_game.get(game_id, [])
        result.append(game_dict)
    return result


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
        "launch_mode",
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
    if "launch_mode" in safe_fields:
        safe_fields["launch_mode"] = normalize_launch_mode(safe_fields["launch_mode"])

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
