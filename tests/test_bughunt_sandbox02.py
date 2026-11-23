# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for the sandbox/game-files bug hunt (size, uninstall, move, scan)."""

import errno
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

import core.game_files as gf
from core.database import add_game, get_game, init_db
from core.game_native_size import NativeSize, detect_native_size
from core.game_scan import scan_games_folder

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="needs POSIX named pipes or symlinks"
)


# ── Bug 1: a named pipe in an MV game must not stall the size check ──


def _make_mv(root: Path) -> Path:
    (root / "data").mkdir(parents=True)
    (root / "js").mkdir()
    (root / "data" / "System.json").write_text('{"gameTitle": "Test"}')
    (root / "js" / "rpg_core.js").write_text("// core\n")
    return root


def _detect_with_timeout(folder: Path, pipe: Path) -> tuple[bool, object]:
    """Runs the check on a thread; (finished, result). Unblocks a stuck reader."""
    box: list[object] = []
    worker = threading.Thread(
        target=lambda: box.append(detect_native_size(str(folder))), daemon=True
    )
    worker.start()
    worker.join(5)
    finished = not worker.is_alive()
    if not finished:
        # Open and close a writer so the blocked open()/read() sees EOF.
        try:
            fd = os.open(pipe, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            pass
        else:
            os.close(fd)
        worker.join(5)
    return finished, box[0] if box else None


@posix_only
@pytest.mark.parametrize("name", ["plugins.js", "rpg_managers.js"])
def test_named_pipe_in_mv_game_does_not_stall_size_check(tmp_path, name):
    folder = _make_mv(tmp_path)
    if name == "plugins.js":
        (folder / "js" / "rpg_managers.js").write_text(
            "SceneManager._screenWidth = 1024;\nSceneManager._screenHeight = 768;\n"
        )
    pipe = folder / "js" / name
    os.mkfifo(pipe)

    finished, result = _detect_with_timeout(folder, pipe)

    assert finished, "detect_native_size() waited on a named pipe"
    if name == "plugins.js":
        assert result == NativeSize(1024, 768, "js/rpg_managers.js")
    else:
        assert result == NativeSize(816, 624, "MV default")


@posix_only
def test_symlink_to_a_regular_plugins_file_is_still_read(tmp_path):
    folder = _make_mv(tmp_path / "Game")
    real = tmp_path / "plugins-real.js"
    plugins = [
        {
            "name": "YEP_CoreEngine",
            "status": True,
            "description": "",
            "parameters": {"Screen Width": "1280", "Screen Height": "720"},
        }
    ]
    real.write_text("﻿var $plugins =\n" + json.dumps(plugins) + ";\n")
    (folder / "js" / "plugins.js").symlink_to(real)

    assert detect_native_size(str(folder)) == NativeSize(
        1280, 720, "js/plugins.js (YEP_CoreEngine)"
    )


# ── Bug 2: a failed cleanup record after the commit is not a failed uninstall ──


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
        pattern="{title}",
    )
    return library, ctx


def _install(folder: Path) -> tuple[int, Path]:
    exe = folder / "Game.exe"
    folder.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    (folder / "save.dat").write_text("progress")
    game_id = add_game(title="Game", exe_path=str(exe), developer="Dev")
    assert game_id is not None
    return game_id, exe


def test_uninstall_reports_the_commit_when_the_jobs_file_cannot_be_written(
    lib, tmp_path, monkeypatch
):
    _library, ctx = lib
    root = tmp_path / "Elsewhere" / "Game"
    game_id, _exe = _install(root)

    def disk_full(_kind, _path):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(gf, "add_job", disk_full)
    hooks = gf.UninstallHooks(
        running=lambda _gid: False,
        busy=lambda _gid: None,
        saves_folder=lambda _gid: "",
    )

    results = gf.run_uninstall(
        [game_id],
        {game_id: str(root)},
        backup=False,
        remove_from_library=False,
        hooks=hooks,
        ctx=ctx,
    )

    result = results[0]
    # The library change is saved, so the uninstall did happen.
    game = get_game(game_id)
    assert game is not None and game["exe_path"] == ""
    assert result["success"] is True
    assert result["error"] == ""
    assert result["trash"] == "kept"
    staged = [
        name for name in os.listdir(root.parent) if name.startswith(gf.UNINSTALL_PREFIX)
    ]
    assert len(staged) == 1
    kept = root.parent / staged[0]
    # Nothing is deleted without a record, and the warning names the folder.
    time.sleep(0.1)
    assert (kept / "save.dat").read_text() == "progress"
    warning = str(result["warning"])
    assert str(kept) in warning
    assert "No space left on device" in warning


# ── Bug 3: a move queued before the worker publishes keeps the earlier result ──


class _GapLock:
    """Wraps the queue lock; pauses the worker before its Nth acquire."""

    def __init__(self, inner: threading.Lock, pause_at: int) -> None:
        self._inner = inner
        self._pause_at = pause_at
        self._count = 0
        self.reached = threading.Event()
        self.proceed = threading.Event()

    def __enter__(self):
        if threading.current_thread().name == "wlib-game-move":
            self._count += 1
            if self._count == self._pause_at:
                self.reached.set()
                assert self.proceed.wait(5)
        self._inner.acquire()
        return self

    def __exit__(self, *_exc):
        self._inner.release()


def _wait_worker_gone(queue, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with queue._lock:
            if queue._thread is None and queue._current is None and not queue._queue:
                return
        time.sleep(0.01)
    raise AssertionError("queue did not finish")


def test_move_queued_before_the_summary_keeps_the_earlier_failure():
    def runner(job, cancel, on_bytes, set_total):
        if job.game_id == 1:
            return gf.MoveOutcome("failed", "Disk full")
        return gf.MoveOutcome("moved")

    queue = gf.MoveQueue(runner)
    # Worker acquires: 1 takes job 1, 2 adds its outcome, 3 is the loop top
    # that would publish the summary. Pause before 3.
    gap = _GapLock(queue._lock, pause_at=3)
    queue._lock = gap
    assert queue.enqueue([gf.MoveJob(1, "/d")]) == [1]
    assert gap.reached.wait(5)
    # Job 1 is done and its summary is not published yet.
    assert queue.enqueue([gf.MoveJob(2, "/d")]) == [2]
    gap.proceed.set()
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


# ── Bug 4: a game folder and a symlink to it are one scan result ──


@posix_only
def test_scan_lists_a_game_and_its_symlink_alias_once(tmp_path):
    root = tmp_path / "Games"
    game = root / "Game"
    game.mkdir(parents=True)
    (game / "Game.exe").touch()
    # "Alias" sorts before "Game"; the direct folder still wins.
    (root / "Alias").symlink_to(game, target_is_directory=True)

    games = scan_games_folder(str(root))

    assert [entry["exe_path"] for entry in games] == [str(game / "Game.exe")]


@posix_only
def test_scan_keeps_a_game_reachable_only_through_a_symlink(tmp_path):
    outside = tmp_path / "Elsewhere" / "Other"
    outside.mkdir(parents=True)
    (outside / "Other.exe").touch()
    root = tmp_path / "Games"
    game = root / "Game"
    game.mkdir(parents=True)
    (game / "Game.exe").touch()
    (root / "Linked").symlink_to(outside, target_is_directory=True)

    games = scan_games_folder(str(root))

    assert [entry["exe_path"] for entry in games] == [
        str(game / "Game.exe"),
        str(root / "Linked" / "Other.exe"),
    ]
