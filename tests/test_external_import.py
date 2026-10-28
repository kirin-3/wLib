# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for importing libraries from F95Checker, Atlas, XLibrary, and f69."""

import json
import os
import sqlite3
import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest

from core import host_platform
from core.api import Api
from core.database import add_game, get_all_games, init_db, update_setting
from core.external_import import (
    apply_path_rule,
    locate_external_library,
    parse_timestamp,
    read_external_library,
)
from core.library_backup import (
    import_external_library,
    inspect_external_library,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_wlib_external.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


@pytest.fixture(autouse=True)
def _force_linux_paths(monkeypatch):
    # Executable fixtures use Linux-absolute paths so results are identical
    # on Windows hosts and in the Linux container unless a test overrides this.
    monkeypatch.setattr("core.external_import.is_windows", lambda: False)


PLAYNITE_FIXTURE = Path(__file__).parent / "fixtures/playnite/library"


def _fingerprints(folder):
    return {
        str(p.relative_to(folder)): (
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in folder.rglob("*")
        if p.is_file()
    }


def test_playnite_fixture_mapping_and_unchanged():
    before = _fingerprints(PLAYNITE_FIXTURE)
    backup, stats = read_external_library(
        "playnite", str(PLAYNITE_FIXTURE / "games.db")
    )
    assert _fingerprints(PLAYNITE_FIXTURE) == before
    assert stats == {
        "skipped_no_identity": 1,
        "dropped_paths": 1,
        "schema_version": "4",
    }
    games = backup["games"]
    assert len(games) == 146
    novel = games[0]
    assert novel["metadata"] == {
        "title": "Fixture novel",
        "f95_url": "https://f95zone.to/threads/12345/",
        "links": [{"source": "vndb", "id": "v1234", "url": "https://vndb.org/v1234"}],
        "developer": "Fixture studio",
        "version": "0.9",
        "engine": "Ren'Py",
        "thread_completed": 1,
    }
    state = novel["user_state"]
    assert state["play_status"] == "Completed"
    assert state["playtime_seconds"] == 7200
    assert state["collections"] == ["Favorites VN", "Backlog"]
    assert state["progress"] == "Route A done"
    assert state["is_favorite"] is True
    assert state["last_played"].startswith("2025-01-02T03:04:05")
    assert state["date_added"].startswith("2024-01-02")
    assert all(
        state[f"rating_{key}"] == 4
        for key in ("story", "graphics", "gameplay", "fappability")
    )
    assert novel["executable_paths"]["exe_path"] == "/opt/games/novel/game.sh"
    assert novel["launch_targets"] == [
        {"label": "Play (32-bit)", "exe_path": "/opt/games/novel/game32.sh"}
    ]
    assert Path(novel["cover_file"]).is_file()
    assert "play_status" not in games[1]["user_state"]
    assert games[1]["metadata"]["f95_url"] == "https://f95zone.to/threads/23456/"
    assert games[2]["metadata"]["links"][0]["source"] == "dlsite"
    assert games[3]["metadata"]["cover_image_path"] == "https://example.com/cover.jpg"
    assert "executable_paths" not in games[3]
    assert "user_state" not in games[3]  # CommunityScore is never a personal rating.


@pytest.mark.parametrize(
    "version,message", [(3, "Update Playnite"), (5, "newer than wLib")]
)
def test_playnite_unsupported_version(tmp_path, version, message):
    shutil.copy2(PLAYNITE_FIXTURE / "games.db", tmp_path / "games.db")
    (tmp_path / "database.json").write_text(json.dumps({"Version": version}))
    with pytest.raises(ValueError, match=message):
        read_external_library("playnite", str(tmp_path / "games.db"))


def test_playnite_locked_wrong_file_and_permissions(tmp_path, monkeypatch):
    shutil.copy2(PLAYNITE_FIXTURE / "games.db", tmp_path / "games.db")
    journal = tmp_path / "games-journal.db"
    journal.touch()
    from core.backup_format import BackupValidationError

    with pytest.raises(BackupValidationError) as exc:
        read_external_library("playnite", str(tmp_path / "games.db"))
    assert exc.value.error_code == "source_locked"
    journal.unlink()
    monkeypatch.setattr(
        "core.external_import.shutil.copy2",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError()),
    )
    with pytest.raises(BackupValidationError) as exc:
        read_external_library("playnite", str(tmp_path / "games.db"))
    assert exc.value.error_code == "source_locked"
    wrong = tmp_path / "wrong.db"
    wrong.write_text("not a library")
    with pytest.raises(BackupValidationError):
        read_external_library("playnite", str(wrong))


def test_playnite_detection_and_no_linux_detection(tmp_path, monkeypatch):
    monkeypatch.setattr("core.external_import.is_windows", lambda: True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    folder = tmp_path / "Playnite"
    folder.mkdir()
    custom = folder / "custom"
    custom.mkdir()
    (custom / "games.db").touch()
    for path in ("%AppData%/Playnite/custom", "{PlayniteDir}/custom"):
        (folder / "config.json").write_text(json.dumps({"DatabasePath": path}))
        assert locate_external_library("playnite")["path"] == str(custom / "games.db")
    monkeypatch.setattr("core.external_import.is_windows", lambda: False)
    assert locate_external_library("playnite") == {
        "source": "playnite",
        "path": "",
        "checked": [],
    }


def test_playnite_never_reads_extension_settings(tmp_path, monkeypatch):
    import builtins

    folder = tmp_path / "Playnite"
    library = folder / "library"
    shutil.copytree(PLAYNITE_FIXTURE, library)
    for name in ("ExtensionsData", "Extensions"):
        (folder / name).mkdir()
        (folder / name / "config.json").write_text('{"cookies":"secret"}')
    original_open = builtins.open
    original_copy = shutil.copy2

    def guarded_open(file, *a, **k):
        assert "Extensions" not in str(file)
        return original_open(file, *a, **k)

    def guarded_copy(file, *a, **k):
        assert "Extensions" not in str(file)
        return original_copy(file, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(shutil, "copy2", guarded_copy)
    read_external_library("playnite", str(library / "games.db"))


def test_playnite_import_covers_sections_merge_rollback(tmp_path, monkeypatch):
    from core import database, game_covers
    from core.library_backup import build_library_backup, _available_backup_sections

    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path / "data"))
    preview = inspect_external_library("playnite", str(PLAYNITE_FIXTURE / "games.db"))
    assert preview["counts"]["covers_to_copy"] == 2
    assert "covers" in preview["available_sections"]
    assert "covers" not in _available_backup_sections(build_library_backup())
    result = import_external_library(
        "playnite", str(PLAYNITE_FIXTURE / "games.db"), {"sections": []}
    )
    assert result["created"] == 146
    assert not game_covers.covers_dir().exists()
    result = import_external_library("playnite", str(PLAYNITE_FIXTURE / "games.db"))
    assert result["covers_copied"] == 2
    names = {p.name for p in game_covers.covers_dir().iterdir()}
    result = import_external_library(
        "playnite", str(PLAYNITE_FIXTURE / "games.db"), {"merge_mode": "fill_missing"}
    )
    assert result["covers_copied"] == 0
    assert {p.name for p in game_covers.covers_dir().iterdir()} == names
    original = database.set_game_local_cover

    def fail_after_write(*a, **k):
        original(*a, **k)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(database, "set_game_local_cover", fail_after_write)
    with pytest.raises(RuntimeError, match="injected failure"):
        import_external_library("playnite", str(PLAYNITE_FIXTURE / "games.db"))
    assert {p.name for p in game_covers.covers_dir().iterdir()} == names
    assert {g["cover_local"] for g in get_all_games() if g["cover_local"]} == names


def test_playnite_action_working_dir_windows_and_fallback(tmp_path, monkeypatch):
    from core.external_import import _translate_playnite_game

    # Linux path rules are forced for this module, so use a POSIX install
    # folder rather than tmp_path, which is a drive path on Windows hosts.
    base = {
        "Name": "F95-12345",
        "InstallDirectory": "/opt/games/sakura",
        "GameActions": [
            {
                "Name": "Play",
                "Type": 0,
                "IsPlayAction": True,
                "Path": "run.sh",
                "WorkingDir": "bin",
            }
        ],
    }
    game, dropped = _translate_playnite_game(base, {}, str(tmp_path))
    assert game["executable_paths"]["exe_path"] == "/opt/games/sakura/bin/run.sh"
    assert dropped == 0
    monkeypatch.setattr("core.external_import.is_windows", lambda: True)
    base["InstallDirectory"] = "D:\\Games\\Sakura"
    base["GameActions"][0]["Path"] = "{InstallDir}\\Game.exe"
    game, _ = _translate_playnite_game(base, {}, str(tmp_path))
    assert game["executable_paths"]["exe_path"] == "D:\\Games\\Sakura\\Game.exe"
    # The launcher fallback needs a real folder, so use the host's own rules.
    monkeypatch.setattr("core.external_import.is_windows", lambda: os.name == "nt")
    base["InstallDirectory"] = str(tmp_path)
    base["GameActions"] = []
    monkeypatch.setattr(
        "core.external_import.find_launcher", lambda path: str(tmp_path / "run.sh")
    )
    game, _ = _translate_playnite_game(base, {}, str(tmp_path))
    assert game["executable_paths"]["exe_path"] == str(tmp_path / "run.sh")


def test_playnite_failed_new_import_removes_copies_and_rows(tmp_path, monkeypatch):
    from core import database, game_covers

    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path / "data"))
    original = database.set_game_local_cover

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("write failed")

    monkeypatch.setattr(database, "set_game_local_cover", write_then_fail)
    with pytest.raises(RuntimeError, match="write failed"):
        import_external_library("playnite", str(PLAYNITE_FIXTURE / "games.db"))
    assert get_all_games() == []
    assert list(game_covers.covers_dir().iterdir()) == []


