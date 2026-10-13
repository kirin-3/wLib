# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from pathlib import Path
from typing import cast

import pytest

from core.api import Api
from core.database import (
    add_game,
    add_game_launch_target,
    get_all_games,
    get_connection,
    get_setting,
    init_db,
    list_game_launch_targets,
    update_game,
    update_setting,
)
from core.library_backup import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    SECTION_METADATA,
    SECTION_EXECUTABLE_PATHS,
    SECTION_LAUNCH_CONFIG,
    SECTION_LAUNCH_TARGETS,
    SECTION_SETTINGS_GENERAL,
    SECTION_SETTINGS_PATHS,
    SECTION_USER_STATE,
    BackupValidationError,
    build_library_backup,
    build_sync_backup,
    encrypt_library_backup,
    decrypt_library_backup,
    load_library_backup,
    apply_library_backup,
    SYNC_SECTIONS,
)


def test_encryption_round_trip_and_fresh_nonce():
    payload = base_backup([{"metadata": {"title": "Private game"}}])
    first = encrypt_library_backup(payload, "test passphrase")
    second = encrypt_library_backup(payload, "test passphrase")
    assert decrypt_library_backup(first, "test passphrase") == payload
    assert first["nonce"] != second["nonce"]
    assert first["ciphertext"] != second["ciphertext"]
    assert "Private game" not in json.dumps(first)
    with pytest.raises(BackupValidationError, match="passphrase") as error:
        decrypt_library_backup(first)
    assert error.value.error_code == "passphrase_required"
    with pytest.raises(BackupValidationError) as error:
        decrypt_library_backup(first, "wrong")
    assert error.value.error_code == "decrypt_failed"


@pytest.mark.parametrize("tamper", ["ciphertext", "kdf", "nonce"])
def test_encryption_tampering_rejected(tamper):
    envelope = encrypt_library_backup(base_backup([]), "test")
    if tamper == "kdf":
        envelope["kdf"]["n"] = 2
    else:
        envelope[tamper] = "AAAA"
    with pytest.raises(BackupValidationError) as error:
        decrypt_library_backup(envelope, "test")
    assert error.value.error_code == "decrypt_failed"


def test_future_envelope_rejected():
    with pytest.raises(BackupValidationError) as error:
        decrypt_library_backup({"format": "wlib.encrypted", "format_version": 2})
    assert error.value.error_code == "unsupported_format_version"


def test_encrypted_backup_api(tmp_path):
    add_game(
        "Secret", "/local/game.exe", f95_url="https://f95zone.to/threads/secret.123/"
    )
    path = str(tmp_path / "encrypted.json")
    api = Api()
    result = api.export_library_backup(destination_path=path, passphrase="phrase")
    assert result["success"] and result["encrypted"]
    assert api.inspect_library_backup(path)["error_code"] == "passphrase_required"
    assert (
        api.import_library_backup(path, passphrase="wrong")["error_code"]
        == "decrypt_failed"
    )
    assert api.inspect_library_backup(path, "phrase")["counts"]["total_games"] == 1
    assert api.import_library_backup(path, passphrase="phrase")["success"]


def test_sync_payload_excludes_machine_paths_and_secrets(tmp_path):
    game_id = add_game(
        "Game",
        "/secret/game.exe",
        cover_image="/secret/cover.png",
        custom_prefix="/secret/prefix",
    )
    add_game_launch_target(game_id, "Extra", "/secret/extra.exe")
    for key in (
        "sync_dropbox_refresh_token",
        "sync_encryption_key",
        "sync_encryption_salt",
        "sync_folder_path",
    ):
        update_setting(key, "SECRET_VALUE")
    update_setting("proton_path", "/secret/proton")
    sync = build_sync_backup("2026-10-09T00:00:00Z", "test-device")
    serialized = json.dumps(sync)
    assert "/secret" not in serialized and "SECRET_VALUE" not in serialized
    assert not any(
        section in sync["games"][0]
        for section in ("launch_config", "launch_targets", "executable_paths")
    )
    assert "paths" not in sync["settings"]
    assert load_library_backup(write_backup(tmp_path / "sync.json", sync)) == sync
    exported = json.dumps(
        build_library_backup(
            {
                "sections": [
                    *SYNC_SECTIONS,
                    SECTION_SETTINGS_PATHS,
                    SECTION_LAUNCH_CONFIG,
                    SECTION_EXECUTABLE_PATHS,
                    SECTION_LAUNCH_TARGETS,
                ]
            }
        )
    )
    assert "sync_" not in exported and "SECRET_VALUE" not in exported


