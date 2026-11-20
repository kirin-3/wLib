# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import sqlite3
import shlex
import stat
import types
import re
import time
import os
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime
from typing import NamedTuple, Protocol, TypedDict, cast
from urllib.parse import urlsplit

from core.f95zone import normalize_thread_url, thread_urls_match
from core.host_platform import (
    WINDOWS_FOLDER_DOWNLOADS,
    get_data_dir,
    is_windows,
    windows_known_folder,
)
from core.store_links import (
    LINK_ORIGINS,
    MATCHABLE_SOURCES,
    StoreLink,
    normalize_store_links,
)
from core import gamescope, lewdcorner
from core.versions import normalize_version
from core.thread_details import (
    BASELINE_COLUMN,
    LIST_FIELDS,
    THREAD_DETAIL_FIELDS,
    normalize_detail,
)
from core.thread_details import column_value as thread_detail_column_value

DATA_DIR = get_data_dir()
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    # main.py checks the folder next and shows the user why it cannot be
    # used; failing here at import would crash before that dialog.
    pass
DB_PATH = os.path.join(DATA_DIR, "wlib.db")
# Copies of wlib.db made before a migration changes it; device-local.
DB_BACKUP_DIR_NAME = "db-backups"
DB_BACKUP_KEEP = 3
_DB_BACKUP_RE = re.compile(r"wlib-\d{8}-\d{6}\.db")

DEFAULT_PLAY_STATUS = "Not Started"
DEFAULT_LAUNCH_MODE = "auto"
RPGMAKER_LINUX_LAUNCH_MODE = "rpgmaker_linux"
RPGMAKER_LINUX_RUNNER_SETTING = "rpgmaker_linux_runner_path"
DEFAULT_LIBRARY_FOLDER_PATTERN = "{creator}/{title}/{version}"
GENERAL_SETTINGS_KEYS = (
    *lewdcorner.LC_SETTINGS,
    lewdcorner.LC_PLUS_MODE,
    "enable_logging",
    "auto_update_check",
    "update_notifications",
    "custom_play_statuses",
    "collections",
    "library_filter_presets",
    "tag_colors",
    "save_backup_keep",
    "sandbox_new_games",
    "gamemode_enabled",
    "mangohud_enabled",
    "gamescope_enabled",
    "gamescope_window_mode",
    "gamescope_output_resolution",
    "gamescope_game_resolution",
    "gamescope_scaler",
    "gamescope_filter",
    "gamescope_fps_limit",
    "gamescope_args",
    "html_builtin_player",
    "web_player_devtools",
    "torrent_seed_mode",
    "torrent_seed_ratio",
    "torrent_seed_hours",
    # True once the F95 account import gave its two list tags default colors.
    "f95_list_tag_colors_seeded",
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
    "block_network",
    "html_player",
    "title",
    "developer",
    "engine",
    "tags",
    "tags_user_edited",
    "f95_tags",
    "f95_url",
    "lc_url",
    "lc_mode",
    "lc_dismissed",
    "version",
    "latest_version",
    "f95_latest_version",
    "f95_latest_at",
    "lc_latest_version",
    "lc_latest_at",
    "latest_site",
    "thread_completed",
    "thread_onhold",
    "thread_abandoned",
    # Not `thread_states_at`: every live read writes it, so watching it would
    # mark each checked game as changed for sync. It travels with the states.
    "cover_image_path",
    "play_status",
    "finished_version",
    "last_played_version",
    "skipped_version",
    "is_favorite",
    "rating",
    "rating_updated_at",
    "rating_graphics",
    "rating_story",
    "rating_fappability",
    "rating_gameplay",
    "progress",
    "note_links",
    "playtime_seconds",
    "last_played",
    "date_added",
    "collections",
    *THREAD_DETAIL_FIELDS,
    BASELINE_COLUMN,
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
    "Archived",
)

