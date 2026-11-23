# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for backup/import bug hunt 02."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from test_gamelist_hyperfile import gamelist_record, write_gamelist

from core import database
from core.backup_format import BackupValidationError
from core.external_import import (
    _translate_playnite_game,
    coerce_thread_url,
    read_external_library,
)
from core.library_backup import write_library_backup


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setattr("core.external_import.is_windows", lambda: False)
    database.init_db()


# ---------------------------------------------------------------------------
# Bug 1: Export can clobber an unrelated temporary file
# ---------------------------------------------------------------------------


def test_export_preserves_preexisting_tmp_sibling(tmp_path):
    database.add_game("Exported Game", "/games/exported.sh")
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    destination = export_dir / "library.json"
    sibling = export_dir / "library.json.tmp"
    sibling.write_text("unrelated temp data", encoding="utf-8")

    result = write_library_backup(str(destination))

    assert result["path"] == str(destination)
    assert sibling.exists()
    assert sibling.read_text(encoding="utf-8") == "unrelated temp data"
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert [game["metadata"]["title"] for game in payload["games"]] == ["Exported Game"]


def test_export_failure_does_not_delete_preexisting_tmp_sibling(tmp_path, monkeypatch):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    destination = export_dir / "library.json"
    sibling = export_dir / "library.json.tmp"
    sibling.write_text("unrelated temp data", encoding="utf-8")

    def fail_replace(_src: str, _dst: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("core.library_backup.os.replace", fail_replace)

    with pytest.raises(BackupValidationError) as exc:
        write_library_backup(str(destination))

    assert exc.value.error_code == "write_failed"
    assert sibling.exists()
    assert sibling.read_text(encoding="utf-8") == "unrelated temp data"
    assert [p.name for p in export_dir.iterdir()] == ["library.json.tmp"]


# ---------------------------------------------------------------------------
# Bug 2: Live SQLite import can lose committed rows during a checkpoint
# ---------------------------------------------------------------------------


def _create_live_wal_f95checker_db(path: Path) -> sqlite3.Connection:
    writer = sqlite3.connect(str(path))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute(
        "CREATE TABLE games ("
        "id INTEGER PRIMARY KEY, custom INTEGER DEFAULT 0, "
        "name TEXT DEFAULT '', url TEXT DEFAULT '', executables TEXT DEFAULT '[]'"
        ")"
    )
    writer.execute(
        "INSERT INTO games (id, custom, name, url) VALUES (101, 0, 'Main Row', 'https://f95zone.to/threads/101/')"
    )
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # Committed row lives only in the WAL until the next checkpoint.
    writer.execute(
        "INSERT INTO games (id, custom, name, url) VALUES (102, 0, 'WAL Row', 'https://f95zone.to/threads/102/')"
    )
    writer.commit()
    return writer


def test_live_wal_snapshot_survives_checkpoint_during_import(tmp_path, monkeypatch):
    source = tmp_path / "f95checker.sqlite3"
    writer = _create_live_wal_f95checker_db(source)
    real_copy2 = shutil.copy2
    checkpointed = False

    def copy2_with_checkpoint(src, dst, *args, **kwargs):
        nonlocal checkpointed
        result = real_copy2(src, dst, *args, **kwargs)
        if not checkpointed and os.path.abspath(str(src)) == os.path.abspath(
            str(source)
        ):
            checkpointed = True
            writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return result

    monkeypatch.setattr("core.external_import.shutil.copy2", copy2_with_checkpoint)
    try:
        backup, stats = read_external_library("f95checker", str(source))
    finally:
        writer.close()

    titles = {game["metadata"]["title"] for game in backup["games"]}
    assert titles == {"Main Row", "WAL Row"}
    assert stats["skipped_no_identity"] == 0


def test_live_wal_snapshot_survives_mid_backup_checkpoint(tmp_path, monkeypatch):
    source = tmp_path / "f95checker_mid.sqlite3"
    writer = _create_live_wal_f95checker_db(source)
    # Add enough rows in WAL so multi-page backup executes progress callbacks.
    writer.executemany(
        "INSERT INTO games (id, custom, name, url) VALUES (?, 0, ?, ?)",
        [
            (
                200 + i,
                f"Extra {i} " + ("x" * 400),
                f"https://f95zone.to/threads/{200 + i}/",
            )
            for i in range(60)
        ],
    )
    writer.commit()

    real_connect = sqlite3.connect
    checkpointed = False

    class _SteppingConnection(sqlite3.Connection):
        def backup(self, target, *, pages=-1, progress=None, name="main", sleep=0.250):
            def wrapped_progress(status, remaining, total):
                nonlocal checkpointed
                if not checkpointed and remaining > 0:
                    checkpointed = True
                    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                if progress is not None:
                    progress(status, remaining, total)

            return super().backup(
                target,
                pages=1,
                progress=wrapped_progress,
                name=name,
                sleep=sleep,
            )

    def connect_stepping(*args, **kwargs):
        kwargs.setdefault("factory", _SteppingConnection)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr("core.external_import.sqlite3.connect", connect_stepping)
    try:
        backup, _stats = read_external_library("f95checker", str(source))
    finally:
        writer.close()

    titles = {game["metadata"]["title"] for game in backup["games"]}
    assert checkpointed is True
    assert "Main Row" in titles
    assert "WAL Row" in titles
    assert len(titles) == 62


def test_locked_sqlite_source_falls_back_to_a_file_copy(tmp_path, monkeypatch):
    source = tmp_path / "locked.sqlite3"
    writer = sqlite3.connect(str(source))
    writer.execute(
        "CREATE TABLE games (id INTEGER PRIMARY KEY, custom INTEGER, name TEXT)"
    )
    writer.execute("INSERT INTO games VALUES (1, 0, 'Locked')")
    writer.commit()
    writer.execute("BEGIN EXCLUSIVE")

    monkeypatch.setattr(
        "core.external_import._SNAPSHOT_LOCK_TIMEOUT", 0.15, raising=False
    )
    try:
        # The source app keeps its database locked: the live snapshot cannot
        # get access, so the import copies the files as before and reads the
        # committed rows instead of failing.
        backup, _stats = read_external_library("f95checker", str(source))
    finally:
        writer.rollback()
        writer.close()

    games = backup["games"]
    assert isinstance(games, list)
    assert [game["metadata"]["title"] for game in games] == ["Locked"]


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permission check requires non-root Unix",
)
def test_unreadable_sqlite_source_reports_read_failed(tmp_path):
    source = tmp_path / "unreadable.sqlite3"
    conn = sqlite3.connect(str(source))
    conn.execute(
        "CREATE TABLE games (id INTEGER PRIMARY KEY, custom INTEGER, name TEXT)"
    )
    conn.commit()
    conn.close()
    os.chmod(source, 0)
    try:
        with pytest.raises(BackupValidationError, match="Could not read") as exc:
            read_external_library("f95checker", str(source))
    finally:
        os.chmod(source, 0o600)

    assert exc.value.error_code == "read_failed"


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permission check requires non-root Unix",
)
def test_readonly_dir_wal_sqlite_source_imports(tmp_path):
    ro_dir = tmp_path / "ro_source"
    ro_dir.mkdir()
    source = ro_dir / "wal.sqlite3"
    conn = sqlite3.connect(str(source))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE games (id INTEGER PRIMARY KEY, custom INTEGER, name TEXT, url TEXT)"
    )
    conn.execute(
        "INSERT INTO games VALUES (501, 0, 'ReadOnly WAL', 'https://f95zone.to/threads/501/')"
    )
    conn.commit()
    conn.close()
    os.chmod(ro_dir, 0o555)
    try:
        backup, stats = read_external_library("f95checker", str(source))
    finally:
        os.chmod(ro_dir, 0o755)

    assert [game["metadata"]["title"] for game in backup["games"]] == ["ReadOnly WAL"]
    assert stats["skipped_no_identity"] == 0