def test_mirror_preserves_paths_deletes_targets_and_snapshots(tmp_path):
    keep = add_game("Manual", "/local/game.exe", launch_mode="native")
    remove = add_game("Gone", "/gone.exe")
    add_game_launch_target(keep, "Keep", "/keep.exe")
    add_game_launch_target(remove, "Remove", "/remove.exe")
    payload = base_backup(
        [
            {"metadata": {"title": "Manual"}, "user_state": {"rating": "8"}},
            {"metadata": {"title": "New", "developer": "Dev"}},
        ]
    )
    result = apply_library_backup(
        payload, {"sections": SYNC_SECTIONS}, mirror=True, sync=True
    )
    assert result["deleted"] == 1 and result["created"] == 1
    games = {game["title"]: game for game in get_all_games()}
    assert games["Manual"]["exe_path"] == "/local/game.exe"
    assert games["Manual"]["launch_mode"] == "native"
    assert games["Manual"]["rating"] == "8"
    assert games["New"]["exe_path"] == ""
    assert len(list_game_launch_targets(keep)) == 1
    assert list_game_launch_targets(remove) == []
    snapshot = tmp_path / "sync" / "last-local.json"
    assert len(load_library_backup(snapshot)["games"]) == 2


def test_mirror_keeps_ambiguous_manual_games():
    add_game("Duplicate", "/one.exe")
    add_game("Duplicate", "/two.exe")
    result = apply_library_backup(
        base_backup([{"metadata": {"title": "Duplicate"}}]),
        {"sections": SYNC_SECTIONS},
        mirror=True,
        sync=True,
    )
    assert result["ambiguous"] == 1
    assert result["deleted"] == 0
    assert len(get_all_games()) == 2


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_wlib_backup.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


