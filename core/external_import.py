# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only readers for external game-tracking apps, including Playnite.

Each reader translates a source library into the backup game shape used by
``core.library_backup`` (``metadata``, ``user_state``, ``executable_paths``
and ``launch_targets`` sections), so matching, preview, transactions and
section selection are reused instead of rewritten.

Source files are never written to. SQLite sources are read from a temporary
copy (including ``-journal``/``-wal``/``-shm`` sidecars) opened read-only, so
the source app can keep running during the import.
"""

from __future__ import annotations

import json
import math
import ntpath
import os
import posixpath
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from core.backup_format import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    SQLITE_INTEGER_MAX,
    BackupValidationError,
)
from core.f95zone import (
    F95_HOSTS,
    extract_thread_id,
    normalize_thread_url,
    normalize_prefix_label,
)
from core.database import CANONICAL_PLAY_STATUSES
from core.litedb import LiteDBReader
from core.store_links import StoreLink, coerce_store_links, link_from_id
from core.game_scan import find_launcher
from core.host_platform import is_windows


def _validation_error(
    message: str, error_code: str = "invalid_source"
) -> BackupValidationError:
    """Build the shared backup validation error."""
    return BackupValidationError(message, error_code)


EXTERNAL_SOURCES = ("f95checker", "atlas", "xlibrary", "f69", "playnite")

SOURCE_LABELS = {
    "f95checker": "F95Checker",
    "atlas": "Atlas",
    "xlibrary": "XLibrary",
    "f69": "f69",
    "playnite": "Playnite",
}

# pywebview rejects a filter whose patterns are not all "*.ext" (see
# webview.util.parse_file_type), which would make browse_external_library_file
# raise on the Qt dialog path. The wildcard patterns still match the default
# file names, and the Linux backends parse the same strings.
EXTERNAL_IMPORT_FILE_FILTERS = {
    "playnite": ("Playnite Library (*.db)", "All files (*.*)"),
    "f95checker": (
        "F95Checker Databases (*.sqlite3;*.db)",
        "All files (*.*)",
    ),
    "atlas": (
        "Atlas Databases (*.db)",
        "All files (*.*)",
    ),
    "xlibrary": (
        "XLibrary Libraries (*.json)",
        "All files (*.*)",
    ),
    "f69": (
        "f69 Databases (*.db)",
        "All files (*.*)",
    ),
}

F95CHECKER_OS_KEYS = {"windows": ("1", "0", "windows"), "linux": ("2", "linux")}
XLIBRARY_FAMILY = "library.games"

XLIBRARY_COMPLETION_MAP = {
    "in progress": "Playing",
    "completed": "Completed",
    "in queue": "Plan to Play",
    "waiting for update": "Waiting For Update",
    "abandoned": "Abandoned",
}

ATLAS_PLAYSTATE_MAP = {
    "finished": "Completed",
    "played": "Playing",
    "dropped": "Abandoned",
    "on_hold": "On Hold",
    "planned": "Plan to Play",
}

F69_COMPLETION_MAP = {
    "in_queue": "Plan to Play",
    "in_progress": "Playing",
    "replaying": "Playing",
    "completed": "Completed",
    "waiting_for_update": "Waiting For Update",
    "abandoned": "Abandoned",
}

# f69 engine enum names mapped to the F95 thread prefix labels wLib stores.
F69_ENGINE_MAP = {
    "renpy": "Ren'Py",
    "rpgm_mv": "RPGM",
    "rpgm_mz": "RPGM",
    "rpgm_vx": "RPGM",
    "unity": "Unity",
    "unreal": "Unreal Engine",
    "html": "HTML",
    "flash": "Flash",
    "java": "Java",
    "wolf_rpg": "Wolf RPG",
    "qsp": "QSP",
    "tyranobuilder": "Tyrano",
    "twine": "Twine",
    "adrift": "ADRIFT",
    "rags": "RAGS",
    "tads": "Tads",
    "webgl": "WebGL",
    "other": "Others",
}


def normalize_source(source: object) -> str:
    key = str(source or "").strip().lower()
    if key not in EXTERNAL_SOURCES:
        raise _validation_error(
            f"Unknown library source: {source}. Choose F95Checker, Atlas, XLibrary, f69, or Playnite.",
            "unknown_source",
        )
    return key


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

LINK_ONLY_SOURCES = frozenset({"dlsite", "fanza", "vndb", "ryuugames"})


def _has_import_identity(f95_url: str, links: list[StoreLink], exe_path: str) -> bool:
    return bool(
        f95_url
        or exe_path
        or any(link["source"] in LINK_ONLY_SOURCES for link in links)
    )


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(dict[str, object], value))


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return list(cast(list[object], value))


def _as_str(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _row_dict(row: sqlite3.Row) -> dict[str, object]:
    record: dict[str, object] = {}
    for key in row.keys():
        record[key] = cast(object, row[key])
    return record


def _fetch_rows(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    return cast(list[sqlite3.Row], conn.execute(sql).fetchall())


@contextmanager
def open_snapshot(path: str) -> Generator[sqlite3.Connection, None, None]:
    """Copy a SQLite file plus sidecars into a temp dir and open it read-only."""
    with tempfile.TemporaryDirectory(prefix="wlib-external-") as tmp_dir:
        copy_path = os.path.join(tmp_dir, os.path.basename(path))
        _ = shutil.copy2(path, copy_path)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = path + suffix
            if os.path.isfile(sidecar):
                _ = shutil.copy2(sidecar, copy_path + suffix)
        # A normal open lets SQLite recover a hot journal on the copy first.
        recover = sqlite3.connect(copy_path)
        try:
            _ = recover.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
            recover.commit()
        finally:
            recover.close()
        conn = sqlite3.connect(f"file:{copy_path}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = _fetch_rows(conn, f"PRAGMA table_info({table})")
    except sqlite3.Error:
        return set()
    return {_as_str(_row_dict(row).get("name")) for row in rows}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = cast(
        object,
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone(),
    )
    return row is not None


def _unix_to_iso(timestamp: float) -> str:
    if timestamp <= 0:
        return ""
    if timestamp >= 1e12:
        timestamp /= 1000.0
    try:
        moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    if moment.year < 1971:
        return ""
    return moment.isoformat()


def parse_timestamp(value: object) -> str:
    """Parse unix seconds/milliseconds or ISO strings into an ISO string."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return _unix_to_iso(float(value))
    text = _as_str(value)
    if not text or text == "0":
        return ""
    try:
        return _unix_to_iso(float(text))
    except (TypeError, ValueError):
        pass
    iso = text
    if iso.endswith(("Z", "z")):
        iso = f"{iso[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if moment.year < 1971:
        return ""
    return moment.isoformat()


