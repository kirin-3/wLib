# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import base64
import hashlib
import math
import secrets
import ntpath
import os
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import datetime
from typing import cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.backup_format import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    ENCRYPTED_FORMAT,
    BackupValidationError,
)
from core.database import (
    DEFAULT_PLAY_STATUS,
    GENERAL_SETTINGS_KEYS,
    RPGMAKER_LINUX_RUNNER_SETTING,
    get_all_games,
    get_connection,
    find_matching_game_row,
    normalize_launch_mode,
    normalize_play_status,
)
from core.f95zone import normalize_thread_url, thread_urls_match
from core.host_platform import is_windows

__all__ = [
    "BACKUP_FORMAT",
    "BACKUP_FORMAT_VERSION",
    "ENCRYPTED_FORMAT",
    "BackupValidationError",
]

SCRYPT_N = 131072
SYNC_SECTIONS = ("metadata", "user_state", "settings_general")

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
    "thread_completed",
    "cover_image_path",
)
USER_STATE_FIELDS = (
    "play_status",
    "finished_version",
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
PATH_SETTINGS_KEYS = (
    "proton_path",
    "wine_prefix_path",
    RPGMAKER_LINUX_RUNNER_SETTING,
    "urm_rpa_path",
    "library_folder",
    "library_folder_pattern",
    "downloads_folder",
    "download_detection_enabled",
)
# Exported with the path settings but not filesystem paths, so never host-checked.
NON_PATH_SETTINGS_KEYS = frozenset({"library_folder_pattern", "download_detection_enabled"})

BOOLEAN_GAME_FIELDS = (
    "thread_completed",
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


def derive_backup_key(passphrase: str, salt: bytes) -> bytes:
    if not passphrase:
        raise BackupValidationError("Enter a passphrase.", "passphrase_required")
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=8,
        p=1,
        dklen=32,
        maxmem=256 * 1024 * 1024,
    )


def _header_bytes(envelope: Mapping[str, object]) -> bytes:
    return json.dumps(
        {
            key: envelope[key]
            for key in ("format", "format_version", "kdf", "cipher", "nonce")
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def encrypted_backup_salt(envelope: Mapping[str, object]) -> bytes:
    version = envelope.get("format_version")
    if isinstance(version, int) and version > 1:
        raise BackupValidationError(
            "This encrypted file needs a newer wLib.", "unsupported_format_version"
        )
    try:
        kdf = _coerce_mapping(envelope.get("kdf"))
        salt = base64.b64decode(str(kdf.get("salt", "")), validate=True)
        nonce = base64.b64decode(str(envelope.get("nonce", "")), validate=True)
        if (
            envelope.get("format") != ENCRYPTED_FORMAT
            or version != 1
            or envelope.get("cipher") != "AES-256-GCM"
            or kdf
            != {
                "name": "scrypt",
                "salt": kdf.get("salt"),
                "n": SCRYPT_N,
                "r": 8,
                "p": 1,
            }
            or len(salt) != 16
            or len(nonce) != 12
        ):
            raise ValueError("Invalid encryption header")
        return salt
    except (ValueError, TypeError) as exc:
        raise BackupValidationError(
            "The file could not be decrypted.", "decrypt_failed"
        ) from exc


def encrypt_library_backup(
    backup: Mapping[str, object],
    passphrase: str | None = None,
    *,
    key: bytes | None = None,
    salt: bytes | None = None,
) -> dict[str, object]:
    salt = salt if salt is not None else secrets.token_bytes(16)
    key = key if key is not None else derive_backup_key(passphrase or "", salt)
    if len(key) != 32 or len(salt) != 16:
        raise BackupValidationError("Invalid encryption key.", "decrypt_failed")
    nonce = secrets.token_bytes(12)
    envelope: dict[str, object] = {
        "format": ENCRYPTED_FORMAT,
        "format_version": 1,
        "kdf": {
            "name": "scrypt",
            "salt": base64.b64encode(salt).decode("ascii"),
            "n": SCRYPT_N,
            "r": 8,
            "p": 1,
        },
        "cipher": "AES-256-GCM",
        "nonce": base64.b64encode(nonce).decode("ascii"),
    }
    plaintext = json.dumps(backup, ensure_ascii=False).encode("utf-8")
    envelope["ciphertext"] = base64.b64encode(
        AESGCM(key).encrypt(nonce, plaintext, _header_bytes(envelope))
    ).decode("ascii")
    return envelope


def decrypt_library_backup(
    envelope: Mapping[str, object],
    passphrase: str | None = None,
    *,
    key: bytes | None = None,
) -> dict[str, object]:
    salt = encrypted_backup_salt(envelope)
    if key is None and passphrase is None:
        raise BackupValidationError(
            "Enter the passphrase for this file.", "passphrase_required"
        )
    key = key if key is not None else derive_backup_key(passphrase or "", salt)
    try:
        nonce = base64.b64decode(str(envelope["nonce"]), validate=True)
        ciphertext = base64.b64decode(
            str(envelope.get("ciphertext", "")), validate=True
        )
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _header_bytes(envelope))
        payload = cast(object, json.loads(plaintext))
    except (InvalidTag, ValueError, TypeError, KeyError) as exc:
        raise BackupValidationError(
            "The file could not be decrypted.", "decrypt_failed"
        ) from exc
    return validate_library_backup(payload)


def _coerce_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item for key, item in cast(Mapping[object, object], value).items()
    }


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
    except (TypeError, ValueError, OverflowError):
        return 0


def _coerce_float(value: object) -> float:
    try:
        result = float(str(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(result):
        return 0.0
    return result


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


def _pick_fields(
    source: Mapping[str, object], fields: Sequence[str]
) -> dict[str, object]:
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
    passphrase: str | None = None,
) -> dict[str, object]:
    output_path = _resolve_export_destination(destination_path)
    backup = build_library_backup(options, app_version=app_version)
    document = (
        encrypt_library_backup(backup, passphrase) if passphrase is not None else backup
    )
    tmp_path = f"{output_path}.tmp"

    try:
        with open(tmp_path, "w", encoding="utf-8") as backup_file:
            json.dump(document, backup_file, ensure_ascii=False, indent=2)
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
        "encrypted": passphrase is not None,
    }


def load_library_backup(
    path: object, passphrase: str | None = None
) -> dict[str, object]:
    backup_path = os.path.expanduser(str(path or "").strip())
    if not backup_path:
        raise BackupValidationError("Choose a JSON backup file.", "missing_path")
    if not os.path.isfile(backup_path):
        raise BackupValidationError("Backup file does not exist.", "file_not_found")

    try:
        # utf-8-sig also accepts a byte-order mark, which Windows editors add.
        with open(backup_path, encoding="utf-8-sig") as backup_file:
            payload = cast(object, json.load(backup_file))
    except json.JSONDecodeError as exc:
        raise BackupValidationError(
            f"Backup file is not valid JSON: {exc}", "invalid_json"
        ) from exc
    except UnicodeError as exc:
        raise BackupValidationError(
            f"Backup file is not valid UTF-8 JSON: {exc}", "invalid_json"
        ) from exc
    except RecursionError as exc:
        raise BackupValidationError(
            "Backup file is not valid JSON: it is nested too deeply.",
            "invalid_json",
        ) from exc
    except OSError as exc:
        raise BackupValidationError(
            f"Could not read backup file: {exc}", "read_failed"
        ) from exc

    backup = _coerce_mapping(payload)
    if backup.get("format") == ENCRYPTED_FORMAT:
        return decrypt_library_backup(backup, passphrase)
    return validate_library_backup(payload)


def validate_library_backup(payload: object) -> dict[str, object]:
    backup = _coerce_mapping(payload)
    if not backup:
        raise BackupValidationError("Backup file is not a supported wLib export.")

    if backup.get("format") != BACKUP_FORMAT:
        raise BackupValidationError("Backup file is not a supported wLib export.")

    try:
        raw_version = backup.get("format_version", "0")
        if isinstance(raw_version, bool):
            raise ValueError("non-integer version")
        if isinstance(raw_version, int):
            version = raw_version
        elif isinstance(raw_version, float):
            if not raw_version.is_integer():
                raise ValueError("non-integer version")
            version = int(raw_version)
        else:
            text = str(raw_version or "0").strip()
            if "." in text:
                float_value = float(text)
                if not float_value.is_integer():
                    raise ValueError("non-integer version")
                version = int(float_value)
            else:
                version = int(text)
    except (ValueError, TypeError, OverflowError) as exc:
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

    for index, raw_game in enumerate(cast(list[object], games), start=1):
        game = _coerce_mapping(raw_game)
        metadata = _game_section(game, SECTION_METADATA)
        if not any(
            isinstance(metadata.get(field), str) and str(metadata[field]).strip()
            for field in ("title", "f95_url")
        ):
            raise BackupValidationError(
                f"Game record {index} must contain a title or thread URL in metadata."
            )
        if SECTION_LAUNCH_TARGETS in game:
            targets = game[SECTION_LAUNCH_TARGETS]
            if not isinstance(targets, list) or any(
                _normalize_launch_target(target) is None
                for target in cast(list[object], targets)
            ):
                raise BackupValidationError(
                    f"Game record {index} has invalid launch targets; each needs a label and executable path."
                )

    return backup


def _backup_game_carries_section(game: Mapping[str, object], section: str) -> bool:
    """True when a game record actually carries data for a backup section."""
    if section not in game:
        return False
    if section == SECTION_LAUNCH_TARGETS:
        # An empty list carries no targets; treating it as data would clear
        # local targets on import.
        return bool(_coerce_sequence(game[section]))
    return bool(_coerce_mapping(game[section]))


def _available_backup_sections(backup: Mapping[str, object]) -> list[str]:
    selected = set(
        str(section) for section in _coerce_sequence(backup.get("selected_sections"))
    )
    games = [_coerce_mapping(game) for game in _coerce_sequence(backup.get("games"))]
    available: list[str] = []
    if any(_game_section(game, SECTION_METADATA) for game in games):
        available.append(SECTION_METADATA)

    # Backups written by other tools may omit the section manifest; a section
    # the file actually carries stays importable instead of being dropped.
    for section in SUPPORTED_SECTIONS:
        if section in selected or any(
            _backup_game_carries_section(game, section) for game in games
        ):
            available.append(section)

    settings = _coerce_mapping(backup.get("settings"))
    if "general" in settings and SECTION_SETTINGS_GENERAL not in available:
        available.append(SECTION_SETTINGS_GENERAL)
    if "paths" in settings and SECTION_SETTINGS_PATHS not in available:
        available.append(SECTION_SETTINGS_PATHS)

    return available


def _local_games() -> list[dict[str, object]]:
    return [
        {str(key): value for key, value in game.items()} for game in get_all_games()
    ]


def _fallback_identity_key(game_metadata: Mapping[str, object]) -> tuple[str, str]:
    return (
        _normalize_identity_text(game_metadata.get("title")),
        _normalize_identity_text(game_metadata.get("developer")),
    )


def _match_imported_game(
    imported_game: Mapping[str, object],
    local_games: Sequence[Mapping[str, object]],
    *,
    sync: bool = False,
) -> dict[str, object]:
    metadata = _game_section(imported_game, "metadata")
    title = _coerce_text(metadata.get("title")).strip()
    f95_url = normalize_thread_url(metadata.get("f95_url"))
    title_key, developer_key = _fallback_identity_key(metadata)

    if f95_url:
        f95_matches = [
            game
            for game in local_games
            if thread_urls_match(game.get("f95_url"), f95_url)
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
                "candidate_ids": [game["id"] for game in f95_matches],
            }
        # The thread is not in this library, but the same game may have been
        # added manually with matching title and developer and no URL yet.
        # Only those URL-less copies are merged; two different threads that
        # share a title and developer stay separate entries.
        url_less_matches = [
            game
            for game in local_games
            if _fallback_identity_key(game) == (title_key, developer_key)
            and not _coerce_text(game.get("f95_url")).strip()
        ]
        if len(url_less_matches) == 1:
            return {
                "status": "matched",
                "game": imported_game,
                "local_id": int(str(url_less_matches[0].get("id", 0))),
                "match_type": "title_developer",
            }
        if len(url_less_matches) > 1:
            return {
                "status": "ambiguous",
                "game": imported_game,
                "title": title,
                "reason": "Multiple local games share this title and developer.",
                "candidate_count": len(url_less_matches),
                "candidate_ids": [game["id"] for game in url_less_matches],
            }
        return {"status": "new", "game": imported_game, "title": title}

    # Manually added games have no developer; their executable identifies them.
    exe_key = _coerce_text(
        _game_section(imported_game, SECTION_EXECUTABLE_PATHS).get("exe_path")
    ).strip()
    second_key = "developer" if developer_key else "executable"
    if title_key and (developer_key or exe_key or sync):
        fallback_matches = [
            game
            for game in local_games
            if _fallback_identity_key(game) == (title_key, developer_key)
            and (
                developer_key
                or sync
                or _coerce_text(game.get("exe_path")).strip() == exe_key
            )
        ]
        if len(fallback_matches) == 1:
            return {
                "status": "matched",
                "game": imported_game,
                "local_id": int(str(fallback_matches[0].get("id", 0))),
                "match_type": f"title_{second_key}",
            }
        if len(fallback_matches) > 1:
            return {
                "status": "ambiguous",
                "game": imported_game,
                "title": title,
                "reason": f"Multiple local games share this title and {second_key}.",
                "candidate_count": len(fallback_matches),
                "candidate_ids": [game["id"] for game in fallback_matches],
            }

    if title_key and not developer_key and not exe_key and not sync:
        # The record carries no identity beyond its title (for example an export
        # without executable paths). Match it when exactly one local game has
        # that title so re-importing never duplicates the game. Local games with
        # their own thread URL or developer are never merged on title alone.
        title_matches = [
            game
            for game in local_games
            if _normalize_identity_text(game.get("title")) == title_key
            and not _coerce_text(game.get("f95_url")).strip()
            and not _coerce_text(game.get("developer")).strip()
        ]
        if len(title_matches) == 1:
            return {
                "status": "matched",
                "game": imported_game,
                "local_id": int(str(title_matches[0].get("id", 0))),
                "match_type": "title",
            }
        if len(title_matches) > 1:
            return {
                "status": "ambiguous",
                "game": imported_game,
                "title": title,
                "reason": "Multiple local games share this title.",
                "candidate_count": len(title_matches),
                "candidate_ids": [game["id"] for game in title_matches],
            }

    return {"status": "new", "game": imported_game, "title": title}


def _build_import_plan(
    backup: Mapping[str, object], *, sync: bool = False
) -> list[dict[str, object]]:
    local_games = _local_games()
    return [
        _match_imported_game(_coerce_mapping(game), local_games, sync=sync)
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


def _is_host_path_setting(value: str) -> bool:
    """False for a path written for the other OS, e.g. /home/... on Windows."""
    if "/" not in value and "\\" not in value:
        return True  # empty, or a bare command such as "wine"
    if is_windows():
        return bool(ntpath.splitdrive(value)[0])  # C:\... or \\server\share
    return value.startswith(("/", "~"))


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
        if key in NON_PATH_SETTINGS_KEYS:
            continue
        path_text = _coerce_text(path_settings.get(key)).strip()
        if not _is_host_path_setting(path_text):
            warnings.append(
                {
                    "type": "foreign_path",
                    "scope": "setting",
                    "field": key,
                    "title": "",
                    "path": path_text,
                    "message": f"{key} is not a valid path on this system and will not be imported: {path_text}",
                }
            )
            continue
        _path_warning(
            warnings,
            scope="setting",
            field=key,
            path=path_text,
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


def inspect_library_backup(
    path: object, passphrase: str | None = None
) -> dict[str, object]:
    backup = load_library_backup(path, passphrase)
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


def _collections_json_text(value: object) -> str:
    # Backups carry membership as a JSON array; sync rows may hold JSON text.
    parsed_value: object = None
    if isinstance(value, str):
        try:
            parsed_value = cast(object, json.loads(value))
        except (ValueError, TypeError):
            parsed_value = None
    else:
        parsed_value = value
    names: list[str] = []
    if isinstance(parsed_value, list):
        for name in cast(list[object], parsed_value):
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return json.dumps(names)


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
    if field == "collections":
        return _collections_json_text(value)
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
            _normalized_fields(
                _game_section(imported_game, SECTION_METADATA), METADATA_FIELDS
            )
        )

    if SECTION_USER_STATE in sections:
        fields.update(
            _normalized_fields(
                _game_section(imported_game, SECTION_USER_STATE), USER_STATE_FIELDS
            )
        )
    if SECTION_LAUNCH_CONFIG in sections:
        fields.update(
            _normalized_fields(
                _game_section(imported_game, SECTION_LAUNCH_CONFIG),
                LAUNCH_CONFIG_FIELDS,
            )
        )
    if SECTION_EXECUTABLE_PATHS in sections:
        fields.update(
            _normalized_fields(
                _game_section(imported_game, SECTION_EXECUTABLE_PATHS),
                EXECUTABLE_PATH_FIELDS,
            )
        )

    return {
        key: value for key, value in fields.items() if key in IMPORTABLE_GAME_FIELDS
    }


def _insert_game(cursor: sqlite3.Cursor, fields: Mapping[str, object]) -> int:
    insert_fields = dict(fields)
    _ = insert_fields.setdefault("title", "Imported Game")
    _ = insert_fields.setdefault("exe_path", "")
    _ = insert_fields.setdefault("f95_url", "")
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
    if not isinstance(target.get("label"), str) or not isinstance(
        target.get("exe_path"), str
    ):
        return None
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
    if SECTION_LAUNCH_TARGETS not in imported_game:
        return
    targets = [
        _normalize_launch_target(target)
        for target in _coerce_sequence(imported_game.get(SECTION_LAUNCH_TARGETS))
    ]
    if any(target is None for target in targets):
        raise BackupValidationError("Invalid launch targets.")
    _ = cursor.execute("DELETE FROM game_launch_targets WHERE game_id = ?", (game_id,))
    for target in targets:
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


def _coerce_setting_value(key: str, raw: object) -> str:
    """Serialize a backup setting value without mangling foreign types.

    Exports store every setting as TEXT (JSON-encoded for lists). Foreign
    backups may use native JSON types instead: lists/dicts must be re-encoded
    as JSON (``_coerce_text`` would join a list with ", " and corrupt
    ``collections`` etc.), and booleans must use the lowercase "true"/"false"
    strings the app reads (``str(True)`` is "True").
    """
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, (list, dict)):
        return json.dumps(raw, ensure_ascii=False)
    if key in ("enable_logging", "download_detection_enabled") and isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "false"):
            return lowered
    return _coerce_text(raw)


