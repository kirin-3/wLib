# SPDX-License-Identifier: GPL-3.0-or-later
"""M14: a helper restart whose spawn fails must spend the budget and try again or fail the jobs."""
import json
from pathlib import Path

import pytest

from core.downloads import torrents
from core.downloads.torrents import MSG_KEEPS_STOPPING, TorrentSupervisor, _Job  # pyright: ignore[reportPrivateUsage]


class _Pipe:
    def __init__(self) -> None:
        self.lines: list[dict[str, object]] = []

    def write(self, text: str) -> int:
        self.lines.append(json.loads(text))
        return len(text)

    def flush(self) -> None:
        pass


class _FakeProc:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.stdin = _Pipe()
        self.stdout = None
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


class _Timers:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float, _fn: object) -> "_Timers":
        self.delays.append(delay)
        return self

    daemon = True

    def start(self) -> None:
        pass


def _no_popen(*_args: object, **_kwargs: object) -> None:
    raise OSError("Resource temporarily unavailable")


@pytest.fixture
def timers(monkeypatch: pytest.MonkeyPatch) -> _Timers:
    fake = _Timers()
    monkeypatch.setattr(torrents.threading, "Timer", fake)
    return fake


@pytest.fixture
def sup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TorrentSupervisor:
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(torrents.subprocess, "Popen", _FakeProc)
    supervisor = TorrentSupervisor(command=lambda: (["helper"], {}), options=lambda: {}, backoff=(1.0, 5.0, 15.0))
    monkeypatch.setattr(supervisor, "_read", lambda proc: None)
    return supervisor


def _crash_with_active_job(sup: TorrentSupervisor, tmp_path: Path) -> _Job:
    job = _Job(1, "Game", "1.0", 7)
    job.stage = job.phase = "downloading"
    job.folder = tmp_path / "torrent"
    sup._jobs[job.id] = job  # pyright: ignore[reportPrivateUsage]
    assert sup._spawn()  # pyright: ignore[reportPrivateUsage]
    proc = sup._proc  # pyright: ignore[reportPrivateUsage]
    assert isinstance(proc, _FakeProc)
    proc.returncode = 1
    sup._on_exit(proc)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    return job


def test_failed_respawn_schedules_the_next_try(
    sup: TorrentSupervisor, tmp_path: Path, timers: _Timers, monkeypatch: pytest.MonkeyPatch
):
    job = _crash_with_active_job(sup, tmp_path)
    assert timers.delays == [1.0] and job.restarting

    monkeypatch.setattr(torrents.subprocess, "Popen", _no_popen)
    sup._restart()  # pyright: ignore[reportPrivateUsage]

    assert timers.delays == [1.0, 5.0]
    assert job.restarting and job.stage == "downloading"


def test_failed_respawns_spend_the_budget_then_fail_the_jobs(
    sup: TorrentSupervisor, tmp_path: Path, timers: _Timers, monkeypatch: pytest.MonkeyPatch
):
    job = _crash_with_active_job(sup, tmp_path)
    monkeypatch.setattr(torrents.subprocess, "Popen", _no_popen)
    for _ in range(torrents.RESTART_LIMIT):
        sup._restart()  # pyright: ignore[reportPrivateUsage]

    assert timers.delays == [1.0, 5.0, 15.0]
    assert job.stage == "failed" and job.error == MSG_KEEPS_STOPPING
    assert sup._gave_up  # pyright: ignore[reportPrivateUsage]
    sup._restart()  # pyright: ignore[reportPrivateUsage]
    assert len(timers.delays) == 3


def test_a_working_respawn_schedules_nothing(sup: TorrentSupervisor, tmp_path: Path, timers: _Timers):
    _ = _crash_with_active_job(sup, tmp_path)
    sup._restart()  # pyright: ignore[reportPrivateUsage]
    assert isinstance(sup._proc, _FakeProc)  # pyright: ignore[reportPrivateUsage]
    assert timers.delays == [1.0]