def _is_abs_any(path: str) -> bool:
    return bool(ntpath.isabs(path) or posixpath.isabs(path))


def apply_path_rule(path: object) -> str:
    """Keep a path only when it is absolute for the current OS."""
    text = _as_str(path)
    if not text:
        return ""
    if "://" in text:
        return ""
    if is_windows():
        # A drive letter alone ("C:" or "C:foo") is drive-relative, not
        # absolute. Require both a drive/UNC prefix and an absolute path.
        return text if (ntpath.splitdrive(text)[0] and ntpath.isabs(text)) else ""
    return text if text.startswith(("/", "~")) else ""


def resolve_relative_path(raw: str, base: str) -> str:
    """Join a source-relative path to its base folder for the current OS."""
    text = raw.strip()
    if not text or text.startswith("~") or "://" in text:
        return text
    if _is_abs_any(text):
        return text
    base_text = base.strip()
    if not base_text:
        return text
    # Join with the base folder's own separator style so results are stable
    # regardless of which OS runs the import.
    forward = text.replace("\\", "/")
    if "\\" in base_text or ntpath.splitdrive(base_text)[0]:
        return ntpath.join(base_text, forward.replace("/", "\\"))
    if base_text.startswith(("/", "~")):
        return posixpath.join(base_text, forward)
    normalized = text.replace("\\", os.sep).replace("/", os.sep)
    return os.path.join(base_text, normalized)


def _target_label(path: str) -> str:
    stem = re.split(r"[\\/]", path.strip())[-1].strip()
    return stem or path.strip()


def build_thread_url_from_id(value: object) -> str:
    try:
        thread_id = int(_as_str(value))
    except (TypeError, ValueError):
        return ""
    if thread_id <= 0:
        return ""
    return normalize_thread_url(f"https://f95zone.to/threads/{thread_id}/")


def coerce_thread_url(value: object) -> str:
    normalized = normalize_thread_url(value)
    if not normalized:
        return ""
    host = (urlparse(normalized).hostname or "").lower()
    if host not in F95_HOSTS:
        return ""
    return normalized


def _coerce_rating(value: object) -> float | None:
    try:
        rating = float(_as_str(value))
    except (TypeError, ValueError):
        return None
    return rating if rating > 0 else None


def _coerce_playtime_seconds(value: object, multiplier: float = 1.0) -> int | None:
    """Parse a source playtime into whole seconds, or None when unusable.

    Non-finite or out-of-SQLite-range values are omitted so a corrupt source
    value cannot abort the import or overwrite local playtime with 0.
    """
    try:
        seconds = float(_as_str(value) or "0") * multiplier
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or not 0 < seconds <= SQLITE_INTEGER_MAX:
        return None
    return int(seconds)


# ---------------------------------------------------------------------------
# F95Checker reader
# ---------------------------------------------------------------------------


def _f95checker_base_dir(conn: sqlite3.Connection) -> str:
    if not table_exists(conn, "settings"):
        return ""
    if "default_exe_dir" not in table_columns(conn, "settings"):
        return ""
    try:
        fetched = cast(
            object,
            conn.execute("SELECT default_exe_dir FROM settings LIMIT 1").fetchone(),
        )
    except sqlite3.Error:
        return ""
    if not isinstance(fetched, sqlite3.Row):
        return ""
    try:
        decoded = cast(
            object,
            json.loads(_as_str(_row_dict(fetched).get("default_exe_dir")) or "{}"),
        )
    except (TypeError, ValueError, RecursionError):
        return ""
    mapping = _as_dict(decoded)
    if not mapping:
        return ""
    normalized = {key.strip().lower(): value for key, value in mapping.items()}
    if "" in normalized and len(normalized) == 1:
        return _as_str(normalized[""])
    keys = F95CHECKER_OS_KEYS["windows" if is_windows() else "linux"]
    for key in (*keys, ""):
        candidate = _as_str(normalized.get(key))
        if candidate:
            return candidate
    return ""


def _f95checker_url(row: Mapping[str, object]) -> str:
    direct = coerce_thread_url(row.get("url"))
    if direct:
        return direct
    custom = _as_str(row.get("custom")).lower()
    is_custom = custom in ("1", "true") or (
        custom not in ("0", "false") and _safe_int(row.get("id")) < 0
    )
    if not is_custom:
        return build_thread_url_from_id(row.get("id"))
    return ""


def _safe_int(value: object) -> int:
    try:
        return int(_as_str(value))
    except (TypeError, ValueError):
        return 0


def _parse_json_list(value: object) -> list[str]:
    items = _as_list(value)
    if items:
        return [_as_str(item) for item in items]
    try:
        decoded = cast(object, json.loads(_as_str(value) or "[]"))
    except (TypeError, ValueError, RecursionError):
        return []
    return [_as_str(item) for item in _as_list(decoded)]


def read_f95checker_library(path: str) -> tuple[dict[str, object], dict[str, object]]:
    if not os.path.isfile(path):
        raise _validation_error("F95Checker database was not found.", "file_not_found")
    try:
        with open_snapshot(path) as conn:
            if not table_exists(conn, "games"):
                raise _validation_error(
                    "The file is not an F95Checker library.", "invalid_source"
                )
            columns = table_columns(conn, "games")
            base_dir = _f95checker_base_dir(conn)
            try:
                rows = _fetch_rows(conn, "SELECT * FROM games")
            except sqlite3.Error as exc:
                raise _validation_error(
                    "The file is not an F95Checker library.", "invalid_source"
                ) from exc
            has_playtime = "playtime" in columns
            games: list[dict[str, object]] = []
            skipped = 0
            dropped = 0
            for row in rows:
                record = _row_dict(row)
                game, game_dropped = _translate_f95checker_game(
                    record, base_dir, has_playtime
                )
                dropped += game_dropped
                if game is None:
                    skipped += 1
                    continue
                games.append(game)
    except sqlite3.Error as exc:
        raise _validation_error(
            "The file is not an F95Checker library.", "invalid_source"
        ) from exc
    except OSError as exc:
        raise _validation_error(
            f"Could not read F95Checker database: {exc}", "read_failed"
        ) from exc
    backup = _external_backup("f95checker", games)
    return backup, {
        "skipped_no_identity": skipped,
        "dropped_paths": dropped,
        "schema_version": "",
    }


def _title_metadata(title: str, f95_url: str) -> dict[str, object]:
    """Omit a missing title when the game has a thread URL so an overwrite
    never replaces a real local title with the placeholder. New games still
    get the placeholder from the insert default."""
    if title:
        return {"title": title}
    return {} if f95_url else {"title": "Imported Game"}


