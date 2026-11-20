# SPDX-License-Identifier: GPL-3.0-or-later
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from core.external_import import read_external_library


def test_xlibrary_promotes_first_compatible_launch_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.external_import.is_windows", lambda: False)
    source = tmp_path / "xlibrary.json"
    source.write_text(
        json.dumps(
            {
                "games": [
                    {
                        "name": "Local Game",
                        "launchSettings": {
                            "defaultConfigurationId": "windows",
                            "configurations": [
                                {
                                    "id": "windows",
                                    "executablePath": r"C:\Games\Game.exe",
                                },
                                {
                                    "id": "linux",
                                    "executablePath": "/games/Play.sh",
                                },
                                {
                                    "name": "Bonus",
                                    "executablePath": "/games/Bonus.sh",
                                },
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    backup, stats = read_external_library("xlibrary", str(source))

    assert stats["skipped_no_identity"] == 0
    assert stats["dropped_paths"] == 1
    (game,) = cast(list[dict[str, object]], backup["games"])
    assert game["executable_paths"] == {"exe_path": "/games/Play.sh"}
    assert game["launch_targets"] == [
        {"label": "Bonus", "exe_path": "/games/Bonus.sh"},
    ]


def test_atlas_promotes_first_compatible_launch_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.external_import.is_windows", lambda: False)
    source = tmp_path / "atlas.db"
    with closing(sqlite3.connect(source)) as conn, conn:
        _ = conn.execute(
            "CREATE TABLE games (record_id INTEGER PRIMARY KEY, title TEXT, "
            "selected_version_id INTEGER)"
        )
        _ = conn.execute(
            "CREATE TABLE versions (record_id INTEGER, version TEXT, game_path TEXT, "
            "exec_path TEXT)"
        )
        _ = conn.execute("INSERT INTO games VALUES (1, 'Local Game', 1)")
        _ = conn.executemany(
            "INSERT INTO versions VALUES (?, ?, ?, ?)",
            [
                (1, "3.0", r"C:\Games", "Game.exe"),
                (1, "1.0", "/games/v1", "Play.sh"),
                (1, "2.0", "/games/v2", "Play.sh"),
            ],
        )

    backup, stats = read_external_library("atlas", str(source))

    assert stats["skipped_no_identity"] == 0
    assert stats["dropped_paths"] == 1
    (game,) = cast(list[dict[str, object]], backup["games"])
    assert game["executable_paths"] == {"exe_path": "/games/v1/Play.sh"}
    assert game["launch_targets"] == [
        {"label": "v2.0", "exe_path": "/games/v2/Play.sh"},
    ]
