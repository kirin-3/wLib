# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3
from contextlib import closing

import pytest

from core import database
from core.database import (
    DOWNLOAD_JOB_COLUMNS,
    add_game,
    delete_download_job,
    init_db,
    insert_download_job,
    list_download_jobs,
    reorder_download_jobs,
    update_download_job,
)


def _job(job_id: str, game_id: int = 1, **extra: object) -> dict[str, object]:
    return {"id": job_id, "kind": "host", "game_id": game_id, "state": "queued", **extra}


def _columns() -> list[str]:
    with closing(database.get_connection()) as conn:
        return [str(row[1]) for row in conn.execute("PRAGMA table_info(download_jobs)").fetchall()]


def test_fresh_database_has_the_table():
    init_db()
    assert _columns() == list(DOWNLOAD_JOB_COLUMNS)
    assert list_download_jobs() == []


def test_database_from_before_this_change_gets_the_table():
    init_db()
    game_id = add_game(title="Kept", exe_path="/games/kept/game.sh")
    with closing(database.get_connection()) as conn:
        _ = conn.execute("DROP TABLE download_jobs")
        conn.commit()
    init_db()
    assert _columns() == list(DOWNLOAD_JOB_COLUMNS)
    assert database.get_game(game_id) is not None
    insert_download_job(_job("a"))
    assert [job["id"] for job in list_download_jobs()] == ["a"]


def test_insert_appends_and_reorder_changes_order():
    init_db()
    for job_id in ("a", "b", "c", "d"):
        insert_download_job(_job(job_id))
    assert [job["id"] for job in list_download_jobs()] == ["a", "b", "c", "d"]

    reorder_download_jobs(["c", "a"])
    assert [job["id"] for job in list_download_jobs()] == ["c", "a", "b", "d"]

    # Unknown and repeated ids are ignored.
    reorder_download_jobs(["zz", "d", "d"])
    assert [job["id"] for job in list_download_jobs()] == ["d", "c", "a", "b"]

    insert_download_job(_job("e"))
    assert [job["id"] for job in list_download_jobs()][-1] == "e"


def test_update_and_delete():
    init_db()
    insert_download_job(_job("a", source='{"host": "pixeldrain"}'))
    assert update_download_job("a", {"state": "running", "bytes_done": 10, "bytes_total": 100})
    row = list_download_jobs()[0]
    assert (row["state"], row["bytes_done"], row["bytes_total"]) == ("running", 10, 100)
    assert row["source"] == '{"host": "pixeldrain"}'
    assert not update_download_job("missing", {"state": "running"})
    with pytest.raises(ValueError):
        _ = update_download_job("a", {"id": "b"})
    assert delete_download_job("a")
    assert not delete_download_job("a")
    assert list_download_jobs() == []


def test_rows_do_not_change_the_sync_time():
    init_db()
    before = database.get_setting("sync_local_modified_at")
    insert_download_job(_job("a"))
    _ = update_download_job("a", {"state": "running"})
    _ = delete_download_job("a")
    assert database.get_setting("sync_local_modified_at") == before


def test_job_id_is_unique():
    init_db()
    insert_download_job(_job("a"))
    with pytest.raises(sqlite3.IntegrityError):
        insert_download_job(_job("a"))