@pytest.mark.parametrize("status,expected", [("Beaten", "Completed"), ("playing", "Playing"), ("Waiting For Update", "Waiting For Update"), ("Not Started", None), ("Played", None), ("Not Played", None), ("custom", None)])
def test_playnite_status_mapping(status, expected, tmp_path):
    from core.external_import import _translate_playnite_game

    game, _ = _translate_playnite_game(
        {"Name": "F95-12345", "CompletionStatusId": "test"},
        {"completionstatuses": {"test": status}}, str(tmp_path),
    )
    assert game.get("user_state", {}).get("play_status") == expected


def test_playnite_rejects_private_addon_paths_and_symlinks(tmp_path):
    private = tmp_path / "ExtensionsData"
    private.mkdir()
    source = private / "games.db"
    source.write_text('{"cookies":"secret"}')
    with pytest.raises(ValueError, match="library folder"):
        read_external_library("playnite", str(source))
    library = tmp_path / "library"
    library.mkdir()
    (library / "games.db").symlink_to(source)
    with pytest.raises(ValueError, match="library folder"):
        read_external_library("playnite", str(library / "games.db"))


class _InlineThread:
    def __init__(self, target, args=(), daemon=None):
        _ = daemon
        self._run = lambda: target(*args)

    def start(self):
        self._run()


# ---------------------------------------------------------------------------
# 1.3: check_all_updates with a game_ids filter
# ---------------------------------------------------------------------------


def test_check_all_updates_limits_to_given_ids(monkeypatch):
    api = Api()
    monkeypatch.setattr("core.api.threading.Thread", _InlineThread)
    monkeypatch.setattr(
        api.scraper, "get_thread_metadata", lambda *_a, **_k: {"success": False}
    )
    captured: list[list[str]] = []

    def fake_check_versions(urls, callback):
        captured.append(list(urls))
        for url in urls:
            callback(url, {"success": True, "version": "2.0"})
        return {}

    monkeypatch.setattr(api.scraper, "check_versions", fake_check_versions)

    alpha = api.add_game("Alpha", "/alpha.sh", "https://f95zone.to/threads/a.1/")
    beta = api.add_game("Beta", "/beta.sh", "https://f95zone.to/threads/b.2/")
    gamma = api.add_game("Gamma", "/gamma.sh", "https://f95zone.to/threads/c.3/")
    assert alpha["id"] is not None and beta["id"] is not None

    result = api.check_all_updates([int(str(alpha["id"])), int(str(gamma["id"]))])
    assert result["success"] is True
    assert result["total"] == 2
    assert len(captured) == 1
    assert "https://f95zone.to/threads/b.2/" not in captured[0]
    assert len(captured[0]) == 2

    result = api.check_all_updates()
    assert result["success"] is True
    assert result["total"] == 3
    assert len(captured) == 2
    assert len(captured[1]) == 3