def write_backup(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def base_backup(games: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "exported_at": "2026-05-05T12:00:00",
        "app": {"name": "wLib", "version": "test"},
        "selected_sections": [
            SECTION_USER_STATE,
            SECTION_LAUNCH_CONFIG,
            SECTION_EXECUTABLE_PATHS,
            SECTION_LAUNCH_TARGETS,
            SECTION_SETTINGS_GENERAL,
            SECTION_SETTINGS_PATHS,
        ],
        "games": games,
    }


@pytest.mark.parametrize(
    "invalid", [None, 42, "bad", {}, {"metadata": "bad"}, {"metadata": {"title": " "}}]
)
def test_invalid_game_records_rejected_before_import(tmp_path, invalid):
    payload = base_backup([{"metadata": {"title": "Valid"}}])
    payload["games"] = [{"metadata": {"title": "Valid"}}, invalid]
    path = write_backup(tmp_path / "invalid.json", payload)
    api = Api()
    assert api.inspect_library_backup(str(path))["success"] is False
    assert api.import_library_backup(str(path))["success"] is False
    assert get_all_games() == []


@pytest.mark.parametrize(
    "targets",
    [[{"label": "Broken"}], None, "bad", [None], [{"label": "Broken", "exe_path": 42}]],
)
def test_invalid_targets_preserve_existing_targets(tmp_path, targets):
    url = "https://f95zone.to/threads/game.123/"
    game_id = add_game("Original", "", f95_url=url)
    assert game_id is not None
    add_game_launch_target(game_id, "Existing", "/game.sh")
    before = list_game_launch_targets(game_id)
    payload = base_backup(
        [{"metadata": {"title": "Changed", "f95_url": url}, "launch_targets": targets}]
    )
    path = write_backup(tmp_path / "invalid.json", payload)
    assert Api().import_library_backup(str(path))["success"] is False
    assert list_game_launch_targets(game_id) == before
    assert get_all_games()[0]["title"] == "Original"


@pytest.mark.parametrize("include_targets", [False, True])
def test_missing_targets_preserved_and_empty_targets_clear(tmp_path, include_targets):
    url = "https://f95zone.to/threads/game.123/"
    game_id = add_game("Original", "", f95_url=url)
    assert game_id is not None
    add_game_launch_target(game_id, "Existing", "/game.sh")
    game = {"metadata": {"title": "Original", "f95_url": url}}
    if include_targets:
        game["launch_targets"] = []
    path = write_backup(tmp_path / "targets.json", base_backup([game]))
    assert Api().import_library_backup(str(path))["success"] is True
    assert len(list_game_launch_targets(game_id)) == (0 if include_targets else 1)


def test_custom_status_and_urm_settings_round_trip(tmp_path):
    api = Api()
    game_id = add_game(
        "Custom",
        "/tmp/game.exe",
        f95_url="https://f95zone.to/threads/custom.123/",
        launch_mode="custom",
        command_line_args="xsystem35",
    )
    assert game_id is not None
    update_game(game_id, {"play_status": "Backlog"})
    source_path = str(tmp_path / "URM.rpa")
    assert api.save_settings(
        {"custom_play_statuses": ["Backlog"], "urm_rpa_path": source_path}
    )["success"]
    sections = [
        SECTION_USER_STATE,
        SECTION_LAUNCH_CONFIG,
        SECTION_SETTINGS_GENERAL,
        SECTION_SETTINGS_PATHS,
    ]
    destination = tmp_path / "backup.json"
    result = api.export_library_backup({"sections": sections}, str(destination))
    assert result["success"]
    backup = json.loads(destination.read_text(encoding="utf-8"))
    assert "urm_rpa_path" not in backup["settings"]["general"]
    assert backup["settings"]["paths"]["urm_rpa_path"] == source_path
    update_game(game_id, {"play_status": "Playing", "launch_mode": "auto"})
    update_setting("custom_play_statuses", "[]")
    update_setting("urm_rpa_path", "")
    result = api.import_library_backup(str(destination), {"sections": sections})
    assert result["success"]
    game = get_all_games()[0]
    assert game["play_status"] == "Backlog"
    assert game["launch_mode"] == "custom"
    assert api.get_settings()["custom_play_statuses"] == ["Backlog"]
    assert api.get_settings()["urm_rpa_path"] == source_path


def test_export_writes_format_metadata_and_selected_sections(tmp_path):
    api = Api()
    game_id = add_game(
        title="Exported Game",
        exe_path="/tmp/exported/game.sh",
        f95_url="https://f95zone.to/threads/exported.12345/",
        version="1.0",
        cover_image="https://img.example/cover.jpg",
        tags="sandbox, romance",
        developer="Dev A",
        engine="Ren'Py",
    )
    assert game_id is not None
    update_game(game_id, {"play_status": "Playing", "is_favorite": True})
    add_game_launch_target(game_id, "Bonus", "/tmp/exported/bonus.sh")
    update_setting("wine_prefix_path", "/tmp/wlib-prefix")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE games SET playtime_seconds = ?, progress = ? WHERE id = ?",
        (3661, "Chapter 4", game_id),
    )
    conn.commit()
    conn.close()

    result = api.export_library_backup(
        {
            "sections": [
                SECTION_USER_STATE,
                SECTION_LAUNCH_TARGETS,
                SECTION_SETTINGS_PATHS,
            ]
        },
        str(tmp_path / "library-export"),
    )

    assert result["success"] is True
    backup_path = Path(str(result["path"]))
    assert backup_path.name == "library-export.json"
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backup["format"] == BACKUP_FORMAT
    assert backup["format_version"] == BACKUP_FORMAT_VERSION
    assert backup["app"]["version"]
    assert backup["selected_sections"] == [
        SECTION_LAUNCH_TARGETS,
        SECTION_SETTINGS_PATHS,
        SECTION_USER_STATE,
    ]

    exported_game = backup["games"][0]
    metadata = exported_game["metadata"]
    assert metadata["title"] == "Exported Game"
    assert metadata["developer"] == "Dev A"
    assert metadata["engine"] == "Ren'Py"
    assert metadata["tags"] == "sandbox, romance"
    assert metadata["f95_url"] == "https://f95zone.to/threads/exported.12345/"
    assert metadata["version"] == "1.0"
    assert metadata["cover_image_path"] == "https://img.example/cover.jpg"
    assert exported_game[SECTION_USER_STATE]["playtime_seconds"] == 3661
    assert exported_game[SECTION_LAUNCH_TARGETS][0]["label"] == "Bonus"
    assert "id" not in exported_game[SECTION_LAUNCH_TARGETS][0]
    assert SECTION_EXECUTABLE_PATHS not in exported_game
    assert "browser_session" not in json.dumps(backup)


def test_export_reports_invalid_destination(tmp_path):
    api = Api()
    result = api.export_library_backup({}, str(tmp_path / "missing" / "backup.json"))

    assert result["success"] is False
    assert result["error_code"] == "invalid_destination"


def test_reimport_matches_games_without_url_or_developer(tmp_path):
    api = Api()
    add_game(title="Manual Game", exe_path="/games/manual/game.exe")
    add_game(title="Manual Game", exe_path="/games/other/game.exe")
    exported = api.export_library_backup({}, str(tmp_path / "backup.json"))

    result = api.import_library_backup(str(exported["path"]))

    assert result["success"] is True
    assert (result["created"], result["updated"]) == (0, 2)
    assert len(get_all_games()) == 2


