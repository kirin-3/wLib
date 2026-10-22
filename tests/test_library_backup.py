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
    create_collection,
    get_all_games,
    get_collections,
    get_connection,
    get_setting,
    init_db,
    list_game_launch_targets,
    set_game_collections,
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


def test_library_filter_presets_round_trip_through_backup(tmp_path):
    api = Api()
    saved = api.save_library_filter_presets(
        [
            {
                "name": "Ren'Py now",
                "filterCollection": "Favorites",
                "filterStatuses": ["Playing"],
                "filterInstallState": "installed",
                "filterEngines": ["Ren'Py"],
                "filterTags": ["dating-sim"],
                "sortBy": "rating",
                "sortDir": "desc",
            }
        ]
    )
    assert saved["success"]

    sections = [SECTION_SETTINGS_GENERAL]
    destination = tmp_path / "presets.json"
    result = api.export_library_backup({"sections": sections}, str(destination))
    assert result["success"]
    backup = json.loads(destination.read_text(encoding="utf-8"))
    assert json.loads(backup["settings"]["general"]["library_filter_presets"]) == [
        {
            "name": "Ren'Py now",
            "filterCollection": "Favorites",
            "filterStatuses": ["Playing"],
            "filterInstallState": "installed",
            "filterEngines": ["Ren'Py"],
            "filterTags": ["dating-sim"],
            "sortBy": "rating",
            "sortDir": "desc",
        }
    ]

    update_setting("library_filter_presets", "[]")
    result = api.import_library_backup(str(destination), {"sections": sections})
    assert result["success"]
    assert [
        preset["name"] for preset in api.get_settings()["library_filter_presets"]
    ] == ["Ren'Py now"]


def test_collections_and_membership_round_trip(tmp_path):
    api = Api()
    game_id = add_game(
        "Collected",
        "/tmp/game.exe",
        f95_url="https://f95zone.to/threads/collected.123/",
    )
    assert game_id is not None
    create_collection("Backlog", game_id=game_id)
    create_collection("Best of")
    assert set_game_collections(game_id, ["Backlog", "Best of"]) == [
        "Backlog",
        "Best of",
    ]
    sections = [
        SECTION_USER_STATE,
        SECTION_SETTINGS_GENERAL,
    ]
    destination = tmp_path / "backup.json"
    result = api.export_library_backup({"sections": sections}, str(destination))
    assert result["success"]
    backup = json.loads(destination.read_text(encoding="utf-8"))
    exported_game = backup["games"][0]
    assert exported_game[SECTION_USER_STATE]["collections"] == ["Backlog", "Best of"]
    assert json.loads(backup["settings"]["general"]["collections"]) == [
        "Backlog",
        "Best of",
    ]

    # Wipe local state, then import restores collections and membership.
    update_setting("collections", "[]")
    conn = get_connection()
    conn.execute("UPDATE games SET collections = '[]'")
    conn.commit()
    conn.close()
    result = api.import_library_backup(str(destination), {"sections": sections})
    assert result["success"]
    assert get_collections() == ["Backlog", "Best of"]
    game = get_all_games()[0]
    assert game["collections"] == ["Backlog", "Best of"]