# ---------------------------------------------------------------------------
# 2.1: shared helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host_is_windows", "path", "kept"),
    [
        (True, "D:\\Games\\x\\Game.exe", True),
        (True, "D:/Games/x/Game.exe", True),
        (True, "\\\\server\\share\\Game.exe", True),
        (True, "/home/user/game.sh", False),
        (True, "relative/Game.exe", False),
        (True, "", False),
        (False, "/home/user/game.sh", True),
        (False, "~/games/game.sh", True),
        (False, "D:\\Games\\x\\Game.exe", False),
        (False, "C:/Games/x/Game.exe", False),
        (False, "relative/Game.exe", False),
        (False, "", False),
    ],
)
def test_apply_path_rule_keeps_only_host_absolute_paths(
    monkeypatch, host_is_windows, path, kept
):
    monkeypatch.setattr("core.external_import.is_windows", lambda: host_is_windows)
    assert bool(apply_path_rule(path)) is kept


def test_parse_timestamp_handles_seconds_millis_and_iso():
    seconds = parse_timestamp(1700000000)
    assert "2023-11-14" in seconds and seconds.endswith("+00:00")
    assert parse_timestamp(1700000000000) == seconds
    assert parse_timestamp("1700000000") == seconds
    iso = parse_timestamp("2026-01-02T03:04:05Z")
    assert iso.endswith("+00:00") and "2026-01-02" in iso
    assert parse_timestamp(0) == ""
    assert parse_timestamp("") == ""
    assert parse_timestamp("bogus") == ""


def test_sqlite_snapshot_leaves_source_untouched(tmp_path):
    source = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(str(source))
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO games VALUES (1, 'Game')")
    conn.commit()
    conn.close()
    before_bytes = source.read_bytes()
    before_mtime = source.stat().st_mtime_ns

    backup, stats = read_external_library("f95checker", str(source))

    assert source.read_bytes() == before_bytes
    assert source.stat().st_mtime_ns == before_mtime
    assert len(backup["games"]) == 1
    assert stats["skipped_no_identity"] == 0


# ---------------------------------------------------------------------------
# 2.2: F95Checker reader
# ---------------------------------------------------------------------------


def _write_f95checker_db(
    path,
    games: list[dict[str, Any]],
    *,
    settings_base: dict[str, str] | None = None,
    with_playtime: bool = True,
) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE settings (default_exe_dir TEXT DEFAULT '{}')")
    conn.execute(
        "INSERT INTO settings (default_exe_dir) VALUES (?)",
        (json.dumps(settings_base or {}),),
    )
    columns = (
        "id INTEGER PRIMARY KEY, custom INTEGER DEFAULT NULL, "
        "name TEXT DEFAULT '', developer TEXT DEFAULT '', url TEXT DEFAULT '', "
        "version TEXT DEFAULT '', installed TEXT DEFAULT '', "
        "finished TEXT DEFAULT '', archived INTEGER DEFAULT 0, "
        "executables TEXT DEFAULT '[]', last_launched INTEGER DEFAULT 0, "
        "added_on INTEGER DEFAULT 0"
    )
    if with_playtime:
        columns += ", playtime REAL DEFAULT 0"
    conn.execute(f"CREATE TABLE games ({columns})")
    for game in games:
        keys = ", ".join(game.keys())
        placeholders = ", ".join("?" for _ in game)
        conn.execute(
            f"INSERT INTO games ({keys}) VALUES ({placeholders})",
            tuple(game.values()),
        )
    conn.commit()
    conn.close()


def test_f95checker_reader_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr("core.external_import.is_windows", lambda: False)
    source = tmp_path / "db.sqlite3"
    _write_f95checker_db(
        source,
        [
            {
                "id": 100,
                "custom": 0,
                "name": "Thread Game",
                "developer": "Dev",
                "url": "https://f95zone.to/threads/thread-game.100/",
                "version": "0.8",
                "installed": "0.7",
                "finished": "0.5",
                "archived": 0,
                "executables": json.dumps(["/games/thread/Game.sh"]),
                "last_launched": 1700000000,
                "added_on": 1690000000,
                "playtime": 3600.0,
            },
            {
                "id": 200,
                "custom": 0,
                "name": "Archived Game",
                "developer": "Dev",
                "url": "https://f95zone.to/threads/archived.200/",
                "version": "1.0",
                "installed": "1.0",
                "finished": "1.0",
                "archived": 1,
                "executables": json.dumps(["D:\\Games\\archived\\Game.exe"]),
                "last_launched": 0,
                "added_on": 0,
                "playtime": 0.0,
            },
            {
                "id": -1,
                "custom": 1,
                "name": "Custom game (-1)",
                "developer": "",
                "url": "",
                "version": "",
                "installed": "",
                "finished": "",
                "archived": 0,
                "executables": json.dumps([]),
                "last_launched": 0,
                "added_on": 0,
                "playtime": 0.0,
            },
        ],
    )
    backup, stats = read_external_library("f95checker", str(source))
    assert stats["skipped_no_identity"] == 1
    assert stats["dropped_paths"] == 1
    by_title = {game["metadata"]["title"]: game for game in backup["games"]}
    assert set(by_title) == {"Thread Game", "Archived Game"}

    thread = by_title["Thread Game"]
    assert thread["metadata"]["version"] == "0.7"
    assert thread["metadata"]["latest_version"] == "0.8"
    assert thread["user_state"]["finished_version"] == "0.5"
    assert thread["user_state"]["play_status"] == "Completed"
    assert thread["user_state"]["playtime_seconds"] == 3600
    assert "2023-11-14" in thread["user_state"]["last_played"]
    assert thread["executable_paths"]["exe_path"] == "/games/thread/Game.sh"

    archived = by_title["Archived Game"]
    assert archived["user_state"]["play_status"] == "Abandoned"
    assert "executable_paths" not in archived


def test_f95checker_reader_relative_exe_on_windows_base(tmp_path, monkeypatch):
    monkeypatch.setattr("core.external_import.is_windows", lambda: True)
    source = tmp_path / "db.sqlite3"
    _write_f95checker_db(
        source,
        [
            {
                "id": 300,
                "custom": 0,
                "name": "Relative Game",
                "developer": "",
                "url": "https://f95zone.to/threads/relative.300/",
                "version": "",
                "installed": "0.1",
                "finished": "",
                "archived": 0,
                "executables": json.dumps(["Sakura/Game.exe", "Sakura/Bonus.exe"]),
                "last_launched": 0,
                "added_on": 0,
            }
        ],
        settings_base={"1": "D:\\Spice", "2": "/home/user/spice"},
        with_playtime=False,
    )
    backup, stats = read_external_library("f95checker", str(source))
    assert stats["dropped_paths"] == 0
    game = backup["games"][0]
    assert "Spice" in game["executable_paths"]["exe_path"]
    assert game["executable_paths"]["exe_path"].endswith("Game.exe")
    assert len(game["launch_targets"]) == 1
    assert game["launch_targets"][0]["exe_path"].endswith("Bonus.exe")
    assert "playtime_seconds" not in game.get("user_state", {})


