# SPDX-License-Identifier: GPL-3.0-or-later
"""Database bug hunt fixes: settings, migration, thread and status races."""
import json
import sqlite3
import struct
import threading
import time
from contextlib import closing
from decimal import Decimal

import pytest

from core import database
from core.api import Api
from core.litedb import decode_bson

F95_A = "https://f95zone.to/threads/game-a.111/"
F95_B = "https://f95zone.to/threads/game-b.222/"
VNDB = {"url": "https://vndb.org/v123"}


@pytest.fixture(autouse=True)
def _db():
    database.init_db()


def _row(game_id: int, *columns: str) -> tuple[object, ...]:
    with closing(database.get_connection()) as conn:
        return conn.execute(
            f"SELECT {', '.join(columns)} FROM games WHERE id = ?", (game_id,)
        ).fetchone()


def test_folder_repair_does_not_undo_a_concurrent_save(monkeypatch):
    database.update_settings({"library_folder": "/old", "library_folders": "[]"})
    real = database._normalized_library_folders  # pyright: ignore[reportPrivateUsage]
    calls = 0

    def racing(raw_folders: str | None, raw_default: str | None):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Settings saves a new folder after the first read.
            database.update_settings(
                {"library_folder": "/new", "library_folders": json.dumps(["/new"])}
            )
        return real(raw_folders, raw_default)

    monkeypatch.setattr(database, "_normalized_library_folders", racing)
    assert database.get_library_folders() == (["/new"], "/new")
    assert database.get_setting("library_folder") == "/new"
    assert database.get_setting("library_folders") == json.dumps(["/new"])


def test_folder_read_without_repair_writes_nothing():
    database.update_settings({"library_folder": "/a", "library_folders": json.dumps(["/a"])})
    database.update_setting("sync_local_modified_at", "2000-01-01T00:00:00Z")
    assert database.get_library_folders() == (["/a"], "/a")
    assert database.get_setting("sync_local_modified_at") == "2000-01-01T00:00:00Z"


@pytest.mark.schema_fingerprint
def test_second_migration_waits_and_skips(monkeypatch):
    _ = database.add_game("Game", "/games/game.exe")
    monkeypatch.setattr(database, "schema_fingerprint", lambda: 1001)
    database.init_db()
    backups: list[object] = []
    real_backup = database._backup_before_migration  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(
        database, "_backup_before_migration", lambda: backups.append(real_backup())
    )
    monkeypatch.setattr(database, "schema_fingerprint", lambda: 1002)

    # Another process holds the write lock and finishes the same update.
    other = database.get_connection()
    _ = other.execute("BEGIN IMMEDIATE")
    state = database._schema_state(other, 1002)  # pyright: ignore[reportPrivateUsage]
    _ = other.execute(f"PRAGMA user_version = {state}")
    errors: list[BaseException] = []

    def run() -> None:
        try:
            database.init_db()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.3)
    other.commit()
    other.close()
    worker.join(10)
    assert not errors
    assert backups == [], "the copy after another process's update holds the new state"


def test_changing_f95_thread_drops_old_first_post_links():
    game_id = database.add_game("Game", "", F95_A)
    steam = {"url": "https://store.steampowered.com/app/123/"}
    database.replace_game_links(game_id, "f95_post", [VNDB])
    database.replace_game_links(game_id, "user", [steam])
    _ = database.update_game(game_id, {"f95_url": F95_B})
    assert [link["url"] for link in database.get_game_links(game_id)] == [steam["url"]]
    assert database.find_games_by_links([VNDB]) == []


def test_equivalent_f95_url_keeps_first_post_links():
    game_id = database.add_game("Game", "", F95_A)
    database.replace_game_links(game_id, "f95_post", [VNDB])
    _ = database.update_game(game_id, {"f95_url": F95_A + "page-2"})
    assert len(database.get_game_links(game_id)) == 1


def test_refresh_does_not_write_to_a_thread_changed_meanwhile(monkeypatch):
    game_id = database.add_game("Game", "", F95_A)
    real = database.thread_state_fields
    editor: list[threading.Thread] = []

    def edit_during_refresh(game, states):
        # The user saves thread B while the refresh is between match and write.
        thread = threading.Thread(
            target=lambda: database.update_game(game_id, {"f95_url": F95_B})
        )
        thread.start()
        editor.append(thread)
        time.sleep(0.3)
        return real(game, states)

    monkeypatch.setattr(database, "thread_state_fields", edit_during_refresh)
    _ = Api()._update_thread_edit_metadata_for_url(  # pyright: ignore[reportPrivateUsage]
        F95_A, {"thread_completed": True, "store_links": [VNDB]}
    )
    editor[0].join(10)
    assert _row(game_id, "f95_url", "thread_completed") == (F95_B, 0)
    assert database.get_game_links(game_id) == []


def test_dated_read_fills_an_undated_version():
    game_id = database.add_game("Game", "", F95_A)
    _ = database.store_thread_update(game_id, "f95", F95_A, ("2.0", ""))
    assert _row(game_id, "f95_latest_at") == ("",)
    _ = database.store_thread_update(game_id, "f95", F95_A, ("2.0", "2026-01-02T00:00:00Z"))
    assert _row(game_id, "f95_latest_at") == ("2026-01-02T00:00:00Z",)
    # A dated version keeps its first date.
    _ = database.store_thread_update(game_id, "f95", F95_A, ("2.0", "2026-03-04T00:00:00Z"))
    assert _row(game_id, "f95_latest_at") == ("2026-01-02T00:00:00Z",)


def test_replacing_lc_only_thread_clears_finished_flag():
    game_id = database.add_game(
        "Game", "", lc_url="https://lewdcorner.com/threads/111/"
    )
    _ = database.update_game(game_id, {"thread_completed": True})
    _ = database.update_game(game_id, {"lc_url": "https://lewdcorner.com/threads/222/"})
    assert _row(game_id, "thread_completed") == (0,)
    _ = database.update_game(
        game_id, {"lc_url": "https://lewdcorner.com/threads/333/", "thread_completed": True}
    )
    assert _row(game_id, "thread_completed") == (1,)


def test_removing_custom_status_resets_non_ascii_case_variant():
    database.update_custom_play_statuses(["Épilogue"])
    game_id = database.add_game("Game", "")
    with closing(database.get_connection()) as conn:
        _ = conn.execute("UPDATE games SET play_status = 'Épilogue' WHERE id = ?", (game_id,))
        conn.commit()
    database.update_custom_play_statuses(["épilogue"])
    assert _row(game_id, "play_status") == ("Épilogue",)
    database.update_custom_play_statuses([])
    assert _row(game_id, "play_status") == (database.DEFAULT_PLAY_STATUS,)


def _bson(kind: int, key: str, value: bytes) -> bytes:
    body = bytes([kind]) + key.encode() + b"\0" + value + b"\0"
    return struct.pack("<i", len(body) + 4) + body


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        ((15, 0, 0, 1 << 16), Decimal("1.5")),
        ((15, 0, 0, 0x80010000), Decimal("-1.5")),
        ((0, 0, 1, 0), Decimal(2**64)),
    ],
)
def test_litedb_decimal(words, expected):
    assert decode_bson(_bson(19, "d", struct.pack("<IIII", *words))) == {"d": expected}


@pytest.mark.parametrize("flags", [1, 29 << 16, 1 << 24])
def test_litedb_invalid_decimal(flags):
    from core.backup_format import BackupValidationError

    with pytest.raises((BackupValidationError, sqlite3.Error, ValueError)):
        _ = decode_bson(_bson(19, "d", struct.pack("<IIII", 1, 0, 0, flags)))
