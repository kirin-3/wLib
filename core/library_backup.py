from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import datetime
from typing import cast

from core.database import (
    DEFAULT_PLAY_STATUS,
    RPGMAKER_LINUX_RUNNER_SETTING,
    get_all_games,
    get_connection,
    normalize_launch_mode,
    normalize_play_status,
)
from core.f95zone import normalize_thread_url, thread_urls_match

BACKUP_FORMAT = "wlib.library_migration"
BACKUP_FORMAT_VERSION = 1

SECTION_METADATA = "metadata"
SECTION_USER_STATE = "user_state"
SECTION_LAUNCH_CONFIG = "launch_config"
SECTION_EXECUTABLE_PATHS = "executable_paths"
SECTION_LAUNCH_TARGETS = "launch_targets"
SECTION_SETTINGS_GENERAL = "settings_general"
SECTION_SETTINGS_PATHS = "settings_paths"

SUPPORTED_SECTIONS = (
    SECTION_USER_STATE,
    SECTION_LAUNCH_CONFIG,
    SECTION_EXECUTABLE_PATHS,
    SECTION_LAUNCH_TARGETS,
    SECTION_SETTINGS_GENERAL,
    SECTION_SETTINGS_PATHS,
)
DEFAULT_EXPORT_SECTIONS = (
    SECTION_USER_STATE,
    SECTION_LAUNCH_CONFIG,
    SECTION_EXECUTABLE_PATHS,
    SECTION_LAUNCH_TARGETS,
    SECTION_SETTINGS_GENERAL,
)
SUPPORTED_IMPORT_SECTIONS = (SECTION_METADATA, *SUPPORTED_SECTIONS)

METADATA_FIELDS = (
    "title",
    "developer",
    "engine",
    "tags",
    "f95_url",
    "version",
    "latest_version",
    "cover_image_path",
)
USER_STATE_FIELDS = (
    "play_status",
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
)
LAUNCH_CONFIG_FIELDS = (
    "command_line_args",
    "launch_mode",
    "run_japanese_locale",
    "run_wayland",
    "auto_inject_ce",
    "custom_prefix",
    "proton_version",
)
EXECUTABLE_PATH_FIELDS = ("exe_path",)
LAUNCH_TARGET_FIELDS = ("label", "exe_path", "sort_order", "created_at", "updated_at")
GENERAL_SETTINGS_KEYS = ("enable_logging", "auto_update_check")
PATH_SETTINGS_KEYS = (
    "proton_path",
    "wine_prefix_path",
    "playwright_browsers_path",
    RPGMAKER_LINUX_RUNNER_SETTING,
)

BOOLEAN_GAME_FIELDS = (
    "run_japanese_locale",
    "run_wayland",
    "auto_inject_ce",
    "is_favorite",
)
INTEGER_GAME_FIELDS = ("playtime_seconds",)
REAL_GAME_FIELDS = (
    "rating_graphics",
    "rating_story",
    "rating_fappability",
    "rating_gameplay",
)
TIMESTAMP_GAME_FIELDS = ("last_played", "date_added")
IMPORTABLE_GAME_FIELDS = (
    *METADATA_FIELDS,
    *USER_STATE_FIELDS,
    *LAUNCH_CONFIG_FIELDS,
    *EXECUTABLE_PATH_FIELDS,
)


class BackupValidationError(ValueError):
    def __init__(self, message: str, error_code: str = "invalid_backup") -> None:
        super().__init__(message)
        self.error_code: str = error_code


def _coerce_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[object, object], value).items()}


