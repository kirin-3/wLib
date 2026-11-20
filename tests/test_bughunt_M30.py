# SPDX-License-Identifier: GPL-3.0-or-later
"""M30: the library database holds host account secrets, so only the owner may read it."""
import os
import sqlite3
import stat
from contextlib import closing

import pytest

from core import database
from core.downloads import credentials

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")


def _mode(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def open_umask():
    old = os.umask(0o022)
    try:
        yield
    finally:
        _ = os.umask(old)


def test_new_database_and_wal_files_are_owner_only(open_umask):
    database.init_db()
    credentials.put({credentials.PIXELDRAIN_API_KEY: "secret-key"})
    with closing(database.get_connection()) as conn:
        _ = conn.execute("SELECT 1").fetchone()
        for suffix in ("", "-wal", "-shm"):
            path = database.DB_PATH + suffix
            if os.path.exists(path):
                assert _mode(path) & 0o077 == 0, (suffix, oct(_mode(path)))


def test_first_connection_before_init_db_is_owner_only(open_umask):
    assert not os.path.exists(database.DB_PATH)
    with closing(database.get_connection()) as conn:
        _ = conn.execute("CREATE TABLE t (a)")
    assert _mode(database.DB_PATH) & 0o077 == 0


def test_init_db_restricts_an_existing_readable_database(open_umask):
    with closing(sqlite3.connect(database.DB_PATH)) as conn:
        _ = conn.execute("PRAGMA journal_mode=WAL")
        _ = conn.execute("CREATE TABLE t (a)")
        conn.commit()
        os.chmod(database.DB_PATH, 0o644)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(database.DB_PATH + suffix):
                os.chmod(database.DB_PATH + suffix, 0o644)
        database.init_db()
        for suffix in ("", "-wal", "-shm"):
            path = database.DB_PATH + suffix
            if os.path.exists(path):
                assert _mode(path) == 0o600, (suffix, oct(_mode(path)))


@pytest.mark.schema_fingerprint
def test_migration_copies_are_owner_only(open_umask):
    database.init_db()
    _ = database.add_game("Game", "/games/game.exe")
    credentials.put({credentials.RPDL_PASSWORD: "hunter2"})
    with closing(database.get_connection()) as conn:
        target = database._backup_before_migration(conn)  # pyright: ignore[reportPrivateUsage]
    assert target is not None
    assert _mode(target) == 0o600
    assert _mode(database.db_backup_dir()) == 0o700


def test_init_db_restricts_old_migration_copies(open_umask):
    database.init_db()
    _ = database.add_game("Game", "/games/game.exe")
    folder = database.db_backup_dir()
    os.makedirs(folder, mode=0o755)
    os.chmod(folder, 0o755)
    old = os.path.join(folder, "wlib-20250101-120000.db")
    with closing(sqlite3.connect(old)) as conn:
        _ = conn.execute("CREATE TABLE t (a)")
    os.chmod(old, 0o644)
    database.init_db()
    assert _mode(old) == 0o600
    assert _mode(folder) == 0o700