def test_f95checker_reader_rejects_wrong_file(tmp_path):
    source = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(str(source))
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="not an F95Checker library"):
        read_external_library("f95checker", str(source))


# ---------------------------------------------------------------------------
# 2.3: XLibrary reader
# ---------------------------------------------------------------------------


def _xlink(thread_id: int) -> dict[str, Any]:
    return {
        "providerId": "f95zone",
        "externalId": str(thread_id),
        "url": f"https://f95zone.to/threads/game.{thread_id}/",
        "addedAt": "2026-01-01T00:00:00.000Z",
    }


def _xgame(
    name: str,
    *,
    links: list[dict[str, Any]] | None = None,
    exe: str = "",
    extra_exe: str = "",
    completion: str = "In Progress",
    playtime: int = 0,
    developer: str = "Dev",
) -> dict[str, Any]:
    configs: list[dict[str, Any]] = []
    if exe:
        configs.append(
            {"id": "default", "name": "Main", "type": "exe", "executablePath": exe}
        )
    if extra_exe:
        configs.append(
            {"id": "extra", "name": "Bonus", "type": "exe", "executablePath": extra_exe}
        )
    game: dict[str, Any] = {
        "id": f"uuid-{name}",
        "name": name,
        "developer": developer,
        "externalLinks": links if links is not None else [],
        "completionStatus": completion,
        "totalPlaytime": playtime,
        "createdAt": "2026-02-01T00:00:00.000Z",
        "lastPlayedAt": "2026-03-01T00:00:00.000Z",
        "categoryRatings": {"story": 4, "graphics": 3, "gameplay": 5},
    }
    if configs:
        game["launchSettings"] = {
            "configurations": configs,
            "defaultConfigurationId": "default",
        }
    return game