_CANONICAL_PLAY_STATUS_MAP = {
    status.lower(): status for status in CANONICAL_PLAY_STATUSES
}
_LEGACY_PLAY_STATUS_MAP = {
    "completed": "Completed",
    "in_progress": "Playing",
    "replaying": "Playing",
    "waiting_update": "Waiting For Update",
    # The play status "Abandoned" was renamed to "Archived".
    "abandoned": "Archived",
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


def local_now_iso() -> str:
    """Local time with its UTC offset, so synced times read the same anywhere."""
    return datetime.now().astimezone().isoformat()


def with_local_offset(value: object) -> object:
    """Add this device's offset to a stored time written without one."""
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return value
    if moment.tzinfo is not None:
        return value
    return moment.astimezone().isoformat()


def _normalize_status_key(value: object) -> str:
    return str(value or "").strip().lower()


def default_downloads_folder() -> str:
    """OS Downloads folder: `xdg-user-dir DOWNLOAD` with `~/Downloads` fallback."""
    fallback = os.path.abspath(os.path.expanduser(os.path.join("~", "Downloads")))
    if is_windows():
        # The Downloads folder can be moved off the profile (Properties > Location).
        return windows_known_folder(WINDOWS_FOLDER_DOWNLOADS) or fallback
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


def normalize_html_player(value: object) -> str:
    """Per-game HTML player choice: '' (use the setting), 'builtin' or 'browser'."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"builtin", "browser"} else ""


def html_builtin_player_enabled() -> bool:
    """The "Play HTML games in the built-in player" setting; absent reads as on."""
    return get_setting("html_builtin_player") != "false"


def web_player_devtools_enabled() -> bool:
    """The "Web player DevTools (F12)" setting; absent reads as on."""
    return get_setting("web_player_devtools") != "false"


def normalize_tool_override(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"default", "on", "off"} else "default"


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


ARCHIVED_PLAY_STATUS = "Archived"


def is_archived(game: Mapping[str, object]) -> bool:
    """Whether a game has the Archived play status (or the former "Abandoned")."""
    return normalize_play_status(game.get("play_status")) == ARCHIVED_PLAY_STATUS


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


_ARCHIVED_STATUS_ALIASES = {"archived", "abandoned"}


def _migrate_archived_status_settings(cursor: sqlite3.Cursor) -> None:
    """Move the settings that name the former "Abandoned" status to "Archived".

    The game rows are already normalized by `_normalize_game_play_statuses`.
    A custom status named "Archived" or "Abandoned" is dropped, because its
    games now carry the built-in status, and saved filter presets select
    "Archived" instead. Running it again changes nothing.
    """
    def read_list(key: str) -> list[object] | None:
        row = cast(
            sqlite3.Row | None,
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone(),
        )
        if row is None:
            return None
        try:
            value = cast(object, json.loads(str(cast(object, row[0]) or "[]")))
        except (ValueError, TypeError):
            return None
        return cast(list[object], value) if isinstance(value, list) else None

    def write(key: str, value: list[object]) -> None:
        _ = cursor.execute(
            "UPDATE settings SET value = ? WHERE key = ?", (json.dumps(value), key)
        )

    statuses = read_list("custom_play_statuses")
    if statuses is not None:
        kept = [
            status
            for status in statuses
            if not (
                isinstance(status, str)
                and status.strip().casefold() in _ARCHIVED_STATUS_ALIASES
            )
        ]
        if len(kept) != len(statuses):
            write("custom_play_statuses", kept)

    presets = read_list("library_filter_presets")
    if presets is not None:
        changed = False
        for preset in presets:
            if not isinstance(preset, dict):
                continue
            entry = cast(dict[str, object], preset)
            raw = entry.get("filterStatuses")
            if not isinstance(raw, list):
                continue
            rewritten = _archived_filter_statuses(cast(list[object], raw))
            if rewritten != raw:
                entry["filterStatuses"] = rewritten
                changed = True
        if changed:
            write("library_filter_presets", presets)


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


# The thread On Hold and Abandoned states: NULL is unknown, 0 not set, 1 set.
THREAD_STATE_COLUMNS = ("thread_onhold", "thread_abandoned")


def coerce_thread_state(value: object) -> int | None:
    """Keep the unknown state (None or an unreadable value) apart from 0 and 1."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int | float):
        return 1 if value != 0 else 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return 1
    if text in {"0", "false", "no", "off"}:
        return 0
    return None


# When the stored thread states were read (UTC ISO time); NULL when unknown.
THREAD_STATES_AT_COLUMN = "thread_states_at"


class ThreadStates(NamedTuple):
    """The On Hold and Abandoned prefixes of one thread read.

    `seen_at` is when the catalog builder last read the thread; it is empty
    for a live read (thread page or LC feed), which happens now.
    """

    onhold: bool
    abandoned: bool
    seen_at: str = ""


def thread_states_from(source: Mapping[str, object]) -> ThreadStates | None:
    """The states in a page result or catalog entry; None when it has none."""
    onhold = source.get("thread_onhold")
    abandoned = source.get("thread_abandoned")
    if onhold is None or abandoned is None:
        return None
    seen_at = source.get("thread_states_seen_at")
    return ThreadStates(bool(onhold), bool(abandoned), seen_at if isinstance(seen_at, str) else "")


def _reset_thread_states(fields: dict[str, object], *, force: bool = False) -> None:
    """Clear the On Hold and Abandoned states and their time after a thread change.

    Without `force`, states the same update sets stay.
    """
    if not force and any(column in fields for column in THREAD_STATE_COLUMNS):
        return
    for column in (*THREAD_STATE_COLUMNS, THREAD_STATES_AT_COLUMN):
        fields[column] = None


def thread_states_unknown(game: Mapping[str, object]) -> bool:
    """Whether no read has stored the game's On Hold and Abandoned states yet."""
    return all(game.get(column) is None for column in THREAD_STATE_COLUMNS)


def site_thread_states(
    game: Mapping[str, object],
    f95: ThreadStates | None,
    lc: ThreadStates | None,
) -> ThreadStates | None:
    """The states to store from an F95 and an LC read of the same game.

    F95 decides for a game with an F95 thread. LC states apply to such a game
    only while its states are unknown: once an F95 read stored them, an LC
    read that differs would undo it, and the next F95 read would flip it back
    and repeat the abandoned alert.
    """
    if f95 is not None:
        return f95
    if lc is None:
        return None
    if not str(game.get("f95_url") or "").strip() or thread_states_unknown(game):
        return lc
    return None


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def caller_thread_states(
    onhold: object, abandoned: object, seen_at: object, *, has_f95: bool
) -> tuple[bool, bool, str] | None:
    """The On Hold and Abandoned states an add or install got from its caller.

    States with `seen_at` came from the catalog and keep that time. Without
    it they came from an LC page read just now: they apply with the time now
    to an LC-only game, and never to a game with an F95 thread, where F95
    decides (its catalog entry or the thread scrape that follows fills them).
    """
    if not isinstance(onhold, bool) or not isinstance(abandoned, bool):
        return None
    seen = coerce_thread_states_at(seen_at)
    if seen:
        return onhold, abandoned, seen
    if has_f95:
        return None
    return onhold, abandoned, utc_now_iso()


def coerce_thread_states_at(value: object) -> str | None:
    """A readable time of the stored thread states, or None (unknown)."""
    return value.strip() if isinstance(value, str) and _utc_moment(value) else None


def _utc_moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def thread_state_fields(
    game: Mapping[str, object], states: ThreadStates | None
) -> tuple[dict[str, object], bool]:
    """The columns to write for `states`, and whether the game is newly abandoned.

    Newly abandoned means the stored state goes from known not Abandoned (0)
    to Abandoned, on a game that is not Archived. A change from unknown
    (NULL) never counts, so the first fill after an update sends no alert.

    Catalog states older than the stored read are ignored: the builder may not
    have seen a thread since its prefix changed, and writing its old state
    would undo a newer page or feed read and repeat the alert on the next one.
    """
    if states is None:
        return {}, False
    stored_at = _utc_moment(game.get(THREAD_STATES_AT_COLUMN))
    seen_at = _utc_moment(states.seen_at)
    if states.seen_at and (seen_at is None or (stored_at is not None and seen_at <= stored_at)):
        return {}, False
    fields: dict[str, object] = {}
    for column, value in zip(THREAD_STATE_COLUMNS, (states.onhold, states.abandoned), strict=True):
        if game.get(column) != int(value):
            fields[column] = int(value)
    read_at = states.seen_at or utc_now_iso()
    if game.get(THREAD_STATES_AT_COLUMN) != read_at:
        fields[THREAD_STATES_AT_COLUMN] = read_at
    old_abandoned = game.get("thread_abandoned")
    newly_abandoned = (
        states.abandoned
        and old_abandoned is not None
        and not old_abandoned
        and not is_archived(game)
    )
    return fields, bool(newly_abandoned)


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


def _find_lc_game_row(
    conn: sqlite3.Connection, url: str, exclude_id: int | None = None
) -> sqlite3.Row | None:
    return cast(sqlite3.Row | None, conn.execute(
        "SELECT id, title, f95_url, lc_url, play_status, exe_path FROM games "
        + "WHERE lc_url = ? AND lc_url != '' AND (? IS NULL OR id != ?) ORDER BY id LIMIT 1",
        (url, exclude_id, exclude_id),
    ).fetchone())


def find_game_by_lc_url(url: object, exclude_id: int | None = None) -> dict[str, object] | None:
    normalized = lewdcorner.normalize_thread_url(url)
    if not normalized:
        return None
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = _find_lc_game_row(conn, normalized, exclude_id)
    return {str(key): cast(object, row[key]) for key in row.keys()} if row else None


def fill_lc_threads(partners: Mapping[str, tuple[str, str]]) -> int:
    """Attach catalog partners atomically without replacing or reviving a thread."""
    from core.f95zone import extract_thread_id

    if not partners:
        return 0
    count = 0
    with closing(get_connection()) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        settings = dict(cast(list[tuple[str, str]], conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('lc_enabled', ?, ?)",
            (lewdcorner.LC_PLUS_MODE, lewdcorner.LC_PLUS_DETECTED),
        ).fetchall()))
        if not lewdcorner.setting_enabled(settings.get("lc_enabled")):
            return 0
        has_plus = lewdcorner.plus_effective(
            settings.get(lewdcorner.LC_PLUS_MODE), settings.get(lewdcorner.LC_PLUS_DETECTED)
        )
        rows = cast(list[tuple[int, str, str]], conn.execute(
            "SELECT id, f95_url, lc_dismissed FROM games "
            + "WHERE f95_url != '' AND COALESCE(lc_url, '') = '' ORDER BY id"
        ).fetchall())
        claimed = {url for (url,) in cast(list[tuple[str]], conn.execute(
            "SELECT lc_url FROM games WHERE lc_url != ''"
        ).fetchall())}
        for game_id, f95_url, dismissed in rows:
            partner = partners.get(extract_thread_id(f95_url))
            if partner is None:
                continue
            thread_id, tier = partner
            if thread_id == dismissed or (tier.casefold() == "premium" and not has_plus):
                continue
            url = f"https://lewdcorner.com/threads/{thread_id}/"
            if url not in claimed:
                _ = conn.execute("UPDATE games SET lc_url = ? WHERE id = ?", (url, game_id))
                claimed.add(url)
                count += 1
        conn.commit()
    return count


def attach_lc_thread(game_id: int, url: str, f95_url: str) -> bool:
    """Attach to a still-matching, empty row. A concurrent edit wins."""
    normalized = lewdcorner.validated_thread_url(url)
    if not normalized:
        return False
    with closing(get_connection()) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        conn.row_factory = sqlite3.Row
        row = cast(sqlite3.Row | None, conn.execute(
            "SELECT f95_url, lc_url FROM games WHERE id = ?", (game_id,)
        ).fetchone())
        if (row is None or row["lc_url"]
            or not thread_urls_match(cast(object, row["f95_url"]), f95_url)
            or _find_lc_game_row(conn, normalized)):
            return False
        _ = conn.execute("UPDATE games SET lc_url = ? WHERE id = ?", (normalized, game_id))
        conn.commit()
    return True


# wlib.db holds host account secrets (core/downloads/credentials.py), so it
# and its copies are owner-only. SQLite gives new -wal, -shm and -journal
# files the main file's mode.
_DB_SIDE_SUFFIXES = ("-wal", "-shm", "-journal")


def _create_private_file(path: str) -> None:
    """Create an empty owner-only file; an existing file is left alone."""
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError:
        pass  # It exists, or sqlite3.connect reports the folder it cannot use.


def _make_private(path: str) -> None:
    """Remove group and other access from one wLib file or folder.

    On Windows chmod only reads the owner write bit, so this changes nothing.
    """
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            os.chmod(path, mode & ~0o077)
    except OSError:
        pass


def _restrict_db_files() -> None:
    """Make the database, its WAL files and the migration copies owner-only."""
    for path in (DB_PATH, *(DB_PATH + suffix for suffix in _DB_SIDE_SUFFIXES)):
        _make_private(path)
    folder = db_backup_dir()
    if os.path.isdir(folder):
        _make_private(folder)
        for name in list_db_backups():
            _make_private(os.path.join(folder, name))


def get_connection() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        # An empty file is a new SQLite database; making it here sets the
        # mode before any secret is written.
        _create_private_file(DB_PATH)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    _ = conn.execute("PRAGMA foreign_keys=ON;")
    return conn


class _Digest(Protocol):
    def update(self, data: bytes, /) -> None: ...


def _stable_repr(value: object) -> str:
    """repr() that does not depend on hash randomization (set order)."""
    kind = type(value)
    if kind in (set, frozenset):
        items = sorted(_stable_repr(item) for item in cast(set[object], value))
        return f"{type(value).__name__}({items!r})"
    if kind in (tuple, list):
        items = [_stable_repr(item) for item in cast(list[object], value)]
        return f"{type(value).__name__}({items!r})"
    if kind is dict:
        pairs = sorted(
            f"{_stable_repr(key)}:{_stable_repr(item)}"
            for key, item in cast(dict[object, object], value).items()
        )
        return f"dict({pairs!r})"
    return repr(value)


