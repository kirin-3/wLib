# SPDX-License-Identifier: GPL-3.0-or-later
"""M13: a helper spawned before the old one's reader ran `_on_exit` must get every job again."""

import json
from pathlib import Path

import pytest

from core.downloads import torrents
from core.downloads.torrents import TorrentSupervisor, _Job  # pyright: ignore[reportPrivateUsage]


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


@pytest.fixture
def sup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TorrentSupervisor:
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(torrents.subprocess, "Popen", _FakeProc)
    supervisor = TorrentSupervisor(command=lambda: (["helper"], {}), options=lambda: {})
    # No reader threads: the test delivers each helper's events and exit itself.
    monkeypatch.setattr(supervisor, "_read", lambda proc: None)
    return supervisor


def _active_job(sup: TorrentSupervisor, tmp_path: Path) -> _Job:
    job = _Job(1, "Game", "1.0", 7)
    job.stage = job.phase = "downloading"
    job.folder = tmp_path / "torrent"
    sup._jobs[job.id] = job  # pyright: ignore[reportPrivateUsage]
    return job


def _adds(proc: _FakeProc) -> list[object]:
    return [line["id"] for line in proc.stdin.lines if line.get("cmd") == "add"]


def test_replacement_helper_gets_jobs_the_dead_helper_held(
    sup: TorrentSupervisor, tmp_path: Path
):
    job = _active_job(sup, tmp_path)
    assert sup._spawn()  # pyright: ignore[reportPrivateUsage]
    old = sup._proc  # pyright: ignore[reportPrivateUsage]
    assert isinstance(old, _FakeProc)
    sup._on_event({"event": "ready"}, old)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    assert _adds(old) == [job.id] and job.added

    # The helper dies; another download spawns a new one before the old reader runs `_on_exit`.
    old.returncode = 1
    assert sup._spawn()  # pyright: ignore[reportPrivateUsage]
    new = sup._proc  # pyright: ignore[reportPrivateUsage]
    assert isinstance(new, _FakeProc) and new is not old
    sup._on_exit(old)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    sup._on_event({"event": "ready"}, new)  # pyright: ignore[reportPrivateUsage, reportArgumentType]

    assert _adds(new) == [job.id]
    assert job.added


def test_events_from_a_replaced_helper_are_ignored(
    sup: TorrentSupervisor, tmp_path: Path
):
    job = _active_job(sup, tmp_path)
    assert sup._spawn()  # pyright: ignore[reportPrivateUsage]
    old = sup._proc  # pyright: ignore[reportPrivateUsage]
    assert isinstance(old, _FakeProc)
    old.returncode = 1
    assert sup._spawn()  # pyright: ignore[reportPrivateUsage]
    new = sup._proc  # pyright: ignore[reportPrivateUsage]
    assert isinstance(new, _FakeProc)

    # A late `ready` and `status` from the dead helper change nothing.
    sup._on_event({"event": "ready"}, old)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    sup._on_event(  # pyright: ignore[reportPrivateUsage]
        {
            "event": "status",
            "torrents": [
                {"id": job.id, "state": "downloading", "size": 100, "downloaded": 50}
            ],
        },
        old,  # pyright: ignore[reportArgumentType]
    )
    assert not sup._ready  # pyright: ignore[reportPrivateUsage]
    assert not job.added and job.bytes == 0 and _adds(new) == []

    sup._on_event({"event": "ready"}, new)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    assert _adds(new) == [job.id]