def _write_xlibrary_live(path, games: list[dict[str, Any]]) -> None:
    payload = {
        "family": "library.games",
        "schemaVersion": 9,
        "documents": [
            {
                "id": game["id"],
                "family": "library.games",
                "schemaVersion": 9,
                "createdAt": "2026-02-01T00:00:00.000Z",
                "updatedAt": "2026-03-01T00:00:00.000Z",
                "data": game,
                "revision": "rev",
            }
            for game in games
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_xlibrary_reader_live_shapes_and_skip(tmp_path):
    source = tmp_path / "library.games.json"
    steam_only = _xgame("Steam Only", developer="jutsu games")
    steam_only["externalLinks"] = [
        {
            "providerId": "steam",
            "externalId": "503560",
            "url": "https://store.steampowered.com/app/503560/",
            "addedAt": "2026-10-12T20:05:07.221Z",
        }
    ]
    steam_only["launchSettings"] = {
        "configurations": [
            {
                "id": "steam-id",
                "name": "Steam",
                "type": "steam",
                "steamAppId": "503560",
                "steamUrl": "steam://rungameid/503560",
            }
        ],
        "defaultConfigurationId": "steam-id",
    }
    f95_game = _xgame(
        "F95 Game",
        links=[_xlink(500)],
        exe="/games/f95/Game.sh",
        extra_exe="/games/f95/Bonus.sh",
        completion="In Queue",
        playtime=90,
    )
    # The URL thread id wins over a conflicting externalId.
    conflicted = _xgame("Conflicted", links=[_xlink(600)], exe="/games/c/Game.sh")
    conflicted["externalLinks"][0]["externalId"] = "999"
    _write_xlibrary_live(source, [steam_only, f95_game, conflicted])

    backup, stats = read_external_library("xlibrary", str(source))
    assert stats["skipped_no_identity"] == 1
    assert stats["schema_version"] == 9
    by_title = {game["metadata"]["title"]: game for game in backup["games"]}
    assert set(by_title) == {"F95 Game", "Conflicted"}

    f95 = by_title["F95 Game"]
    assert f95["metadata"]["f95_url"] == "https://f95zone.to/threads/500/"
    assert f95["user_state"]["play_status"] == "Plan to Play"
    assert f95["user_state"]["playtime_seconds"] == 90
    assert f95["user_state"]["rating_story"] == 4.0
    assert "rating" not in f95["user_state"]
    assert f95["executable_paths"]["exe_path"] == "/games/f95/Game.sh"
    assert f95["launch_targets"] == [
        {"label": "Bonus", "exe_path": "/games/f95/Bonus.sh"}
    ]
    assert by_title["Conflicted"]["metadata"]["f95_url"] == (
        "https://f95zone.to/threads/600/"
    )


def test_xlibrary_reader_dated_export_and_family_rejection(tmp_path):
    dated = tmp_path / "xlibrary-data-2026-01-01.json"
    dated.write_text(
        json.dumps(
            {
                "games": [_xgame("Exported", links=[_xlink(700)])],
                "settings": {"schemaVersion": 4},
            }
        ),
        encoding="utf-8",
    )
    backup, stats = read_external_library("xlibrary", str(dated))
    assert len(backup["games"]) == 1
    assert stats["schema_version"] == 4

    wrong = tmp_path / "wrong.json"
    wrong.write_text(
        json.dumps({"family": "library.other", "schemaVersion": 9, "documents": []}),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="not an XLibrary library"):
        read_external_library("xlibrary", str(wrong))


# ---------------------------------------------------------------------------
# 2.4: Atlas reader
# ---------------------------------------------------------------------------


def _write_atlas_db(path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE games (record_id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
        "creator TEXT NOT NULL, engine TEXT, last_played_r INTEGER DEFAULT 0, "
        "total_playtime INTEGER DEFAULT 0, date_added INTEGER DEFAULT 0, "
        "playstate TEXT, selected_version_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE versions (record_id INTEGER, version TEXT, game_path TEXT, "
        "exec_path TEXT, date_added INTEGER, playstate TEXT)"
    )
    conn.execute("CREATE TABLE f95_zone_mappings (record_id INTEGER, f95_id INTEGER)")
    conn.execute(
        "CREATE TABLE game_personal_ratings (record_id INTEGER PRIMARY KEY, "
        "story INTEGER, graphics INTEGER, gameplay INTEGER)"
    )
    conn.execute(
        "INSERT INTO games VALUES (1, 'Atlas Game', 'Creator', 'Unity', 1700000000, "
        "90, 1690000000, 'dropped', NULL)"
    )
    conn.execute(
        "INSERT INTO versions VALUES (1, '0.1', '/games/atlas', 'Game-v1.sh', "
        "1690000000, 'played')"
    )
    conn.execute(
        "INSERT INTO versions VALUES (1, '0.2', '/games/atlas', 'Game-v2.sh', "
        "1695000000, 'played')"
    )
    conn.execute("INSERT INTO f95_zone_mappings VALUES (1, 800)")
    conn.execute("INSERT INTO game_personal_ratings VALUES (1, 8, 7, 9)")
    conn.execute(
        "INSERT INTO games VALUES (2, 'Loose Files', 'Someone', '', 0, 0, 0, "
        "'planned', NULL)"
    )
    conn.execute(
        "INSERT INTO versions VALUES (2, '1.0', '/games/loose', 'Play.sh', 0, '')"
    )
    conn.commit()
    conn.close()


def test_atlas_reader_mapping(tmp_path):
    source = tmp_path / "data.db"
    _write_atlas_db(source)
    backup, stats = read_external_library("atlas", str(source))
    assert stats["skipped_no_identity"] == 0
    assert stats["dropped_paths"] == 0
    by_title = {game["metadata"]["title"]: game for game in backup["games"]}
    assert set(by_title) == {"Atlas Game", "Loose Files"}

    game = by_title["Atlas Game"]
    assert game["metadata"]["f95_url"] == "https://f95zone.to/threads/800/"
    assert game["metadata"]["version"] == "0.2"
    assert "latest_version" not in game["metadata"]
    assert game["user_state"]["play_status"] == "Abandoned"
    assert game["user_state"]["playtime_seconds"] == 5400
    assert game["user_state"]["rating_story"] == 8.0
    assert game["executable_paths"]["exe_path"] == "/games/atlas/Game-v2.sh"
    assert game["launch_targets"] == [
        {"label": "v0.1", "exe_path": "/games/atlas/Game-v1.sh"}
    ]

    loose = by_title["Loose Files"]
    assert "f95_url" not in loose["metadata"]
    assert loose["user_state"]["play_status"] == "Plan to Play"
    assert loose["executable_paths"]["exe_path"] == "/games/loose/Play.sh"


# ---------------------------------------------------------------------------
# f69 reader
# ---------------------------------------------------------------------------


@pytest.fixture
def _host_paths(monkeypatch):
    # f69 fixtures point at real temp folders, so keep the host's own rule.
    monkeypatch.setattr("core.external_import.is_windows", host_platform.is_windows)


def _write_f69_db(path, install_root) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE _schema_version (id INTEGER PRIMARY KEY, hash TEXT NOT NULL,
          applied_at INTEGER NOT NULL);
        CREATE TABLE games (f95_thread_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
          developer TEXT, completion_status TEXT NOT NULL DEFAULT 'not_started',
          engine TEXT NOT NULL DEFAULT 'unknown', latest_version TEXT,
          default_install_id TEXT, last_played_at INTEGER,
          total_playtime_s INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
          dev_status TEXT NOT NULL DEFAULT 'unknown');
        CREATE TABLE installs (id TEXT PRIMARY KEY, game_thread_id INTEGER NOT NULL,
          version TEXT NOT NULL, install_path TEXT NOT NULL UNIQUE, executable TEXT,
          launch_args TEXT, recipe_id TEXT NOT NULL, installed_at INTEGER NOT NULL,
          name TEXT, source TEXT NOT NULL DEFAULT 'recipe', archive_sha256 TEXT);
        CREATE TABLE user_labels (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
          color TEXT);
        CREATE TABLE game_labels (game_thread_id INTEGER NOT NULL,
          label_id INTEGER NOT NULL, PRIMARY KEY (game_thread_id, label_id));
        INSERT INTO _schema_version VALUES (1, 'a', 0), (23, 'b', 0);
        INSERT INTO user_labels VALUES (1, 'Favorites', NULL), (2, 'Backlog', NULL);
        """
    )
    v1 = install_root / "Game-0.1"
    v2 = install_root / "Game-0.2"
    custom = install_root / "Custom"
    for folder in (v1, v2, custom):
        folder.mkdir(parents=True)
    (v1 / "Game.sh").write_text("#!/bin/sh\n")
    (v2 / "Game.sh").write_text("#!/bin/sh\n")
    (custom / "Start.exe").write_bytes(b"MZ")
    conn.execute(
        "INSERT INTO games VALUES (900, 'f69 Game', 'Dev', 'replaying', 'renpy', "
        "'0.3', 'inst-v2', 1700000000, 3600, 1690000000, 'completed')"
    )
    conn.execute(
        "INSERT INTO installs VALUES ('inst-v1', 900, '0.1', ?, NULL, NULL, 'r', "
        "1690000000, NULL, 'manual', NULL)",
        (str(v1),),
    )
    conn.execute(
        "INSERT INTO installs VALUES ('inst-v2', 900, '0.2', ?, NULL, NULL, 'r', "
        "1680000000, 'Modded', 'manual', NULL)",
        (str(v2),),
    )
    conn.execute("INSERT INTO game_labels VALUES (900, 1), (900, 2)")
    # Custom game: high-bit synthetic thread id stored as a negative integer.
    conn.execute(
        "INSERT INTO games VALUES (-42, 'My Custom', NULL, 'not_started', 'other', "
        "NULL, NULL, NULL, 0, 1690000000, 'unknown')"
    )
    conn.execute(
        "INSERT INTO installs VALUES ('inst-c', -42, '1.0', ?, 'Start.exe', NULL, "
        "'r', 1690000000, NULL, 'manual', NULL)",
        (str(custom),),
    )
    # Custom game with nothing launchable has no identity and is skipped.
    conn.execute(
        "INSERT INTO games VALUES (-7, 'Orphan', NULL, 'not_started', 'unknown', "
        "NULL, NULL, NULL, 0, 1690000000, 'unknown')"
    )
    conn.execute(
        "INSERT INTO installs VALUES ('inst-o', -7, '1.0', ?, 'missing.exe', NULL, "
        "'r', 1690000000, NULL, 'manual', NULL)",
        (str(install_root),),
    )
    conn.commit()
    conn.close()


def test_f69_reader_mapping(tmp_path, _host_paths):
    source = tmp_path / "f69.db"
    games_root = tmp_path / "games"
    _write_f69_db(source, games_root)
    backup, stats = read_external_library("f69", str(source))
    assert stats["skipped_no_identity"] == 1
    assert stats["dropped_paths"] == 1
    assert stats["schema_version"] == "23"
    assert backup["app"] == {"name": "f69"}
    by_title = {game["metadata"]["title"]: game for game in backup["games"]}
    assert set(by_title) == {"f69 Game", "My Custom"}

    game = by_title["f69 Game"]
    assert game["metadata"] == {
        "title": "f69 Game",
        "f95_url": "https://f95zone.to/threads/900/",
        "developer": "Dev",
        "engine": "Ren'Py",
        "version": "0.2",
        "latest_version": "0.3",
        "thread_completed": 1,
    }
    assert game["user_state"]["play_status"] == "Playing"
    assert game["user_state"]["playtime_seconds"] == 3600
    assert game["user_state"]["collections"] == ["Backlog", "Favorites"]
    assert game["user_state"]["last_played"].startswith("2023-11-14")
    assert game["executable_paths"]["exe_path"] == str(
        games_root / "Game-0.2" / "Game.sh"
    )
    assert game["launch_targets"] == [
        {"label": "v0.1", "exe_path": str(games_root / "Game-0.1" / "Game.sh")}
    ]

    custom = by_title["My Custom"]
    assert "f95_url" not in custom["metadata"]
    assert custom["metadata"]["engine"] == "Others"
    assert "play_status" not in custom.get("user_state", {})
    assert custom["executable_paths"]["exe_path"] == str(
        games_root / "Custom" / "Start.exe"
    )


def test_f69_reader_rejects_wrong_file(tmp_path):
    source = tmp_path / "other.db"
    conn = sqlite3.connect(str(source))
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="not an f69 library"):
        read_external_library("f69", str(source))


@pytest.mark.parametrize("default", ["false", "true"])
def test_external_import_uses_sandbox_default(tmp_path, default):
    update_setting("sandbox_new_games", default)
    source = tmp_path / "f95checker.db"
    _write_f95checker_db(source, [{"id": 123, "name": "Imported game"}])
    result = import_external_library("f95checker", str(source))
    assert result["created"] == 1
    assert get_all_games()[0]["sandbox_enabled"] == int(default == "true")


def test_f69_import_creates_games_with_collections(tmp_path, _host_paths):
    source = tmp_path / "f69.db"
    _write_f69_db(source, tmp_path / "games")
    result = import_external_library("f69", str(source))
    assert result["created"] == 2
    assert result["skipped_no_identity"] == 1
    games = {game["title"]: game for game in get_all_games()}
    assert set(games) == {"f69 Game", "My Custom"}
    imported = games["f69 Game"]
    assert imported["play_status"] == "Playing"
    assert imported["collections"] == ["Backlog", "Favorites"]


def test_locate_f69_library_uses_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    staged = tmp_path / "data" / "f69" / "f69.db"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"fake")
    found = locate_external_library("f69")
    assert found["path"] == str(staged)


# ---------------------------------------------------------------------------
# 2.5: locate_external_library
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host_is_windows", [False, True])
def test_locate_external_library_found_and_missing(
    tmp_path, monkeypatch, host_is_windows
):
    monkeypatch.setattr("core.external_import.is_windows", lambda: host_is_windows)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    if host_is_windows:
        staged = tmp_path / "f95checker" / "db.sqlite3"
    else:
        staged = tmp_path / ".config" / "f95checker" / "db.sqlite3"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"fake")

    found = locate_external_library("f95checker")
    assert found["path"] == str(staged)
    assert found["checked"]

    missing = locate_external_library("atlas")
    assert missing["path"] == ""
    assert missing["checked"]


# ---------------------------------------------------------------------------
# 3.1: inspect/import entry points
# ---------------------------------------------------------------------------


def _preview_fixture(path) -> None:
    games = []
    for thread_id in (11, 12, 13):
        games.append(_xgame(f"Matched {thread_id}", links=[_xlink(thread_id)]))
    for thread_id in range(21, 27):
        games.append(_xgame(f"New {thread_id}", links=[_xlink(thread_id)]))
    steam_only = _xgame("Steam Only")
    steam_only["externalLinks"] = [
        {
            "providerId": "steam",
            "externalId": "1",
            "url": "https://store.steampowered.com/app/1/",
            "addedAt": "2026-01-01T00:00:00.000Z",
        }
    ]
    games.append(steam_only)
    _write_xlibrary_live(path, games)


def test_inspect_external_library_preview_counts(tmp_path):
    for thread_id in (11, 12, 13):
        add_game(
            f"Existing {thread_id}",
            f"/existing/{thread_id}.sh",
            f95_url=f"https://f95zone.to/threads/game.{thread_id}/",
        )
    source = tmp_path / "library.games.json"
    _preview_fixture(source)

    preview = inspect_external_library("xlibrary", str(source))
    counts = preview["counts"]
    assert counts["new_games"] == 6
    assert counts["matched_games"] == 3
    assert counts["skipped_no_identity"] == 1
    assert counts["total_games"] == 10
    assert preview["schema_version"] == 9
    assert len(get_all_games()) == 3


def test_import_external_library_rolls_back_on_write_failure(tmp_path, monkeypatch):
    local_id = add_game("Safe", "/safe.sh")
    assert local_id is not None
    source = tmp_path / "library.games.json"
    _write_xlibrary_live(
        source,
        [_xgame("New A", links=[_xlink(901)]), _xgame("New B", links=[_xlink(902)])],
    )

    import sqlite3 as sqlite3_module
    from core import library_backup as backup_module

    calls = {"count": 0}
    real_insert = backup_module._insert_game

    def failing_insert(cursor, fields):
        calls["count"] += 1
        if calls["count"] == 2:
            raise sqlite3_module.IntegrityError("forced failure")
        return real_insert(cursor, fields)

    monkeypatch.setattr(backup_module, "_insert_game", failing_insert)
    with pytest.raises(sqlite3_module.IntegrityError):
        import_external_library("xlibrary", str(source), {"merge_mode": "fill_missing"})

    games = get_all_games()
    assert len(games) == 1
    assert games[0]["title"] == "Safe"


def test_overwrite_keeps_fields_the_source_does_not_track(tmp_path):
    from contextlib import closing

    from core.database import get_connection

    url = "https://f95zone.to/threads/foo.77/"
    local_id = add_game("Foo", "/old/foo.sh", f95_url=url, developer="Bar")
    assert local_id is not None
    with closing(get_connection()) as conn:
        conn.execute(
            "UPDATE games SET playtime_seconds = 18000 WHERE id = ?", (local_id,)
        )
        conn.commit()
    source = tmp_path / "db.sqlite3"
    # A hand-added F95Checker game: no thread URL, no playtime column.
    _write_f95checker_db(
        source,
        [
            {
                "id": -1,
                "custom": 1,
                "name": "Foo",
                "developer": "Bar",
                "executables": json.dumps(["/new/foo.sh"]),
            },
            {
                "id": -2,
                "custom": 1,
                "name": "Local Only",
                "executables": json.dumps(["/games/local.sh"]),
            },
        ],
        with_playtime=False,
    )

    result = import_external_library(
        "f95checker", str(source), {"merge_mode": "overwrite"}
    )

    assert result["updated"] == 1 and result["created"] == 1
    games = {game["title"]: game for game in get_all_games()}
    assert games["Foo"]["f95_url"] == url
    assert games["Foo"]["playtime_seconds"] == 18000
    assert games["Foo"]["exe_path"] == "/new/foo.sh"
    assert games["Local Only"]["f95_url"] == ""


# ---------------------------------------------------------------------------
# 3.2: Api methods
# ---------------------------------------------------------------------------


def _stub_update_check(monkeypatch, api: Api) -> list[list[str]]:
    monkeypatch.setattr("core.api.threading.Thread", _InlineThread)
    monkeypatch.setattr(
        api.scraper, "get_thread_metadata", lambda *_a, **_k: {"success": False}
    )
    captured: list[list[str]] = []

    def fake_check_versions(urls, callback):
        captured.append(list(urls))
        for url in urls:
            callback(url, {"success": True, "version": "2.0"})
        return {}

    monkeypatch.setattr(api.scraper, "check_versions", fake_check_versions)
    return captured


def test_api_import_external_library_metadata_check_started(tmp_path, monkeypatch):
    api = Api()
    captured = _stub_update_check(monkeypatch, api)
    add_game("Unrelated", "/unrelated.sh", "https://f95zone.to/threads/z.999/")
    source = tmp_path / "library.games.json"
    _write_xlibrary_live(
        source,
        [
            _xgame("Imported A", links=[_xlink(911)], exe="/games/a/Game.sh"),
            _xgame("Imported B", links=[_xlink(912)]),
        ],
    )
    result = api.import_external_library(
        "xlibrary", str(source), {"merge_mode": "fill_missing"}
    )
    assert result["success"] is True
    assert result["created"] == 2
    assert result["metadata_check"] == "started"
    assert len(captured) == 1
    assert "https://f95zone.to/threads/z.999/" not in captured[0]
    assert len(captured[0]) == 2


def test_api_import_external_library_metadata_check_busy(tmp_path, monkeypatch):
    api = Api()
    captured = _stub_update_check(monkeypatch, api)
    api._update_running = True
    try:
        source = tmp_path / "library.games.json"
        _write_xlibrary_live(source, [_xgame("Imported", links=[_xlink(921)])])
        result = api.import_external_library("xlibrary", str(source), {})
    finally:
        api._update_running = False
    assert result["success"] is True
    assert result["created"] == 1
    assert result["metadata_check"] == "busy"
    assert captured == []


def test_api_import_external_library_metadata_check_none(tmp_path, monkeypatch):
    api = Api()
    captured = _stub_update_check(monkeypatch, api)
    source = tmp_path / "library.games.json"
    local_game = _xgame("Local Only", exe="/games/local/Game.sh")
    local_game["externalLinks"] = []
    _write_xlibrary_live(source, [local_game])
    result = api.import_external_library("xlibrary", str(source), {})
    assert result["success"] is True
    assert result["created"] == 1
    assert result["metadata_check"] == "none"
    assert captured == []


def test_api_detect_and_browse_external_library(tmp_path, monkeypatch):
    api = Api()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    staged = tmp_path / "xlibrary" / "canonical" / "library.games.json"
    staged.parent.mkdir(parents=True)
    staged.write_text("{}", encoding="utf-8")

    detected = api.detect_external_library("xlibrary")
    assert detected["success"] is True
    assert detected["path"] == str(staged)

    assert api.detect_external_library("bogus")["success"] is False
    assert (
        api.inspect_external_library("xlibrary", str(tmp_path / "missing.json"))[
            "success"
        ]
        is False
    )

    monkeypatch.setattr(api, "_browse_linux_dialog", lambda *a, **k: "/picked.json")
    monkeypatch.setattr(api, "_browse_qt_dialog", lambda *a, **k: "/picked.json")
    assert api.browse_external_library_file("xlibrary", "") == "/picked.json"
    assert api.browse_external_library_file("bogus", "") == ""


# ---------------------------------------------------------------------------
# Fixed bugs: external import robustness
# ---------------------------------------------------------------------------


def test_xlibrary_non_utf8_reports_invalid_source(tmp_path):
    from core.backup_format import BackupValidationError

    source = tmp_path / "library.games.json"
    payload = {
        "family": "library.games",
        "schemaVersion": 9,
        "documents": [
            {
                "id": "1",
                "family": "library.games",
                "schemaVersion": 9,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
                "data": {
                    "id": "1",
                    "name": "Caf\u00e9",
                    "externalLinks": [
                        {
                            "providerId": "f95zone",
                            "externalId": "123",
                            "url": "https://f95zone.to/threads/game.123/",
                        }
                    ],
                },
            }
        ],
    }
    # latin-1 byte 0xe9 is invalid UTF-8; must surface as invalid_source,
    # not a raw UnicodeDecodeError.
    source.write_bytes(json.dumps(payload, ensure_ascii=False).encode("latin-1"))
    with pytest.raises(BackupValidationError, match="not an XLibrary library"):
        read_external_library("xlibrary", str(source))


def test_xlibrary_accepts_utf8_bom(tmp_path):
    source = tmp_path / "library.games.json"
    payload = {
        "family": "library.games",
        "schemaVersion": 9,
        "documents": [
            {
                "id": "1",
                "family": "library.games",
                "schemaVersion": 9,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
                "data": _xgame("BOM Game", links=[_xlink(801)]),
            }
        ],
    }
    source.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))
    backup, _stats = read_external_library("xlibrary", str(source))
    assert len(backup["games"]) == 1
    assert backup["games"][0]["metadata"]["title"] == "BOM Game"


def test_apply_path_rule_rejects_windows_drive_relative(monkeypatch):
    monkeypatch.setattr("core.external_import.is_windows", lambda: True)
    # Drive-relative paths are not absolute and must not be kept.
    assert apply_path_rule("C:Games\\Game.exe") == ""
    assert apply_path_rule("C:") == ""
    # Sanity: real absolute Windows paths are still kept.
    assert apply_path_rule("D:\\Games\\x\\Game.exe") != ""
    assert apply_path_rule("\\\\server\\share\\Game.exe") != ""


def test_overwrite_preserves_title_when_source_title_missing(tmp_path):
    url = "https://f95zone.to/threads/game.555/"
    local_id = add_game("Real Title", "/real/game.sh", f95_url=url)
    assert local_id is not None
    source = tmp_path / "library.games.json"
    nameless = _xgame("Whatever", links=[_xlink(555)])
    nameless["name"] = ""
    _write_xlibrary_live(source, [nameless])
    from core.library_backup import import_external_library as run_import

    result = run_import("xlibrary", str(source), {"merge_mode": "overwrite"})
    assert result["updated"] == 1 and result["created"] == 0
    games = {game["title"]: game for game in get_all_games()}
    assert "Real Title" in games
    assert "Imported Game" not in games


def test_sqlite_copy_failure_reports_read_failed(tmp_path, monkeypatch):
    import sqlite3 as sqlite3_module

    from core.backup_format import BackupValidationError

    for source_name, table in (("f95checker", "games"), ("atlas", "games")):
        source = tmp_path / f"{source_name}.db"
        conn = sqlite3_module.connect(str(source))
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        monkeypatch.setattr(
            "core.external_import.shutil.copy2",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(BackupValidationError, match="Could not read"):
            read_external_library(source_name, str(source))
        monkeypatch.undo()


def test_xlibrary_deeply_nested_reports_invalid_source(tmp_path):
    from core.backup_format import BackupValidationError

    source = tmp_path / "library.games.json"
    depth = 5000
    source.write_text('{"a":' * depth + "0" + "}" * depth, encoding="utf-8")
    with pytest.raises(BackupValidationError, match="not an XLibrary library"):
        read_external_library("xlibrary", str(source))


def test_f95checker_deeply_nested_json_does_not_crash(tmp_path):
    nested_array = "[" * 5000 + "]" * 5000
    nested_object = '{"a":' * 5000 + "0" + "}" * 5000
    source = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(str(source))
    conn.execute("CREATE TABLE settings (default_exe_dir TEXT DEFAULT '{}')")
    conn.execute("INSERT INTO settings VALUES (?)", (nested_object,))
    conn.execute(
        "CREATE TABLE games (id INTEGER PRIMARY KEY, custom INTEGER, "
        "name TEXT, url TEXT, executables TEXT, "
        "last_launched INTEGER DEFAULT 0, added_on INTEGER DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO games VALUES (100, 0, 'Deep', "
        "'https://f95zone.to/threads/deep.100/', ?, 0, 0)",
        (nested_array,),
    )
    conn.commit()
    conn.close()
    backup, _stats = read_external_library("f95checker", str(source))
    assert len(backup["games"]) == 1
    assert backup["games"][0]["metadata"]["title"] == "Deep"


def test_nonfinite_and_huge_playtime_is_omitted(tmp_path):
    source = tmp_path / "library.games.json"
    huge = _xgame("Huge", links=[_xlink(931)])
    huge["totalPlaytime"] = 10**30
    infinite = _xgame("Infinite", links=[_xlink(932)])
    infinite["totalPlaytime"] = float("inf")
    _write_xlibrary_live(source, [huge, infinite])
    backup, _stats = read_external_library("xlibrary", str(source))
    by_title = {game["metadata"]["title"]: game for game in backup["games"]}
    assert "playtime_seconds" not in by_title["Huge"].get("user_state", {})
    assert "playtime_seconds" not in by_title["Infinite"].get("user_state", {})

    from core.library_backup import import_external_library as run_import

    result = run_import("xlibrary", str(source), {"merge_mode": "overwrite"})
    assert result["created"] == 2
    games = {game["title"]: game for game in get_all_games()}
    assert games["Huge"]["playtime_seconds"] == 0
    assert games["Infinite"]["playtime_seconds"] == 0


def test_sqlite_nonfinite_playtime_is_omitted(tmp_path):
    f95_source = tmp_path / "f95.db"
    _write_f95checker_db(
        f95_source,
        [
            {
                "id": 941,
                "custom": 0,
                "name": "F95 Inf",
                "url": "https://f95zone.to/threads/inf.941/",
                "executables": json.dumps(["/games/inf/Game.sh"]),
                "playtime": float("inf"),
            }
        ],
    )
    backup, _stats = read_external_library("f95checker", str(f95_source))
    assert "playtime_seconds" not in backup["games"][0].get("user_state", {})

    atlas_source = tmp_path / "atlas.db"
    conn = sqlite3.connect(str(atlas_source))
    conn.execute(
        "CREATE TABLE games (record_id INTEGER PRIMARY KEY, title TEXT, "
        "creator TEXT, engine TEXT, last_played_r REAL DEFAULT 0, "
        "total_playtime REAL DEFAULT 0, date_added INTEGER DEFAULT 0, "
        "playstate TEXT, selected_version_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE versions (record_id INTEGER, version TEXT, "
        "game_path TEXT, exec_path TEXT, date_added INTEGER, playstate TEXT)"
    )
    conn.execute(
        "INSERT INTO games VALUES (1, 'Atlas Inf', 'Creator', '', 0, ?, 0, "
        "'played', NULL)",
        (float("inf"),),
    )
    conn.execute(
        "INSERT INTO versions VALUES (1, '0.1', '/games/atlas', 'Game.sh', 0, 'played')"
    )
    conn.commit()
    conn.close()
    backup, _stats = read_external_library("atlas", str(atlas_source))
    assert "playtime_seconds" not in backup["games"][0].get("user_state", {})


def test_playnite_unfinished_write_asks_to_close_playnite(tmp_path):
    """A LiteDB recovery flag means Playnite crashed or is writing: source_locked."""
    from core.backup_format import BackupValidationError

    library = tmp_path / "library"
    shutil.copytree(PLAYNITE_FIXTURE, library)
    database = library / "games.db"
    data = bytearray(database.read_bytes())
    # HeaderPage.Recovery is the last byte of the first page.
    data[4095] = 1
    database.write_bytes(data)
    with pytest.raises(BackupValidationError) as exc:
        read_external_library("playnite", str(database))
    assert exc.value.error_code == "source_locked"
    assert "Close Playnite" in str(exc.value)


def test_playnite_encrypted_library_reports_unsupported_encryption(tmp_path):
    from core.backup_format import BackupValidationError

    library = tmp_path / "library"
    shutil.copytree(PLAYNITE_FIXTURE, library)
    database = library / "games.db"
    data = bytearray(database.read_bytes())
    # HeaderPage.Password is a 20-byte block after the 65-byte page prefix.
    data[65] = 1
    database.write_bytes(data)
    with pytest.raises(BackupValidationError, match="Encrypted Playnite"):
        read_external_library("playnite", str(database))