def test_inspect_validates_format_and_matches_games(tmp_path):
    api = Api()
    add_game(
        title="Thread Match",
        exe_path="/tmp/local-thread.sh",
        f95_url="https://f95zone.to/threads/old-slug.222/",
        developer="Dev T",
    )
    add_game(title="Fallback Match", exe_path="/tmp/fallback.sh", developer="Dev F")
    add_game(title="Ambiguous", exe_path="/tmp/a.sh", developer="Same Dev")
    add_game(title="Ambiguous", exe_path="/tmp/b.sh", developer="Same Dev")

    backup_path = write_backup(
        tmp_path / "backup.json",
        base_backup(
            [
                {
                    "metadata": {
                        "title": "Thread Match",
                        "developer": "Dev T",
                        "f95_url": "https://f95zone.to/threads/new-slug.222/page-2",
                    }
                },
                {
                    "metadata": {
                        "title": "Fallback Match",
                        "developer": "Dev F",
                        "f95_url": "",
                    }
                },
                {
                    "metadata": {
                        "title": "New Game",
                        "developer": "Dev N",
                        "f95_url": "",
                    }
                },
                {
                    "metadata": {
                        "title": "Ambiguous",
                        "developer": "Same Dev",
                        "f95_url": "",
                    }
                },
            ]
        ),
    )

    result = api.inspect_library_backup(str(backup_path))

    assert result["success"] is True
    counts = cast(dict[str, object], result["counts"])
    assert counts["total_games"] == 4
    assert counts["matched_games"] == 2
    assert counts["new_games"] == 1
    assert counts["ambiguous_games"] == 1
    assert SECTION_METADATA in cast(list[str], result["available_sections"])
    assert (
        cast(list[dict[str, object]], result["ambiguous_games"])[0]["title"]
        == "Ambiguous"
    )

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("not json", encoding="utf-8")
    invalid = api.inspect_library_backup(str(invalid_path))
    assert invalid["success"] is False
    assert invalid["error_code"] == "invalid_json"

    future = base_backup([])
    future["format_version"] = BACKUP_FORMAT_VERSION + 1
    future_path = write_backup(tmp_path / "future.json", future)
    future_result = api.inspect_library_backup(str(future_path))
    assert future_result["success"] is False
    assert future_result["error_code"] == "unsupported_format_version"


def test_import_merges_backup_values_and_preserves_unselected_fields(tmp_path):
    api = Api()
    local_id = add_game(
        title="Old Title",
        exe_path="/tmp/local/game.sh",
        f95_url="https://f95zone.to/threads/old.333/",
        developer="Old Dev",
        engine="Unity",
    )
    assert local_id is not None
    add_game_launch_target(local_id, "Old Target", "/tmp/local/old-target.sh")
    update_setting("enable_logging", "false")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE games SET playtime_seconds = ?, progress = ?, play_status = ?, launch_mode = ? WHERE id = ?",
        (10, "Local progress", "Playing", "native", local_id),
    )
    conn.commit()
    conn.close()

    backup_path = write_backup(
        tmp_path / "import.json",
        {
            **base_backup(
                [
                    {
                        "metadata": {
                            "title": "New Title",
                            "developer": "New Dev",
                            "engine": "Ren'Py",
                            "tags": "tag-a, tag-b",
                            "f95_url": "https://f95zone.to/threads/new.333/",
                            "version": "2.0",
                            "latest_version": "2.1",
                            "cover_image_path": "https://img.example/new.jpg",
                        },
                        SECTION_USER_STATE: {
                            "play_status": "waiting_update",
                            "progress": "Imported progress",
                            "playtime_seconds": 99,
                            "is_favorite": True,
                        },
                        SECTION_LAUNCH_CONFIG: {
                            "launch_mode": "broken-mode",
                            "command_line_args": "--safe-mode",
                        },
                        SECTION_EXECUTABLE_PATHS: {"exe_path": "/tmp/backup/game.sh"},
                        SECTION_LAUNCH_TARGETS: [
                            {
                                "id": 999,
                                "game_id": 999,
                                "label": "Imported Target",
                                "exe_path": "/tmp/backup/target.sh",
                                "sort_order": 0,
                            }
                        ],
                    }
                ]
            ),
            "settings": {"general": {"enable_logging": "true"}},
        },
    )

    result = api.import_library_backup(
        str(backup_path),
        {
            "sections": [
                SECTION_USER_STATE,
                SECTION_LAUNCH_CONFIG,
                SECTION_LAUNCH_TARGETS,
                SECTION_SETTINGS_GENERAL,
            ]
        },
    )

    assert result["success"] is True
    assert result["created"] == 0
    assert result["updated"] == 1
    assert int(str(result["warnings"])) >= 1
    assert get_setting("enable_logging") == "true"

    game = get_all_games()[0]
    assert game["id"] == local_id
    assert game["title"] == "Old Title"
    assert game["developer"] == "Old Dev"
    assert game["engine"] == "Unity"
    assert game["f95_url"] == "https://f95zone.to/threads/old.333/"
    assert game["play_status"] == "Waiting For Update"
    assert game["progress"] == "Imported progress"
    assert game["playtime_seconds"] == 99
    assert game["launch_mode"] == "auto"
    assert game["command_line_args"] == "--safe-mode"
    assert game["exe_path"] == "/tmp/local/game.sh"

    targets = list_game_launch_targets(local_id)
    assert len(targets) == 1
    assert targets[0]["label"] == "Imported Target"
    assert targets[0]["game_id"] == local_id
    assert targets[0]["id"] != 999