def _code_digest(code: types.CodeType, digest: _Digest, seen: set[str]) -> None:
    digest.update(code.co_code)
    digest.update(repr(code.co_names).encode())
    for const in cast(tuple[object, ...], code.co_consts):
        if isinstance(const, types.CodeType):
            _code_digest(const, digest, seen)
        else:
            digest.update(_stable_repr(const).encode())
    for name in code.co_names:
        if name in seen:
            continue
        seen.add(name)
        value = cast(object, globals().get(name))
        if isinstance(value, types.FunctionType) and value.__module__ == __name__:
            _code_digest(value.__code__, digest, seen)
        elif type(value) in (str, int, float, tuple, list, dict, set, frozenset):
            digest.update(f"{name}={_stable_repr(value)}".encode())


def schema_fingerprint() -> int:
    """A non-zero 31-bit number that changes whenever the migrations change.

    It covers `_migrate_schema`'s code, the helpers in this module it calls
    and the constants they read, so editing any of them reruns the
    migrations once; no version number has to be bumped by hand.
    """
    digest = hashlib.sha256()
    _code_digest(_migrate_schema.__code__, digest, set())
    return (int.from_bytes(digest.digest()[:4], "big") & 0x7FFFFFFF) or 1


def _schema_state(conn: sqlite3.Connection, fingerprint: int) -> int:
    """Fingerprint of the migrations combined with the schema they left.

    Including the stored table, index and trigger SQL means an older wLib
    that ran its own migrations on this file (a downgrade, or another install
    sharing the data folder) changes the value, so the next start migrates
    again instead of trusting a schema it did not leave.
    """
    digest = hashlib.sha256(str(fingerprint).encode())
    for (sql,) in cast(
        list[tuple[str | None]],
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
        ).fetchall(),
    ):
        digest.update(b"\0" + (sql or "").encode())
    return (int.from_bytes(digest.digest()[:4], "big") & 0x7FFFFFFF) or 1


class DatabaseDamagedError(RuntimeError):
    """`wlib.db` failed SQLite's own check; the message tells the user what to do."""


def db_backup_dir() -> str:
    return os.path.join(os.path.dirname(DB_PATH), DB_BACKUP_DIR_NAME)


def list_db_backups() -> list[str]:
    """Names of the pre-migration copies, oldest first."""
    try:
        names = os.listdir(db_backup_dir())
    except OSError:
        return []
    return sorted(name for name in names if _DB_BACKUP_RE.fullmatch(name))


def _check_database(conn: sqlite3.Connection) -> None:
    """Raise `DatabaseDamagedError` when SQLite reports a damaged file."""
    try:
        rows = cast(
            list[tuple[object]], conn.execute("PRAGMA quick_check(1)").fetchall()
        )
        detail = "" if rows == [("ok",)] else str(rows[0][0] if rows else "no result")
    except sqlite3.OperationalError:
        # Locked or unreadable, not damaged; the caller's own query reports it.
        raise
    except sqlite3.DatabaseError as exc:
        detail = str(exc)
    if not detail:
        return
    detail = " ".join(detail.replace("*** in database main ***", "").split())
    backups = list_db_backups()
    if backups:
        recovery = (
            f"wLib made copies before earlier updates in {db_backup_dir()} "
            f"(newest: {backups[-1]}). To use one, close wLib, move wlib.db, "
            "wlib.db-wal and wlib.db-shm out of the data folder, then put the "
            "copy there with the name wlib.db."
        )
    else:
        recovery = (
            "There is no local copy. Move wlib.db, wlib.db-wal and wlib.db-shm "
            "out of the data folder to start with an empty library, then "
            "import a library backup or connect sync again."
        )
    raise DatabaseDamagedError(
        f"The library database {DB_PATH} is damaged ({detail}). {recovery}"
    )


def _backup_before_migration(conn: sqlite3.Connection) -> str | None:
    """Copy the database to `db-backups/` and keep the newest few copies.

    A failed copy is reported and does not stop the start: refusing to open
    the library because the disk is full would be worse.
    """
    folder = db_backup_dir()
    target = os.path.join(folder, f"wlib-{datetime.now():%Y%m%d-%H%M%S}.db")
    partial = f"{target}.{os.getpid()}.partial"
    if os.path.exists(target):
        # Made this same second, so it already holds the state before this
        # update; a second copy could hold a half-updated one.
        return target
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        _make_private(folder)
        if os.path.exists(partial):
            os.remove(partial)
        # The copy holds the same secrets as wlib.db.
        _create_private_file(partial)
        # The backup API also copies pages that are still in the WAL file.
        with closing(sqlite3.connect(partial)) as copy:
            conn.backup(copy)
        os.replace(partial, target)
    except (OSError, sqlite3.Error) as exc:
        print(f"[wLib] Could not copy the database before its update: {exc}")
        try:
            os.remove(partial)
        except OSError:
            pass
        return None
    for name in list_db_backups()[:-DB_BACKUP_KEEP]:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
    return target


def init_db() -> None:
    """Create or upgrade the schema, skipping the work when it is current.

    `PRAGMA user_version` holds `_schema_state` from the last migration; a
    fresh database starts at 0, so it always migrates. Every call first runs
    SQLite's quick check, and a database that already holds a library is
    copied to `db-backups/` before the migrations change it.
    """
    _restrict_db_files()
    conn = get_connection()
    try:
        _check_database(conn)
        fingerprint = schema_fingerprint()
        row = cast(tuple[int] | None, conn.execute("PRAGMA user_version").fetchone())
        if row is not None and row[0] == _schema_state(conn, fingerprint):
            return
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'games'"
        ).fetchone():
            _ = _backup_before_migration(conn)
        conn.row_factory = sqlite3.Row
        # WAL is stored in the database file, so setting it once covers every connection.
        _ = conn.execute("PRAGMA journal_mode=WAL;")
        _migrate_schema(conn)
        conn.row_factory = None
        state = _schema_state(conn, fingerprint)
        # PRAGMA takes no parameters; the value is an int computed here.
        _ = conn.execute(f"PRAGMA user_version = {int(state)}")
        conn.commit()
    finally:
        conn.close()


