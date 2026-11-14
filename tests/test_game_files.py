# SPDX-License-Identifier: GPL-3.0-or-later
"""Root detection, plans, trash records and the move engine (core/game_files.py)."""

import json
import os
import threading
import time

import pytest

import core.game_files as gf
from core.database import (
    add_game,
    add_game_launch_target,
    get_game,
    init_db,
    list_game_launch_targets,
    update_game,
)


def _wait_gone(path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while os.path.lexists(path) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not os.path.lexists(path)


def _hooks(running=(), busy=None, saves=None):
    busy = busy or {}
    saves = saves or {}
    return gf.GameHooks(
        running=lambda gid: gid in running,
        busy=lambda gid: busy.get(gid),
        saves_folder=lambda gid: saves.get(gid, ""),
    )


@pytest.fixture
def lib(tmp_path, monkeypatch):
    init_db()
    library = tmp_path / "Library"
    library.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    ctx = gf.FilesContext(
        library_folders=[str(library)],
        downloads_folder=str(tmp_path / "Downloads"),
        pattern="{creator}/{title}/{version}",
    )
    return library, ctx


def _install(folder, exe_rel="Game.exe", **fields):
    exe = folder / exe_rel
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    game_id = add_game(
        title=str(fields.pop("title", "Game")),
        exe_path=str(exe),
        developer=str(fields.pop("developer", "Dev")),
    )
    assert game_id is not None
    if fields:
        _ = update_game(game_id, fields)
    return game_id, exe


# ── Root detection and plans ──


def test_detect_root_uses_wrapper_folder(lib):
    library, ctx = lib
    root = library / "Dev" / "Game" / "1.2"
    game_id, _exe = _install(root, "Game-1.2-pc/Game.exe", version="1.2")
    game = get_game(game_id)
    assert game is not None
    assert gf.detect_root(game, ctx) == (str(root), "pattern")


def test_detect_root_falls_back_to_exe_folder(lib):
    library, ctx = lib
    folder = library / "Loose" / "SomeGame"
    game_id, _exe = _install(folder, version="1.0")
    game = get_game(game_id)
    assert game is not None
    assert gf.detect_root(game, ctx) == (str(folder), "exe_dir")


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ("library", "Refusing to remove the library folder."),
        ("home", "Refusing to remove your home folder."),
        ("downloads", "Refusing to remove the Downloads folder."),
        ("root", "Refusing to remove a drive root."),
        ("elsewhere", "The old folder no longer contains the game's executable."),
    ],
)
def test_plan_uninstall_unsafe_overrides(lib, tmp_path, override, reason):
    library, ctx = lib
    home = tmp_path / "home"
    downloads = tmp_path / "Downloads"
    if override == "home":
        game_id, _ = _install(home / "Game")
    elif override == "downloads":
        game_id, _ = _install(downloads / "Game")
    else:
        game_id, _ = _install(library / "Game")
    (tmp_path / "elsewhere").mkdir()
    path = {
        "library": str(library),
        "home": str(home),
        "downloads": str(downloads),
        "root": os.path.abspath(os.sep),
        "elsewhere": str(tmp_path / "elsewhere"),
    }[override]
    entry = gf.plan_uninstall([game_id], {game_id: path}, _hooks(), ctx)[0]
    assert entry["removable"] is False
    assert entry["reason"] == reason
    assert entry["root_source"] == "override"


def test_plan_uninstall_refuses_folder_with_other_game(lib):
    library, ctx = lib
    game_id, _ = _install(library / "Pack" / "A")
    _other, _ = _install(library / "Pack" / "B", title="Other")
    entry = gf.plan_uninstall([game_id], {game_id: str(library / "Pack")}, _hooks(), ctx)[0]
    assert entry["reason"] == "The folder contains another library game."


def test_plan_uninstall_marks_wishlisted_running_and_busy(lib):
    library, ctx = lib
    wish = add_game(title="Wish", exe_path="")
    running, _ = _install(library / "R", title="R")
    busy, _ = _install(library / "B", title="B")
    entries = gf.plan_uninstall(
        [wish, running, busy, 9999],
        {},
        _hooks(running={running}, busy={busy: "Busy now."}),
        ctx,
    )
    assert [e["blocked_reason"] for e in entries] == [
        gf.NOT_INSTALLED,
        gf.UNINSTALL_RUNNING,
        "Busy now.",
        gf.NOT_FOUND,
    ]


def test_plan_uninstall_saves_inside_root(lib, tmp_path):
    library, ctx = lib
    game_id, _ = _install(library / "Game")
    inside = library / "Game" / "www" / "save"
    inside.mkdir(parents=True)
    outside = tmp_path / "renpy"
    outside.mkdir()
    entry = gf.plan_uninstall([game_id], {}, _hooks(saves={game_id: str(inside)}), ctx)[0]
    assert entry["saves_inside_root"] is True
    entry = gf.plan_uninstall([game_id], {}, _hooks(saves={game_id: str(outside)}), ctx)[0]
    assert entry["saves_inside_root"] is False
    assert entry["saves_folder"] == str(outside)