def _coerce_sequence(value: object) -> list[object]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return []
    return list(value)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _coerce_non_negative_int(value: object) -> int:
    try:
        return max(0, int(float(str(value or 0))))
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: object) -> float:
    try:
        return float(str(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        items = cast(list[object], value)
        return ", ".join(str(item).strip() for item in items if str(item).strip())
    return str(value)


def _normalize_identity_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _section_option(options: Mapping[str, object] | None) -> tuple[bool, object]:
    option_map = _coerce_mapping(options or {})
    for key in ("sections", "selected_sections", "include_sections"):
        if key in option_map:
            return True, option_map.get(key)
    return False, None


def _select_sections(
    options: Mapping[str, object] | None,
    *,
    default_sections: Sequence[str],
    available_sections: Sequence[str] | None = None,
    supported_sections: Sequence[str] = SUPPORTED_SECTIONS,
    reject_empty_selection: bool = False,
) -> set[str]:
    supported = set(supported_sections)
    if available_sections is not None:
        supported &= set(available_sections)

    has_section_option, raw_sections = _section_option(options)
    selected: set[str]

    if not has_section_option:
        selected = set(default_sections)
    else:
        selected = set()
        if isinstance(raw_sections, Mapping):
            section_map = cast(Mapping[object, object], raw_sections)
            for key, enabled in section_map.items():
                if _coerce_bool(enabled):
                    selected.add(str(key))
        else:
            selected.update(str(section) for section in _coerce_sequence(raw_sections))

    selected &= supported
    if reject_empty_selection and has_section_option and not selected:
        raise BackupValidationError(
            "Select at least one section to import.", "empty_selection"
        )
    return selected


def _pick_fields(source: Mapping[str, object], fields: Sequence[str]) -> dict[str, object]:
    return {field: source.get(field, "") for field in fields}


def _game_section(game: Mapping[str, object], section: str) -> dict[str, object]:
    return _coerce_mapping(game.get(section))


def _get_settings_payload(keys: Sequence[str]) -> dict[str, str]:
    if not keys:
        return {}

    placeholders = ",".join("?" for _ in keys)
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _ = cursor.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            tuple(keys),
        )
        rows = cast(list[tuple[object, object]], cursor.fetchall())

    values = {str(key): str(value) for key, value in rows}
    return {key: values.get(key, "") for key in keys}


def build_library_backup(
    options: Mapping[str, object] | None = None,
    *,
    app_version: str = "",
) -> dict[str, object]:
    sections = _select_sections(options, default_sections=DEFAULT_EXPORT_SECTIONS)
    games: list[dict[str, object]] = []

    for game in get_all_games():
        exported_game: dict[str, object] = {
            "metadata": _pick_fields(game, METADATA_FIELDS),
        }
        if SECTION_USER_STATE in sections:
            exported_game[SECTION_USER_STATE] = _pick_fields(game, USER_STATE_FIELDS)
        if SECTION_LAUNCH_CONFIG in sections:
            exported_game[SECTION_LAUNCH_CONFIG] = _pick_fields(
                game, LAUNCH_CONFIG_FIELDS
            )
        if SECTION_EXECUTABLE_PATHS in sections:
            exported_game[SECTION_EXECUTABLE_PATHS] = _pick_fields(
                game, EXECUTABLE_PATH_FIELDS
            )
        if SECTION_LAUNCH_TARGETS in sections:
            targets = _coerce_sequence(game.get("launch_targets"))
            exported_game[SECTION_LAUNCH_TARGETS] = [
                _pick_fields(_coerce_mapping(target), LAUNCH_TARGET_FIELDS)
                for target in targets
            ]
        games.append(exported_game)

    backup: dict[str, object] = {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "exported_at": datetime.now().isoformat(),
        "app": {"name": "wLib", "version": app_version},
        "selected_sections": sorted(sections),
        "games": games,
    }

    settings: dict[str, object] = {}
    if SECTION_SETTINGS_GENERAL in sections:
        settings["general"] = _get_settings_payload(GENERAL_SETTINGS_KEYS)
    if SECTION_SETTINGS_PATHS in sections:
        settings["paths"] = _get_settings_payload(PATH_SETTINGS_KEYS)
    if settings:
        backup["settings"] = settings

    return backup


def _default_export_filename() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"wlib-library-{timestamp}.json"


def _resolve_export_destination(destination_path: object) -> str:
    raw_path = str(destination_path or "").strip()
    if not raw_path:
        raise BackupValidationError(
            "Choose where to save the JSON export.", "missing_destination"
        )

    expanded = os.path.expanduser(raw_path)
    if os.path.isdir(expanded):
        expanded = os.path.join(expanded, _default_export_filename())
    elif not expanded.lower().endswith(".json"):
        expanded = f"{expanded}.json"

    parent_dir = os.path.dirname(expanded) or "."
    if not os.path.isdir(parent_dir):
        raise BackupValidationError(
            f"Export folder does not exist: {parent_dir}", "invalid_destination"
        )

    return expanded


def write_library_backup(
    destination_path: object,
    options: Mapping[str, object] | None = None,
    *,
    app_version: str = "",
) -> dict[str, object]:
    output_path = _resolve_export_destination(destination_path)
    backup = build_library_backup(options, app_version=app_version)
    tmp_path = f"{output_path}.tmp"

    try:
        with open(tmp_path, "w", encoding="utf-8") as backup_file:
            json.dump(backup, backup_file, ensure_ascii=False, indent=2)
            _ = backup_file.write("\n")
        os.replace(tmp_path, output_path)
    except OSError as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise BackupValidationError(
            f"Could not write export file: {exc}", "write_failed"
        ) from exc

    return {
        "path": output_path,
        "selected_sections": backup["selected_sections"],
        "game_count": len(_coerce_sequence(backup.get("games"))),
    }


def load_library_backup(path: object) -> dict[str, object]:
    backup_path = os.path.expanduser(str(path or "").strip())
    if not backup_path:
        raise BackupValidationError("Choose a JSON backup file.", "missing_path")
    if not os.path.isfile(backup_path):
        raise BackupValidationError("Backup file does not exist.", "file_not_found")

    try:
        with open(backup_path, encoding="utf-8") as backup_file:
            payload = cast(object, json.load(backup_file))
    except json.JSONDecodeError as exc:
        raise BackupValidationError(
            f"Backup file is not valid JSON: {exc}", "invalid_json"
        ) from exc
    except OSError as exc:
        raise BackupValidationError(
            f"Could not read backup file: {exc}", "read_failed"
        ) from exc

    backup = _coerce_mapping(payload)
    if not backup:
        raise BackupValidationError("Backup file is not a supported wLib export.")

    if backup.get("format") != BACKUP_FORMAT:
        raise BackupValidationError("Backup file is not a supported wLib export.")

    try:
        version = int(str(backup.get("format_version", "0")))
    except ValueError as exc:
        raise BackupValidationError("Backup format version is invalid.") from exc

    if version > BACKUP_FORMAT_VERSION:
        raise BackupValidationError(
            "This backup was created by a newer wLib format and cannot be imported by this version.",
            "unsupported_format_version",
        )
    if version < 1:
        raise BackupValidationError("Backup format version is invalid.")

    games = backup.get("games")
    if not isinstance(games, list):
        raise BackupValidationError("Backup file does not contain a game list.")

    return backup


def _available_backup_sections(backup: Mapping[str, object]) -> list[str]:
    selected = set(
        str(section) for section in _coerce_sequence(backup.get("selected_sections"))
    )
    available: list[str] = []
    if any(
        _game_section(_coerce_mapping(game), SECTION_METADATA)
        for game in _coerce_sequence(backup.get("games"))
    ):
        available.append(SECTION_METADATA)

    available.extend(section for section in SUPPORTED_SECTIONS if section in selected)

    settings = _coerce_mapping(backup.get("settings"))
    if "general" in settings and SECTION_SETTINGS_GENERAL not in available:
        available.append(SECTION_SETTINGS_GENERAL)
    if "paths" in settings and SECTION_SETTINGS_PATHS not in available:
        available.append(SECTION_SETTINGS_PATHS)

    return available


def _local_games() -> list[dict[str, object]]:
    return [{str(key): value for key, value in game.items()} for game in get_all_games()]


def _fallback_identity_key(game_metadata: Mapping[str, object]) -> tuple[str, str]:
    return (
        _normalize_identity_text(game_metadata.get("title")),
        _normalize_identity_text(game_metadata.get("developer")),
    )


def _match_imported_game(
    imported_game: Mapping[str, object], local_games: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    metadata = _game_section(imported_game, "metadata")
    title = _coerce_text(metadata.get("title")).strip()
    f95_url = normalize_thread_url(metadata.get("f95_url"))

    if f95_url:
        f95_matches = [
            game for game in local_games if thread_urls_match(game.get("f95_url"), f95_url)
        ]
        if len(f95_matches) == 1:
            return {
                "status": "matched",
                "game": imported_game,
                "local_id": int(str(f95_matches[0].get("id", 0))),
                "match_type": "f95_url",
            }
        if len(f95_matches) > 1:
            return {
                "status": "ambiguous",
                "game": imported_game,
                "title": title,
                "reason": "Multiple local games share this F95 thread identity.",
                "candidate_count": len(f95_matches),
            }
        return {"status": "new", "game": imported_game, "title": title}

    title_key, developer_key = _fallback_identity_key(metadata)
    if title_key and developer_key:
        fallback_matches = [
            game
            for game in local_games
            if _fallback_identity_key(game) == (title_key, developer_key)
        ]
        if len(fallback_matches) == 1:
            return {
                "status": "matched",
                "game": imported_game,
                "local_id": int(str(fallback_matches[0].get("id", 0))),
                "match_type": "title_developer",
            }
        if len(fallback_matches) > 1:
            return {
                "status": "ambiguous",
                "game": imported_game,
                "title": title,
                "reason": "Multiple local games share this title and developer.",
                "candidate_count": len(fallback_matches),
            }

    return {"status": "new", "game": imported_game, "title": title}


def _build_import_plan(backup: Mapping[str, object]) -> list[dict[str, object]]:
    local_games = _local_games()
    return [
        _match_imported_game(_coerce_mapping(game), local_games)
        for game in _coerce_sequence(backup.get("games"))
    ]


def _path_warning(
    warnings: list[dict[str, object]],
    *,
    scope: str,
    field: str,
    path: object,
    title: str = "",
) -> None:
    path_text = _coerce_text(path).strip()
    if not path_text:
        return
    if os.path.exists(os.path.expanduser(path_text)):
        return
    warnings.append(
        {
            "type": "missing_path",
            "scope": scope,
            "field": field,
            "title": title,
            "path": path_text,
            "message": f"{field} does not exist on this machine: {path_text}",
        }
    )


def _build_backup_warnings(backup: Mapping[str, object]) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []
    has_cover_reference = False

    for raw_game in _coerce_sequence(backup.get("games")):
        game = _coerce_mapping(raw_game)
        metadata = _game_section(game, "metadata")
        title = _coerce_text(metadata.get("title")).strip()
        if _coerce_text(metadata.get("cover_image_path")).strip():
            has_cover_reference = True

        executable_paths = _game_section(game, SECTION_EXECUTABLE_PATHS)
        _path_warning(
            warnings,
            scope="game",
            field="exe_path",
            path=executable_paths.get("exe_path"),
            title=title,
        )

        launch_config = _game_section(game, SECTION_LAUNCH_CONFIG)
        for field in ("custom_prefix", "proton_version"):
            _path_warning(
                warnings,
                scope="game",
                field=field,
                path=launch_config.get(field),
                title=title,
            )

        for raw_target in _coerce_sequence(game.get(SECTION_LAUNCH_TARGETS)):
            target = _coerce_mapping(raw_target)
            target_label = _coerce_text(target.get("label")).strip()
            _path_warning(
                warnings,
                scope="launch_target",
                field="exe_path",
                path=target.get("exe_path"),
                title=f"{title}: {target_label}" if target_label else title,
            )

    settings = _coerce_mapping(backup.get("settings"))
    path_settings = _coerce_mapping(settings.get("paths"))
    for key in PATH_SETTINGS_KEYS:
        _path_warning(
            warnings,
            scope="setting",
            field=key,
            path=path_settings.get(key),
        )

    if has_cover_reference:
        warnings.append(
            {
                "type": "cover_references",
                "scope": "library",
                "message": "Cover references are included, but image files are not embedded. Run update checks after migration to refresh stale metadata and covers.",
            }
        )

    if path_settings:
        warnings.append(
            {
                "type": "machine_specific_settings",
                "scope": "settings",
                "message": "Machine-specific settings paths may need adjustment on the destination machine.",
            }
        )

    return warnings


def inspect_library_backup(path: object) -> dict[str, object]:
    backup = load_library_backup(path)
    plan = _build_import_plan(backup)
    warnings = _build_backup_warnings(backup)
    ambiguous_games = [item for item in plan if item.get("status") == "ambiguous"]

    return {
        "manifest": {
            "format": backup.get("format"),
            "format_version": backup.get("format_version"),
            "exported_at": backup.get("exported_at", ""),
            "app": backup.get("app", {}),
            "selected_sections": backup.get("selected_sections", []),
        },
        "available_sections": _available_backup_sections(backup),
        "counts": {
            "total_games": len(plan),
            "matched_games": sum(1 for item in plan if item.get("status") == "matched"),
            "new_games": sum(1 for item in plan if item.get("status") == "new"),
            "ambiguous_games": len(ambiguous_games),
            "warnings": len(warnings),
        },
        "warnings": warnings,
        "ambiguous_games": [
            {
                "title": item.get("title", ""),
                "reason": item.get("reason", ""),
                "candidate_count": item.get("candidate_count", 0),
            }
            for item in ambiguous_games
        ],
    }


def _normalize_game_field(field: str, value: object) -> object:
    if field == "title":
        return _coerce_text(value).strip() or "Imported Game"
    if field == "f95_url":
        return normalize_thread_url(value)
    if field == "tags":
        return _coerce_text(value)
    if field == "play_status":
        return normalize_play_status(value)
    if field == "launch_mode":
        return normalize_launch_mode(value)
    if field in BOOLEAN_GAME_FIELDS:
        return 1 if _coerce_bool(value) else 0
    if field in INTEGER_GAME_FIELDS:
        return _coerce_non_negative_int(value)
    if field in REAL_GAME_FIELDS:
        return _coerce_float(value)
    if field in TIMESTAMP_GAME_FIELDS and value is None:
        return None
    return _coerce_text(value)


def _normalized_fields(
    source: Mapping[str, object], fields: Sequence[str]
) -> dict[str, object]:
    return {
        field: _normalize_game_field(field, source.get(field))
        for field in fields
        if field in source
    }


def _import_fields_for_game(
    imported_game: Mapping[str, object],
    sections: set[str],
    *,
    include_metadata: bool,
) -> dict[str, object]:
    fields: dict[str, object] = {}
    if include_metadata:
        fields.update(
            _normalized_fields(_game_section(imported_game, SECTION_METADATA), METADATA_FIELDS)
        )

    if SECTION_USER_STATE in sections:
        fields.update(
            _normalized_fields(_game_section(imported_game, SECTION_USER_STATE), USER_STATE_FIELDS)
        )
    if SECTION_LAUNCH_CONFIG in sections:
        fields.update(
            _normalized_fields(
                _game_section(imported_game, SECTION_LAUNCH_CONFIG), LAUNCH_CONFIG_FIELDS
            )
        )
    if SECTION_EXECUTABLE_PATHS in sections:
        fields.update(
            _normalized_fields(
                _game_section(imported_game, SECTION_EXECUTABLE_PATHS),
                EXECUTABLE_PATH_FIELDS,
            )
        )

    return {key: value for key, value in fields.items() if key in IMPORTABLE_GAME_FIELDS}


def _insert_game(cursor: sqlite3.Cursor, fields: Mapping[str, object]) -> int:
    insert_fields = dict(fields)
    _ = insert_fields.setdefault("title", "Imported Game")
    _ = insert_fields.setdefault("exe_path", "")
    _ = insert_fields.setdefault("play_status", DEFAULT_PLAY_STATUS)
    _ = insert_fields.setdefault("date_added", datetime.now().isoformat())

    columns = [field for field in IMPORTABLE_GAME_FIELDS if field in insert_fields]
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    values = tuple(insert_fields[field] for field in columns)

    _ = cursor.execute(
        f"INSERT INTO games ({column_sql}) VALUES ({placeholders})",
        values,
    )
    return int(str(cursor.lastrowid))


def _update_game_row(
    cursor: sqlite3.Cursor, game_id: int, fields: Mapping[str, object]
) -> None:
    safe_fields = {
        key: value for key, value in fields.items() if key in IMPORTABLE_GAME_FIELDS
    }
    if not safe_fields:
        return

    set_clause = ", ".join(f"{key} = ?" for key in safe_fields)
    values = tuple(safe_fields.values()) + (game_id,)
    _ = cursor.execute(f"UPDATE games SET {set_clause} WHERE id = ?", values)


def _normalize_launch_target(raw_target: object) -> dict[str, object] | None:
    target = _coerce_mapping(raw_target)
    label = _coerce_text(target.get("label")).strip()
    exe_path = _coerce_text(target.get("exe_path")).strip()
    if not label or not exe_path:
        return None
    return {
        "label": label,
        "exe_path": exe_path,
        "sort_order": _coerce_non_negative_int(target.get("sort_order")),
        "created_at": _coerce_text(target.get("created_at")).strip()
        or datetime.now().isoformat(),
        "updated_at": _coerce_text(target.get("updated_at")).strip()
        or datetime.now().isoformat(),
    }


def _replace_launch_targets(
    cursor: sqlite3.Cursor, game_id: int, imported_game: Mapping[str, object]
) -> None:
    _ = cursor.execute("DELETE FROM game_launch_targets WHERE game_id = ?", (game_id,))
    for raw_target in _coerce_sequence(imported_game.get(SECTION_LAUNCH_TARGETS)):
        target = _normalize_launch_target(raw_target)
        if target is None:
            continue
        _ = cursor.execute(
            "INSERT INTO game_launch_targets (game_id, label, exe_path, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                game_id,
                target["label"],
                target["exe_path"],
                target["sort_order"],
                target["created_at"],
                target["updated_at"],
            ),
        )


def _import_setting_groups(
    cursor: sqlite3.Cursor, backup: Mapping[str, object], sections: set[str]
) -> int:
    settings = _coerce_mapping(backup.get("settings"))
    groups: list[tuple[str, Sequence[str]]] = []
    if SECTION_SETTINGS_GENERAL in sections:
        groups.append(("general", GENERAL_SETTINGS_KEYS))
    if SECTION_SETTINGS_PATHS in sections:
        groups.append(("paths", PATH_SETTINGS_KEYS))

    updated = 0
    for group_name, allowed_keys in groups:
        group = _coerce_mapping(settings.get(group_name))
        for key in allowed_keys:
            if key not in group:
                continue
            value = _coerce_text(group.get(key))
            _ = cursor.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, value, value),
            )
            updated += 1
    return updated