# ---------------------------------------------------------------------------
# Bug 3: A non-thread F95 URL can collapse unrelated Game-List entries
# ---------------------------------------------------------------------------


def test_gamelist_non_thread_f95_url_does_not_collapse_unrelated_entries(tmp_path):
    path = write_gamelist(
        tmp_path,
        gamelist_record(
            1,
            guid="11111111-1111-1111-1111-111111111111",
            name="Store Game One",
            url="https://f95zone.to/",
            homepage="https://www.dlsite.com/maniax/work/=/product_id/RJ010001.html",
        ),
        gamelist_record(
            2,
            guid="22222222-2222-2222-2222-222222222222",
            name="Store Game Two",
            url="https://f95zone.to/",
            homepage="https://www.dlsite.com/maniax/work/=/product_id/RJ010002.html",
        ),
        gamelist_record(
            3,
            guid="33333333-3333-3333-3333-333333333333",
            name="Homepage Only One",
            url="https://f95zone.to/",
            homepage="",
        ),
        gamelist_record(
            4,
            guid="44444444-4444-4444-4444-444444444444",
            name="Homepage Only Two",
            url="https://www.f95zone.to/forums/games.2/",
            homepage="",
        ),
    )

    backup, stats = read_external_library("gamelist", str(path))

    by_title = {game["metadata"]["title"]: game["metadata"] for game in backup["games"]}
    assert set(by_title) == {"Store Game One", "Store Game Two"}
    assert "f95_url" not in by_title["Store Game One"]
    assert "f95_url" not in by_title["Store Game Two"]
    assert by_title["Store Game One"]["links"][0]["id"] == "RJ010001"
    assert by_title["Store Game Two"]["links"][0]["id"] == "RJ010002"
    assert stats["skipped_no_identity"] == 2


