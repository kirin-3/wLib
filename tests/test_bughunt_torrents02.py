# SPDX-License-Identifier: GPL-3.0-or-later
"""Torrent bug hunt 02: removal race, failed startup spawn, stale crash cutoff, bare versions."""

import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from core import database
from core.database import init_db
from core.downloads import rpdl_match as m
from core.downloads import seeding, torrent_settings, torrents
from core.downloads.queue import DownloadQueue
from core.downloads.torrents import (
    MSG_KEEPS_STOPPING,
    MSG_UNAVAILABLE,
    TorrentSupervisor,
    _Job,  # pyright: ignore[reportPrivateUsage]
    torrents_dir,
)
from core.downloads.workers import TorrentWorker

# --- bug 1: removal while the helper reports `finished` --------------------


def _state(folder: Path, job_id: str, phase: str) -> None:
    (folder / "data").mkdir(parents=True)
    _ = (folder / "state.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "game_id": 1,
                "rpdl_id": 7,
                "title": "Game",
                "version": "1",
                "phase": phase,
                "size": 100,
                "downloaded": 40,
                "updated": time.time(),
            }
        ),
        encoding="utf-8",
    )


def _load_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TorrentSupervisor, DownloadQueue]:
    supervisor = TorrentSupervisor()
    monkeypatch.setattr(supervisor, "_spawn", Mock(return_value=True))
    supervisor.load()
    queue = DownloadQueue([TorrentWorker(lambda: supervisor)], run_thread=False)
    queue.load()
    return supervisor, queue


def _finish_after_snapshot(
    supervisor: TorrentSupervisor, monkeypatch: pytest.MonkeyPatch, job_id: str
) -> None:
    """The helper's `finished` arrives right after the worker read the stage."""
    original = supervisor.status_for
    fired: list[bool] = []

    def status_for(requested: str) -> dict[str, object] | None:
        snapshot = original(requested)
        if not fired:
            fired.append(True)
            supervisor._on_event({"event": "finished", "id": job_id})  # pyright: ignore[reportPrivateUsage]
        return snapshot

    monkeypatch.setattr(supervisor, "status_for", status_for)


def test_removal_racing_a_finish_leaves_no_ended_torrent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()
    database.update_setting(torrent_settings.SEED_MODE_KEY, seeding.MODE_NEVER)
    folder = torrents_dir() / ("c" * 40)
    _state(folder, "race-never", "downloading")
    supervisor, queue = _load_downloads(monkeypatch)
    assert [job.id for job in queue.jobs()] == ["race-never"]
    _finish_after_snapshot(supervisor, monkeypatch, "race-never")

    assert queue.remove("race-never")

    assert supervisor.status() == []
    assert not folder.exists()
    restarted, restarted_queue = _load_downloads(monkeypatch)
    assert restarted.status() == [] and restarted_queue.jobs() == []


def test_removal_racing_a_finish_marks_a_seeding_torrent_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()
    database.update_setting(torrent_settings.SEED_MODE_KEY, seeding.MODE_ALWAYS)
    folder = torrents_dir() / ("d" * 40)
    _state(folder, "race-seed", "downloading")
    supervisor, queue = _load_downloads(monkeypatch)
    _finish_after_snapshot(supervisor, monkeypatch, "race-seed")

    assert queue.remove("race-seed")

    status = supervisor.status()
    assert status[0]["seed_state"] == "seeding" and status[0]["queue_removed"] is True
    assert folder.is_dir()
    restarted, restarted_queue = _load_downloads(monkeypatch)
    assert restarted.status()[0]["queue_removed"] is True
    assert restarted_queue.jobs() == []


def test_removal_of_an_active_torrent_still_cancels_and_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()
    folder = torrents_dir() / ("e" * 40)
    _state(folder, "active-job", "downloading")
    supervisor, queue = _load_downloads(monkeypatch)

    assert queue.remove("active-job")

    assert not folder.exists()
    assert supervisor.status()[0]["stage"] == "cancelled"
    assert list(queue.jobs()) == []


# --- helpers for the supervisor lifecycle tests ----------------------------


class _Pipe:
    def __init__(self) -> None:
        self.lines: list[dict[str, object]] = []

    def write(self, text: str) -> int:
        self.lines.append(json.loads(text))
        return len(text)

    def flush(self) -> None:
        pass


class _FakeProc:
    def __init__(self) -> None:
        self.stdin = _Pipe()
        self.stdout = None
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


class _Popen:
    """A stand-in for subprocess.Popen: the first `fail` starts raise OSError."""

    def __init__(self, fail: int = 0) -> None:
        self.fail = fail
        self.procs: list[_FakeProc] = []

    def __call__(self, *_args: object, **_kwargs: object) -> _FakeProc:
        if self.fail:
            self.fail -= 1
            raise OSError("Resource temporarily unavailable")
        proc = _FakeProc()
        self.procs.append(proc)
        return proc