def _import_setting_groups(
    cursor: sqlite3.Cursor,
    backup: Mapping[str, object],
    sections: set[str],
    merge_mode: str = "overwrite",
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
            value = _coerce_setting_value(key, group.get(key))
            if (
                group_name == "paths"
                and key not in NON_PATH_SETTINGS_KEYS
                and not _is_host_path_setting(value.strip())
            ):
                continue
            if merge_mode == "fill_missing":
                row = cast(
                    tuple[object] | None,
                    cursor.execute(
                        "SELECT value FROM settings WHERE key = ?", (key,)
                    ).fetchone(),
                )
                local_value = str(row[0] or "").strip() if row else ""
                if local_value not in ("", "[]") or value.strip() in ("", "[]"):
                    continue
            _ = cursor.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, value, value),
            )
            updated += 1
    return updated


def import_library_backup(
    path: object,
    options: Mapping[str, object] | None = None,
    passphrase: str | None = None,
) -> dict[str, object]:
    backup = load_library_backup(path, passphrase)
    return apply_library_backup(backup, options)


def build_sync_backup(modified_at: str, device: str) -> dict[str, object]:
    backup = build_library_backup({"sections": SYNC_SECTIONS})
    # Cover files belong to this machine; only remote cover links can be shared.
    for raw_game in _coerce_sequence(backup.get("games")):
        metadata = _game_section(_coerce_mapping(raw_game), SECTION_METADATA)
        cover = str(metadata.get("cover_image_path") or "")
        if cover and not cover.startswith(("https://", "http://")):
            metadata["cover_image_path"] = ""
        cast(dict[str, object], raw_game)[SECTION_METADATA] = metadata
    backup["selected_sections"] = list(SYNC_SECTIONS)
    backup["sync"] = {"modified_at": modified_at, "device": device}
    return backup