def test_finished_version_exports_with_user_state(tmp_path):
    api = Api()
    game_id = add_game(
        title="Finished Game",
        exe_path="/tmp/finished/game.sh",
        f95_url="https://f95zone.to/threads/finished.777/",
        version="0.5",
    )
    assert game_id is not None
    update_game(game_id, {"play_status": "Completed"})

    result = api.export_library_backup(
        {"sections": [SECTION_USER_STATE]}, str(tmp_path / "export")
    )

    assert result["success"] is True
    backup = json.loads(Path(str(result["path"])).read_text(encoding="utf-8"))
    exported_game = backup["games"][0]
    assert exported_game[SECTION_USER_STATE]["finished_version"] == "0.5"


def test_import_finished_version_semantics(tmp_path):
    api = Api()
    local_id = add_game(
        title="Local Game",
        exe_path="/tmp/local/game.sh",
        f95_url="https://f95zone.to/threads/local.888/",
        version="0.7",
    )
    assert local_id is not None
    update_game(local_id, {"play_status": "Playing", "finished_version": "0.6"})

    def imported_finished_version(name: str, user_state: dict[str, object]) -> object:
        backup_path = write_backup(
            tmp_path / name,
            base_backup(
                [
                    {
                        "metadata": {
                            "title": "Local Game",
                            "f95_url": "https://f95zone.to/threads/local.888/",
                        },
                        SECTION_USER_STATE: user_state,
                    }
                ]
            ),
        )
        result = api.import_library_backup(
            str(backup_path), {"sections": [SECTION_USER_STATE]}
        )
        assert result["success"] is True
        game = next(g for g in get_all_games() if g["id"] == local_id)
        return game["finished_version"]

    # A backup finished_version is applied to the matched game.
    assert imported_finished_version("with.json", {"finished_version": "0.5"}) == "0.5"
    # A backup without the key keeps the local value...
    assert imported_finished_version("without.json", {"rating": "9"}) == "0.5"
    # ...even when the backup status is Completed.
    assert (
        imported_finished_version("completed.json", {"play_status": "Completed"})
        == "0.5"
    )


@pytest.mark.parametrize(
    ("host_is_windows", "foreign_path", "native_path"),
    [
        (False, r"C:\Users\me\Games\prefix", "/opt/proton"),
        (True, "/home/me/.local/share/wLib/prefix", r"D:\Games\Proton\proton"),
    ],
)
def test_import_skips_path_settings_from_other_os(
    tmp_path, monkeypatch, host_is_windows, foreign_path, native_path
):
    monkeypatch.setattr("core.library_backup.is_windows", lambda: host_is_windows)
    api = Api()
    update_setting("wine_prefix_path", "local-prefix")
    backup_path = write_backup(
        tmp_path / "import.json",
        {
            **base_backup([]),
            "settings": {
                "paths": {
                    "wine_prefix_path": foreign_path,
                    "proton_path": native_path,
                }
            },
        },
    )

    inspect = api.inspect_library_backup(str(backup_path))
    result = api.import_library_backup(
        str(backup_path), {"sections": [SECTION_SETTINGS_PATHS]}
    )

    assert result["success"] is True
    assert get_setting("wine_prefix_path") == "local-prefix"
    assert get_setting("proton_path") == native_path
    assert result["settings_updated"] == 1
    warnings = cast(list[dict[str, object]], inspect["warnings"])
    assert any(
        w["type"] == "foreign_path" and w["field"] == "wine_prefix_path"
        for w in warnings
    )