def test_plan_move_refusals(lib, tmp_path):
    library, ctx = lib
    game_id, _ = _install(library / "Game")
    (library / "Game" / "sub").mkdir()
    dest = tmp_path / "dest"
    (dest / "Game").mkdir(parents=True)

    def reason(target):
        return gf.plan_move([game_id], str(target), {}, _hooks(), ctx)[0]["move_reason"]

    assert reason(dest) == gf.TARGET_EXISTS
    assert reason(library / "Game") == gf.DEST_IS_ROOT
    assert reason(library / "Game" / "sub") == gf.DEST_INSIDE_ROOT
    assert reason(library) == gf.SAME_FOLDER
    assert reason(tmp_path / "missing") == gf.DEST_MISSING
    other = tmp_path / "other"
    other.mkdir()
    entry = gf.plan_move([game_id], str(other), {}, _hooks(), ctx)[0]
    assert entry["move_reason"] == ""
    assert entry["size_bytes"] == 2
    assert entry["target"] == str(other / "Game")


def test_plan_move_not_enough_space(lib, tmp_path, monkeypatch):
    library, ctx = lib
    game_id, _ = _install(library / "Game")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(gf, "_same_device", lambda a, b: False)
    monkeypatch.setattr(gf, "_free_bytes", lambda path: 1)
    entry = gf.plan_move([game_id], str(other), {}, _hooks(), ctx)[0]
    assert str(entry["move_reason"]).startswith("Not enough free space")


# ── Trash and records ──


def test_trash_inside_library_folder(lib):
    library, _ctx = lib
    root = library / "Game"
    root.mkdir()
    (root / "file").write_text("x")
    assert gf.trash_root(str(root), [str(library)]) == "library"
    assert not root.exists()
    deadline = time.monotonic() + 5
    while os.listdir(library) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert os.listdir(library) == []
    assert gf.list_jobs("trash") == []


def test_trash_outside_library_is_recorded(lib, tmp_path, monkeypatch):
    library, _ctx = lib
    root = tmp_path / "Elsewhere" / "Game"
    root.mkdir(parents=True)
    (root / "file").write_text("x")
    started = threading.Event()
    hold = threading.Event()
    real_remove = gf.remove_tree_collect

    def slow_remove(path):
        started.set()
        assert hold.wait(5)
        return real_remove(path)

    monkeypatch.setattr(gf, "remove_tree_collect", slow_remove)
    assert gf.trash_root(str(root), [str(library)]) == "recorded"
    assert started.wait(5)
    recorded = gf.list_jobs("trash")
    assert len(recorded) == 1
    assert os.path.basename(recorded[0]).startswith(".wlib-trash-")
    assert not root.exists()
    hold.set()
    assert _wait_gone(recorded[0])
    deadline = time.monotonic() + 5
    while gf.list_jobs("trash") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gf.list_jobs("trash") == []


def test_trash_failed_rename_deletes_in_place(lib, tmp_path, monkeypatch):
    library, _ctx = lib
    root = tmp_path / "Elsewhere" / "Game"
    root.mkdir(parents=True)
    (root / "file").write_text("x")

    def refuse(_src, _dst):
        raise PermissionError("busy")

    monkeypatch.setattr(gf.os, "rename", refuse)
    monkeypatch.setattr(gf, "defer_remove_tree", lambda path, trash: False)
    assert gf.trash_root(str(root), [str(library)]) == "in_place"
    assert gf.list_jobs("trash") == []
    assert _wait_gone(str(root))


def test_sweep_after_crash(lib, tmp_path):
    library, _ctx = lib
    in_library = library / ".wlib-trash-1"
    (in_library / "data").mkdir(parents=True)
    recorded = tmp_path / "Elsewhere" / ".wlib-trash-2"
    (recorded / "data").mkdir(parents=True)
    temp_move = tmp_path / "dest" / ".wlib-move-3"
    (temp_move / "half").mkdir(parents=True)
    gone = tmp_path / "Elsewhere" / ".wlib-trash-gone"
    offline = tmp_path / "offline-drive" / ".wlib-trash-4"
    for path in (recorded, gone, offline):
        gf.add_job("trash", str(path))
    gf.add_job("move", str(temp_move))

    assert gf.sweep_leftovers([str(library)]) == 3
    for path in (in_library, recorded, temp_move):
        assert _wait_gone(str(path))
    deadline = time.monotonic() + 5
    while len(gf.list_jobs("trash")) > 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    # The offline drive keeps its record for a later start.
    assert gf.list_jobs("trash") == [str(offline)]
    assert gf.list_jobs("move") == []