def import_library_backup(
    path: object, options: Mapping[str, object] | None = None
) -> dict[str, object]:
    backup = load_library_backup(path)
    available_sections = _available_backup_sections(backup)
    sections = _select_sections(
        options,
        default_sections=available_sections,
        available_sections=available_sections,
        supported_sections=SUPPORTED_IMPORT_SECTIONS,
        reject_empty_selection=True,
    )
    plan = _build_import_plan(backup)
    warnings = _build_backup_warnings(backup)

    created = 0
    updated = 0
    skipped = 0
    ambiguous = 0
    settings_updated = 0

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            for item in plan:
                imported_game = _coerce_mapping(item.get("game"))
                status = str(item.get("status") or "")
                if status == "ambiguous":
                    ambiguous += 1
                    skipped += 1
                    continue

                if status == "matched":
                    fields = _import_fields_for_game(
                        imported_game,
                        sections,
                        include_metadata=SECTION_METADATA in sections,
                    )
                    local_id = int(str(item.get("local_id", 0)))
                    _update_game_row(cursor, local_id, fields)
                    game_id = local_id
                    updated += 1
                else:
                    fields = _import_fields_for_game(
                        imported_game,
                        sections,
                        include_metadata=True,
                    )
                    game_id = _insert_game(cursor, fields)
                    created += 1

                if SECTION_LAUNCH_TARGETS in sections:
                    _replace_launch_targets(cursor, game_id, imported_game)

            settings_updated = _import_setting_groups(cursor, backup, sections)
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "ambiguous": ambiguous,
        "warnings": len(warnings),
        "warning_records": warnings,
        "settings_updated": settings_updated,
        "selected_sections": sorted(sections),
    }