def _migrate_schema(conn: sqlite3.Connection) -> None:
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
        CREATE TABLE IF NOT EXISTS steam_shortcuts (
            account_id TEXT NOT NULL,
            game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            app_id INTEGER,
            state TEXT NOT NULL,
            art_key TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(account_id, game_id)
        )
    """)
    for key in ("steam_games_mode", "steam_account_id"):
        _ = cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, '')", (key,))

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
    # itch slugs used to keep their case; fold them so identities match exactly.
    _ = cursor.execute(
        "UPDATE OR IGNORE game_links SET external_id = lower(external_id) WHERE source = 'itch' AND external_id <> lower(external_id)"
    )
    _ = cursor.execute(
        "DELETE FROM game_links WHERE source = 'itch' AND external_id <> lower(external_id)"
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
    # Device-local download queue (core/downloads/queue.py). No foreign key:
    # the queue deletes a removed game's jobs together with their data. The
    # rows never hold a download link, tracker address or passkey, and are
    # never exported or synced.
    _ = cursor.execute("""
        CREATE TABLE IF NOT EXISTS download_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            game_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '',
            error_text TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '{}',
            bytes_done INTEGER NOT NULL DEFAULT 0,
            bytes_total INTEGER,
            retries INTEGER NOT NULL DEFAULT 0,
            retry_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            finished_at REAL
        )
    """)
    _ = cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_download_jobs_position ON download_jobs(position)"
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
    for key in lewdcorner.LC_SETTINGS:
        _ = cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, '0')", (key,))
    # The old "I have LC Plus" switch: on becomes Yes, off becomes Auto. Once only.
    _ = cursor.execute(
        "INSERT OR IGNORE INTO settings (key, value) SELECT ?, CASE WHEN lower(trim(value)) "
        + "IN ('1', 'true', 'on', 'yes') THEN 'yes' ELSE 'auto' END FROM settings WHERE key = 'lc_plus'",
        (lewdcorner.LC_PLUS_MODE,),
    )
    _ = cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, 'auto')", (lewdcorner.LC_PLUS_MODE,))
    for key in ("gamemode_enabled", "mangohud_enabled"):
        _ = cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, 'false')", (key,))
    for key, value in gamescope.SETTINGS_DEFAULTS.items():
        _ = cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    _ = cursor.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('sandboxie_custom_path', '')"
    )
    _ = cursor.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('locale_emulator_path', '')"
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
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('library_folders', '[]')"
    )
    _ = cursor.execute(
        """UPDATE settings SET value = json_array((SELECT value FROM settings WHERE key = 'library_folder'))
        WHERE key = 'library_folders' AND value = '[]'
        AND COALESCE((SELECT value FROM settings WHERE key = 'library_folder'), '') != ''"""
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
        ("lc_url", "TEXT DEFAULT ''"),
        ("lc_mode", "TEXT DEFAULT ''"),
        ("lc_dismissed", "TEXT DEFAULT ''"),
        ("steam_shortcut", "BOOLEAN DEFAULT 0"),
        ("cover_local", "TEXT"),
        ("cover_cache", "TEXT"),
        ("cover_cache_url", "TEXT"),
        ("tags", "TEXT DEFAULT ''"),
        ("tags_user_edited", "BOOLEAN DEFAULT 0"),
        ("f95_tags", "TEXT"),
        ("rating", "TEXT DEFAULT ''"),
        ("rating_updated_at", "TEXT DEFAULT ''"),
        ("command_line_args", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT ''"),
        ("rating_graphics", "REAL DEFAULT 0"),
        ("rating_story", "REAL DEFAULT 0"),
        ("rating_fappability", "REAL DEFAULT 0"),
        ("rating_gameplay", "REAL DEFAULT 0"),
        ("engine", "TEXT DEFAULT ''"),
        ("latest_version", "TEXT DEFAULT ''"),
        ("f95_latest_version", "TEXT DEFAULT ''"),
        ("f95_latest_at", "TEXT DEFAULT ''"),
        ("lc_latest_version", "TEXT DEFAULT ''"),
        ("lc_latest_at", "TEXT DEFAULT ''"),
        ("latest_site", "TEXT DEFAULT ''"),
        ("run_japanese_locale", "BOOLEAN DEFAULT 0"),
        ("run_wayland", "BOOLEAN DEFAULT 0"),
        ("auto_inject_ce", "BOOLEAN DEFAULT 0"),
        ("gamemode", "TEXT DEFAULT 'default'"),
        ("mangohud", "TEXT DEFAULT 'default'"),
        ("gamescope", "TEXT DEFAULT 'default'"),
        ("gamescope_resolution", "TEXT DEFAULT ''"),
        ("gamescope_scaler", "TEXT DEFAULT ''"),
        ("gamescope_filter", "TEXT DEFAULT ''"),
        ("gamescope_args", "TEXT DEFAULT ''"),
        ("html_player", "TEXT DEFAULT ''"),
        ("sandbox_enabled", "BOOLEAN DEFAULT 0"),
        ("block_network", "BOOLEAN DEFAULT 0"),
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
        # The last live LC read's Completed prefix (NULL: no read yet). Device-local;
        # it lets an F95 read clear `thread_completed` once LC no longer sets it.
        ("lc_thread_completed", "INTEGER"),
        # No default: NULL means the thread's state is not known yet.
        ("thread_onhold", "INTEGER"),
        ("thread_abandoned", "INTEGER"),
        ("thread_states_at", "TEXT"),
        ("launch_mode", f"TEXT DEFAULT '{DEFAULT_LAUNCH_MODE}'"),
        ("finished_version", "TEXT DEFAULT ''"),
        ("last_played_version", "TEXT DEFAULT ''"),
        ("skipped_version", "TEXT DEFAULT ''"),
        ("collections", "TEXT DEFAULT '[]'"),
        ("note_links", "TEXT DEFAULT '[]'"),
        ("screenshots", "TEXT DEFAULT '[]'"),
        ("save_path_override", "TEXT DEFAULT ''"),
        # Optional name of the canonical default launch target ("" shows "Default").
        ("default_target_label", "TEXT DEFAULT ''"),
        ("sync_uid", "TEXT"),
        # F95 first-post details (core/thread_details.py); lists are JSON text.
        ("overview", "TEXT DEFAULT ''"),
        ("original_title", "TEXT DEFAULT ''"),
        ("aliases", "TEXT DEFAULT '[]'"),
        ("language", "TEXT DEFAULT ''"),
        ("external_links", "TEXT DEFAULT '[]'"),
        ("thread_details_baseline", "TEXT"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in existing_columns:
            _ = cursor.execute(
                f"ALTER TABLE games ADD COLUMN {col_name} {col_type}"
            )

    if "f95_latest_version" not in existing_columns:
        _ = cursor.execute(
            """UPDATE games SET f95_latest_version = latest_version, latest_site = 'f95'
            WHERE TRIM(COALESCE(latest_version, '')) != ''
              AND TRIM(COALESCE(f95_url, '')) != ''"""
        )

    # A stable per-game identity lets sync follow a game whose title or
    # thread URL changed on another device instead of replacing it.
    _ = cursor.execute(
        f"UPDATE games SET sync_uid = {_NEW_SYNC_UID_SQL} WHERE sync_uid IS NULL OR sync_uid = ''"
    )
    _ = cursor.execute("DROP TRIGGER IF EXISTS games_assign_sync_uid")
    _ = cursor.execute(
        f"""CREATE TRIGGER games_assign_sync_uid AFTER INSERT ON games
        WHEN NEW.sync_uid IS NULL OR NEW.sync_uid = ''
        BEGIN UPDATE games SET sync_uid = {_NEW_SYNC_UID_SQL} WHERE id = NEW.id; END"""
    )
    _ = cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_games_sync_uid ON games(sync_uid)"
    )

    try:
        _ = cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_games_f95_url_unique ON games(f95_url) WHERE f95_url IS NOT NULL AND f95_url != ''"
        )
    except sqlite3.IntegrityError:
        print(
            "[wLib] Warning: duplicate f95_url values exist; unique index not applied"
        )

    try:
        _ = cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_games_lc_url_unique "
            + "ON games(lc_url) WHERE lc_url IS NOT NULL AND lc_url != ''"
        )
    except sqlite3.IntegrityError:
        print("[wLib] Warning: duplicate lc_url values exist; unique index not applied")
    rows = cast(list[tuple[int, str]], cursor.execute(
        "SELECT id, lc_url FROM games WHERE lc_url IS NOT NULL AND lc_url != '' ORDER BY id"
    ).fetchall())
    for game_id, stored_url in rows:
        normalized = lewdcorner.normalize_thread_url(stored_url)
        if normalized and normalized != stored_url:
            try:
                _ = cursor.execute("UPDATE games SET lc_url = ? WHERE id = ?", (normalized, game_id))
            except sqlite3.IntegrityError:
                continue

    _normalize_game_play_statuses(cursor, existing_columns | {"play_status"})
    _migrate_archived_status_settings(cursor)
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
    # LC's engine "Other" is the engine F95 names "Others". Costs nothing once done.
    _ = cursor.execute("UPDATE games SET engine = 'Others' WHERE engine = 'Other'")
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
    block_network: bool = False,
    *,
    lc_url: str = "",
    lc_mode: str = "",
    lc_dismissed: str = "",
    screenshots: Sequence[str] = (),
) -> int | None:
    # tags might be a list, so convert to comma-separated string if needed
    if isinstance(tags, list):
        tags = ", ".join(str(tag) for tag in tags)

    normalized_url = normalize_thread_url(f95_url)
    normalized_lc = lewdcorner.validated_thread_url(lc_url)
    mode = lewdcorner.normalize_mode(lc_mode)
    if sandbox_enabled is None:
        sandbox_enabled = bool(_coerce_bool_int(get_setting("sandbox_new_games")))

    now_iso = local_now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = conn.execute("BEGIN IMMEDIATE")
        if normalized_lc and _find_lc_game_row(conn, normalized_lc):
            raise sqlite3.IntegrityError(f"LewdCorner thread already in the library: {normalized_lc}")
        if (
            normalized_url
            and find_matching_game_row(cursor, normalized_url) is not None
        ):
            raise sqlite3.IntegrityError("duplicate f95_url")

        _ = cursor.execute(
            "INSERT INTO games (title, exe_path, f95_url, lc_url, lc_mode, lc_dismissed, version, cover_image_path, tags, rating, developer, engine, run_japanese_locale, run_wayland, auto_inject_ce, custom_prefix, proton_version, launch_mode, date_added, play_status, command_line_args, sandbox_enabled, block_network, screenshots) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title,
                exe_path,
                normalized_url,
                normalized_lc,
                mode,
                lc_dismissed,
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
                _coerce_bool_int(block_network),
                json.dumps(list(screenshots)),
            ),
        )
        game_id = cursor.lastrowid
        conn.commit()
    return game_id


def _tag_list(value: object) -> list[str]:
    """A stored comma list as tags: stripped, no empty ones, first of each key."""
    from core.f95_tags import split_tags

    return split_tags(value) or []


def _merge_tag_lists(*lists: Sequence[str]) -> list[str]:
    from core.f95_tags import split_tags

    return split_tags(",".join(tag for tags in lists for tag in tags)) or []


def insert_wishlist_game(
    cursor: sqlite3.Cursor,
    *,
    title: str,
    f95_url: str,
    version: str = "",
    developer: str = "",
    engine: str = "",
    cover_image: str = "",
    f95_tags: str = "",
    extra_tags: Sequence[str] = (),
    rating: str = "",
    thread_completed: bool | None = None,
) -> int:
    """Insert a wishlisted game (empty executable) inside the caller's transaction.

    ``f95_tags`` (the thread's tags, possibly empty) is the game's F95 tag
    baseline, so ``extra_tags`` count as user-added tags on later refreshes.
    Raises ``sqlite3.IntegrityError`` when the thread is already in the library."""
    normalized_url = normalize_thread_url(f95_url)
    if normalized_url and find_matching_game_row(cursor, normalized_url) is not None:
        raise sqlite3.IntegrityError("duplicate f95_url")
    baseline = _tag_list(f95_tags)
    tags = _merge_tag_lists(baseline, extra_tags)
    _ = cursor.execute("SELECT value FROM settings WHERE key = 'sandbox_new_games'")
    sandbox_row = cast(tuple[object, ...] | None, cursor.fetchone())
    sandbox_enabled = _coerce_bool_int(sandbox_row[0] if sandbox_row else None)
    _ = cursor.execute(
        "INSERT INTO games (title, exe_path, f95_url, version, cover_image_path, tags, f95_tags, rating, developer, engine, launch_mode, date_added, play_status, sandbox_enabled, thread_completed, screenshots) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')",
        (
            title,
            normalized_url,
            version,
            cover_image,
            ", ".join(tags),
            ", ".join(baseline),
            rating,
            developer,
            engine,
            DEFAULT_LAUNCH_MODE,
            local_now_iso(),
            DEFAULT_PLAY_STATUS,
            sandbox_enabled,
            1 if thread_completed else 0,
        ),
    )
    game_id = cursor.lastrowid
    if game_id is None:
        raise sqlite3.DatabaseError("game insert returned no id")
    return game_id


def add_game_tags(cursor: sqlite3.Cursor, game_id: int, tags: Sequence[str]) -> bool:
    """Add ``tags`` to a game inside the caller's transaction; True when it changed.

    A game with no F95 tag baseline yet (``f95_tags`` NULL) whose tags the
    user never edited first gets its current tags as the baseline. Else the
    first "Refresh F95 info" would take the new tags as F95 tags and drop them."""
    _ = cursor.execute(
        "SELECT tags, f95_tags, tags_user_edited FROM games WHERE id = ?", (game_id,)
    )
    row = cast(tuple[object, object, object] | None, cursor.fetchone())
    if row is None:
        return False
    stored_tags, baseline, user_edited = row
    current = _tag_list(stored_tags)
    current_keys = {tag.lower() for tag in current}
    missing = [tag for tag in _tag_list(",".join(tags)) if tag.lower() not in current_keys]
    if not missing:
        return False
    fields: dict[str, object] = {"tags": ", ".join([*current, *missing])}
    if baseline is None and not user_edited:
        fields["f95_tags"] = ", ".join(current)
    set_clause = ", ".join(f"{key} = ?" for key in fields)
    _ = cursor.execute(f"UPDATE games SET {set_clause} WHERE id = ?", (*fields.values(), game_id))
    return True


def seed_tag_colors_once(
    cursor: sqlite3.Cursor, marker_key: str, defaults: Mapping[str, str]
) -> bool:
    """Give each tag in ``defaults`` its color when it has none, once.

    ``marker_key`` records that this ran, so a color the user later cleared
    stays cleared. Returns True when it ran now."""
    _ = cursor.execute("SELECT value FROM settings WHERE key = ?", (marker_key,))
    marker = cast(tuple[object, ...] | None, cursor.fetchone())
    if marker is not None and str(marker[0]).strip().lower() in ("1", "true"):
        return False
    _ = cursor.execute("SELECT value FROM settings WHERE key = 'tag_colors'")
    raw = cast(tuple[object, ...] | None, cursor.fetchone())
    stored: object = {}
    try:
        if raw and raw[0]:
            stored = cast(object, json.loads(str(raw[0])))
    except (ValueError, TypeError):
        stored = {}
    colors: dict[str, object] = (
        {str(key): value for key, value in cast(dict[object, object], stored).items()}
        if isinstance(stored, dict) else {}
    )
    present = {key.strip().lower() for key in colors}
    for tag, swatch in defaults.items():
        key = tag.strip().lower()
        if key and key not in present and swatch in TAG_COLOR_SWATCHES:
            colors[key] = swatch
    _ = cursor.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [("tag_colors", json.dumps(colors)), (marker_key, "true")],
    )
    return True


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
                (game_id, source, identity, url, origin, local_now_iso()),
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
    now_iso = local_now_iso()

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

    safe_fields["updated_at"] = local_now_iso()
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

    now_iso = local_now_iso()
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
        game_dict["note_links"] = normalize_note_links(game_dict.get("note_links"))
        game_dict["screenshots"] = _decode_game_screenshots(game_dict.get("screenshots"))
        for field in LIST_FIELDS:
            game_dict[field] = normalize_detail(field, game_dict.get(field))
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


def _latest_for_game(
    game: Mapping[str, object], lc_enabled: bool
) -> tuple[str, lewdcorner.LatestSite]:
    f95 = (
        (str(game.get("f95_latest_version") or ""), str(game.get("f95_latest_at") or ""))
        if game.get("f95_url") else None
    )
    lc = (
        (str(game.get("lc_latest_version") or ""), str(game.get("lc_latest_at") or ""))
        if lewdcorner.lc_active(game, lc_enabled) else None
    )
    return lewdcorner.pick_latest(f95, lc)


def _lc_enabled_in(connection: sqlite3.Connection) -> bool:
    row = cast(tuple[object, ...] | sqlite3.Row | None, connection.execute(
        "SELECT value FROM settings WHERE key = 'lc_enabled'"
    ).fetchone())
    return row is not None and lewdcorner.setting_enabled(cast(object, row[0]))


def refresh_latest_versions() -> int:
    """Pick each LC game's newest version again under the current setting.

    "Use LewdCorner" changes which games count LC; the stored winner must
    follow it. Returns the number of games changed.
    """
    changed = 0
    with closing(get_connection()) as conn, conn:
        conn.row_factory = sqlite3.Row
        _ = conn.execute("BEGIN IMMEDIATE")
        lc_enabled = _lc_enabled_in(conn)
        rows = cast(list[sqlite3.Row], conn.execute(
            "SELECT id, f95_url, lc_url, lc_mode, f95_latest_version, f95_latest_at, "
            + "lc_latest_version, lc_latest_at, latest_version, latest_site "
            + "FROM games WHERE COALESCE(lc_url, '') != ''"
        ).fetchall())
        for row in rows:
            game = {str(key): cast(object, row[key]) for key in row.keys()}
            latest, site = _latest_for_game(game, lc_enabled)
            # The same rule as update_game: a row with no winner yet keeps a
            # version that came from an older wLib.
            if not (site or game.get("latest_site")):
                continue
            if (latest, site) != (game.get("latest_version") or "", game.get("latest_site") or ""):
                _ = conn.execute(
                    "UPDATE games SET latest_version = ?, latest_site = ? WHERE id = ?",
                    (latest, site, game["id"]),
                )
                changed += 1
    return changed


def store_thread_update(
    game_id: int,
    site: lewdcorner.Site,
    url: str,
    observation: lewdcorner.SiteVersion | None,
    *,
    completed: bool = False,
    keep_newer: bool = False,
    states: ThreadStates | None = None,
    lc_states: ThreadStates | None = None,
    lc_completed: bool | None = None,
) -> tuple[dict[str, object], bool, bool] | None:
    """Write the observation and winner together, if the thread still matches.

    `lc_completed` is an LC read's Completed prefix; None means the read had
    no live prefix data. False clears the Completed flag of an LC-only game;
    for a game on both sites the next F95 page read decides.

    `lc_states` is the LC read of a game the F95 step stores; it applies by
    the rule in `site_thread_states` when `states` (F95 data) is None.

    `states` holds the thread's On Hold and Abandoned prefixes; None means
    the read had no prefix data and leaves both stored states unchanged.
    Returns the stored game, whether it is newly finished (wishlist only)
    and whether it is newly abandoned (see `thread_state_fields`).
    """
    with closing(get_connection()) as conn, conn:
        conn.row_factory = sqlite3.Row
        _ = conn.execute("BEGIN IMMEDIATE")
        row = cast(sqlite3.Row | None, conn.execute(
            "SELECT * FROM games WHERE id = ?", (game_id,)
        ).fetchone())
        if row is None:
            return None
        game = {str(key): cast(object, row[key]) for key in row.keys()}
        matches = lewdcorner.thread_urls_match if site == "lc" else thread_urls_match
        if not matches(game.get(f"{site}_url"), url):
            return None
        lc_enabled = _lc_enabled_in(conn)
        if site == "lc" and not lewdcorner.lc_active(game, lc_enabled):
            return None

        fields: dict[str, object] = {}
        if observation is not None:
            version, at = observation
            version_key, time_key = f"{site}_latest_version", f"{site}_latest_at"
            stored_at = str(game.get(time_key) or "")
            same = normalize_version(version) == normalize_version(game.get(version_key))
            if not (keep_newer and at and stored_at > at and not same):
                fields[version_key] = version
                fields[time_key] = stored_at if same else at
            fields["latest_version"], fields["latest_site"] = _latest_for_game(
                {**game, **fields}, lc_enabled
            )

        newly_finished = completed and not game.get("thread_completed") and not str(
            game.get("exe_path") or ""
        ).strip()
        if completed:
            fields["thread_completed"] = 1
        if site == "lc" and lc_completed is not None:
            fields["lc_thread_completed"] = int(lc_completed)
            has_f95 = bool(str(game.get("f95_url") or "").strip())
            if not lc_completed and not has_f95 and game.get("thread_completed"):
                fields["thread_completed"] = 0
        if site == "lc" and str(game.get("f95_url") or "").strip():
            # F95 decides for a game on both sites; the F95 check that follows
            # uses these LC states only when it has no prefix data itself.
            states = None
        elif site == "f95":
            states = site_thread_states(game, states, lc_states)
        state_fields, newly_abandoned = thread_state_fields(game, states)
        fields.update(state_fields)
        if fields:
            assignments = ", ".join(f"{field} = ?" for field in fields)
            _ = conn.execute(
                f"UPDATE games SET {assignments} WHERE id = ?", (*fields.values(), game_id)
            )
        return {**game, **fields}, newly_finished, newly_abandoned


def delete_game(game_id: int) -> None:
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _ = cursor.execute("DELETE FROM games WHERE id = ?", (game_id,))
        conn.commit()


def _resolved_path(path: str) -> str:
    expanded = os.path.abspath(os.path.expanduser(path))
    try:
        return os.path.realpath(expanded)
    except OSError:
        return expanded


def _moved_path(value: object, old_root: str, new_root: str) -> str | None:
    """`value` rebased from old_root to new_root, or None when it is outside."""
    raw = str(value or "").strip()
    if not raw or "://" in raw:
        return None
    path_real = _resolved_path(raw)
    root_real = _resolved_path(old_root)
    if path_real == root_real:
        return new_root
    prefix = root_real.rstrip(os.sep) + os.sep
    if not path_real.startswith(prefix):
        return None
    return os.path.join(new_root, os.path.relpath(path_real, root_real))


_REPOINT_GAME_COLUMNS = (
    "exe_path",
    "cover_image_path",
    "custom_prefix",
    "save_path_override",
)


def repoint_game_paths(game_id: int, old_root: str, new_root: str) -> dict[str, object]:
    """Point the game's stored paths inside old_root at new_root, in one transaction.

    Changes `exe_path`, `cover_image_path`, `custom_prefix`,
    `save_path_override` and each launch target that is inside old_root.
    Paths outside old_root stay. Returns the changed game columns and the
    number of changed launch targets.
    """
    now_iso = local_now_iso()
    with closing(get_connection()) as conn, conn:
        conn.row_factory = sqlite3.Row
        _ = conn.execute("BEGIN IMMEDIATE")
        row = cast(
            sqlite3.Row | None,
            conn.execute(
                f"SELECT {', '.join(_REPOINT_GAME_COLUMNS)} FROM games WHERE id = ?",
                (game_id,),
            ).fetchone(),
        )
        if row is None:
            raise sqlite3.IntegrityError("The game was not found.")
        changed: dict[str, object] = {}
        for column in _REPOINT_GAME_COLUMNS:
            moved = _moved_path(cast(object, row[column]), old_root, new_root)
            if moved is not None:
                changed[column] = moved
        if changed:
            set_clause = ", ".join(f"{key} = ?" for key in changed)
            _ = conn.execute(
                f"UPDATE games SET {set_clause} WHERE id = ?",
                (*changed.values(), game_id),
            )
        targets = cast(
            list[sqlite3.Row],
            conn.execute(
                "SELECT id, exe_path FROM game_launch_targets WHERE game_id = ?",
                (game_id,),
            ).fetchall(),
        )
        moved_targets = 0
        for target in targets:
            moved = _moved_path(cast(object, target["exe_path"]), old_root, new_root)
            if moved is None:
                continue
            _ = conn.execute(
                "UPDATE game_launch_targets SET exe_path = ?, updated_at = ? WHERE id = ?",
                (moved, now_iso, cast(object, target["id"])),
            )
            moved_targets += 1
    return {"game": changed, "targets": moved_targets}


def clear_game_install(game_id: int) -> None:
    """Make a game wishlisted: empty its executable and delete its launch targets.

    Playtime, notes, status, collections and every other field stay.
    """
    with closing(get_connection()) as conn, conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("UPDATE games SET exe_path = '' WHERE id = ?", (game_id,))
        if cursor.rowcount == 0:
            raise sqlite3.IntegrityError("The game was not found.")
        _ = conn.execute("DELETE FROM game_launch_targets WHERE game_id = ?", (game_id,))


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


def get_cover_cache_work() -> list[dict[str, object]]:
    # Include valid rows too so missing files are repaired on the next wake.
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = cast(list[sqlite3.Row], conn.execute(
            "SELECT id, cover_image_path, cover_cache, cover_cache_url FROM games WHERE COALESCE(cover_image_path, '') != '' OR cover_cache IS NOT NULL"
        ).fetchall())
        return [{str(key): cast(object, row[key]) for key in row.keys()} for row in rows]


def set_game_cover_cache(game_id: int, url: str, name: str) -> bool:
    with closing(get_connection()) as conn:
        cursor = conn.execute(
            "UPDATE games SET cover_cache = ?, cover_cache_url = ? WHERE id = ? AND cover_image_path = ?",
            (name, url, game_id, url),
        )
        conn.commit()
        return cursor.rowcount == 1


def clear_game_cover_cache(game_id: int | None = None) -> None:
    with closing(get_connection()) as conn:
        if game_id is None:
            _ = conn.execute("UPDATE games SET cover_cache = NULL, cover_cache_url = NULL")
        else:
            _ = conn.execute("UPDATE games SET cover_cache = NULL, cover_cache_url = NULL WHERE id = ?", (game_id,))
        conn.commit()


def referenced_cover_cache_names() -> set[str]:
    with closing(get_connection()) as conn:
        rows = cast(list[tuple[str]], conn.execute("SELECT cover_cache FROM games WHERE cover_cache IS NOT NULL").fetchall())
        return {row[0] for row in rows}


def lc_screenshot_sources() -> list[tuple[str, list[str]]]:
    """``(lc_url, stored screenshots)`` of each game that has either."""
    with closing(get_connection()) as conn:
        rows = cast(list[tuple[object, object]], conn.execute(
            "SELECT lc_url, screenshots FROM games WHERE COALESCE(lc_url, '') != '' OR COALESCE(screenshots, '[]') != '[]'"
        ).fetchall())
    return [(str(lc_url or ""), _decode_game_screenshots(screens)) for lc_url, screens in rows]


def referenced_cover_local_names() -> set[str]:
    with closing(get_connection()) as conn:
        rows = cast(list[tuple[str]], conn.execute("SELECT cover_local FROM games WHERE cover_local IS NOT NULL AND cover_local != ''").fetchall())
        return {row[0] for row in rows}


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


def normalize_note_links(value: object) -> list[dict[str, str]]:
    if isinstance(value, str):
        try:
            value = cast(object, json.loads(value))
        except (ValueError, TypeError):
            return []
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[object, object], item)
        raw_url = entry.get("url")
        if not isinstance(raw_url, str):
            continue
        url = raw_url.strip()
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme.lower() in ("http", "https")
                and bool(parsed.hostname)
                and not any(char.isspace() or ord(char) < 32 for char in url)
            )
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid or len(url) > 2048 or url in seen:
            continue
        label = entry.get("label")
        result.append({"url": url, "label": label.strip()[:200] if isinstance(label, str) else ""})
        seen.add(url)
    return result


def update_game(game_id: int, fields: Mapping[str, object]) -> dict[str, object]:
    """Update arbitrary fields on a game row. `fields` is a dict of column->value.

    Returns the fields actually applied, including a `finished_version` the
    caller never sent when a move to `Completed` auto-filled it.
    """
    if not fields:
        return {}
    allowed = {
        "title",
        "steam_shortcut",
        "exe_path",
        "f95_url",
        "lc_url",
        "lc_mode",
        "version",
        "progress",
        "note_links",
        "developer",
        "cover_image_path",
        "tags",
        "tags_user_edited",
        "f95_tags",
        "rating",
        "rating_updated_at",
        "command_line_args",
        "rating_graphics",
        "rating_story",
        "rating_fappability",
        "rating_gameplay",
        "engine",
        "latest_version",
        "f95_latest_version",
        "f95_latest_at",
        "lc_latest_version",
        "lc_latest_at",
        "latest_site",
        "run_japanese_locale",
        "run_wayland",
        "auto_inject_ce",
        "sandbox_enabled",
        "block_network",
        "gamemode",
        "mangohud",
        "gamescope",
        *gamescope.GAME_OVERRIDE_COLUMNS,
        "html_player",
        "custom_prefix",
        "proton_version",
        "save_path_override",
        "default_target_label",
        "launch_mode",
        "play_status",
        "finished_version",
        "skipped_version",
        "is_favorite",
        "thread_completed",
        *THREAD_STATE_COLUMNS,
        THREAD_STATES_AT_COLUMN,
        *THREAD_DETAIL_FIELDS,
    }
    # Only allow known columns
    # `f95_tags` may be set back to NULL ("no baseline yet"), as an undo does,
    # and the thread states back to unknown.
    safe_fields: dict[str, object] = {
        k: v
        for k, v in fields.items()
        if k in allowed
        and (
            v is not None
            or k == "f95_tags"
            or k in THREAD_STATE_COLUMNS
            or k == THREAD_STATES_AT_COLUMN
        )
    }
    if not safe_fields:
        return {}

    if "note_links" in safe_fields:
        safe_fields["note_links"] = json.dumps(normalize_note_links(safe_fields["note_links"]))

    raw_tags = safe_fields.get("tags")
    if isinstance(raw_tags, list):
        safe_fields["tags"] = ", ".join(
            str(tag) for tag in cast(list[object], raw_tags)
        )

    for bool_column in (
        "thread_completed",
        "tags_user_edited",
        "is_favorite",
        "steam_shortcut",
        "run_japanese_locale",
        "run_wayland",
        "auto_inject_ce",
        "sandbox_enabled",
        "block_network",
    ):
        if bool_column in safe_fields:
            safe_fields[bool_column] = _coerce_bool_int(safe_fields[bool_column])
    for state_column in THREAD_STATE_COLUMNS:
        if state_column in safe_fields:
            safe_fields[state_column] = coerce_thread_state(safe_fields[state_column])
    if THREAD_STATES_AT_COLUMN in safe_fields:
        safe_fields[THREAD_STATES_AT_COLUMN] = coerce_thread_states_at(
            safe_fields[THREAD_STATES_AT_COLUMN]
        )

    if "f95_url" in safe_fields:
        safe_fields["f95_url"] = normalize_thread_url(safe_fields["f95_url"])

    if "lc_url" in safe_fields:
        safe_fields["lc_url"] = lewdcorner.validated_thread_url(safe_fields["lc_url"])
    if "lc_mode" in safe_fields:
        safe_fields["lc_mode"] = lewdcorner.normalize_mode(safe_fields["lc_mode"])

    for field in THREAD_DETAIL_FIELDS:
        if field in safe_fields:
            safe_fields[field] = thread_detail_column_value(field, safe_fields[field])

    if "play_status" in safe_fields:
        safe_fields["play_status"] = normalize_play_status(safe_fields["play_status"])
    if "finished_version" in safe_fields:
        safe_fields["finished_version"] = str(safe_fields["finished_version"]).strip()
    if "default_target_label" in safe_fields:
        safe_fields["default_target_label"] = str(safe_fields["default_target_label"]).strip()
    for tool in ("gamemode", "mangohud", "gamescope"):
        if tool in safe_fields:
            safe_fields[tool] = normalize_tool_override(safe_fields[tool])
    for column in gamescope.GAME_OVERRIDE_COLUMNS:
        if column in safe_fields:
            safe_fields[column] = gamescope.normalize_game_field(column, safe_fields[column])
    if "html_player" in safe_fields:
        safe_fields["html_player"] = normalize_html_player(safe_fields["html_player"])
    if "launch_mode" in safe_fields:
        safe_fields["launch_mode"] = normalize_launch_mode(safe_fields["launch_mode"])

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = conn.execute("BEGIN IMMEDIATE")
        refresh_winner = False
        if "lc_url" in safe_fields:
            lc_url = str(safe_fields["lc_url"])
            if lc_url and _find_lc_game_row(conn, lc_url, game_id):
                raise sqlite3.IntegrityError(f"LewdCorner thread already in the library: {lc_url}")
            row = cast(sqlite3.Row | None, conn.execute(
                "SELECT lc_url, screenshots, f95_url FROM games WHERE id = ?", (game_id,)
            ).fetchone())
            old_id = lewdcorner.extract_thread_id(cast(object, row[0])) if row else ""
            if old_id and old_id != lewdcorner.extract_thread_id(lc_url):
                safe_fields["lc_dismissed"] = old_id
                safe_fields["lc_latest_version"] = ""
                safe_fields["lc_latest_at"] = ""
                safe_fields["lc_thread_completed"] = None
                refresh_winner = True
                # Without an F95 thread the states came from the old LC thread.
                f95_after = safe_fields.get("f95_url", row[2] if row else "")
                if not str(f95_after or "").strip():
                    _reset_thread_states(safe_fields)
                # LC screenshots show the old thread; F95 and other ones stay.
                if row is not None and "screenshots" not in safe_fields:
                    stored = _decode_game_screenshots(cast(object, row[1]))
                    kept = [url for url in stored if not lewdcorner.hosts_url(url)]
                    if kept != stored:
                        safe_fields["screenshots"] = json.dumps(kept)

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
                safe_fields["f95_latest_version"] = ""
                safe_fields["f95_latest_at"] = ""
                safe_fields["latest_site"] = ""
                refresh_winner = True
                safe_fields["skipped_version"] = ""
                safe_fields["thread_main_post_last_edit_at"] = None
                safe_fields["thread_main_post_checked_at"] = None
                safe_fields["thread_completed"] = 0
                _reset_thread_states(safe_fields, force=True)
                safe_fields["screenshots"] = "[]"
                # The old thread's details too; the catalog fills the new ones.
                for field in THREAD_DETAIL_FIELDS:
                    safe_fields[field] = thread_detail_column_value(field, "")
                safe_fields[BASELINE_COLUMN] = None
            elif not normalize_thread_url(stored_url) and normalize_thread_url(safe_fields["f95_url"]):
                # A first F95 thread decides from now on; the stored states
                # came from LC.
                _reset_thread_states(safe_fields)

        if refresh_winner or "lc_mode" in safe_fields:
            stored = cast(sqlite3.Row | None, conn.execute(
                "SELECT * FROM games WHERE id = ?", (game_id,)
            ).fetchone())
            if stored:
                game = {str(key): cast(object, stored[key]) for key in stored.keys()}
                latest, site = _latest_for_game({**game, **safe_fields}, _lc_enabled_in(conn))
                if refresh_winner or site or game.get("latest_site"):
                    safe_fields["latest_version"], safe_fields["latest_site"] = latest, site

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
    # Callers see the same shapes as get_all_games: lists, no merge state.
    _ = safe_fields.pop(BASELINE_COLUMN, None)
    for field in LIST_FIELDS:
        if field in safe_fields:
            safe_fields[field] = normalize_detail(field, safe_fields[field])
    return safe_fields


def update_playtime(game_id: int, delta_seconds: int) -> None:
    clamped_delta = max(0, int(delta_seconds))

    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        now_iso = local_now_iso()
        _ = cursor.execute(
            "UPDATE games SET playtime_seconds = COALESCE(playtime_seconds, 0) + ?, last_played = ? WHERE id = ?",
            (clamped_delta, now_iso, game_id),
        )
        conn.commit()


def start_play_session(game_id: int, version: str) -> int:
    with closing(get_connection()) as conn:
        cursor = conn.execute(
            "INSERT INTO play_sessions (game_id, version, started_at) VALUES (?, ?, ?)",
            (game_id, version, local_now_iso()),
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
                (clamped_delta, local_now_iso(), game_id),
            )
        elif session["ended_at"] is None or not is_final:
            now = local_now_iso()
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
def normalize_library_folders(raw: object) -> list[str]:
    """Strip and deduplicate folders by identity, preserving the first spelling."""
    if not isinstance(raw, list):
        raise ValueError("Library folders must be a list of paths")
    folders: list[str] = []
    seen: set[str] = set()
    for entry in cast(list[object], raw):
        if not isinstance(entry, str):
            raise ValueError("Library folders must be a list of paths")
        path = entry.strip()
        identity = os.path.realpath(os.path.expanduser(path))
        if path and identity not in seen:
            folders.append(path)
            seen.add(identity)
    return folders


_NEW_SYNC_UID_SQL = "lower(hex(randomblob(16)))"


def read_library_folders() -> tuple[list[str], str]:
    """Return the normalized folder-list/default pair without writing repairs.

    Safe to call while another connection holds the write lock.
    """
    default = (get_setting("library_folder") or "").strip()
    try:
        folders = normalize_library_folders(
            cast(object, json.loads(get_setting("library_folders") or "[]"))
        )
    except (ValueError, TypeError):
        folders = []
    if default:
        identity = os.path.realpath(os.path.expanduser(default))
        match = next(
            (p for p in folders if os.path.realpath(os.path.expanduser(p)) == identity),
            None,
        )
        if match is None:
            folders.insert(0, default)
        else:
            default = match
    elif folders:
        default = folders[0]
    return folders, default


def get_library_folders() -> tuple[list[str], str]:
    """Read and repair the additive folder-list/default setting pair."""
    folders, default = read_library_folders()
    encoded = json.dumps(folders)
    if get_setting("library_folders") != encoded:
        update_setting("library_folders", encoded)
    if get_setting("library_folder") != default:
        update_setting("library_folder", default)
    return folders, default


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


def lc_plus_effective() -> bool:
    """The effective LC Plus state: Yes, or Auto with Plus detected on this device."""
    with closing(get_connection()) as conn:
        rows = dict(cast(list[tuple[str, str]], conn.execute(
            "SELECT key, value FROM settings WHERE key IN (?, ?)",
            (lewdcorner.LC_PLUS_MODE, lewdcorner.LC_PLUS_DETECTED),
        ).fetchall()))
    return lewdcorner.plus_effective(rows.get(lewdcorner.LC_PLUS_MODE), rows.get(lewdcorner.LC_PLUS_DETECTED))


def update_settings(
    values: Mapping[str, str], *, custom_play_statuses: Sequence[str] | None = None
) -> None:
    """Write settings (and optionally the custom play statuses) in one transaction."""
    removed: list[str] = []
    if custom_play_statuses is not None:
        # Match case-insensitively like the reset below, so a case-only rename keeps its games.
        kept = {str(status).strip().casefold() for status in custom_play_statuses}
        removed = [
            status for status in get_custom_play_statuses() if status.casefold() not in kept
        ]
        values = {
            **values,
            "custom_play_statuses": json.dumps(
                [str(status).strip() for status in custom_play_statuses]
            ),
        }
    with closing(get_connection()) as conn:
        for status in removed:
            _ = conn.execute(
                "UPDATE games SET play_status = ? WHERE play_status = ? COLLATE NOCASE",
                (DEFAULT_PLAY_STATUS, status),
            )
        _ = conn.executemany(
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
    update_settings({}, custom_play_statuses=statuses)


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


def register_collection_names(cursor: sqlite3.Cursor, names: Sequence[str]) -> int:
    """Add valid names missing from the collections registry; returns how many.

    Membership only shows for registered collections, so an import that brings
    memberships must also bring their names."""
    collections = _read_collections(cursor)
    known = {name.casefold() for name in collections}
    added = 0
    for raw in names:
        name = str(raw or "").strip()
        if (
            not 1 <= len(name) <= 40
            or name.casefold() in known
            or name.casefold() in RESERVED_COLLECTION_KEYS
        ):
            continue
        collections.append(name)
        known.add(name.casefold())
        added += 1
    if added:
        _write_collections_setting(cursor, collections)
    return added


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
FILTER_PRESET_THREAD_STATES = ("finished", "onhold", "abandoned", "ongoing")


def _archived_filter_statuses[Status](statuses: Sequence[Status]) -> list[Status | str]:
    """Select "Archived" where an older preset selected "Abandoned".

    Other entries, strings or not, stay in order without repeats.
    """
    result: list[Status | str] = []
    for status in statuses:
        value: Status | str = status
        if isinstance(status, str) and status.strip().casefold() in _ARCHIVED_STATUS_ALIASES:
            value = "Archived"
        if value not in result:
            result.append(value)
    return result


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
        "filterStatuses": _archived_filter_statuses(string_list(source.get("filterStatuses"))),
        "filterInstallState": (
            install_state if install_state in FILTER_PRESET_INSTALL_STATES else ""
        ),
        "filterThreadStates": [
            state
            for state in FILTER_PRESET_THREAD_STATES
            if state in string_list(source.get("filterThreadStates"))
        ],
        "filterSources": string_list(source.get("filterSources")),
        "filterEngines": string_list(source.get("filterEngines")),
        "filterTags": string_list(source.get("filterTags")),
        "filterExcludedSources": string_list(source.get("filterExcludedSources")),
        "filterExcludedEngines": string_list(source.get("filterExcludedEngines")),
        "filterExcludedTags": string_list(source.get("filterExcludedTags")),
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
    now = local_now_iso()
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


def list_steam_shortcuts(account_id: str) -> list[dict[str, object]]:
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = cast(list[sqlite3.Row], conn.execute(
            'SELECT * FROM steam_shortcuts WHERE account_id = ?', (account_id,)
        ).fetchall())
        return [{str(key): cast(object, row[key]) for key in row.keys()} for row in rows]


def put_steam_shortcut(account_id: str, game_id: int, app_id: int | None, state: str, art_key: str = '') -> None:
    with closing(get_connection()) as conn:
        _ = conn.execute('''INSERT INTO steam_shortcuts(account_id, game_id, app_id, state, art_key)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(account_id, game_id) DO UPDATE SET
            app_id=excluded.app_id, state=excluded.state, art_key=excluded.art_key''',
            (account_id, game_id, app_id, state, art_key))
        conn.commit()


def delete_steam_shortcut(account_id: str, game_id: int) -> None:
    with closing(get_connection()) as conn:
        _ = conn.execute('DELETE FROM steam_shortcuts WHERE account_id = ? AND game_id = ?', (account_id, game_id))
        conn.commit()


# Download queue (device-local; see core/downloads/queue.py)
DOWNLOAD_JOB_COLUMNS = (
    "id", "kind", "game_id", "title", "version", "position", "state", "reason",
    "error_code", "error_text", "source", "bytes_done", "bytes_total", "retries",
    "retry_at", "created_at", "updated_at", "finished_at",
)
# Columns update_download_job may change; id, kind and created_at stay fixed.
_DOWNLOAD_JOB_UPDATABLE = frozenset(DOWNLOAD_JOB_COLUMNS) - {"id", "kind", "created_at"}


def list_download_jobs() -> list[dict[str, object]]:
    """Every saved download job, in queue order."""
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = cast(
            list[sqlite3.Row],
            conn.execute("SELECT * FROM download_jobs ORDER BY position, created_at, id").fetchall(),
        )
    return [{str(key): cast(object, row[key]) for key in row.keys()} for row in rows]


def insert_download_job(job: Mapping[str, object]) -> None:
    """Add a job at the end of the queue unless `job` names a position."""
    values = {key: job[key] for key in DOWNLOAD_JOB_COLUMNS if key in job}
    now = time.time()
    _ = values.setdefault("created_at", now)
    _ = values.setdefault("updated_at", now)
    with closing(get_connection()) as conn:
        if "position" not in values:
            row = cast(
                tuple[int | None],
                conn.execute("SELECT MAX(position) FROM download_jobs").fetchone(),
            )
            values["position"] = (row[0] + 1) if row[0] is not None else 0
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        _ = conn.execute(
            f"INSERT INTO download_jobs ({columns}) VALUES ({marks})", tuple(values.values())
        )
        conn.commit()


def update_download_job(job_id: str, fields: Mapping[str, object]) -> bool:
    """Change allowed columns of one job; False when no such job exists."""
    values = {key: value for key, value in fields.items() if key in _DOWNLOAD_JOB_UPDATABLE}
    unknown = set(fields) - _DOWNLOAD_JOB_UPDATABLE
    if unknown:
        raise ValueError(f"Unknown download job fields: {', '.join(sorted(unknown))}")
    _ = values.setdefault("updated_at", time.time())
    assignments = ", ".join(f"{key} = ?" for key in values)
    with closing(get_connection()) as conn:
        cursor = conn.execute(
            f"UPDATE download_jobs SET {assignments} WHERE id = ?", (*values.values(), job_id)
        )
        conn.commit()
        return cursor.rowcount > 0


def reorder_download_jobs(job_ids: Sequence[str]) -> None:
    """Put the named jobs first, in the given order; the rest follow in their old order."""
    with closing(get_connection()) as conn:
        existing = [
            str(row[0])
            for row in cast(
                list[tuple[object]],
                conn.execute("SELECT id FROM download_jobs ORDER BY position, created_at, id").fetchall(),
            )
        ]
        known = set(existing)
        named = list(dict.fromkeys(job_id for job_id in job_ids if job_id in known))
        rest = [job_id for job_id in existing if job_id not in set(named)]
        _ = conn.executemany(
            "UPDATE download_jobs SET position = ? WHERE id = ?",
            [(index, job_id) for index, job_id in enumerate(named + rest)],
        )
        conn.commit()


def delete_download_job(job_id: str) -> bool:
    with closing(get_connection()) as conn:
        cursor = conn.execute("DELETE FROM download_jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cursor.rowcount > 0