def test_jobs_file_is_json(lib):
    gf.add_job("trash", "/x/.wlib-trash-1")
    gf.add_job("trash", "/x/.wlib-trash-1")
    with open(gf._jobs_path(), encoding="utf-8") as handle:
        assert json.load(handle) == {"trash": ["/x/.wlib-trash-1"], "move": []}
    gf.remove_job("trash", "/x/.wlib-trash-1")
    assert gf.list_jobs("trash") == []


# ── Move engine ──


def _move_hooks(moved=None, running=()):
    return gf.MoveHooks(
        running=lambda gid: gid in running,
        busy=lambda gid: None,
        moved=(moved.append if moved is not None else (lambda gid: None)),
    )


def _run(job, ctx, hooks=None, cancel=None, totals=None):
    seen = totals if totals is not None else []
    return gf.run_move_job(
        job,
        cancel or threading.Event(),
        seen.append,
        lambda total: None,
        hooks or _move_hooks(),
        ctx,
    )


def _game_with_targets(library):
    root = library / "Game"
    game_id, _exe = _install(root, "Game.exe")
    (root / "bin").mkdir()
    (root / "patch.exe").write_bytes(b"MZ")
    _ = add_game_launch_target(game_id, "Patch", str(root / "patch.exe"))
    _ = update_game(game_id, {"custom_prefix": str(root / "prefix")})
    return game_id, root


def test_same_device_move_renames_and_repoints(lib, tmp_path):
    library, ctx = lib
    game_id, root = _game_with_targets(library)
    dest = tmp_path / "new"
    dest.mkdir()
    moved = []
    outcome = _run(gf.MoveJob(game_id, str(dest)), ctx, _move_hooks(moved))
    assert outcome.status == "moved"
    assert not root.exists()
    row = get_game(game_id)
    assert row is not None
    assert row["exe_path"] == str(dest / "Game" / "Game.exe")
    assert row["custom_prefix"] == str(dest / "Game" / "prefix")
    assert list_game_launch_targets(game_id)[0]["exe_path"] == str(dest / "Game" / "patch.exe")
    assert moved == [game_id]


def test_same_device_db_error_rolls_back(lib, tmp_path, monkeypatch):
    import core.database

    library, ctx = lib
    game_id, root = _game_with_targets(library)
    dest = tmp_path / "new"
    dest.mkdir()

    def broken(*_args):
        raise core.database.sqlite3.OperationalError("locked")

    monkeypatch.setattr(core.database, "repoint_game_paths", broken)
    outcome = _run(gf.MoveJob(game_id, str(dest)), ctx)
    assert outcome.status == "failed"
    assert (root / "Game.exe").is_file()
    assert not (dest / "Game").exists()


def test_cross_device_move_copies_then_trashes(lib, tmp_path, monkeypatch):
    library, ctx = lib
    game_id, root = _game_with_targets(library)
    dest = tmp_path / "other-drive"
    dest.mkdir()
    monkeypatch.setattr(gf, "_same_device", lambda a, b: False)
    seen = []
    outcome = _run(gf.MoveJob(game_id, str(dest)), ctx, totals=seen)
    assert outcome.status == "moved"
    assert (dest / "Game" / "Game.exe").read_bytes() == b"MZ"
    assert sum(seen) == 4
    assert _wait_gone(str(root))
    assert [n for n in os.listdir(dest) if n.startswith(".wlib-move-")] == []
    assert gf.list_jobs("move") == []
    row = get_game(game_id)
    assert row is not None
    assert row["exe_path"] == str(dest / "Game" / "Game.exe")


def test_cross_device_cancel_keeps_old_place(lib, tmp_path, monkeypatch):
    library, ctx = lib
    game_id, root = _game_with_targets(library)
    dest = tmp_path / "other-drive"
    dest.mkdir()
    monkeypatch.setattr(gf, "_same_device", lambda a, b: False)
    cancel = threading.Event()
    real_copy = gf.copy_tree_parallel

    def cancelling_copy(src, dst, workers, cancel_event, on_bytes):
        cancel.set()
        return real_copy(src, dst, workers, cancel_event, on_bytes)

    monkeypatch.setattr(gf, "copy_tree_parallel", cancelling_copy)
    outcome = _run(gf.MoveJob(game_id, str(dest)), ctx, cancel=cancel)
    assert outcome.status == "cancelled"
    assert os.listdir(dest) == []
    assert (root / "Game.exe").is_file()
    assert gf.list_jobs("move") == []
    row = get_game(game_id)
    assert row is not None
    assert row["exe_path"] == str(root / "Game.exe")