def _translate_f95checker_game(
    record: Mapping[str, object], base_dir: str, has_playtime: bool
) -> tuple[dict[str, object] | None, int]:
    dropped = 0
    title = _as_str(record.get("name"))
    developer = _as_str(record.get("developer"))
    f95_url = _f95checker_url(record)

    installed = _as_str(record.get("installed"))
    latest = _as_str(record.get("version"))
    finished = _as_str(record.get("finished"))

    archived = _as_str(record.get("archived")).lower() not in (
        "",
        "0",
        "false",
        "no",
    )
    if archived:
        play_status = "Abandoned"
    elif finished:
        play_status = "Completed"
    elif installed:
        play_status = "Playing"
    else:
        play_status = ""

    metadata = _title_metadata(title, f95_url)
    # Omitted when empty so an overwrite never clears a local thread URL.
    if f95_url:
        metadata["f95_url"] = f95_url
    if developer:
        metadata["developer"] = developer
    if installed:
        metadata["version"] = installed
    if latest:
        metadata["latest_version"] = latest

    user_state: dict[str, object] = {}
    if play_status:
        user_state["play_status"] = play_status
    if finished:
        user_state["finished_version"] = finished
    if has_playtime:
        playtime_seconds = _coerce_playtime_seconds(record.get("playtime"))
        if playtime_seconds is not None:
            user_state["playtime_seconds"] = playtime_seconds
    last_played = parse_timestamp(record.get("last_launched"))
    if last_played:
        user_state["last_played"] = last_played
    date_added = parse_timestamp(record.get("added_on"))
    if date_added:
        user_state["date_added"] = date_added

    raw_executables = [
        item.strip() for item in _parse_json_list(record.get("executables"))
    ]
    raw_executables = [item for item in raw_executables if item]

    resolved = [resolve_relative_path(item, base_dir) for item in raw_executables]
    kept = [apply_path_rule(item) for item in resolved]
    for raw, candidate in zip(raw_executables, kept):
        if raw and not candidate:
            dropped += 1
    kept = [item for item in kept if item]

    executable_paths: dict[str, object] = {}
    if kept:
        executable_paths["exe_path"] = kept[0]

    launch_targets: list[dict[str, object]] = []
    for extra in kept[1:]:
        launch_targets.append({"label": _target_label(extra), "exe_path": extra})

    if not _has_import_identity(
        f95_url,
        coerce_store_links(metadata.get("links")),
        _as_str(executable_paths.get("exe_path")),
    ):
        return None, dropped

    game: dict[str, object] = {"metadata": metadata}
    if user_state:
        game["user_state"] = user_state
    if executable_paths:
        game["executable_paths"] = executable_paths
    if launch_targets:
        game["launch_targets"] = launch_targets
    return game, dropped


# ---------------------------------------------------------------------------
# XLibrary reader
# ---------------------------------------------------------------------------


def _xlibrary_games(payload: object) -> tuple[list[Mapping[str, object]], object]:
    games: list[Mapping[str, object]] = []
    if isinstance(payload, list):
        for item in _as_list(cast(list[object], payload)):
            entry = _as_dict(item)
            if entry:
                games.append(entry)
        if not games and payload:
            raise _validation_error(
                "The file is not an XLibrary library.", "invalid_source"
            )
        return games, ""
    mapping = _as_dict(payload)
    if not mapping:
        raise _validation_error(
            "The file is not an XLibrary library.", "invalid_source"
        )
    if "documents" in mapping:
        family = _as_str(mapping.get("family"))
        if family and family != XLIBRARY_FAMILY:
            raise _validation_error(
                "The file is not an XLibrary library.", "invalid_source"
            )
        documents = mapping.get("documents")
        if not isinstance(documents, list):
            raise _validation_error(
                "The file is not an XLibrary library.", "invalid_source"
            )
        for raw_document in _as_list(cast(list[object], documents)):
            data = _as_dict(raw_document).get("data")
            entry = _as_dict(data)
            if entry:
                games.append(entry)
        return games, mapping.get("schemaVersion", "")
    if "games" in mapping:
        games_value = mapping.get("games")
        if not isinstance(games_value, list):
            raise _validation_error(
                "The file is not an XLibrary library.", "invalid_source"
            )
        settings = _as_dict(mapping.get("settings"))
        schema_version: object = mapping.get("schemaVersion", "")
        if not schema_version:
            schema_version = settings.get("schemaVersion", "")
        for item in _as_list(cast(list[object], games_value)):
            entry = _as_dict(item)
            if entry:
                games.append(entry)
        return games, schema_version
    raise _validation_error("The file is not an XLibrary library.", "invalid_source")


def _xlibrary_f95_url(data: Mapping[str, object]) -> str:
    links = _as_list(data.get("externalLinks"))
    for raw_link in links:
        link = _as_dict(raw_link)
        if not link:
            continue
        if _as_str(link.get("providerId")).lower() != "f95zone":
            continue
        from_url = extract_thread_id(link.get("url"))
        declared = _as_str(link.get("externalId"))
        declared_valid = declared.isdigit() and int(declared) > 0
        chosen = from_url or (declared if declared_valid else "")
        if chosen:
            return build_thread_url_from_id(chosen)
    return ""


def _xlibrary_executables(
    data: Mapping[str, object],
) -> tuple[str, list[dict[str, object]], int]:
    settings = _as_dict(data.get("launchSettings"))
    configurations = _as_list(settings.get("configurations"))
    default_id = _as_str(settings.get("defaultConfigurationId"))
    entries: list[tuple[str, str, str]] = []
    for raw_config in configurations:
        config = _as_dict(raw_config)
        if not config:
            continue
        exe = _as_str(config.get("executablePath"))
        if not exe:
            continue
        name = _as_str(config.get("name")) or _target_label(exe)
        entries.append((_as_str(config.get("id")), name, exe))
    default_entry: tuple[str, str] | None = None
    others: list[tuple[str, str]] = []
    for config_id, name, exe in entries:
        if default_id and config_id == default_id and default_entry is None:
            default_entry = (name, exe)
        else:
            others.append((name, exe))
    if default_entry is None and entries:
        _, name, exe = entries[0]
        default_entry = (name, exe)
        others = [(name, exe) for _, name, exe in entries[1:]]
    dropped = 0
    exe_path = ""
    if default_entry is not None:
        kept = apply_path_rule(default_entry[1])
        if default_entry[1] and not kept:
            dropped += 1
        exe_path = kept
    targets: list[dict[str, object]] = []
    for name, exe in others:
        kept = apply_path_rule(exe)
        if exe and not kept:
            dropped += 1
            continue
        if kept:
            targets.append({"label": name, "exe_path": kept})
    return exe_path, targets, dropped