def test_legacy_browser_path_is_skipped_in_backups(tmp_path):
    api = Api()
    # Host-native path: the importer skips paths written for the other OS.
    proton_path = str(tmp_path / "proton")
    path = write_backup(
        tmp_path / "legacy.json",
        {
            **base_backup([]),
            "settings": {
                "paths": {
                    "playwright_browsers_path": "/legacy/cache",
                    "proton_path": proton_path,
                }
            },
        },
    )
    inspected = api.inspect_library_backup(str(path))
    assert inspected["success"] is True
    assert not any(
        w.get("field") == "playwright_browsers_path" for w in inspected["warnings"]
    )
    imported = api.import_library_backup(
        str(path), {"sections": [SECTION_SETTINGS_PATHS]}
    )
    assert imported["success"] is True
    assert get_setting("playwright_browsers_path") is None
    assert get_setting("proton_path") == proton_path
    update_setting("playwright_browsers_path", "/stale")
    exported_path = tmp_path / "export.json"
    assert (
        api.export_library_backup(
            {"sections": [SECTION_SETTINGS_PATHS]}, str(exported_path)
        )["success"]
        is True
    )
    assert "playwright_browsers_path" not in exported_path.read_text()


def test_import_rejects_empty_section_list_without_writes(tmp_path):
    api = Api()
    local_id = add_game(
        title="Keep Local",
        exe_path="/tmp/local/game.sh",
        f95_url="https://f95zone.to/threads/keep.444/",
        developer="Local Dev",
    )
    assert local_id is not None
    update_setting("enable_logging", "false")

    backup_path = write_backup(
        tmp_path / "empty-sections.json",
        {
            **base_backup(
                [
                    {
                        "metadata": {
                            "title": "Backup Title",
                            "developer": "Backup Dev",
                            "f95_url": "https://f95zone.to/threads/keep.444/",
                        },
                        SECTION_USER_STATE: {
                            "play_status": "completed",
                            "progress": "Imported progress",
                        },
                    }
                ]
            ),
            "settings": {"general": {"enable_logging": "true"}},
        },
    )

    result = api.import_library_backup(str(backup_path), {"sections": []})

    assert result["success"] is False
    assert result["error_code"] == "empty_selection"
    game = get_all_games()[0]
    assert game["title"] == "Keep Local"
    assert game["developer"] == "Local Dev"
    assert game["play_status"] == "Not Started"
    assert game["progress"] == ""
    assert get_setting("enable_logging") == "false"


def test_import_rejects_all_disabled_section_map_without_writes(tmp_path):
    api = Api()
    local_id = add_game(
        title="Map Local",
        exe_path="/tmp/local/map.sh",
        f95_url="https://f95zone.to/threads/map.445/",
    )
    assert local_id is not None

    backup_path = write_backup(
        tmp_path / "disabled-map.json",
        base_backup(
            [
                {
                    "metadata": {
                        "title": "Map Backup",
                        "f95_url": "https://f95zone.to/threads/map.445/",
                    },
                    SECTION_USER_STATE: {"progress": "Should not import"},
                }
            ]
        ),
    )

    result = api.import_library_backup(
        str(backup_path),
        {
            "sections": {
                SECTION_METADATA: False,
                SECTION_USER_STATE: False,
            }
        },
    )

    assert result["success"] is False
    assert result["error_code"] == "empty_selection"
    game = get_all_games()[0]
    assert game["title"] == "Map Local"
    assert game["progress"] == ""


def test_import_selected_metadata_updates_matched_game(tmp_path):
    api = Api()
    local_id = add_game(
        title="Old Metadata",
        exe_path="/tmp/local/metadata.sh",
        f95_url="https://f95zone.to/threads/meta.446/",
        developer="Old Dev",
        engine="Unity",
    )
    assert local_id is not None

    backup_path = write_backup(
        tmp_path / "metadata-import.json",
        base_backup(
            [
                {
                    "metadata": {
                        "title": "New Metadata",
                        "developer": "New Dev",
                        "engine": "Ren'Py",
                        "tags": "tag-a",
                        "f95_url": "https://f95zone.to/threads/meta.446/",
                        "version": "2.0",
                        "latest_version": "2.1",
                        "cover_image_path": "https://img.example/meta.jpg",
                    },
                    SECTION_USER_STATE: {
                        "progress": "Should stay local unless selected",
                    },
                }
            ]
        ),
    )

    result = api.import_library_backup(
        str(backup_path), {"sections": [SECTION_METADATA]}
    )

    assert result["success"] is True
    game = get_all_games()[0]
    assert game["title"] == "New Metadata"
    assert game["developer"] == "New Dev"
    assert game["engine"] == "Ren'Py"
    assert game["tags"] == "tag-a"
    assert game["version"] == "2.0"
    assert game["latest_version"] == "2.1"
    assert game["cover_image_path"] == "https://img.example/meta.jpg"
    assert game["progress"] == ""