class _Timers:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float, _fn: object) -> "_Timers":
        self.delays.append(delay)
        return self

    daemon = True

    def start(self) -> None:
        pass


@pytest.fixture
def timers(monkeypatch: pytest.MonkeyPatch) -> _Timers:
    fake = _Timers()
    monkeypatch.setattr(torrents.threading, "Timer", fake)
    return fake


def _supervisor(
    monkeypatch: pytest.MonkeyPatch, popen: _Popen, clock: object = None
) -> TorrentSupervisor:
    monkeypatch.setattr(torrents.subprocess, "Popen", popen)
    kwargs: dict[str, object] = {}
    if clock is not None:
        kwargs["clock"] = clock
    supervisor = TorrentSupervisor(
        command=lambda: (["helper"], {}),
        options=lambda: {},
        backoff=(1.0, 5.0, 15.0),
        **kwargs,  # pyright: ignore[reportArgumentType]
    )
    monkeypatch.setattr(supervisor, "_read", lambda proc: None)
    return supervisor


def _jobs(supervisor: TorrentSupervisor) -> dict[str, _Job]:
    return supervisor._jobs  # pyright: ignore[reportPrivateUsage]


# --- bug 2: a failed startup spawn ----------------------------------------


def test_failed_startup_spawn_schedules_a_retry_and_keeps_the_pause(
    monkeypatch: pytest.MonkeyPatch, timers: _Timers
) -> None:
    init_db()
    _state(torrents_dir() / ("f" * 40), "restored", "downloading")
    popen = _Popen(fail=1)
    supervisor = _supervisor(monkeypatch, popen)

    supervisor.load()

    job = _jobs(supervisor)["restored"]
    assert popen.procs == []
    assert timers.delays == [1.0] and job.restarting

    supervisor._restart()  # pyright: ignore[reportPrivateUsage]
    assert len(popen.procs) == 1
    supervisor._on_event({"event": "ready"}, popen.procs[0])  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    adds = [c for c in popen.procs[0].stdin.lines if c.get("cmd") == "add"]
    # The queue decides when it moves data: the torrent is added paused.
    assert adds == [
        {"cmd": "add", "id": "restored", "dir": str(job.folder), "paused": True}
    ]
    assert not job.restarting and job.stage == "paused"


def test_queue_start_of_a_restored_job_after_failed_spawn_is_not_stranded(
    monkeypatch: pytest.MonkeyPatch, timers: _Timers
) -> None:
    init_db()
    _state(torrents_dir() / ("1" * 40), "stranded", "downloading")
    popen = _Popen(fail=1)
    supervisor = _supervisor(monkeypatch, popen)
    supervisor.load()
    worker = TorrentWorker(lambda: supervisor)
    queue = DownloadQueue([worker], run_thread=False)
    queue.load()
    job = queue.jobs()[0]

    worker.start(job)

    # Either a helper runs or a restart is scheduled; never "downloading" with neither.
    assert popen.procs or timers.delays


def test_resume_without_helper_or_pending_restart_starts_one(
    monkeypatch: pytest.MonkeyPatch, timers: _Timers
) -> None:
    init_db()
    _state(torrents_dir() / ("2" * 40), "no-helper", "downloading")
    popen = _Popen(fail=1)
    supervisor = _supervisor(monkeypatch, popen)
    supervisor.load()
    job = _jobs(supervisor)["no-helper"]
    job.restarting = False  # no retry is pending (for example it gave up)

    assert supervisor.resume("no-helper")

    assert len(popen.procs) == 1 and job.stage == "downloading"
    supervisor._on_event({"event": "ready"}, popen.procs[0])  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    adds = [c for c in popen.procs[0].stdin.lines if c.get("cmd") == "add"]
    assert adds[0]["paused"] is False


def test_resume_reports_an_engine_error_when_the_helper_cannot_start(
    monkeypatch: pytest.MonkeyPatch, timers: _Timers
) -> None:
    init_db()
    _state(torrents_dir() / ("3" * 40), "no-engine", "downloading")
    popen = _Popen(fail=5)
    supervisor = _supervisor(monkeypatch, popen)
    supervisor.load()
    job = _jobs(supervisor)["no-engine"]
    job.restarting = False

    assert not supervisor.resume("no-engine")

    assert job.stage == "failed" and job.error == MSG_UNAVAILABLE


def test_resume_waits_for_an_already_scheduled_restart(
    monkeypatch: pytest.MonkeyPatch, timers: _Timers
) -> None:
    init_db()
    _state(torrents_dir() / ("4" * 40), "waiting", "downloading")
    popen = _Popen(fail=1)
    supervisor = _supervisor(monkeypatch, popen)
    supervisor.load()
    assert timers.delays == [1.0]

    assert supervisor.resume("waiting")

    assert popen.procs == []  # the scheduled restart starts the helper
    assert _jobs(supervisor)["waiting"].stage == "downloading"


# --- bug 3: an old crash cutoff must not disable a later download ----------