MERGE_MODES = ("overwrite", "fill_missing")


def _filter_fill_missing(
    cursor: sqlite3.Cursor,
    game_id: int,
    fields: Mapping[str, object],
) -> dict[str, object]:
    """Keep only the imported fields that are empty on the stored row.

    The stored row is re-read for every match: an earlier game in the same
    import may already have applied a value, and fill-missing must see that
    instead of the snapshot taken before the import started.
    """
    row = cast(
        sqlite3.Row | None,
        cursor.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone(),
    )
    if row is None:
        return dict(fields)
    local_row = {str(key): cast(object, row[key]) for key in row.keys()}
    return {
        key: value
        for key, value in fields.items()
        if _is_fill_missing_empty(key, local_row.get(key))
    }


def _is_fill_missing_empty(field: str, local_value: object) -> bool:
    """True when a local value counts as missing for fill-missing merges."""
    if field == "collections":
        # Membership is stored as a JSON list; "[]" means no collections yet.
        return _coerce_text(local_value).strip() in ("", "[]")
    if field == "play_status":
        if local_value is None:
            return True
        text = str(local_value).strip()
        return text == "" or text == DEFAULT_PLAY_STATUS
    if (
        field in INTEGER_GAME_FIELDS
        or field in REAL_GAME_FIELDS
        or field in BOOLEAN_GAME_FIELDS
    ):
        try:
            return float(str(local_value if local_value is not None else 0)) == 0.0
        except (TypeError, ValueError):
            return True
    if local_value is None:
        return True
    return str(local_value).strip() == ""