def _translate_xlibrary_game(
    data: Mapping[str, object],
) -> tuple[dict[str, object] | None, int]:
    title = _as_str(data.get("name"))
    developer = _as_str(data.get("developer"))
    f95_url = _xlibrary_f95_url(data)

    metadata = _title_metadata(title, f95_url)
    # Omitted when empty so an overwrite never clears a local thread URL.
    if f95_url:
        metadata["f95_url"] = f95_url
    if developer:
        metadata["developer"] = developer
    installed = _as_str(data.get("installedVersion"))
    if installed:
        metadata["version"] = installed
    latest = _as_str(data.get("version"))
    if latest:
        metadata["latest_version"] = latest

    user_state: dict[str, object] = {}
    completion = _as_str(data.get("completionStatus")).lower()
    if completion and completion != "not started":
        mapped = XLIBRARY_COMPLETION_MAP.get(completion)
        if mapped:
            user_state["play_status"] = mapped
    ratings = _as_dict(data.get("categoryRatings"))
    if ratings:
        for source_key, target_key in (
            ("story", "rating_story"),
            ("graphics", "rating_graphics"),
            ("gameplay", "rating_gameplay"),
        ):
            rating = _coerce_rating(ratings.get(source_key))
            if rating is not None:
                user_state[target_key] = rating
    playtime_seconds = _coerce_playtime_seconds(data.get("totalPlaytime"))
    if playtime_seconds is not None:
        user_state["playtime_seconds"] = playtime_seconds
    last_played = parse_timestamp(data.get("lastPlayedAt"))
    if last_played:
        user_state["last_played"] = last_played
    date_added = parse_timestamp(data.get("createdAt"))
    if date_added:
        user_state["date_added"] = date_added

    exe_path, targets, dropped = _xlibrary_executables(data)

    if not _has_import_identity(
        f95_url, coerce_store_links(metadata.get("links")), exe_path
    ):
        return None, dropped
    game: dict[str, object] = {"metadata": metadata}
    if user_state:
        game["user_state"] = user_state
    if exe_path:
        game["executable_paths"] = {"exe_path": exe_path}
    if targets:
        game["launch_targets"] = targets
    return game, dropped


def read_xlibrary_library(path: str) -> tuple[dict[str, object], dict[str, object]]:
    if not os.path.isfile(path):
        raise _validation_error(
            "XLibrary library file was not found.", "file_not_found"
        )
    try:
        with open(path, encoding="utf-8-sig") as handle:
            payload = cast(object, json.load(handle))
    except json.JSONDecodeError as exc:
        raise _validation_error(
            "The file is not an XLibrary library.", "invalid_source"
        ) from exc
    except (UnicodeDecodeError, RecursionError) as exc:
        raise _validation_error(
            "The file is not an XLibrary library.", "invalid_source"
        ) from exc
    except OSError as exc:
        raise _validation_error(
            f"Could not read XLibrary library file: {exc}", "read_failed"
        ) from exc
    raw_games, schema_version = _xlibrary_games(payload)
    games: list[dict[str, object]] = []
    skipped = 0
    dropped = 0
    for data in raw_games:
        game, game_dropped = _translate_xlibrary_game(data)
        dropped += game_dropped
        if game is None:
            skipped += 1
            continue
        games.append(game)
    backup = _external_backup("xlibrary", games)
    return backup, {
        "skipped_no_identity": skipped,
        "dropped_paths": dropped,
        "schema_version": schema_version if schema_version != "" else "",
    }


# ---------------------------------------------------------------------------
# Atlas reader
# ---------------------------------------------------------------------------


def _atlas_mapped_playstate(value: object) -> str:
    return ATLAS_PLAYSTATE_MAP.get(_as_str(value).lower(), "")


def _translate_atlas_game(
    record: Mapping[str, object],
    versions: list[dict[str, object]],
    f95_id: str,
    ratings: Mapping[str, object],
) -> tuple[dict[str, object] | None, int]:
    dropped = 0
    title = _as_str(record.get("title"))
    developer = _as_str(record.get("creator"))
    engine = _as_str(record.get("engine"))
    f95_url = build_thread_url_from_id(f95_id) if f95_id else ""

    selected_id: int | None = None
    try:
        selected_raw = _as_str(record.get("selected_version_id"))
        if selected_raw:
            selected_id = int(selected_raw)
    except (TypeError, ValueError):
        selected_id = None

    by_rowid: dict[int, Mapping[str, object]] = {}
    for version in versions:
        try:
            by_rowid[int(_as_str(version.get("rowid")))] = version
        except (TypeError, ValueError):
            continue
    selected: dict[str, object] | None = None
    if selected_id is not None:
        found = by_rowid.get(selected_id)
        if found is not None:
            selected = dict(found)
    if selected is None and versions:
        selected = dict(
            sorted(
                versions,
                key=lambda item: (
                    _safe_int(item.get("date_added")),
                    _safe_int(item.get("rowid")),
                ),
                reverse=True,
            )[0]
        )

    metadata = _title_metadata(title, f95_url)
    # Omitted when empty so an overwrite never clears a local thread URL.
    if f95_url:
        metadata["f95_url"] = f95_url
    if developer:
        metadata["developer"] = developer
    if engine:
        metadata["engine"] = engine
    if selected is not None:
        version_text = _as_str(selected.get("version"))
        if version_text:
            metadata["version"] = version_text

    user_state: dict[str, object] = {}
    play_status = _atlas_mapped_playstate(record.get("playstate"))
    if not play_status and versions:
        agreed = {
            _atlas_mapped_playstate(item.get("playstate"))
            for item in versions
            if _atlas_mapped_playstate(item.get("playstate"))
        }
        if len(agreed) == 1:
            play_status = next(iter(agreed))
    if play_status:
        user_state["play_status"] = play_status

    finished_versions = [
        item
        for item in versions
        if _as_str(item.get("playstate")).lower() == "finished"
        and _as_str(item.get("version"))
    ]
    if finished_versions:
        newest_finished = sorted(
            finished_versions,
            key=lambda item: (
                _safe_int(item.get("date_added")),
                _safe_int(item.get("rowid")),
            ),
            reverse=True,
        )[0]
        user_state["finished_version"] = _as_str(newest_finished.get("version"))

    for source_key, target_key in (
        ("story", "rating_story"),
        ("graphics", "rating_graphics"),
        ("gameplay", "rating_gameplay"),
        ("fappability", "rating_fappability"),
    ):
        rating = _coerce_rating(ratings.get(source_key))
        if rating is not None:
            user_state[target_key] = rating

    playtime_seconds = _coerce_playtime_seconds(
        record.get("total_playtime"), multiplier=60.0
    )
    if playtime_seconds is not None:
        user_state["playtime_seconds"] = playtime_seconds
    last_played = parse_timestamp(record.get("last_played_r"))
    if last_played:
        user_state["last_played"] = last_played
    date_added = parse_timestamp(record.get("date_added"))
    if not date_added and versions:
        stamps = [parse_timestamp(item.get("date_added")) for item in versions]
        stamps = [item for item in stamps if item]
        if stamps:
            date_added = sorted(stamps)[0]
    if date_added:
        user_state["date_added"] = date_added
    if _as_str(record.get("is_favorite")).lower() not in ("", "0", "false", "no"):
        try:
            if int(float(_as_str(record.get("is_favorite")))):
                user_state["is_favorite"] = 1
        except (TypeError, ValueError):
            pass

    def _resolve_exec(version: Mapping[str, object]) -> str:
        exec_path = _as_str(version.get("exec_path"))
        if not exec_path:
            return ""
        game_path = _as_str(version.get("game_path"))
        return resolve_relative_path(exec_path, game_path)

    exe_path = ""
    if selected is not None:
        candidate = apply_path_rule(_resolve_exec(selected))
        raw = _resolve_exec(selected)
        if raw and not candidate:
            dropped += 1
        exe_path = candidate

    targets: list[dict[str, object]] = []
    for version in versions:
        if selected is not None and _as_str(version.get("rowid")) == _as_str(
            selected.get("rowid")
        ):
            continue
        version_text = _as_str(version.get("version"))
        if not version_text:
            continue
        raw = _resolve_exec(version)
        if not raw:
            continue
        kept = apply_path_rule(raw)
        if not kept:
            dropped += 1
            continue
        targets.append({"label": f"v{version_text}", "exe_path": kept})

    if not _has_import_identity(
        f95_url, coerce_store_links(metadata.get("links")), exe_path
    ):
        return None, dropped
    game: dict[str, object] = {"metadata": metadata}
    if user_state:
        game["user_state"] = user_state
    if exe_path:
        game["executable_paths"] = {"exe_path": exe_path}
    if targets:
        game["launch_targets"] = targets
    return game, dropped