def test_coerce_thread_url_requires_thread_id_and_preserves_fallbacks(tmp_path):
    assert coerce_thread_url("https://f95zone.to/") == ""
    assert coerce_thread_url("https://www.f95zone.to/forums/games.2/") == ""
    assert coerce_thread_url("https://f95zone.to/members/dev.42/") == ""
    assert coerce_thread_url("https://f95zone.to/threads/") == ""
    assert coerce_thread_url("/threads/1234/") == ""
    assert (
        coerce_thread_url("http://www.f95zone.to/threads/slug.1234/page-2")
        == "https://f95zone.to/threads/slug.1234/"
    )
    assert (
        coerce_thread_url("https://f95zone.to/threads/7006")
        == "https://f95zone.to/threads/7006/"
    )

    # F95Checker: non-thread F95 URL falls back to numeric thread ID.
    f95c_db = tmp_path / "f95c.sqlite3"
    conn = sqlite3.connect(str(f95c_db))
    conn.execute(
        "CREATE TABLE games (id INTEGER PRIMARY KEY, custom INTEGER, name TEXT, url TEXT)"
    )
    conn.execute(
        "INSERT INTO games VALUES (5555, 0, 'Fallback Game', 'https://f95zone.to/')"
    )
    conn.commit()
    conn.close()
    backup, _stats = read_external_library("f95checker", str(f95c_db))
    assert (
        backup["games"][0]["metadata"]["f95_url"] == "https://f95zone.to/threads/5555/"
    )

    # Playnite: skips non-thread F95 link to find the real thread link.
    game, _dropped = _translate_playnite_game(
        {
            "Name": "Playnite Game",
            "Links": [
                {"Name": "Home", "Url": "https://f95zone.to/"},
                {"Name": "Thread", "Url": "https://f95zone.to/threads/real.999/page-3"},
            ],
        },
        {},
        str(tmp_path),
    )
    assert game is not None
    assert game["metadata"]["f95_url"] == "https://f95zone.to/threads/real.999/"


def test_closed_wal_source_gets_no_new_sidecar_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    db_path = source / "library.db"
    conn = sqlite3.connect(db_path)
    _ = conn.execute("PRAGMA journal_mode=WAL")
    _ = conn.execute("CREATE TABLE t (x TEXT)")
    _ = conn.execute("INSERT INTO t VALUES ('row')")
    conn.commit()
    conn.close()
    before = sorted(os.listdir(source))
    assert before == ["library.db"]

    from core.external_import import open_snapshot

    with open_snapshot(str(db_path)) as snapshot:
        rows = snapshot.execute("SELECT x FROM t").fetchall()

    assert [tuple(row) for row in rows] == [("row",)]
    assert sorted(os.listdir(source)) == before
