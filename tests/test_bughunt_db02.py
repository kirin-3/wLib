# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for database bug hunt 02."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone

import pytest

from core import database, lewdcorner
from core.library_backup import (
    SECTION_METADATA,
    apply_library_backup,
    build_sync_backup,
)
from core.library_sync import SyncLocation, SyncManager, timestamp

LC_URL = "https://lewdcorner.com/threads/101/"
LC_URL_2 = "https://lewdcorner.com/threads/102/"
MARK = 1794000000


class _MemoryLocation(SyncLocation):
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self._data: bytes | None = (
            json.dumps(payload).encode("utf-8") if payload is not None else None
        )
        self._rev: int = 1 if payload is not None else 0

    def revision(self) -> str | None:
        return None if self._data is None else f"rev-{self._rev}"

    def read(self) -> tuple[bytes, str]:
        assert self._data is not None
        return self._data, f"rev-{self._rev}"

    def write(self, data: bytes, expected_revision: str | None) -> str:
        assert expected_revision == self.revision()
        self._data = data
        self._rev += 1
        return f"rev-{self._rev}"


@pytest.fixture(autouse=True)
def _db():
    database.init_db()


def _row(game_id: int, *columns: str) -> tuple[object, ...]:
    with closing(database.get_connection()) as conn:
        row = conn.execute(
            f"SELECT {', '.join(columns)} FROM games WHERE id = ?", (game_id,)
        ).fetchone()
        assert row is not None
        return tuple(row)


# ---------------------------------------------------------------------------
# Bug 1: A local edit can be skipped by sync after a pull with UTC offset
# ---------------------------------------------------------------------------


def test_local_edit_after_offset_pull_advances_sync_timestamp():
    game_id = database.add_game("Offset Game", "/games/offset.exe")
    assert game_id is not None

    # Remote change time is 2 minutes ahead of UTC now, expressed at -08:00 so
    # it sorts lexicographically earlier than the current UTC timestamp.
    offset_tz = timezone(timedelta(hours=-8))
    remote_modified = (
        (datetime.now(UTC) + timedelta(minutes=2))
        .astimezone(offset_tz)
        .isoformat(timespec="milliseconds")
    )
    remote_backup = build_sync_backup(remote_modified, "remote-device")
    database.update_setting("sync_location", "folder")
    sync = SyncManager(location=_MemoryLocation(remote_backup))

    assert sync.run("use-remote")["state"] == "idle"
    assert database.get_setting("sync_last_synced_at") == remote_modified
    assert not sync.local_changed()

    current_game_id = int(str(database.get_all_games()[0]["id"]))
    assert database.update_game(current_game_id, {"progress": "local edit"})

    after_first = database.get_setting("sync_local_modified_at") or ""
    assert timestamp(after_first) > timestamp(remote_modified)
    assert sync.local_changed()

    # A second immediate edit keeps advancing monotonically.
    assert database.update_game(current_game_id, {"progress": "second edit"})
    after_second = database.get_setting("sync_local_modified_at") or ""
    assert timestamp(after_second) > timestamp(after_first)


# ---------------------------------------------------------------------------
# Bug 2: Concurrent settings saves can leave a removed custom status on games
# ---------------------------------------------------------------------------


def test_concurrent_settings_save_resets_status_registered_before_write_lock(
    monkeypatch,
):
    game_id = database.add_game("Race Game", "/games/race.exe")
    assert game_id is not None
    real_get_connection = database.get_connection
    raced = False

    def register_and_assign_before_write(statement: str) -> None:
        nonlocal raced
        if not raced and statement.startswith("BEGIN"):
            raced = True
            database.update_custom_play_statuses(["Replay"])
            assert database.update_game(game_id, {"play_status": "Replay"})

    def traced_connection() -> sqlite3.Connection:
        conn = real_get_connection()
        if not raced:
            conn.set_trace_callback(register_and_assign_before_write)
        return conn

    monkeypatch.setattr(database, "get_connection", traced_connection)
    database.update_custom_play_statuses([])

    assert raced
    assert database.get_custom_play_statuses() == []
    assert _row(game_id, "play_status") == (database.DEFAULT_PLAY_STATUS,)


# ---------------------------------------------------------------------------
# Bug 3: Older LewdCorner data can flip Finished for the same release
# ---------------------------------------------------------------------------