def read_atlas_library(path: str) -> tuple[dict[str, object], dict[str, object]]:
    if not os.path.isfile(path):
        raise _validation_error("Atlas database was not found.", "file_not_found")
    try:
        with open_snapshot(path) as conn:
            if not table_exists(conn, "games"):
                raise _validation_error(
                    "The file is not an Atlas library.", "invalid_source"
                )
            version_columns = (
                table_columns(conn, "versions")
                if table_exists(conn, "versions")
                else set[str]()
            )
            try:
                game_rows = _fetch_rows(conn, "SELECT * FROM games")
            except sqlite3.Error as exc:
                raise _validation_error(
                    "The file is not an Atlas library.", "invalid_source"
                ) from exc
            version_rows: list[sqlite3.Row] = []
            if version_columns:
                try:
                    version_rows = _fetch_rows(conn, "SELECT rowid, * FROM versions")
                except sqlite3.Error:
                    version_rows = []
            mappings: dict[int, str] = {}
            if table_exists(conn, "f95_zone_mappings") and table_columns(
                conn, "f95_zone_mappings"
            ) >= {"record_id", "f95_id"}:
                try:
                    for mapping_row in _fetch_rows(
                        conn, "SELECT record_id, f95_id FROM f95_zone_mappings"
                    ):
                        mapping_record = _row_dict(mapping_row)
                        try:
                            record_id = int(_as_str(mapping_record.get("record_id")))
                            f95_id = int(_as_str(mapping_record.get("f95_id")))
                        except (TypeError, ValueError):
                            continue
                        if record_id > 0 and f95_id > 0 and record_id not in mappings:
                            mappings[record_id] = str(f95_id)
                except sqlite3.Error:
                    mappings = {}
            ratings_by_game: dict[int, dict[str, object]] = {}
            if table_exists(conn, "game_personal_ratings"):
                try:
                    for rating_row in _fetch_rows(
                        conn, "SELECT * FROM game_personal_ratings"
                    ):
                        rating_record = _row_dict(rating_row)
                        try:
                            record_id = int(_as_str(rating_record.get("record_id")))
                        except (TypeError, ValueError):
                            continue
                        ratings_by_game[record_id] = rating_record
                except sqlite3.Error:
                    ratings_by_game = {}
            versions_by_game: dict[int, list[dict[str, object]]] = {}
            for version_row in version_rows:
                version_record = _row_dict(version_row)
                try:
                    record_id = int(_as_str(version_record.get("record_id")))
                except (TypeError, ValueError):
                    continue
                versions_by_game.setdefault(record_id, []).append(version_record)
            games: list[dict[str, object]] = []
            skipped = 0
            dropped = 0
            for game_row in game_rows:
                record = _row_dict(game_row)
                try:
                    record_id = int(_as_str(record.get("record_id")))
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                game, game_dropped = _translate_atlas_game(
                    record,
                    versions_by_game.get(record_id, []),
                    mappings.get(record_id, ""),
                    ratings_by_game.get(record_id, {}),
                )
                dropped += game_dropped
                if game is None:
                    skipped += 1
                    continue
                games.append(game)
    except sqlite3.Error as exc:
        raise _validation_error(
            "The file is not an Atlas library.", "invalid_source"
        ) from exc
    except OSError as exc:
        raise _validation_error(
            f"Could not read Atlas database: {exc}", "read_failed"
        ) from exc
    backup = _external_backup("atlas", games)
    return backup, {
        "skipped_no_identity": skipped,
        "dropped_paths": dropped,
        "schema_version": "",
    }


# ---------------------------------------------------------------------------
# f69 reader
# ---------------------------------------------------------------------------


def _f69_install_exe(install: Mapping[str, object]) -> str:
    """Resolve an f69 install to a launch file.

    f69 leaves ``executable`` empty to auto-pick the game binary inside
    ``install_path``; wLib needs a file, so the folder is scanned the same way
    wLib's own library scan does. A custom launch command ("wine game.exe")
    only maps when it resolves to an existing file.
    """
    install_path = _as_str(install.get("install_path"))
    executable = _as_str(install.get("executable"))
    if executable:
        resolved = apply_path_rule(resolve_relative_path(executable, install_path))
        if resolved and (os.path.isfile(resolved) or not os.path.isdir(install_path)):
            return resolved
        return ""
    if not install_path or not os.path.isdir(install_path):
        return ""
    return apply_path_rule(find_launcher(install_path))