def test_thread_completion_backup_round_trip_and_missing_field_preserves_local(tmp_path):
    game_id = add_game(
        "Finished thread", "", f95_url="https://f95zone.to/threads/finished.445/"
    )
    assert game_id is not None
    conn = get_connection()
    conn.execute("UPDATE games SET thread_completed = 1 WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()

    api = Api()
    exported = api.export_library_backup(destination_path=str(tmp_path / "wishlist.json"))
    assert exported["success"] is True
    path = Path(str(exported["path"]))
    backup = json.loads(path.read_text(encoding="utf-8"))
    assert backup["games"][0]["metadata"]["thread_completed"] == 1

    conn = get_connection()
    conn.execute("UPDATE games SET thread_completed = 0 WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    assert api.import_library_backup(str(path), {"sections": [SECTION_METADATA]})["success"]
    assert get_all_games()[0]["thread_completed"] == 1

    del backup["games"][0]["metadata"]["thread_completed"]
    path.write_text(json.dumps(backup), encoding="utf-8")
    assert api.import_library_backup(str(path), {"sections": [SECTION_METADATA]})["success"]
    assert get_all_games()[0]["thread_completed"] == 1


def test_import_new_game_uses_metadata_without_metadata_section(tmp_path):
    api = Api()
    backup_path = write_backup(
        tmp_path / "new-game.json",
        base_backup(
            [
                {
                    "metadata": {
                        "title": "Created From Metadata",
                        "developer": "New Dev",
                        "engine": "Godot",
                        "f95_url": "https://f95zone.to/threads/new-game.447/",
                    },
                    SECTION_USER_STATE: {
                        "play_status": "playing",
                        "progress": "Chapter 2",
                    },
                }
            ]
        ),
    )

    result = api.import_library_backup(
        str(backup_path), {"sections": [SECTION_USER_STATE]}
    )

    assert result["success"] is True
    assert result["created"] == 1
    game = get_all_games()[0]
    assert game["title"] == "Created From Metadata"
    assert game["developer"] == "New Dev"
    assert game["engine"] == "Godot"
    assert game["play_status"] == "Playing"
    assert game["progress"] == "Chapter 2"


def test_import_invalid_file_does_not_partially_write(tmp_path):
    api = Api()
    local_id = add_game(title="Safe", exe_path="/tmp/safe.sh")
    assert local_id is not None
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("not json", encoding="utf-8")

    result = api.import_library_backup(
        str(invalid_path), {"sections": [SECTION_USER_STATE]}
    )

    assert result["success"] is False
    assert result["error_code"] == "invalid_json"
    games = get_all_games()
    assert len(games) == 1
    assert games[0]["title"] == "Safe"


def test_apply_merge_mode_overwrite_replaces_matched_values():
    url = "https://f95zone.to/threads/merge.999/"
    game_id = add_game("Local", "/local/game.sh", f95_url=url)
    assert game_id is not None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE games SET rating_story = ?, play_status = ?, playtime_seconds = ? WHERE id = ?",
        (4.0, "Playing", 100, game_id),
    )
    conn.commit()
    conn.close()
    add_game_launch_target(game_id, "Existing", "/local/extra.sh")

    payload = base_backup(
        [
            {
                "metadata": {"title": "Local", "f95_url": url},
                SECTION_USER_STATE: {
                    "rating_story": 3.0,
                    "play_status": "Completed",
                    "playtime_seconds": 50,
                },
                SECTION_LAUNCH_TARGETS: [
                    {"label": "Imported", "exe_path": "/backup/extra.sh"}
                ],
            }
        ]
    )
    result = apply_library_backup(
        payload,
        {"sections": [SECTION_USER_STATE, SECTION_LAUNCH_TARGETS]},
        merge_mode="overwrite",
    )
    assert result["updated"] == 1
    game = get_all_games()[0]
    assert game["rating_story"] == 3.0
    assert game["play_status"] == "Completed"
    assert game["playtime_seconds"] == 50
    assert [target["label"] for target in list_game_launch_targets(game_id)] == [
        "Imported"
    ]


def test_apply_merge_mode_fill_missing_fills_blanks_and_keeps_values():
    url = "https://f95zone.to/threads/fill.998/"
    game_id = add_game("Local", "/local/game.sh", f95_url=url)
    assert game_id is not None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE games SET rating_story = ?, play_status = ?, playtime_seconds = ? WHERE id = ?",
        (4.0, "Playing", 100, game_id),
    )
    conn.commit()
    conn.close()
    add_game_launch_target(game_id, "Existing", "/local/extra.sh")

    payload = base_backup(
        [
            {
                "metadata": {"title": "Local", "f95_url": url},
                SECTION_USER_STATE: {
                    "rating_story": 3.0,
                    "play_status": "Completed",
                    "playtime_seconds": 50,
                },
                SECTION_LAUNCH_TARGETS: [
                    {"label": "Imported", "exe_path": "/backup/extra.sh"}
                ],
            }
        ]
    )
    result = apply_library_backup(
        payload,
        {"sections": [SECTION_USER_STATE, SECTION_LAUNCH_TARGETS]},
        merge_mode="fill_missing",
    )
    assert result["updated"] == 1
    game = get_all_games()[0]
    assert game["rating_story"] == 4.0
    assert game["play_status"] == "Playing"
    assert game["playtime_seconds"] == 100
    assert [target["label"] for target in list_game_launch_targets(game_id)] == [
        "Existing"
    ]