def test_older_backup_without_collections_leaves_them_unchanged(tmp_path):
    api = Api()
    game_id = add_game(
        "Legacy",
        "/tmp/legacy.exe",
        f95_url="https://f95zone.to/threads/legacy.124/",
    )
    assert game_id is not None
    create_collection("Backlog", game_id=game_id)

    older = tmp_path / "older.json"
    older.write_text(
        json.dumps(
            {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_FORMAT_VERSION,
                "exported_at": "2020-01-01T00:00:00",
                "app": {"name": "wLib", "version": "0.0.1"},
                "selected_sections": [SECTION_USER_STATE],
                "games": [
                    {
                        "metadata": {
                            "title": "Legacy",
                            "f95_url": "https://f95zone.to/threads/legacy.124/",
                        },
                        SECTION_USER_STATE: {"play_status": "Playing"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = api.import_library_backup(str(older))
    assert result["success"]
    assert get_collections() == ["Backlog"]
    game = get_all_games()[0]
    assert game["collections"] == ["Backlog"]


def test_imported_membership_for_unknown_collection_is_ignored(tmp_path):
    api = Api()
    add_game(
        "Ghost member",
        "/tmp/ghost.exe",
        f95_url="https://f95zone.to/threads/ghost.125/",
    )
    create_collection("Backlog")

    source = tmp_path / "ghost.json"
    source.write_text(
        json.dumps(
            {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_FORMAT_VERSION,
                "exported_at": "2020-01-01T00:00:00",
                "app": {"name": "wLib", "version": "0.0.1"},
                "selected_sections": [SECTION_USER_STATE],
                "games": [
                    {
                        "metadata": {
                            "title": "Ghost member",
                            "f95_url": "https://f95zone.to/threads/ghost.125/",
                        },
                        SECTION_USER_STATE: {
                            "collections": ["Ghost", "Backlog"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = api.import_library_backup(str(source))
    assert result["success"]
    game = get_all_games()[0]
    assert game["collections"] == ["Backlog"]


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


def test_import_without_identity_sections_matches_unique_title(tmp_path):
    api = Api()
    game_id = add_game("Solo", "/solo.exe")
    assert game_id is not None
    # Exporting only user state leaves the record without any executable
    # identity, but the title is unique in the local library.
    destination = tmp_path / "user-state-only.json"
    exported = api.export_library_backup(
        {"sections": [SECTION_USER_STATE]}, str(destination)
    )
    assert exported["success"] is True

    result = api.import_library_backup(str(destination))

    assert result["success"] is True
    assert (result["created"], result["updated"]) == (0, 1)
    assert [game["id"] for game in get_all_games()] == [game_id]

    # Importing the same file again must not grow the library.
    again = api.import_library_backup(str(destination))
    assert (again["created"], again["updated"]) == (0, 1)
    assert len(get_all_games()) == 1


def test_import_records_without_identity_and_repeated_title_are_ambiguous(tmp_path):
    api = Api()
    add_game("Manual Game", "/games/manual/game.exe")
    add_game("Manual Game", "/games/other/game.exe")
    destination = tmp_path / "ambiguous.json"
    exported = api.export_library_backup(
        {"sections": [SECTION_USER_STATE]}, str(destination)
    )
    assert exported["success"] is True

    result = api.import_library_backup(str(destination))

    assert result["success"] is True
    assert result["created"] == 0
    assert result["ambiguous"] == 2
    assert len(get_all_games()) == 2


def test_import_with_url_matches_local_game_added_without_url(tmp_path):
    api = Api()
    local_id = add_game("Same Game", "/same.exe", developer="Same Dev")
    assert local_id is not None
    backup_path = write_backup(
        tmp_path / "with-url.json",
        base_backup(
            [
                {
                    "metadata": {
                        "title": "Same Game",
                        "developer": "Same Dev",
                        "f95_url": "https://f95zone.to/threads/same.4242/",
                    },
                    SECTION_USER_STATE: {"progress": "From backup"},
                }
            ]
        ),
    )

    result = api.import_library_backup(str(backup_path))

    assert result["success"] is True
    assert (result["created"], result["updated"]) == (0, 1)
    games = get_all_games()
    assert [game["id"] for game in games] == [local_id]
    assert games[0]["f95_url"] == "https://f95zone.to/threads/same.4242/"
    assert games[0]["exe_path"] == "/same.exe"


def test_import_with_url_keeps_local_game_with_another_thread(tmp_path):
    api = Api()
    local_id = add_game(
        "Same Game",
        "/same.exe",
        f95_url="https://f95zone.to/threads/same.4242/",
        developer="Same Dev",
    )
    assert local_id is not None
    backup_path = write_backup(
        tmp_path / "other-thread.json",
        base_backup(
            [
                {
                    "metadata": {
                        "title": "Same Game",
                        "developer": "Same Dev",
                        "f95_url": "https://f95zone.to/threads/same.9999/",
                    }
                }
            ]
        ),
    )

    result = api.import_library_backup(str(backup_path))

    assert result["success"] is True
    assert (result["created"], result["updated"]) == (1, 0)
    games = get_all_games()
    assert len(games) == 2
    assert {game["f95_url"] for game in games} == {
        "https://f95zone.to/threads/same.4242/",
        "https://f95zone.to/threads/same.9999/",
    }


def test_title_only_record_does_not_merge_into_game_with_thread(tmp_path):
    api = Api()
    url = "https://f95zone.to/threads/scraped.4343/"
    local_id = add_game("Shared Title", "/scraped.exe", f95_url=url, developer="Dev")
    assert local_id is not None
    backup_path = write_backup(
        tmp_path / "title-only.json",
        base_backup(
            [
                {
                    "metadata": {
                        "title": "Shared Title",
                        "f95_url": "",
                        "developer": "",
                    },
                    SECTION_USER_STATE: {"progress": "Manual copy"},
                }
            ]
        ),
    )

    result = api.import_library_backup(str(backup_path))

    assert result["success"] is True
    assert (result["created"], result["updated"]) == (1, 0)
    local = next(game for game in get_all_games() if game["id"] == local_id)
    assert (local["f95_url"], local["developer"]) == (url, "Dev")


def test_backup_without_manifest_ignores_empty_launch_target_lists(tmp_path):
    api = Api()
    path = tmp_path / "empty-targets.json"
    path.write_text(
        json.dumps(
            {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_FORMAT_VERSION,
                "exported_at": "2020-01-01T00:00:00",
                "app": {"name": "wLib", "version": "0.0.1"},
                "games": [{"metadata": {"title": "X"}, SECTION_LAUNCH_TARGETS: []}],
            }
        ),
        encoding="utf-8",
    )

    inspected = api.inspect_library_backup(str(path))

    assert inspected["success"] is True
    assert SECTION_LAUNCH_TARGETS not in cast(
        list[str], inspected["available_sections"]
    )


def test_backup_without_section_manifest_imports_present_sections(tmp_path):
    api = Api()
    game_id = add_game(
        "Legacy",
        "",
        f95_url="https://f95zone.to/threads/legacy.210/",
    )
    assert game_id is not None
    path = tmp_path / "no-manifest.json"
    path.write_text(
        json.dumps(
            {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_FORMAT_VERSION,
                "exported_at": "2020-01-01T00:00:00",
                "app": {"name": "wLib", "version": "0.0.1"},
                "games": [
                    {
                        "metadata": {
                            "title": "Legacy",
                            "f95_url": "https://f95zone.to/threads/legacy.210/",
                        },
                        SECTION_USER_STATE: {"progress": "Kept progress"},
                        SECTION_LAUNCH_TARGETS: [
                            {"label": "Bonus", "exe_path": "/backup/bonus.sh"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    inspected = api.inspect_library_backup(str(path))

    assert inspected["success"] is True
    assert SECTION_USER_STATE in cast(list[str], inspected["available_sections"])
    assert SECTION_LAUNCH_TARGETS in cast(list[str], inspected["available_sections"])

    result = api.import_library_backup(str(path))

    assert result["success"] is True
    game = get_all_games()[0]
    assert game["progress"] == "Kept progress"
    assert [target["label"] for target in list_game_launch_targets(game_id)] == [
        "Bonus"
    ]


def test_backup_with_utf8_bom_is_accepted(tmp_path):
    api = Api()
    add_game(
        "Bom",
        "/bom.exe",
        f95_url="https://f95zone.to/threads/bom.211/",
    )
    destination = tmp_path / "bom.json"
    exported = api.export_library_backup({}, str(destination))
    assert exported["success"] is True

    payload = destination.read_bytes()
    _ = destination.write_bytes(b"\xef\xbb\xbf" + payload)

    inspected = api.inspect_library_backup(str(destination))
    assert inspected["success"] is True
    assert inspected["counts"]["total_games"] == 1

    result = api.import_library_backup(str(destination))
    assert result["success"] is True
    assert result["updated"] == 1


def test_backup_with_invalid_utf8_bytes_is_reported_as_invalid_json(tmp_path):
    path = tmp_path / "bad-encoding.json"
    _ = path.write_bytes(b'{"format": "\xff\xfe"}')

    result = Api().inspect_library_backup(str(path))

    assert result["success"] is False
    assert result["error_code"] == "invalid_json"


def test_deeply_nested_backup_is_reported_as_invalid_json(tmp_path):
    path = tmp_path / "deep.json"
    _ = path.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")

    result = Api().inspect_library_backup(str(path))

    assert result["success"] is False
    assert result["error_code"] == "invalid_json"


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


def test_thread_completion_backup_round_trip_and_missing_field_preserves_local(
    tmp_path,
):
    game_id = add_game(
        "Finished thread", "", f95_url="https://f95zone.to/threads/finished.445/"
    )
    assert game_id is not None
    conn = get_connection()
    conn.execute("UPDATE games SET thread_completed = 1 WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()

    api = Api()
    exported = api.export_library_backup(
        destination_path=str(tmp_path / "wishlist.json")
    )
    assert exported["success"] is True
    path = Path(str(exported["path"]))
    backup = json.loads(path.read_text(encoding="utf-8"))
    assert backup["games"][0]["metadata"]["thread_completed"] == 1

    conn = get_connection()
    conn.execute("UPDATE games SET thread_completed = 0 WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    assert api.import_library_backup(str(path), {"sections": [SECTION_METADATA]})[
        "success"
    ]
    assert get_all_games()[0]["thread_completed"] == 1

    del backup["games"][0]["metadata"]["thread_completed"]
    path.write_text(json.dumps(backup), encoding="utf-8")
    assert api.import_library_backup(str(path), {"sections": [SECTION_METADATA]})[
        "success"
    ]
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


def test_apply_merge_mode_fill_missing_keeps_local_play_sessions():
    from core import database

    url = "https://f95zone.to/threads/journal-fill.997/"
    kept_id = add_game("Played", "/local/played.sh", f95_url=url)
    empty_url = "https://f95zone.to/threads/journal-empty.996/"
    empty_id = add_game("Unplayed", "/local/unplayed.sh", f95_url=empty_url)
    assert kept_id is not None and empty_id is not None
    database.record_play_tick(
        kept_id, database.start_play_session(kept_id, "0.7"), 120, True
    )
    session = {
        "version": "0.6",
        "started_at": "2026-01-01T00:00:00",
        "ended_at": "2026-01-01T01:00:00",
        "duration_seconds": 3600,
    }
    payload = base_backup(
        [
            {
                "metadata": {"title": "Played", "f95_url": url},
                SECTION_USER_STATE: {"play_sessions": [session]},
            },
            {
                "metadata": {"title": "Unplayed", "f95_url": empty_url},
                SECTION_USER_STATE: {"play_sessions": [session]},
            },
        ]
    )
    _ = apply_library_backup(
        payload, {"sections": [SECTION_USER_STATE]}, merge_mode="fill_missing"
    )
    assert [s["version"] for s in database.get_play_sessions(kept_id)] == ["0.7"]
    assert [s["version"] for s in database.get_play_sessions(empty_id)] == ["0.6"]


def test_apply_merge_mode_fill_missing_fills_empty_collection_membership():
    url = "https://f95zone.to/threads/fill-collections.996/"
    game_id = add_game("Local", "", f95_url=url)
    assert game_id is not None
    update_setting("collections", '["Backlog"]')

    payload = base_backup(
        [
            {
                "metadata": {"title": "Local", "f95_url": url},
                SECTION_USER_STATE: {"collections": ["Backlog"]},
            }
        ]
    )
    result = apply_library_backup(
        payload,
        {"sections": [SECTION_USER_STATE]},
        merge_mode="fill_missing",
    )
    assert result["updated"] == 1
    game = get_all_games()[0]
    assert game["collections"] == ["Backlog"]


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


def test_non_path_settings_import_without_path_warnings(tmp_path):
    payload = base_backup([])
    payload["settings"] = {
        "paths": {
            "library_folder_pattern": "{title}/{version}",
            "download_detection_enabled": "false",
        }
    }
    backup_path = write_backup(tmp_path / "pattern.json", payload)
    api = Api()

    inspected = api.inspect_library_backup(str(backup_path))
    assert inspected["success"] is True
    assert not [
        w
        for w in inspected["warnings"]
        if w.get("field") in ("library_folder_pattern", "download_detection_enabled")
    ]

    result = api.import_library_backup(
        str(backup_path), options={"sections": [SECTION_SETTINGS_PATHS]}
    )
    assert result["success"] is True
    assert get_setting("library_folder_pattern") == "{title}/{version}"
    assert get_setting("download_detection_enabled") == "false"


def test_fill_missing_settings_keeps_local_values_and_fills_empty(tmp_path):
    update_setting("enable_logging", "true")
    update_setting("collections", "[]")

    payload = base_backup([])
    payload["settings"] = {
        "general": {"enable_logging": "false", "collections": '["Backup"]'}
    }
    backup_path = write_backup(tmp_path / "settings_fill.json", payload)

    result = Api().import_library_backup(
        str(backup_path),
        options={"sections": [SECTION_SETTINGS_GENERAL], "merge_mode": "fill_missing"},
    )

    assert result["success"] is True
    assert get_setting("enable_logging") == "true"
    assert get_setting("collections") == '["Backup"]'


def test_import_merges_backup_games_sharing_a_thread_url(tmp_path):
    """Backup rows repeating a thread URL must merge instead of aborting.

    Regression: games.f95_url carries a unique index, so a second INSERT of the
    same thread raised IntegrityError, rolled the whole transaction back and
    left the library untouched. Copies written under different F95 URL slugs
    (added after a thread rename) normalize to the same thread, so the local
    row and the multiplied backup rows all collapse onto one game.
    """
    api = Api()
    url = "https://f95zone.to/threads/duplicate.555/"
    local_id = add_game("Local Copy", "/local/game.sh", f95_url=url, version="1.0")
    assert local_id is not None
    add_game_launch_target(local_id, "Existing", "/local/existing.sh")

    backup_path = write_backup(
        tmp_path / "duplicate-url.json",
        base_backup(
            [
                {
                    "metadata": {
                        "title": "Backup Copy A",
                        "f95_url": "https://f95zone.to/threads/old-slug.555/",
                        "version": "2.0",
                    },
                    "user_state": {"progress": "From copy A"},
                    "launch_targets": [
                        {"label": "Target A", "exe_path": "/backup/a.sh"}
                    ],
                },
                {
                    "metadata": {
                        "title": "Backup Copy B",
                        "f95_url": url,
                        "version": "3.0",
                    },
                    "user_state": {"progress": "From copy B"},
                    "launch_targets": [
                        {"label": "Target B", "exe_path": "/backup/b.sh"}
                    ],
                },
                {
                    "metadata": {
                        "title": "Backup Copy C",
                        "f95_url": "https://f95zone.to/threads/duplicate.555/page-3/",
                    },
                    "user_state": {"progress": "From copy C"},
                },
            ]
        ),
    )

    result = api.import_library_backup(str(backup_path))

    assert result["success"] is True
    games = get_all_games()
    # The duplicated rows merge onto the one game that owns the thread.
    assert [game["id"] for game in games] == [local_id]
    assert games[0]["version"] == "3.0"
    assert games[0]["progress"] == "From copy C"
    targets = list_game_launch_targets(local_id)
    assert [target["game_id"] for target in targets] == [local_id]
    assert [target["label"] for target in targets] == ["Target B"]


def test_import_merges_duplicate_new_rows_added_within_one_backup(tmp_path):
    """A backup that repeats a URL that is not local must import one row."""
    api = Api()
    url = "https://f95zone.to/threads/repeated.556/"
    backup_path = write_backup(
        tmp_path / "repeated-new.json",
        base_backup(
            [
                {
                    "metadata": {"title": "First", "f95_url": url},
                    "user_state": {"progress": "First"},
                },
                {
                    "metadata": {"title": "Second", "f95_url": url},
                    "user_state": {"progress": "Second"},
                },
            ]
        ),
    )

    result = api.import_library_backup(str(backup_path))

    assert result["success"] is True
    assert result["created"] == 1
    games = get_all_games()
    assert len(games) == 1
    assert games[0]["progress"] == "Second"
    assert list_game_launch_targets(games[0]["id"]) == []


def test_import_duplicate_url_fill_missing_keeps_local_values(tmp_path):
    """fill-missing must still see local values for a duplicate-URL row."""
    api = Api()
    url = "https://f95zone.to/threads/fill-dup.557/"
    game_id = add_game("Local", "/local.exe", f95_url=url)
    assert game_id is not None
    conn = get_connection()
    conn.execute(
        "UPDATE games SET playtime_seconds = ?, progress = ? WHERE id = ?",
        (100, "Local progress", game_id),
    )
    conn.commit()
    conn.close()

    backup_path = write_backup(
        tmp_path / "fill-dup.json",
        base_backup(
            [
                {
                    "metadata": {"title": "Backup", "f95_url": url},
                    "user_state": {
                        "progress": "Backup progress",
                        "playtime_seconds": 5,
                    },
                }
            ]
        ),
    )

    result = api.import_library_backup(str(backup_path), {"merge_mode": "fill_missing"})

    assert result["success"] is True
    game = get_all_games()[0]
    assert game["id"] == game_id
    assert game["progress"] == "Local progress"
    assert game["playtime_seconds"] == 100


def test_import_oversized_playtime_value_is_clamped(tmp_path):
    """A huge numeric string must be clamped, not raise OverflowError."""
    api = Api()
    url = "https://f95zone.to/threads/huge.558/"
    backup_path = write_backup(
        tmp_path / "huge.json",
        base_backup(
            [
                {
                    "metadata": {"title": "Huge", "f95_url": url},
                    "user_state": {"playtime_seconds": "9" * 5000},
                }
            ]
        ),
    )

    result = api.import_library_backup(
        str(backup_path), {"sections": [SECTION_USER_STATE]}
    )

    assert result["success"] is True
    game = get_all_games()[0]
    assert game["playtime_seconds"] == 0


def test_validate_accepts_integer_valued_format_versions():
    from core.library_backup import validate_library_backup

    assert (
        validate_library_backup(
            {"format": BACKUP_FORMAT, "format_version": 1.0, "games": []}
        )["format_version"]
        == 1.0
    )
    assert (
        validate_library_backup(
            {"format": BACKUP_FORMAT, "format_version": "1.0", "games": []}
        )["format_version"]
        == "1.0"
    )
    with pytest.raises(BackupValidationError):
        validate_library_backup(
            {"format": BACKUP_FORMAT, "format_version": 1.5, "games": []}
        )
    with pytest.raises(BackupValidationError):
        validate_library_backup(
            {"format": BACKUP_FORMAT, "format_version": True, "games": []}
        )


def test_import_settings_preserves_json_types(tmp_path):
    """Native JSON settings must be stored JSON-encoded, not Python-repr."""
    api = Api()
    update_setting("collections", "[]")
    update_setting("custom_play_statuses", "[]")
    update_setting("library_filter_presets", "[]")

    backup_path = write_backup(
        tmp_path / "typed-settings.json",
        {
            **base_backup([]),
            "settings": {
                "general": {
                    "collections": ["Backlog", "Best of"],
                    "custom_play_statuses": ["Backlog"],
                    "library_filter_presets": [
                        {
                            "name": "Ren'Py now",
                            "filterCollection": "Favorites",
                            "filterStatuses": ["Playing"],
                            "filterInstallState": "installed",
                            "filterEngines": ["Ren'Py"],
                            "filterTags": ["dating-sim"],
                            "sortBy": "rating",
                            "sortDir": "desc",
                        }
                    ],
                    "enable_logging": True,
                    "auto_update_check": "hourly",
                }
            },
        },
    )

    result = api.import_library_backup(
        str(backup_path), {"sections": [SECTION_SETTINGS_GENERAL]}
    )

    assert result["success"] is True
    assert get_collections() == ["Backlog", "Best of"]
    settings = api.get_settings()
    assert settings["custom_play_statuses"] == ["Backlog"]
    assert settings["library_filter_presets"] == [
        {
            "name": "Ren'Py now",
            "filterCollection": "Favorites",
            "filterStatuses": ["Playing"],
            "filterInstallState": "installed",
            "filterEngines": ["Ren'Py"],
            "filterTags": ["dating-sim"],
            "sortBy": "rating",
            "sortDir": "desc",
        }
    ]
    assert settings["enable_logging"] is True
    assert get_setting("auto_update_check") == "hourly"


def test_import_settings_normalizes_boolean_strings(tmp_path):
    """Mixed-case booleans must be stored as the lowercase "true"/"false"."""
    api = Api()
    backup_path = write_backup(
        tmp_path / "bool-settings.json",
        {
            **base_backup([]),
            "settings": {
                "general": {"enable_logging": "TRUE"},
                "paths": {"download_detection_enabled": "False"},
            },
        },
    )

    result = api.import_library_backup(
        str(backup_path),
        {"sections": [SECTION_SETTINGS_GENERAL, SECTION_SETTINGS_PATHS]},
    )

    assert result["success"] is True
    assert api.get_settings()["enable_logging"] is True
    assert api.get_settings()["download_detection_enabled"] is False


def test_import_merges_new_rows_for_one_thread_under_different_slugs(tmp_path):
    """Copies of one non-local thread under old and new slugs import once."""
    api = Api()
    backup_path = write_backup(
        tmp_path / "renamed-thread.json",
        base_backup(
            [
                {
                    "metadata": {
                        "title": "Old Name",
                        "f95_url": "https://f95zone.to/threads/old-slug.557/",
                    },
                },
                {
                    "metadata": {
                        "title": "New Name",
                        "f95_url": "https://f95zone.to/threads/new-slug.557/",
                    },
                },
            ]
        ),
    )

    result = api.import_library_backup(str(backup_path))

    assert result["success"] is True
    assert result["created"] == 1
    games = get_all_games()
    assert len(games) == 1
    assert games[0]["title"] == "New Name"


def test_import_duplicate_url_fill_missing_keeps_targets_from_same_import(tmp_path):
    """fill-missing must see launch targets written earlier in the import."""
    api = Api()
    url = "https://f95zone.to/threads/targets.558/"
    local_id = add_game("Local", "/local/game.sh", f95_url=url)
    assert local_id is not None
    backup = base_backup(
        [
            {
                "metadata": {"title": "First", "f95_url": url},
                "launch_targets": [{"label": "First", "exe_path": "/b/first.sh"}],
            },
            {
                "metadata": {"title": "Second", "f95_url": url},
                "launch_targets": [{"label": "Second", "exe_path": "/b/second.sh"}],
            },
        ]
    )
    backup_path = write_backup(tmp_path / "targets.json", backup)

    result = api.import_library_backup(
        str(backup_path), {"merge_mode": "fill_missing"}
    )

    assert result["success"] is True
    targets = list_game_launch_targets(local_id)
    assert [target["label"] for target in targets] == ["First"]


def test_import_out_of_range_playtime_is_clamped(tmp_path):
    """A finite value beyond SQLite's INTEGER range must not abort the import."""
    api = Api()
    url = "https://f95zone.to/threads/huge.559/"
    backup_path = write_backup(
        tmp_path / "huge.json",
        base_backup(
            [
                {
                    "metadata": {"title": "Huge", "f95_url": url},
                    "user_state": {"playtime_seconds": 10**30},
                }
            ]
        ),
    )

    result = api.import_library_backup(
        str(backup_path), {"sections": [SECTION_USER_STATE]}
    )

    assert result["success"] is True
    game = get_all_games()[0]
    assert game["playtime_seconds"] == 0


def test_screenshots_export_and_import_round_trip(tmp_path):
    import json as json_module

    from core.database import get_connection as _get_connection

    api = Api()
    url = "https://f95zone.to/threads/shots-backup.300/"
    game_id = add_game("Shots Backup", "/tmp/shots.exe", f95_url=url)
    assert game_id is not None
    conn = _get_connection()
    conn.execute(
        "UPDATE games SET screenshots = ? WHERE id = ?",
        (
            json_module.dumps(
                ["https://example.com/a.png", "https://example.com/b.png"]
            ),
            game_id,
        ),
    )
    conn.commit()
    conn.close()

    destination = tmp_path / "shots.json"
    exported = api.export_library_backup({}, str(destination))
    assert exported["success"] is True
    backup = json_module.loads(destination.read_text(encoding="utf-8"))
    assert backup["games"][0]["metadata"]["screenshots"] == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]

    conn = _get_connection()
    conn.execute("UPDATE games SET screenshots = '[]' WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    result = api.import_library_backup(str(destination))
    assert result["success"] is True
    assert get_all_games()[0]["screenshots"] == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]


def test_screenshots_fill_missing_and_missing_field(tmp_path):
    import json as json_module

    api = Api()
    empty_url = "https://f95zone.to/threads/empty.301/"
    full_url = "https://f95zone.to/threads/full.302/"
    empty_id = add_game("Empty", "/tmp/empty.exe", f95_url=empty_url)
    full_id = add_game("Full", "/tmp/full.exe", f95_url=full_url)
    assert empty_id is not None and full_id is not None
    conn = get_connection()
    conn.execute(
        "UPDATE games SET screenshots = ? WHERE id = ?",
        (json_module.dumps(["https://example.com/kept.png"]), full_id),
    )
    conn.commit()
    conn.close()

    payload = base_backup(
        [
            {
                "metadata": {
                    "title": "Empty",
                    "f95_url": empty_url,
                    "screenshots": ["https://example.com/filled.png"],
                }
            },
            {
                "metadata": {
                    "title": "Full",
                    "f95_url": full_url,
                    "screenshots": ["https://example.com/new.png"],
                }
            },
        ]
    )
    path = write_backup(tmp_path / "fill.json", payload)
    result = api.import_library_backup(str(path), {"merge_mode": "fill_missing"})
    assert result["success"] is True
    games = {game["title"]: game for game in get_all_games()}
    assert games["Empty"]["screenshots"] == ["https://example.com/filled.png"]
    assert games["Full"]["screenshots"] == ["https://example.com/kept.png"]

    older = base_backup(
        [{"metadata": {"title": "Full", "f95_url": full_url}}]
    )
    older_path = write_backup(tmp_path / "older.json", older)
    result = api.import_library_backup(str(older_path))
    assert result["success"] is True
    assert get_all_games()[1]["screenshots"] == ["https://example.com/kept.png"]


def test_screenshots_malformed_values_dropped(tmp_path):
    from core.library_backup import _screenshots_json_text

    assert _screenshots_json_text('["https://example.com/a.png", 7, "", "ftp://x/y"]') == '["https://example.com/a.png"]'
    assert _screenshots_json_text("not json") == "[]"
    many = [f"https://example.com/{i}.png" for i in range(25)]
    assert len(json.loads(_screenshots_json_text(many))) == 20

    api = Api()
    url = "https://f95zone.to/threads/malformed.303/"
    game_id = add_game("Malformed", "/tmp/mal.exe", f95_url=url)
    assert game_id is not None
    payload = base_backup(
        [
            {
                "metadata": {
                    "title": "Malformed",
                    "f95_url": url,
                    "screenshots": ["not a url", 7, "https://example.com/ok.png"],
                }
            }
        ]
    )
    path = write_backup(tmp_path / "mal.json", payload)
    assert api.import_library_backup(str(path))["success"] is True
    assert get_all_games()[0]["screenshots"] == ["https://example.com/ok.png"]


def test_mirror_apply_prunes_deleted_game_attachments(tmp_path, monkeypatch):
    from core import database
    from core.game_attachments import add_attachment, attachments_dir, list_attachments

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(database, "DATA_DIR", str(data_dir))

    keep_url = "https://f95zone.to/threads/keep.500/"
    gone_url = "https://f95zone.to/threads/gone.501/"
    keep_id = add_game("Keep", "/keep.exe", f95_url=keep_url)
    gone_id = add_game("Gone", "/gone.exe", f95_url=gone_url)
    assert keep_id is not None and gone_id is not None

    keep_source = tmp_path / "keep-guide.pdf"
    keep_source.write_bytes(b"%PDF-1.4 keep")
    gone_source = tmp_path / "gone-guide.pdf"
    gone_source.write_bytes(b"%PDF-1.4 gone")
    assert add_attachment(keep_id, str(keep_source))["name"] == "keep-guide.pdf"
    assert add_attachment(gone_id, str(gone_source))["name"] == "gone-guide.pdf"

    payload = base_backup([{"metadata": {"title": "Keep", "f95_url": keep_url}}])
    result = apply_library_backup(
        payload, {"sections": SYNC_SECTIONS}, mirror=True, sync=True
    )

    assert result["deleted"] == 1
    import os

    assert not os.path.exists(attachments_dir(gone_id))
    assert [item["name"] for item in list_attachments(keep_id)] == ["keep-guide.pdf"]


def test_export_carries_notes_but_not_attachments(tmp_path, monkeypatch):
    from core import database
    from core.game_attachments import add_attachment

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(database, "DATA_DIR", str(data_dir))

    api = Api()
    game_id = add_game("Noted", "/noted.exe")
    assert game_id is not None
    update_game(game_id, {"progress": "# My notes\nSome **markdown**"})
    source = tmp_path / "walkthrough.pdf"
    source.write_bytes(b"%PDF-1.4 walkthrough contents")
    assert add_attachment(game_id, str(source))["name"] == "walkthrough.pdf"

    destination = tmp_path / "export.json"
    result = api.export_library_backup({}, str(destination))
    assert result["success"] is True
    exported_text = destination.read_text(encoding="utf-8")
    exported = json.loads(exported_text)
    assert exported["games"][0]["user_state"]["progress"] == "# My notes\nSome **markdown**"
    assert "walkthrough.pdf" not in exported_text
    assert "walkthrough contents" not in exported_text
    assert "%PDF" not in exported_text


def test_journal_export_import_and_older_reader(tmp_path, monkeypatch):
    from core import database, library_backup

    game_id = add_game("Journal", "/game", version="0.7")
    session_id = database.start_play_session(game_id, "0.7")
    database.record_play_tick(game_id, session_id, 300, True)
    update_game(game_id, {"skipped_version": "0.8"})
    backup = build_library_backup()
    state = backup["games"][0]["user_state"]
    assert state["last_played_version"] == "0.7"
    assert state["skipped_version"] == "0.8"
    assert len(state["play_sessions"]) == 1
    assert set(state["play_sessions"][0]) == {
        "version",
        "started_at",
        "ended_at",
        "duration_seconds",
    }
    assert "user_state" not in build_library_backup({"sections": []})["games"][0]
    path = write_backup(tmp_path / "journal.json", backup)
    database.record_play_tick(
        game_id, database.start_play_session(game_id, "0.7"), 60, True
    )
    assert Api().import_library_backup(str(path))["success"]
    assert len(database.get_play_sessions(game_id)) == 1

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "fresh.db"))
    init_db()
    assert Api().import_library_backup(str(path))["success"]
    imported = get_all_games()[0]
    assert imported["last_played_version"] == "0.7"
    assert database.get_play_sessions(imported["id"])[0]["duration_seconds"] == 300

    # The previous reader only picks its known scalar fields from user_state.
    monkeypatch.setattr(
        library_backup,
        "USER_STATE_FIELDS",
        tuple(
            field
            for field in library_backup.USER_STATE_FIELDS
            if field not in ("last_played_version", "skipped_version")
        ),
    )
    fields = library_backup._import_fields_for_game(
        backup["games"][0], {SECTION_USER_STATE}, include_metadata=False
    )
    assert not {"play_sessions", "last_played_version", "skipped_version"}.intersection(
        fields
    )
    assert fields["playtime_seconds"] == 300


@pytest.mark.parametrize("older,select_user_state", [(True, True), (False, False)])
def test_journal_missing_fields_or_unselected_state_preserve_local(
    tmp_path, older, select_user_state
):
    from core import database

    game_id = add_game("Journal", "/game", version="0.7")
    session = database.start_play_session(game_id, "0.7")
    database.record_play_tick(game_id, session, 300, True)
    update_game(game_id, {"skipped_version": "0.8"})
    original = database.get_play_sessions(game_id)
    backup = build_library_backup()
    state = backup["games"][0]["user_state"]
    if older:
        for field in ("play_sessions", "last_played_version", "skipped_version"):
            del state[field]
    else:
        state.update(play_sessions=[], last_played_version="", skipped_version="")
    path = write_backup(tmp_path / "old.json", backup)
    options = {
        "sections": [SECTION_USER_STATE] if select_user_state else [SECTION_METADATA]
    }
    assert Api().import_library_backup(str(path), options)["success"]
    game = get_all_games()[0]
    assert game["last_played_version"] == "0.7"
    assert game["skipped_version"] == "0.8"
    assert database.get_play_sessions(game_id) == original


def test_invalid_journal_rolls_back_import(tmp_path):
    game_id = add_game("Journal", "/game")
    backup = build_library_backup()
    backup["games"][0]["metadata"]["title"] = "Changed"
    backup["games"][0]["user_state"]["play_sessions"] = [{"duration_seconds": -1}]
    path = write_backup(tmp_path / "invalid-journal.json", backup)
    assert not Api().import_library_backup(str(path))["success"]
    assert get_all_games()[0]["id"] == game_id
    assert get_all_games()[0]["title"] == "Journal"