def _translate_f69_game(
    record: Mapping[str, object],
    installs: list[dict[str, object]],
    labels: list[str],
) -> tuple[dict[str, object] | None, int]:
    dropped = 0
    title = _as_str(record.get("name"))
    developer = _as_str(record.get("developer"))
    # Custom games carry a synthetic id with the high bit set, which SQLite
    # stores as a negative integer; only positive ids are real F95 threads.
    f95_url = build_thread_url_from_id(record.get("f95_thread_id"))

    default_id = _as_str(record.get("default_install_id"))
    ordered = sorted(
        installs,
        key=lambda item: (
            _as_str(item.get("id")) != default_id,
            -_safe_int(item.get("installed_at")),
        ),
    )
    selected = ordered[0] if ordered else None

    metadata = _title_metadata(title, f95_url)
    # Omitted when empty so an overwrite never clears a local thread URL.
    if f95_url:
        metadata["f95_url"] = f95_url
    if developer:
        metadata["developer"] = developer
    engine = F69_ENGINE_MAP.get(_as_str(record.get("engine")).lower(), "")
    if engine:
        metadata["engine"] = engine
    if selected is not None:
        installed_version = _as_str(selected.get("version"))
        if installed_version:
            metadata["version"] = installed_version
    latest = _as_str(record.get("latest_version"))
    if latest:
        metadata["latest_version"] = latest
    if _as_str(record.get("dev_status")).lower() == "completed":
        metadata["thread_completed"] = 1

    user_state: dict[str, object] = {}
    play_status = F69_COMPLETION_MAP.get(
        _as_str(record.get("completion_status")).lower(), ""
    )
    if play_status:
        user_state["play_status"] = play_status
    playtime_seconds = _coerce_playtime_seconds(record.get("total_playtime_s"))
    if playtime_seconds is not None:
        user_state["playtime_seconds"] = playtime_seconds
    last_played = parse_timestamp(record.get("last_played_at"))
    if last_played:
        user_state["last_played"] = last_played
    date_added = parse_timestamp(record.get("created_at"))
    if date_added:
        user_state["date_added"] = date_added
    if labels:
        user_state["collections"] = labels

    exe_path = ""
    targets: list[dict[str, object]] = []
    for install in ordered:
        exe = _f69_install_exe(install)
        if not exe:
            if _as_str(install.get("install_path")) or _as_str(
                install.get("executable")
            ):
                dropped += 1
            continue
        if not exe_path:
            exe_path = exe
            continue
        label = _as_str(install.get("name"))
        if not label:
            version_text = _as_str(install.get("version"))
            label = f"v{version_text}" if version_text else _target_label(exe)
        targets.append({"label": label, "exe_path": exe})

    if not _has_import_identity(
        f95_url, coerce_store_links(metadata.get("links")), exe_path
    ):
        return None, dropped
    game: dict[str, object] = {"metadata": metadata}
    if user_state:
        game["user_state"] = user_state
    if exe_path:
        game["executable_paths"] = {"exe_path": exe_path}
    if targets:
        game["launch_targets"] = targets
    return game, dropped


def _f69_labels(conn: sqlite3.Connection) -> dict[int, list[str]]:
    if not (table_exists(conn, "game_labels") and table_exists(conn, "user_labels")):
        return {}
    try:
        rows = _fetch_rows(
            conn,
            """
            SELECT gl.game_thread_id AS game_thread_id, ul.name AS name
            FROM game_labels gl JOIN user_labels ul ON ul.id = gl.label_id
            ORDER BY ul.name COLLATE NOCASE
            """,
        )
    except sqlite3.Error:
        return {}
    labels: dict[int, list[str]] = {}
    for row in rows:
        record = _row_dict(row)
        name = _as_str(record.get("name"))
        if name:
            labels.setdefault(_safe_int(record.get("game_thread_id")), []).append(name)
    return labels


def _f69_schema_version(conn: sqlite3.Connection) -> str:
    if not table_exists(conn, "_schema_version"):
        return ""
    try:
        fetched = cast(
            object, conn.execute("SELECT MAX(id) FROM _schema_version").fetchone()
        )
    except sqlite3.Error:
        return ""
    if not isinstance(fetched, sqlite3.Row):
        return ""
    return _as_str(cast(object, fetched[0]))


def read_f69_library(path: str) -> tuple[dict[str, object], dict[str, object]]:
    if not os.path.isfile(path):
        raise _validation_error("f69 database was not found.", "file_not_found")
    try:
        with open_snapshot(path) as conn:
            if not table_exists(conn, "games") or not {
                "f95_thread_id",
                "name",
            } <= table_columns(conn, "games"):
                raise _validation_error(
                    "The file is not an f69 library.", "invalid_source"
                )
            try:
                game_rows = _fetch_rows(conn, "SELECT * FROM games")
            except sqlite3.Error as exc:
                raise _validation_error(
                    "The file is not an f69 library.", "invalid_source"
                ) from exc
            installs_by_game: dict[int, list[dict[str, object]]] = {}
            if table_exists(conn, "installs"):
                try:
                    for install_row in _fetch_rows(conn, "SELECT * FROM installs"):
                        install = _row_dict(install_row)
                        installs_by_game.setdefault(
                            _safe_int(install.get("game_thread_id")), []
                        ).append(install)
                except sqlite3.Error:
                    installs_by_game = {}
            labels_by_game = _f69_labels(conn)
            schema_version = _f69_schema_version(conn)
            games: list[dict[str, object]] = []
            skipped = 0
            dropped = 0
            for game_row in game_rows:
                record = _row_dict(game_row)
                thread_id = _safe_int(record.get("f95_thread_id"))
                game, game_dropped = _translate_f69_game(
                    record,
                    installs_by_game.get(thread_id, []),
                    labels_by_game.get(thread_id, []),
                )
                dropped += game_dropped
                if game is None:
                    skipped += 1
                    continue
                games.append(game)
    except sqlite3.Error as exc:
        raise _validation_error(
            "The file is not an f69 library.", "invalid_source"
        ) from exc
    except OSError as exc:
        raise _validation_error(
            f"Could not read f69 database: {exc}", "read_failed"
        ) from exc
    backup = _external_backup("f69", games)
    return backup, {
        "skipped_no_identity": skipped,
        "dropped_paths": dropped,
        "schema_version": schema_version,
    }


# ---------------------------------------------------------------------------
# Dispatch and detection
# ---------------------------------------------------------------------------