def _crash(supervisor: TorrentSupervisor) -> None:
    proc = supervisor._proc  # pyright: ignore[reportPrivateUsage]
    assert isinstance(proc, _FakeProc)
    proc.returncode = 1
    supervisor._on_exit(proc)  # pyright: ignore[reportPrivateUsage, reportArgumentType]


def _active_job(supervisor: TorrentSupervisor, tmp_path: Path, name: str) -> _Job:
    job = _Job(1, "Game", "1.0", 7)
    job.stage = job.phase = "downloading"
    job.folder = tmp_path / name
    _jobs(supervisor)[job.id] = job
    return job


def _exhaust_budget(
    supervisor: TorrentSupervisor, tmp_path: Path, popen: _Popen
) -> _Job:
    job = _active_job(supervisor, tmp_path, "first")
    assert supervisor._spawn()  # pyright: ignore[reportPrivateUsage]
    for _ in range(torrents.RESTART_LIMIT):
        _crash(supervisor)
        supervisor._restart()  # pyright: ignore[reportPrivateUsage]
    _crash(supervisor)
    assert job.stage == "failed" and job.error == MSG_KEEPS_STOPPING
    assert supervisor._proc is None  # pyright: ignore[reportPrivateUsage]
    assert len(popen.procs) == torrents.RESTART_LIMIT + 1
    return job


def test_a_crash_after_the_window_restarts_a_later_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, timers: _Timers
) -> None:
    now = [1000.0]
    popen = _Popen()
    supervisor = _supervisor(monkeypatch, popen, clock=lambda: now[0])
    _ = _exhaust_budget(supervisor, tmp_path, popen)
    delays = len(timers.delays)

    now[0] += torrents.RESTART_WINDOW + 100  # the crash window is over
    later = _active_job(supervisor, tmp_path, "later")
    assert supervisor._spawn()  # a new download starts a helper  # pyright: ignore[reportPrivateUsage]
    _crash(supervisor)

    assert later.restarting and len(timers.delays) == delays + 1
    started = len(popen.procs)
    supervisor._restart()  # pyright: ignore[reportPrivateUsage]
    assert supervisor._proc is not None  # pyright: ignore[reportPrivateUsage]
    assert len(popen.procs) == started + 1


def test_a_crash_loop_inside_the_window_stays_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, timers: _Timers
) -> None:
    now = [1000.0]
    popen = _Popen()
    supervisor = _supervisor(monkeypatch, popen, clock=lambda: now[0])
    _ = _exhaust_budget(supervisor, tmp_path, popen)
    delays = len(timers.delays)

    now[0] += 30  # still inside the 10 minute window
    later = _active_job(supervisor, tmp_path, "later")
    assert supervisor._spawn()  # pyright: ignore[reportPrivateUsage]
    _crash(supervisor)

    assert later.stage == "failed" and later.error == MSG_KEEPS_STOPPING
    assert len(timers.delays) == delays
    supervisor._restart()  # pyright: ignore[reportPrivateUsage]
    assert supervisor._proc is None  # pyright: ignore[reportPrivateUsage]


# --- bug 4: a bare integer version equal to a number in the game name ------


def test_bare_version_equal_to_the_name_number_still_ranks() -> None:
    results = [{"id": 1, "title": "Game-2-2-pc", "seeders": 5, "date": 1}]

    out = m.rank(results, "Game 2", "2")

    assert [r["id"] for r in out["results"]] == [1]  # pyright: ignore[reportIndexIssue, reportGeneralTypeIssues]
    assert out["results"][0]["version_match"] is True  # pyright: ignore[reportIndexIssue]
    assert out["preselected_id"] == 1


def test_bare_version_listing_beats_a_plain_sequel_listing() -> None:
    results = [
        {"id": 1, "title": "Game-2-pc", "seeders": 50, "date": 9},
        {"id": 2, "title": "Game-2-2-pc", "seeders": 5, "date": 1},
    ]

    out = m.rank(results, "Game 2", "2")

    shown = out["results"]
    assert shown[0]["id"] == 2 and shown[0]["version_match"] is True  # pyright: ignore[reportIndexIssue]
    assert shown[1]["version_match"] is False  # pyright: ignore[reportIndexIssue]


def test_name_numbers_and_other_versions_are_still_scored_as_before() -> None:
    # A different sequel is still penalised and hidden.
    assert (
        m.rank([{"id": 1, "title": "Game-3-2-pc", "seeders": 5}], "Game 2", "2")[
            "results"
        ]
        == []
    )
    # A game name without a number: a lone number stays a sequel number.
    assert (
        m.rank([{"id": 1, "title": "Game-2-pc", "seeders": 5}], "Game", "2")["results"]
        == []
    )
    # A dotted version next to the name number is unchanged.
    out = m.rank([{"id": 1, "title": "Game-2-1.4-pc", "seeders": 5}], "Game 2", "1.4")
    assert out["preselected_id"] == 1
    # A second, different number is not the wanted version.
    assert m.title_score("Game 2", "Game-2-3-pc") < m.HIDE_BELOW