def test_older_lc_feed_for_same_version_does_not_clear_finished():
    game_id = database.add_game("LC Feed Same Ver", "", lc_url=LC_URL)
    assert game_id is not None

    t1 = lewdcorner.timestamp_iso(MARK)
    t2 = lewdcorner.timestamp_iso(MARK + 1000)

    stored = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t2),
        completed=True,
        keep_newer=True,
        lc_completed=True,
        lc_completed_from_feed=True,
    )
    assert stored is not None
    assert stored[0]["thread_completed"] == 1
    assert stored[0]["lc_thread_completed"] == 1

    # Older feed item for the same version without Completed must not clear it.
    older = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t1),
        keep_newer=True,
        lc_completed=False,
        lc_completed_from_feed=True,
    )
    assert older is not None
    assert _row(game_id, "thread_completed", "lc_thread_completed", "lc_latest_at") == (
        1,
        1,
        t2,
    )


def test_older_lc_catalog_for_same_version_does_not_flip_finished():
    game_id = database.add_game("LC Catalog Same Ver", "", lc_url=LC_URL)
    assert game_id is not None

    t1 = lewdcorner.timestamp_iso(MARK)
    t2 = lewdcorner.timestamp_iso(MARK + 1000)

    stored = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t2),
        keep_newer=True,
        lc_completed=False,
    )
    assert stored is not None
    assert stored[0]["thread_completed"] == 0

    # Older catalog entry for the same version must not re-mark it Finished.
    older = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t1),
        keep_newer=True,
        catalog_completed=True,
    )
    assert older is not None
    _, newly_finished, _ = older
    assert newly_finished is False
    assert _row(game_id, "thread_completed", "lc_thread_completed", "lc_latest_at") == (
        0,
        0,
        t2,
    )


def test_same_version_lc_prefix_freshness_tracks_observation_time():
    game_id = database.add_game("LC Prefix Timeline", "", lc_url=LC_URL)
    assert game_id is not None

    t1 = lewdcorner.timestamp_iso(MARK)
    t2 = lewdcorner.timestamp_iso(MARK + 1000)
    t3 = lewdcorner.timestamp_iso(MARK + 2000)
    t4 = lewdcorner.timestamp_iso(MARK + 3000)
    t5 = lewdcorner.timestamp_iso(MARK + 4000)

    # First sight of version 2.0 at t1 without prefix data.
    assert database.store_thread_update(
        game_id, "lc", LC_URL, ("2.0", t1), keep_newer=True
    )

    # Newer feed read at t3 marks the same version Completed; lc_latest_at stays t1.
    step_completed = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t3),
        completed=True,
        keep_newer=True,
        lc_completed=True,
        lc_completed_from_feed=True,
    )
    assert step_completed is not None
    assert step_completed[1] is True
    assert _row(game_id, "thread_completed", "lc_thread_completed", "lc_latest_at") == (
        1,
        1,
        t1,
    )

    # Intermediate older feed read at t2 (t1 < t2 < t3) must NOT clear Completed.
    assert database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t2),
        keep_newer=True,
        lc_completed=False,
        lc_completed_from_feed=True,
    )
    assert _row(game_id, "thread_completed", "lc_thread_completed") == (1, 1)

    # Newer feed read at t4 (> t3) for the same version DOES clear Completed.
    assert database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t4),
        keep_newer=True,
        lc_completed=False,
        lc_completed_from_feed=True,
    )
    assert _row(game_id, "thread_completed", "lc_thread_completed") == (0, 0)

    # Older catalog entry at t3 (< t4) must NOT set Completed again.
    stale_catalog = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t3),
        keep_newer=True,
        catalog_completed=True,
    )
    assert stale_catalog is not None
    assert stale_catalog[1] is False
    assert _row(game_id, "thread_completed", "lc_thread_completed") == (0, 0)

    # Newer catalog entry at t5 (> t4) DOES set Completed.
    fresh_catalog = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t5),
        keep_newer=True,
        catalog_completed=True,
    )
    assert fresh_catalog is not None
    assert fresh_catalog[1] is True
    assert _row(game_id, "thread_completed", "lc_thread_completed") == (1, 1)

    # Switching LC thread clears the old completion timestamp too.
    assert database.update_game(game_id, {"lc_url": LC_URL_2})
    assert _row(game_id, "lc_thread_completed", "lc_thread_completed_at") == (
        None,
        None,
    )

    # And a backup import that changes lc_url also forgets old completion state.
    assert database.store_thread_update(
        game_id,
        "lc",
        LC_URL_2,
        ("2.0", t5),
        keep_newer=True,
        lc_completed=False,
        lc_completed_from_feed=True,
    )
    assert _row(game_id, "lc_thread_completed", "lc_thread_completed_at") == (0, t5)
    backup = build_sync_backup("2026-11-22T00:00:00Z", "test-device")
    cast_games = backup["games"]
    assert isinstance(cast_games, list)
    metadata = cast_games[0]["metadata"]
    assert "lc_thread_completed_at" not in metadata
    metadata["lc_url"] = LC_URL
    metadata["lc_latest_at"] = ""
    _ = apply_library_backup(backup, {"sections": [SECTION_METADATA]})
    assert _row(game_id, "lc_thread_completed", "lc_thread_completed_at") == (
        None,
        None,
    )
    # Fresh catalog entry at t1 for the new thread should now apply.
    reapplied = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", t1),
        keep_newer=True,
        catalog_completed=True,
    )
    assert reapplied is not None
    assert _row(game_id, "thread_completed", "lc_thread_completed") == (1, 1)