PLAYNITE_COLLECTIONS = {
    "games": "Game",
    "completionstatuses": "CompletionStatus",
    "companies": "Company",
    "categories": "Category",
    "tags": "Tag",
    "features": "GameFeature",
    "genres": "Genre",
}


def _is_playnite_file_action(action: Mapping[str, object]) -> bool:
    """File-type actions. LiteDB serializes the GameActionType enum as its name.

    Playnite's mapper writes ``"File"`` (not ``0``), while older fixtures and
    hand-built documents use the numeric enum value.
    """
    action_type = action.get("Type")
    if isinstance(action_type, str):
        return action_type.strip().casefold() == "file"
    return action_type == 0


def _translate_playnite_game(
    record: Mapping[str, object],
    related: Mapping[str, dict[str, str]],
    library_dir: str,
) -> tuple[dict[str, object] | None, int]:
    title = _as_str(record.get("Name"))
    raw_links = [_as_dict(link) for link in _as_list(record.get("Links"))]
    f95_url = next(
        (url for link in raw_links if (url := coerce_thread_url(link.get("Url")))), ""
    )
    name_url = coerce_thread_url(title)
    placeholder = re.fullmatch(r"F95-(\d+)", title, re.I)
    f95_url = (
        f95_url
        or name_url
        or (build_thread_url_from_id(placeholder[1]) if placeholder else "")
    )
    links = coerce_store_links([{"url": link.get("Url")} for link in raw_links])
    game_id = _as_str(record.get("GameId"))
    if re.fullmatch(r"(?:RJ|VJ|BJ)\d+", game_id, re.I):
        dlsite = link_from_id("dlsite", game_id)
        if dlsite:
            links = coerce_store_links([*links, dlsite])
    metadata: dict[str, object] = _title_metadata(
        "" if name_url or placeholder else title, f95_url
    )
    if f95_url:
        metadata["f95_url"] = f95_url
    if links:
        metadata["links"] = links

    def names(collection: str, field: str) -> list[str]:
        lookup = related.get(collection, {})
        return [
            name
            for raw_id in _as_list(record.get(field))
            if (name := lookup.get(_as_str(raw_id), ""))
        ]

    developers = names("companies", "DeveloperIds")
    if developers:
        metadata["developer"] = developers[0]
    version = _as_str(record.get("Version"))
    if version:
        metadata["version"] = version
    engines = {
        normalize_prefix_label(label): label for label in F69_ENGINE_MAP.values()
    }
    prefixes = (
        names("tags", "TagIds")
        + names("features", "FeatureIds")
        + names("genres", "GenreIds")
    )
    for prefix in prefixes:
        normalized = normalize_prefix_label(prefix)
        if normalized == "completed":
            metadata["thread_completed"] = 1
        if normalized in engines and "engine" not in metadata:
            metadata["engine"] = engines[normalized]
    state: dict[str, object] = {}
    status_name = (
        related.get("completionstatuses", {})
        .get(_as_str(record.get("CompletionStatusId")), "")
        .casefold()
    )
    statuses = {
        status.casefold(): status
        for status in CANONICAL_PLAY_STATUSES
        if status != "Not Started"
    }
    statuses["beaten"] = "Completed"
    if status_name in statuses:
        state["play_status"] = statuses[status_name]
    seconds = _coerce_playtime_seconds(record.get("Playtime"))
    if seconds is not None:
        state["playtime_seconds"] = seconds
    for source, target in (("LastActivity", "last_played"), ("Added", "date_added")):
        timestamp = parse_timestamp(record.get(source))
        if timestamp:
            state[target] = timestamp
    if isinstance(record.get("Favorite"), bool):
        state["is_favorite"] = record["Favorite"]
    categories = names("categories", "CategoryIds")
    if categories:
        state["collections"] = categories
    notes = _as_str(record.get("Notes"))
    if notes:
        state["progress"] = notes
    score = _safe_int(record.get("UserScore"))
    if 1 <= score <= 100:
        for rating in ("story", "graphics", "gameplay", "fappability"):
            state[f"rating_{rating}"] = round(score / 20, 1)

    install = _as_str(record.get("InstallDirectory"))
    playnite_dir = os.path.dirname(library_dir)

    def expand(value: object) -> str:
        return (
            _as_str(value)
            .replace("{InstallDir}", install)
            .replace("{InstallDirName}", ntpath.basename(install.rstrip("/\\")))
            .replace("{PlayniteDir}", playnite_dir)
        )

    executables: list[tuple[str, str]] = []
    dropped = 0
    for raw in _as_list(record.get("GameActions")):
        action = _as_dict(raw)
        if not _is_playnite_file_action(action) or action.get("IsPlayAction") is not True:
            continue
        raw_path = expand(action.get("Path"))
        working = resolve_relative_path(expand(action.get("WorkingDir")), install)
        path = (
            apply_path_rule(resolve_relative_path(raw_path, working or install))
            if raw_path
            else ""
        )
        if path:
            executables.append(
                (_as_str(action.get("Name")) or _target_label(path), path)
            )
        elif raw_path:
            dropped += 1
    if (
        not executables
        and (folder := apply_path_rule(install))
        and os.path.isdir(folder)
    ):
        launcher = find_launcher(folder)
        if launcher:
            executables.append((_target_label(launcher), launcher))
    exe = executables[0][1] if executables else ""
    if not _has_import_identity(f95_url, links, exe):
        return None, dropped
    game: dict[str, object] = {"metadata": metadata}
    if state:
        game["user_state"] = state
    if exe:
        game["executable_paths"] = {"exe_path": exe}
    if len(executables) > 1:
        game["launch_targets"] = [
            {"label": label, "exe_path": path} for label, path in executables[1:]
        ]
    cover = _as_str(record.get("CoverImage"))
    if cover.startswith(("https://", "http://")):
        metadata["cover_image_path"] = cover
    elif cover:
        files_dir = Path(library_dir, "files").resolve()
        candidate = (
            Path(cover)
            if os.path.isabs(cover)
            else files_dir / cover.replace("\\", "/")
        )
        candidate = candidate.resolve()
        if (
            candidate.is_relative_to(files_dir)
            and _playnite_data_path_allowed(candidate)
            and candidate.is_file()
        ):
            game["cover_file"] = str(candidate)
    return game, dropped