def test_apply_merge_mode_fill_missing_fills_default_status_and_zero_playtime():
    url = "https://f95zone.to/threads/fill-blank.997/"
    game_id = add_game("Blank", "", f95_url=url)
    assert game_id is not None
    assert get_all_games()[0]["play_status"] == "Not Started"
    assert get_all_games()[0]["playtime_seconds"] == 0

    payload = base_backup(
        [
            {
                "metadata": {"title": "Blank", "f95_url": url},
                SECTION_USER_STATE: {
                    "play_status": "Playing",
                    "playtime_seconds": 3600,
                },
                SECTION_EXECUTABLE_PATHS: {"exe_path": "/backup/game.sh"},
            }
        ]
    )
    result = apply_library_backup(
        payload,
        {"sections": [SECTION_USER_STATE, SECTION_EXECUTABLE_PATHS]},
        merge_mode="fill_missing",
    )
    assert result["updated"] == 1
    game = get_all_games()[0]
    assert game["play_status"] == "Playing"
    assert game["playtime_seconds"] == 3600
    assert game["exe_path"] == "/backup/game.sh"


def test_apply_returns_created_and_updated_ids():
    first_id = add_game(
        "First", "/first.sh", f95_url="https://f95zone.to/threads/first.111/"
    )
    assert first_id is not None
    payload = base_backup(
        [
            {
                "metadata": {
                    "title": "First",
                    "f95_url": "https://f95zone.to/threads/first.111/",
                },
                SECTION_USER_STATE: {"progress": "Chapter 2"},
            },
            {
                "metadata": {
                    "title": "Second",
                    "f95_url": "https://f95zone.to/threads/second.222/",
                },
            },
        ]
    )
    result = apply_library_backup(payload, {"sections": [SECTION_USER_STATE]})
    assert result["created"] == 1
    assert result["updated"] == 1
    games = {game["title"]: game for game in get_all_games()}
    assert result["created_ids"] == [games["Second"]["id"]]
    assert result["updated_ids"] == [first_id]
    assert games["First"]["progress"] == "Chapter 2"


def test_paths_export_includes_library_settings_excludes_dismissals(tmp_path):
    from core.library_backup import PATH_SETTINGS_KEYS, build_library_backup

    assert "library_folder" in PATH_SETTINGS_KEYS
    assert "library_folder_pattern" in PATH_SETTINGS_KEYS
    assert "downloads_folder" in PATH_SETTINGS_KEYS
    assert "download_detection_enabled" in PATH_SETTINGS_KEYS
    assert "dismissed_download_archives" not in PATH_SETTINGS_KEYS

    update_setting("library_folder", "/games")
    update_setting("downloads_folder", "/downloads")
    update_setting("dismissed_download_archives", '["old"]')
    backup = build_library_backup({"sections": [SECTION_SETTINGS_PATHS]})
    paths = backup["settings"]["paths"]
    assert paths["library_folder"] == "/games"
    assert "library_folder_pattern" in paths
    assert paths["downloads_folder"] == "/downloads"
    assert "dismissed_download_archives" not in paths