def test_cross_device_copy_error(lib, tmp_path, monkeypatch):
    library, ctx = lib
    game_id, root = _game_with_targets(library)
    dest = tmp_path / "other-drive"
    dest.mkdir()
    monkeypatch.setattr(gf, "_same_device", lambda a, b: False)

    def failing_copy(src, dst, *_args):
        (open(os.path.join(dst, "half"), "w")).close()
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(gf, "copy_tree_parallel", failing_copy)
    outcome = _run(gf.MoveJob(game_id, str(dest)), ctx)
    assert outcome.status == "failed"
    assert "No space left" in outcome.reason
    assert os.listdir(dest) == []
    assert (root / "Game.exe").is_file()


def test_cross_device_db_error_deletes_new_copy(lib, tmp_path, monkeypatch):
    import core.database

    library, ctx = lib
    game_id, root = _game_with_targets(library)
    dest = tmp_path / "other-drive"
    dest.mkdir()
    monkeypatch.setattr(gf, "_same_device", lambda a, b: False)

    def broken(*_args):
        raise core.database.sqlite3.OperationalError("locked")

    monkeypatch.setattr(core.database, "repoint_game_paths", broken)
    outcome = _run(gf.MoveJob(game_id, str(dest)), ctx)
    assert outcome.status == "failed"
    assert os.listdir(dest) == []
    assert (root / "Game.exe").is_file()


def test_move_job_rechecks_plan(lib, tmp_path):
    library, ctx = lib
    game_id, _root = _game_with_targets(library)
    dest = tmp_path / "new"
    (dest / "Game").mkdir(parents=True)
    outcome = _run(gf.MoveJob(game_id, str(dest)), ctx)
    assert (outcome.status, outcome.reason) == ("skipped", gf.TARGET_EXISTS)
    running = _run(gf.MoveJob(game_id, str(tmp_path)), ctx, _move_hooks(running={game_id}))
    assert (running.status, running.reason) == ("skipped", gf.MOVE_RUNNING)


# ── Queue ──


def _wait_idle(queue, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = queue.status()
        if not status["running"]:
            return status
        time.sleep(0.01)
    raise AssertionError("queue did not finish")


def test_queue_runs_in_order_and_summarizes():
    order = []
    gate = threading.Event()

    def runner(job, cancel, on_bytes, set_total):
        if job.game_id == 1:
            assert gate.wait(5)
        set_total(10)
        on_bytes(10)
        order.append(job.game_id)
        if job.game_id == 3:
            return gf.MoveOutcome("skipped", "The target folder already exists.")
        if job.game_id == 4:
            return gf.MoveOutcome("failed", "boom")
        return gf.MoveOutcome("moved")

    queue = gf.MoveQueue(runner)
    assert queue.enqueue([gf.MoveJob(1, "/d"), gf.MoveJob(2, "/d"), gf.MoveJob(3, "/d")]) == [1, 2, 3]
    assert queue.enqueue([gf.MoveJob(4, "/d"), gf.MoveJob(2, "/d")]) == [4]
    deadline = time.monotonic() + 5
    while queue.state(1) != "moving" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert queue.state(2) == "waiting"
    status = queue.status()
    assert status["position"] == 1 and status["total"] == 4
    assert status["waiting"] == [2, 3, 4]
    gate.set()
    status = _wait_idle(queue)
    assert order == [1, 2, 3, 4]
    assert status["summary"] == {
        "moved": 2,
        "skipped": 1,
        "cancelled": 0,
        "failed": 1,
        "first_reason": "The target folder already exists.",
    }
    assert "summary" not in queue.status()


def test_queue_cancel_all():
    started = threading.Event()

    def runner(job, cancel, on_bytes, set_total):
        started.set()
        assert cancel.wait(5)
        return gf.MoveOutcome("cancelled")

    queue = gf.MoveQueue(runner)
    _ = queue.enqueue([gf.MoveJob(1, "/d"), gf.MoveJob(2, "/d"), gf.MoveJob(3, "/d")])
    assert started.wait(5)
    queue.cancel(cancel_all=True)
    status = _wait_idle(queue)
    assert status["summary"]["cancelled"] == 3
    assert queue.state(2) == ""


def test_queue_speed_uses_recent_bytes():
    now = [100.0]
    gate = threading.Event()
    progressed = threading.Event()

    def runner(job, cancel, on_bytes, set_total):
        set_total(1000)
        now[0] = 102.0
        on_bytes(400)
        progressed.set()
        assert gate.wait(5)
        return gf.MoveOutcome("moved")

    queue = gf.MoveQueue(runner, clock=lambda: now[0])
    _ = queue.enqueue([gf.MoveJob(7, "/d")])
    assert progressed.wait(5)
    current = queue.status()["current"]
    assert current["bytes_done"] == 400 and current["bytes_total"] == 1000
    assert current["speed_bps"] == 200
    gate.set()
    _ = _wait_idle(queue)
