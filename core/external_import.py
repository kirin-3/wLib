# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only readers for external game-tracking apps (F95Checker, Atlas, XLibrary).

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
from typing import cast
from urllib.parse import urlparse

from core.backup_format import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    BackupValidationError,
)
from core.f95zone import F95_HOSTS, extract_thread_id, normalize_thread_url
from core.host_platform import is_windows


def _validation_error(
    message: str, error_code: str = "invalid_source"
) -> BackupValidationError:
    """Build the shared backup validation error."""
    return BackupValidationError(message, error_code)


EXTERNAL_SOURCES = ("f95checker", "atlas", "xlibrary")

SOURCE_LABELS = {
    "f95checker": "F95Checker",
    "atlas": "Atlas",
    "xlibrary": "XLibrary",
}

EXTERNAL_IMPORT_FILE_FILTERS = {
    "f95checker": (
        "F95Checker Databases (db.sqlite3;*.sqlite3;*.db)",
        "All files (*.*)",
    ),
    "atlas": (
        "Atlas Databases (data.db;*.db)",
        "All files (*.*)",
    ),
    "xlibrary": (
        "XLibrary Libraries (*.json)",
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


def normalize_source(source: object) -> str:
    key = str(source or "").strip().lower()
    if key not in EXTERNAL_SOURCES:
        raise _validation_error(
            f"Unknown library source: {source}. Choose F95Checker, Atlas, or XLibrary.",
            "unknown_source",
        )
    return key


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


#: Largest value SQLite accepts for an INTEGER column. Playtimes beyond this
#: (or non-finite ones like inf/nan) are treated as unknown and omitted so a
#: corrupt source value cannot abort the whole import with OverflowError.
_SQLITE_INTEGER_MAX = 9223372036854775807


def _coerce_playtime_seconds(value: object, multiplier: float = 1.0) -> int | None:
    """Parse a source playtime into whole seconds, or None when unusable."""
    try:
        amount = float(_as_str(value) or "0")
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None
    try:
        scaled = amount * multiplier
    except OverflowError:
        return None
    if not math.isfinite(scaled) or scaled <= 0:
        return None
    if scaled > _SQLITE_INTEGER_MAX:
        return None
    try:
        return int(scaled)
    except (OverflowError, ValueError):
        return None


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

    if not f95_url and not executable_paths:
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

    if not f95_url and not exe_path:
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
    except UnicodeDecodeError as exc:
        raise _validation_error(
            "The file is not an XLibrary library.", "invalid_source"
        ) from exc
    except RecursionError as exc:
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

    if not f95_url and not exe_path:
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
# Dispatch and detection
# ---------------------------------------------------------------------------


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
    found = ""
    for candidate in checked:
        if candidate and os.path.isfile(candidate):
            found = candidate
            break
    return {"source": key, "path": found, "checked": checked}