def _resolve_merge_mode(
    options: Mapping[str, object] | None, merge_mode: str = "overwrite"
) -> str:
    resolved = str(merge_mode or "overwrite").strip().lower()
    if resolved == "overwrite":
        candidate = _coerce_mapping(options or {}).get("merge_mode")
        if isinstance(candidate, str) and candidate.strip():
            resolved = candidate.strip().lower()
    if resolved not in MERGE_MODES:
        raise BackupValidationError(
            "Merge mode must be 'overwrite' or 'fill_missing'.", "invalid_merge_mode"
        )
    return resolved


def apply_library_backup(
    backup: Mapping[str, object],
    options: Mapping[str, object] | None = None,
    *,
    mirror: bool = False,
    sync: bool = False,
    sync_settings: Mapping[str, str] | None = None,
    expected_local_modified_at: str | None = None,
    merge_mode: str = "overwrite",
) -> dict[str, object]:
    backup = validate_library_backup(backup)
    merge_mode = _resolve_merge_mode(options, merge_mode)
    available_sections = _available_backup_sections(backup)
    sections = _select_sections(
        options,
        default_sections=available_sections,
        available_sections=available_sections,
        supported_sections=SUPPORTED_IMPORT_SECTIONS,
        reject_empty_selection=True,
    )
    warnings = _build_backup_warnings(backup)

    created = 0
    updated = 0
    skipped = 0
    ambiguous = 0
    settings_updated = 0
    deleted = 0
    created_ids: list[int] = []
    updated_ids: list[int] = []

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            _ = cursor.execute("BEGIN IMMEDIATE")
            if expected_local_modified_at is not None:
                from core.database import get_setting

                if (
                    get_setting("sync_local_modified_at") or ""
                ) != expected_local_modified_at:
                    raise BackupValidationError(
                        "The local library changed during sync. Try again.",
                        "sync_conflict",
                    )
            plan = _build_import_plan(backup, sync=sync)
            keep_ids = {
                int(str(item["local_id"]))
                for item in plan
                if item.get("status") == "matched"
            }
            for item in plan:
                keep_ids.update(
                    int(str(candidate))
                    for candidate in _coerce_sequence(item.get("candidate_ids"))
                )
            original_ids = {int(str(game["id"])) for game in _local_games()}
            if mirror:
                from core import database

                snapshot_dir = os.path.join(os.path.dirname(database.DB_PATH), "sync")
                os.makedirs(snapshot_dir, exist_ok=True)
                _ = write_library_backup(
                    os.path.join(snapshot_dir, "last-local.json"),
                    {"sections": SUPPORTED_SECTIONS},
                )

            for item in plan:
                imported_game = _coerce_mapping(item.get("game"))
                status = str(item.get("status") or "")
                if status == "ambiguous":
                    ambiguous += 1
                    skipped += 1
                    continue

                local_id: int | None = None
                if status == "matched":
                    local_id = int(str(item.get("local_id", 0)))
                else:
                    # A backup may repeat a thread (two copies of one game, or
                    # the same thread under an old and a new slug). A later
                    # copy merges into the row created earlier in this import
                    # instead of adding a second game for the same thread.
                    duplicate_row = find_matching_game_row(
                        cursor,
                        _game_section(imported_game, SECTION_METADATA).get(
                            "f95_url"
                        ),
                    )
                    if duplicate_row is not None:
                        local_id = int(str(cast(object, duplicate_row["id"])))

                if local_id is not None:
                    fields = _import_fields_for_game(
                        imported_game,
                        sections,
                        include_metadata=SECTION_METADATA in sections,
                    )
                    if sync and not fields.get("cover_image_path"):
                        _ = fields.pop("cover_image_path", None)
                    if merge_mode == "fill_missing":
                        fields = _filter_fill_missing(cursor, local_id, fields)
                    _update_game_row(cursor, local_id, fields)
                    game_id = local_id
                    updated += 1
                    updated_ids.append(local_id)
                    if status != "matched":
                        # The URL matched a row that is only identical from
                        # the database's point of view; mirror sync must keep
                        # it because the backup also references it.
                        keep_ids.add(local_id)
                else:
                    fields = _import_fields_for_game(
                        imported_game,
                        sections,
                        include_metadata=True,
                    )
                    game_id = _insert_game(cursor, fields)
                    created += 1
                    created_ids.append(game_id)

                if SECTION_LAUNCH_TARGETS in sections:
                    if merge_mode == "fill_missing" and local_id is not None:
                        # Read through the import cursor so targets written
                        # earlier in this uncommitted transaction count.
                        _ = cursor.execute(
                            "SELECT 1 FROM game_launch_targets WHERE game_id = ? LIMIT 1",
                            (game_id,),
                        )
                        if cursor.fetchone() is not None:
                            continue
                    _replace_launch_targets(cursor, game_id, imported_game)

            settings_updated = _import_setting_groups(
                cursor, backup, sections, merge_mode
            )
            if mirror:
                for game_id in original_ids - keep_ids:
                    _ = cursor.execute("DELETE FROM games WHERE id = ?", (game_id,))
                    deleted += 1
            for key, value in (sync_settings or {}).items():
                if key.startswith("sync_"):
                    _ = cursor.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, value),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "created": created,
        "deleted": deleted,
        "updated": updated,
        "skipped": skipped,
        "ambiguous": ambiguous,
        "warnings": len(warnings),
        "warning_records": warnings,
        "settings_updated": settings_updated,
        "selected_sections": sorted(sections),
        "created_ids": created_ids,
        "updated_ids": updated_ids,
    }