def read_playnite_library(path: str) -> tuple[dict[str, object], dict[str, object]]:
    source = Path(path)
    if not _playnite_data_path_allowed(source):
        raise _validation_error("Choose games.db from Playnite's library folder.")
    if not source.is_file():
        raise _validation_error("Playnite library was not found.", "file_not_found")
    if source.name.casefold() != "games.db":
        raise _validation_error("Choose games.db from a Playnite library.")
    folder = source.parent

    def check_journals() -> None:
        if any(
            (folder / f"{name}-journal.db").exists() for name in PLAYNITE_COLLECTIONS
        ):
            raise _validation_error("Close Playnite and try again.", "source_locked")

    try:
        check_journals()
        with tempfile.TemporaryDirectory(prefix="wlib-playnite-") as temp:
            for name in (
                "database.json",
                *(f"{col}.db" for col in PLAYNITE_COLLECTIONS),
            ):
                file = folder / name
                if file.is_file():
                    if not _playnite_data_path_allowed(file):
                        raise _validation_error("The file is not a Playnite library.")
                    _ = shutil.copy2(file, Path(temp, name))
            check_journals()
            version_file = Path(temp, "database.json")
            version = 4
            if version_file.exists():
                version = _safe_int(
                    _as_dict(
                        cast(
                            object,
                            json.loads(version_file.read_text(encoding="utf-8-sig")),
                        )
                    ).get("Version")
                )
            if version > 4:
                raise _validation_error(
                    "Playnite's library format is newer than wLib supports.",
                    "unsupported_format_version",
                )
            if version < 4:
                raise _validation_error(
                    "Update Playnite before importing this library.",
                    "unsupported_format_version",
                )
            related: dict[str, dict[str, str]] = {}
            for collection, model in PLAYNITE_COLLECTIONS.items():
                if collection == "games":
                    continue
                file = Path(temp, f"{collection}.db")
                if file.is_file():
                    related[collection] = {
                        _as_str(row.get("_id")): _as_str(row.get("Name"))
                        for row in LiteDBReader(file).read_collection(model)
                    }
            records = LiteDBReader(Path(temp, "games.db")).read_collection("Game")
            games: list[dict[str, object]] = []
            skipped = dropped = 0
            for record in records:
                game, count = _translate_playnite_game(
                    record, related, str(folder.resolve())
                )
                dropped += count
                if game is None:
                    skipped += 1
                else:
                    games.append(game)
    except PermissionError as exc:
        raise _validation_error(
            "Close Playnite and try again.", "source_locked"
        ) from exc
    except (OSError, ValueError) as exc:
        if isinstance(exc, BackupValidationError):
            raise
        raise _validation_error("The file is not a Playnite library.") from exc
    return _external_backup("playnite", games), {
        "skipped_no_identity": skipped,
        "dropped_paths": dropped,
        "schema_version": str(version),
    }


def _playnite_data_path_allowed(path: Path) -> bool:
    """Never follow a picked path or a symlink into private add-on data."""
    return not any(
        part.casefold() in {"extensions", "extensionsdata"}
        for part in (*path.parts, *path.resolve().parts)
    )


def _external_backup(source: str, games: list[dict[str, object]]) -> dict[str, object]:
    from datetime import datetime as _datetime

    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "exported_at": _datetime.now().isoformat(),
        "app": {"name": SOURCE_LABELS[source]},
        "selected_sections": [
            "metadata",
            "user_state",
            "executable_paths",
            "launch_targets",
        ],
        "games": games,
    }


def read_external_library(
    source: object, path: object
) -> tuple[dict[str, object], dict[str, object]]:
    key = normalize_source(source)
    library_path = os.path.expanduser(_as_str(path))
    if not library_path:
        raise _validation_error("Choose a library file to import.", "missing_path")
    if key == "f95checker":
        return read_f95checker_library(library_path)
    if key == "atlas":
        return read_atlas_library(library_path)
    if key == "f69":
        return read_f69_library(library_path)
    if key == "playnite":
        return read_playnite_library(library_path)
    return read_xlibrary_library(library_path)


def _home_dir() -> str:
    override = os.environ.get("HOME", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.abspath(os.path.expanduser("~"))


def _config_base() -> str:
    override = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(_home_dir(), ".config")


def _data_base() -> str:
    override = os.environ.get("XDG_DATA_HOME", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(_home_dir(), ".local", "share")


def locate_external_library(source: object) -> dict[str, object]:
    key = normalize_source(source)
    checked: list[str] = []
    if is_windows():
        appdata = os.environ.get("APPDATA", "").strip()
        program_files = (
            os.environ.get("PROGRAMFILES", "").strip()
            or os.environ.get("ProgramFiles", "").strip()
        )
        if key == "f95checker":
            if appdata:
                checked.append(os.path.join(appdata, "f95checker", "db.sqlite3"))
        elif key == "xlibrary":
            if appdata:
                checked.append(
                    os.path.join(appdata, "xlibrary", "canonical", "library.games.json")
                )
        elif key == "playnite":
            if appdata:
                playnite_dir = os.path.join(appdata, "Playnite")
                config_path = os.path.join(playnite_dir, "config.json")
                try:
                    if not _playnite_data_path_allowed(Path(config_path)):
                        raise ValueError("Private add-on settings are not library configuration")
                    with open(config_path, encoding="utf-8-sig") as handle:
                        config = _as_dict(cast(object, json.load(handle)))
                    custom = _as_str(config.get("DatabasePath"))
                    custom = re.sub(r"%appdata%", lambda _: appdata, custom, flags=re.I)
                    custom = custom.replace("{PlayniteDir}", playnite_dir)
                    if custom:
                        checked.append(
                            os.path.join(os.path.normpath(custom), "games.db")
                        )
                except (OSError, ValueError):
                    pass
                checked.append(os.path.join(playnite_dir, "library", "games.db"))
        elif key == "atlas":
            if program_files:
                checked.append(os.path.join(program_files, "Atlas", "data", "data.db"))
            if appdata:
                checked.append(os.path.join(appdata, "Atlas", "data", "data.db"))
    else:
        home = _home_dir()
        config_base = _config_base()
        if key == "f95checker":
            checked.append(os.path.join(home, ".config", "f95checker", "db.sqlite3"))
            checked.append(os.path.join(home, ".f95checker", "db.sqlite3"))
        elif key == "xlibrary":
            checked.append(
                os.path.join(config_base, "xlibrary", "canonical", "library.games.json")
            )
        elif key == "atlas":
            checked.append(os.path.join(config_base, "atlas", "data", "data.db"))
            checked.append(os.path.join(config_base, "Atlas", "data", "data.db"))
        elif key == "f69":
            # System installs keep data under XDG_DATA_HOME; portable bundles
            # keep it beside the binary, which the user picks by hand.
            checked.append(os.path.join(_data_base(), "f69", "f69.db"))
    found = ""
    for candidate in checked:
        if candidate and os.path.isfile(candidate):
            found = candidate
            break
    return {"source": key, "path": found, "checked": checked}
