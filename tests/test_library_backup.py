# pyright: reportMissingImports=false
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
)


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
    assert cast(list[dict[str, object]], result["ambiguous_games"])[0]["title"] == "Ambiguous"

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

    result = api.import_library_backup(str(backup_path), {"sections": [SECTION_METADATA]})

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

    result = api.import_library_backup(str(backup_path), {"sections": [SECTION_USER_STATE]})

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

    result = api.import_library_backup(str(invalid_path), {"sections": [SECTION_USER_STATE]})

    assert result["success"] is False
    assert result["error_code"] == "invalid_json"
    games = get_all_games()
    assert len(games) == 1
    assert games[0]["title"] == "Safe"