# ---------------------------------------------------------------------------
# Bug 4: A library read can drop membership during a collection rename
# ---------------------------------------------------------------------------


def test_get_all_games_keeps_membership_across_concurrent_collection_rename(
    monkeypatch,
):
    game_id = database.add_game("Collected", "/games/collected.exe")
    assert game_id is not None
    database.create_collection("Before", game_id=game_id)

    real_get_connection = database.get_connection
    renamed = False

    def rename_mid_read(statement: str) -> None:
        nonlocal renamed
        if not renamed and "FROM game_links" in statement:
            renamed = True
            database.rename_collection("Before", "After")

    def traced_connection() -> sqlite3.Connection:
        conn = real_get_connection()
        if not renamed:
            conn.set_trace_callback(rename_mid_read)
        return conn

    monkeypatch.setattr(database, "get_connection", traced_connection)
    games = database.get_all_games()

    assert renamed
    assert len(games) == 1
    assert games[0]["collections"] == ["Before"]
    # A subsequent read sees the renamed collection.
    assert database.get_all_games()[0]["collections"] == ["After"]


# ---------------------------------------------------------------------------
# Bug 5: Startup can leave a current database outside WAL mode
# ---------------------------------------------------------------------------


@pytest.mark.schema_fingerprint
def test_init_db_restores_wal_on_current_schema_database(monkeypatch):
    fingerprint = database.schema_fingerprint()
    database.init_db()
    with closing(database.get_connection()) as conn:
        assert conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"

    monkeypatch.setattr(database, "schema_fingerprint", lambda: fingerprint)
    calls: list[int] = []
    real_migrate = database._migrate_schema  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(
        database,
        "_migrate_schema",
        lambda conn: calls.append(1) or real_migrate(conn),
    )

    database.init_db()
    assert calls == [], "current schema should still skip migrations"
    with closing(database.get_connection()) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    # Once already in WAL, init_db should not issue a journal_mode write.
    statements: list[str] = []
    real_get_connection = database.get_connection

    def traced_connection() -> sqlite3.Connection:
        conn = real_get_connection()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(database, "get_connection", traced_connection)
    database.init_db()
    assert calls == []
    assert not any("journal_mode=wal" in s.lower() for s in statements)


def test_page_read_does_not_hide_a_later_feed_completed_for_the_same_release(
    monkeypatch,
):
    """A page read's clock time is not an LC time: it must not make a later
    feed Completed for the same release look stale."""
    from core import extension_bridge, lewdcorner_plus, lewdcorner_updates

    game_id = database.add_game("LC Page Then Feed", "", lc_url=LC_URL)
    assert game_id is not None
    release = lewdcorner.timestamp_iso(MARK)
    assert database.store_thread_update(
        game_id, "lc", LC_URL, ("2.0", release), keep_newer=True
    )
    monkeypatch.setattr(
        extension_bridge, "is_available", lambda *, site="f95": site == "lc"
    )
    monkeypatch.setattr(
        lewdcorner_updates,
        "_fetch",
        lambda url: extension_bridge.BridgeResponse(200, url, "<html></html>"),
    )
    monkeypatch.setattr(lewdcorner_plus, "note_thread_page", lambda *_args: None)
    monkeypatch.setattr(
        lewdcorner,
        "parse_thread_page",
        lambda *_args, **_kwargs: lewdcorner.LcVersion(
            "2.0", prefixes=(lewdcorner.LcPrefix("Ren'Py"),)
        ),
    )
    # A single check reads the thread page: prefixes known, not Completed.
    _ = lewdcorner_updates.check_thread(game_id, LC_URL, "2.0", {}, single=True)
    assert _row(game_id, "thread_completed", "lc_thread_completed") == (0, 0)

    # The developer marks the thread Completed without a new release; the
    # feed item keeps the release time.
    item = lewdcorner.LcVersion(
        "2.0", at=release, prefixes=(lewdcorner.LcPrefix("Completed"),)
    )
    _ = lewdcorner_updates.check_thread(game_id, LC_URL, "2.0", {}, feed_item=item)
    assert _row(game_id, "thread_completed", "lc_thread_completed") == (1, 1)
