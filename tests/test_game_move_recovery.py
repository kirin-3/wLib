# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery of interrupted moves, offline-drive job records and unread move summaries."""

import json
import threading
import time

import pytest

import core.database
import core.game_files as gf
from core.database import add_game, get_game, init_db


class _Crash(BaseException):
    """Stands for wLib being killed: nothing after it runs."""


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    init_db()
    library = tmp_path / "Library"
    library.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return gf.FilesContext(
        library_folders=[str(library)],
        downloads_folder=str(tmp_path / "Downloads"),
        pattern="{creator}/{title}/{version}",
    )


def _hooks():
    return gf.MoveHooks(running=lambda gid: False, busy=lambda gid: None)


def _game(root):
    root.mkdir(parents=True)
    (root / "Game.exe").write_bytes(b"MZ")
    game_id = add_game(title="Game", exe_path=str(root / "Game.exe"), developer="Dev")
    assert game_id is not None
    return game_id


def test_next_start_finishes_a_move_stopped_before_the_library_update(ctx, tmp_path, monkeypatch):
    root = tmp_path / "Library" / "Game"
    game_id = _game(root)
    dest = tmp_path / "new"
    dest.mkdir()
    real_repoint = core.database.repoint_game_paths

    def killed(*_args):
        raise _Crash()

    monkeypatch.setattr(core.database, "repoint_game_paths", killed)
    with pytest.raises(_Crash):
        _ = gf.run_move_job(
            gf.MoveJob(game_id, str(dest)),
            threading.Event(),
            lambda _n: None,
            lambda _n: None,
            _hooks(),
            ctx,
        )
    # The files moved, the library did not.
    assert (dest / "Game" / "Game.exe").is_file()
    assert not root.exists()
    row = get_game(game_id)
    assert row is not None and row["exe_path"] == str(root / "Game.exe")
    assert len(gf.list_renames()) == 1

    monkeypatch.setattr(core.database, "repoint_game_paths", real_repoint)
    _ = gf.sweep_leftovers(ctx.library_folders)
    row = get_game(game_id)
    assert row is not None and row["exe_path"] == str(dest / "Game" / "Game.exe")
    assert gf.list_renames() == []


def test_same_device_move_leaves_no_rename_record(ctx, tmp_path):
    game_id = _game(tmp_path / "Library" / "Game")
    dest = tmp_path / "new"
    dest.mkdir()
    outcome = gf.run_move_job(
        gf.MoveJob(game_id, str(dest)),
        threading.Event(),
        lambda _n: None,
        lambda _n: None,
        _hooks(),
        ctx,
    )
    assert outcome.status == "moved"
    assert gf.list_renames() == []


def test_rename_record_without_the_rename_is_dropped(ctx, tmp_path):
    src = tmp_path / "Library" / "Game"
    src.mkdir(parents=True)
    dest = tmp_path / "new"
    dest.mkdir()
    _ = gf.add_rename(1, str(src), str(dest / "Game"))
    _ = gf.sweep_leftovers(ctx.library_folders)
    assert gf.list_renames() == []
    assert src.is_dir()


def test_rename_record_of_an_offline_drive_stays(ctx, tmp_path):
    _ = gf.add_rename(1, str(tmp_path / "gone" / "Game"), str(tmp_path / "offline" / "Game"))
    _ = gf.sweep_leftovers(ctx.library_folders)
    assert len(gf.list_renames()) == 1


def _set_recorded_device(path, device):
    with open(gf._jobs_path(), encoding="utf-8") as handle:
        data = json.load(handle)
    data["devices"][path] = device
    with open(gf._jobs_path(), "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def test_record_on_an_unmounted_drive_stays(ctx, tmp_path):
    # The mount point folder is there, but the drive is not mounted on it.
    mount_point = tmp_path / "mnt-drive"
    mount_point.mkdir()
    trash = str(mount_point / ".wlib-trash-1")
    gf.add_job("trash", trash)
    real_device = mount_point.stat().st_dev
    _set_recorded_device(trash, real_device + 1)

    _ = gf.sweep_leftovers(ctx.library_folders)
    assert gf.list_jobs("trash") == [trash]

    # Back on the recorded device, the folder is gone, so the record goes.
    _set_recorded_device(trash, real_device)
    _ = gf.sweep_leftovers(ctx.library_folders)
    assert gf.list_jobs("trash") == []


def test_record_from_an_older_file_without_devices(ctx, tmp_path):
    parent = tmp_path / "Elsewhere"
    parent.mkdir()
    trash = str(parent / ".wlib-trash-1")
    with open(gf._jobs_path(), "w", encoding="utf-8") as handle:
        json.dump({"trash": [trash], "move": []}, handle)
    _ = gf.sweep_leftovers(ctx.library_folders)
    assert gf.list_jobs("trash") == []


def _wait_worker_gone(queue, timeout=5.0):
    # Waits without status(), which would read the summary.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with queue._lock:
            if queue._thread is None and queue._current is None and not queue._queue:
                return
        time.sleep(0.01)
    raise AssertionError("queue did not finish")


def test_new_move_keeps_an_unread_failure():
    def runner(job, cancel, on_bytes, set_total):
        if job.game_id == 1:
            return gf.MoveOutcome("failed", "Disk full")
        return gf.MoveOutcome("moved")

    queue = gf.MoveQueue(runner)
    assert queue.enqueue([gf.MoveJob(1, "/d")]) == [1]
    _wait_worker_gone(queue)
    # The user queues another move before the UI polls.
    assert queue.enqueue([gf.MoveJob(2, "/d")]) == [2]
    _wait_worker_gone(queue)
    status = queue.status()
    assert status["summary"] == {
        "moved": 1,
        "skipped": 0,
        "cancelled": 0,
        "failed": 1,
        "first_reason": "Disk full",
    }
    assert "summary" not in queue.status()