EXTERNAL_IMPORT_SECTIONS = (
    SECTION_METADATA,
    SECTION_USER_STATE,
    SECTION_EXECUTABLE_PATHS,
    SECTION_LAUNCH_TARGETS,
)


def _external_import_options(
    options: Mapping[str, object] | None,
) -> dict[str, object]:
    """Normalize external-import options: metadata always applies to matches."""
    normalized = dict(options or {})
    has_section_option, raw_sections = _section_option(normalized)
    if not has_section_option:
        return normalized
    selected: set[str] = set()
    if isinstance(raw_sections, Mapping):
        section_map = cast(Mapping[object, object], raw_sections)
        for key, enabled in section_map.items():
            if _coerce_bool(enabled):
                selected.add(str(key))
    else:
        selected.update(str(section) for section in _coerce_sequence(raw_sections))
    selected.add(SECTION_METADATA)
    normalized["sections"] = sorted(selected)
    return normalized


def inspect_external_library(source: object, path: object) -> dict[str, object]:
    from core.external_import import read_external_library

    backup, stats = read_external_library(source, path)
    plan = _build_import_plan(backup)
    warnings = _build_backup_warnings(backup)
    ambiguous_games = [item for item in plan if item.get("status") == "ambiguous"]

    return {
        "source": str(source or "").strip().lower(),
        "schema_version": stats.get("schema_version", ""),
        "available_sections": _available_backup_sections(backup),
        "counts": {
            "total_games": len(plan) + int(str(stats.get("skipped_no_identity", 0))),
            "matched_games": sum(1 for item in plan if item.get("status") == "matched"),
            "new_games": sum(1 for item in plan if item.get("status") == "new"),
            "ambiguous_games": len(ambiguous_games),
            "skipped_no_identity": int(str(stats.get("skipped_no_identity", 0))),
            "dropped_paths": int(str(stats.get("dropped_paths", 0))),
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


def import_external_library(
    source: object, path: object, options: Mapping[str, object] | None = None
) -> dict[str, object]:
    from core.external_import import read_external_library

    backup, stats = read_external_library(source, path)
    result = apply_library_backup(backup, _external_import_options(options))
    skipped_no_identity = int(str(stats.get("skipped_no_identity", 0)))
    result["skipped"] = int(str(result.get("skipped", 0))) + skipped_no_identity
    result["skipped_no_identity"] = skipped_no_identity
    result["dropped_paths"] = int(str(stats.get("dropped_paths", 0)))
    result["schema_version"] = stats.get("schema_version", "")
    result["source"] = str(source or "").strip().lower()
    return result
