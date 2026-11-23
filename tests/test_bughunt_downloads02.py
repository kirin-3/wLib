# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for download-queue and transfer bugs (bughunt downloads02)."""

from __future__ import annotations

import http.client
import io
import json
import threading
import time
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from core.database import init_db, insert_download_job, list_download_jobs
from core.downloads import manager as manager_module
from core.downloads import workers as workers_module
from core.downloads.manager import DownloadManager
from core.downloads.pacing import SpeedLimiter
from core.downloads.queue import (
    ATTENTION,
    PAUSED,
    READY,
    RUNNING,
    DownloadQueue,
)
from core.downloads.transfer import (
    DownloadError,
    FileInfo,
    _resume_part,
    transfer,
)
from core.downloads.workers import HostContext, HostWorker

PIXELDRAIN_URL = "https://pixeldrain.com/u/BUGHUNTFILE02"


class _GatedStream(io.BytesIO):
    """Yields `hold` bytes immediately, then waits on `gate` for the rest."""

    def __init__(
        self,
        data: bytes,
        gate: threading.Event,
        offset: int = 0,
        hold: int = 0,
    ) -> None:
        super().__init__(data[offset:])
        self._gate = gate
        self._hold = hold
        self._sent = 0

    def read(self, size: int | None = -1) -> bytes:
        if self._sent >= self._hold:
            assert self._gate.wait(5)
        limit = min(size, 64) if size and size > 0 else 64
        chunk = super().read(limit)
        self._sent += len(chunk)
        return chunk


def _resumable_info(
    data: bytes,
    gate: threading.Event,
    opened: list[int],
    *,
    hold: int = 0,
    resume_key: str = "pixeldrain:bughunt02",
) -> FileInfo:
    def stream() -> Any:
        opened.append(0)
        return _GatedStream(data, gate, hold=hold if len(opened) == 1 else 0)

    def resume(offset: int) -> Any:
        opened.append(offset)
        return _GatedStream(data, gate, offset)

    return FileInfo(
        "game.zip",
        len(data),
        stream,
        resume_factory=resume,
        resume_key=resume_key,
    )


def _patch_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.downloads.transfer.staging_dir", lambda: tmp_path)
    monkeypatch.setattr(manager_module, "staging_dir", lambda: tmp_path)
    monkeypatch.setattr(workers_module, "staging_dir", lambda: tmp_path)


def _resolve_ok(url: str = PIXELDRAIN_URL, calls: list[str] | None = None) -> Any:
    def resolve(_progress: Any, _cancel: threading.Event) -> dict[str, object]:
        if calls is not None:
            calls.append(url)
        return {"ok": True, "url": url}

    return resolve


def _wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _until_tick(queue: DownloadQueue, predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        queue.tick()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _host_source(host: str = "pixeldrain") -> dict[str, object]:
    return {
        "label": "Win",
        "section": "",
        "platform": "windows",
        "host": host,
        "part": "",
    }


# ---------------------------------------------------------------------------
# Bug 1: Pausing before the first progress sample loses the partial on restart
# ---------------------------------------------------------------------------


def test_pause_before_first_progress_sample_keeps_partial_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_staging(tmp_path, monkeypatch)
    init_db()

    gate = threading.Event()
    opened: list[int] = []
    data = b"PK\x03\x04" + bytes(range(256)) * 8

    monkeypatch.setattr(
        manager_module.pixeldrain,
        "probe",
        lambda url, **_: _resumable_info(data, gate, opened, hold=256),
    )

    manager = DownloadManager(limiter=SpeedLimiter())
    queue = DownloadQueue(
        [HostWorker(manager)], run_thread=False, free_space=lambda: 10**12
    )
    job = queue.add(
        "host", 1, "Game", "1.0", _host_source(), HostContext(_resolve_ok())
    )
    # Advance only until the transfer starts running (before any bytes sample).
    assert _until_tick(queue, lambda: queue.get(job.id).state == RUNNING)

    # Wait for the worker thread itself to claim the partial and write bytes,
    # WITHOUT calling queue.tick() so no progress sample has been saved yet.
    assert _wait_for(
        lambda: (
            (snap := manager.snapshot(job.id)) is not None
            and bool(snap["part_name"])
            and int(snap["bytes"]) >= 256
        )
    )
    assert queue.get(job.id).bytes_done == 0

    assert queue.pause(job.id)
    gate.set()
    assert _wait_for(
        lambda: (
            (snap := manager.snapshot(job.id)) is not None
            and snap["outcome"] == "stopped"
        )
    )
    # Even after subsequent ticks on the paused job, the DB row must name part_file.
    queue.tick()
    (part,) = tmp_path.glob("resume-*.part")
    kept = part.stat().st_size
    assert kept >= 256

    rows = list_download_jobs()
    assert len(rows) == 1
    saved_source = json.loads(str(rows[0]["source"]))
    assert saved_source.get("part_file") == part.name

    # Restart wLib with a fresh manager and queue.
    manager2 = DownloadManager(limiter=SpeedLimiter())
    restored = DownloadQueue(
        [HostWorker(manager2, lambda _j: _resolve_ok())],
        run_thread=False,
        free_space=lambda: 10**12,
    )
    restored.load()

    # Startup cleanup must keep the partial file for the paused job.
    assert part.exists() and part.stat().st_size == kept
    assert restored.get(job.id).state == PAUSED

    assert restored.resume(job.id)
    assert _until_tick(restored, lambda: restored.get(job.id).state == READY)
    assert opened == [0, kept]
    assert (tmp_path / "game.zip").read_bytes() == data
    assert not list(tmp_path.glob("*.part"))


def test_part_file_saved_immediately_even_when_progress_save_is_throttled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_staging(tmp_path, monkeypatch)
    init_db()

    gate = threading.Event()
    opened: list[int] = []
    data = b"PK\x03\x04" + bytes(range(256)) * 8
    now = [1_000_000.0]

    monkeypatch.setattr(
        manager_module.pixeldrain,
        "probe",
        lambda url, **_: _resumable_info(data, gate, opened, hold=256),
    )

    manager = DownloadManager(limiter=SpeedLimiter())
    queue = DownloadQueue(
        [HostWorker(manager)],
        run_thread=False,
        clock=lambda: now[0],
        free_space=lambda: 10**12,
    )
    job = queue.add(
        "host", 1, "Game", "1.0", _host_source(), HostContext(_resolve_ok())
    )
    assert _until_tick(queue, lambda: queue.get(job.id).state == RUNNING)
    # Pretend a byte-progress sample was just saved 1 second ago so the 15s
    # throttle would suppress a normal progress save.
    queue.get(job.id).saved_progress_at = now[0] - 1.0

    assert _wait_for(
        lambda: (
            (snap := manager.snapshot(job.id)) is not None
            and bool(snap["part_name"])
            and int(snap["bytes"]) >= 256
        )
    )
    queue.tick()
    (part,) = tmp_path.glob("resume-*.part")
    saved_source = json.loads(str(list_download_jobs()[0]["source"]))
    assert saved_source.get("part_file") == part.name

    gate.set()
    assert _until_tick(queue, lambda: queue.get(job.id).state == READY)


# ---------------------------------------------------------------------------
# Bug 2: Expiring one job deletes a partial still needed by another
# ---------------------------------------------------------------------------


def test_expiring_stale_host_job_keeps_partial_shared_with_newer_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_staging(tmp_path, monkeypatch)
    init_db()

    shared_part = tmp_path / "resume-shared.part"
    solo_part = tmp_path / "resume-solo.part"
    shared_part.write_bytes(b"PK\x03\x04shared")
    solo_part.write_bytes(b"PK\x03\x04solo")

    now = 2_000_000.0
    old = now - 31 * 86400
    recent = now - 60

    # Place the expired row before the surviving row so startup visits it first.
    insert_download_job(
        {
            "id": "expired-shared",
            "kind": "host",
            "game_id": 1,
            "position": 0,
            "state": ATTENTION,
            "error_code": "removed",
            "created_at": old,
            "updated_at": old,
            "source": json.dumps({"host": "pixeldrain", "part_file": shared_part.name}),
        }
    )
    insert_download_job(
        {
            "id": "expired-solo",
            "kind": "host",
            "game_id": 2,
            "position": 1,
            "state": PAUSED,
            "created_at": old,
            "updated_at": old,
            "source": json.dumps({"host": "pixeldrain", "part_file": solo_part.name}),
        }
    )
    insert_download_job(
        {
            "id": "surviving-shared",
            "kind": "host",
            "game_id": 3,
            "position": 2,
            "state": PAUSED,
            "created_at": recent,
            "updated_at": recent,
            "source": json.dumps({"host": "pixeldrain", "part_file": shared_part.name}),
        }
    )

    manager = DownloadManager(limiter=SpeedLimiter())
    queue = DownloadQueue(
        [HostWorker(manager)],
        run_thread=False,
        clock=lambda: now,
        free_space=lambda: 10**12,
    )
    queue.load()

    assert [job.id for job in queue.jobs()] == ["surviving-shared"]
    assert [row["id"] for row in list_download_jobs()] == ["surviving-shared"]
    # The shared partial survives for the newer job; the unshared expired one is gone.
    assert shared_part.exists() and shared_part.read_bytes() == b"PK\x03\x04shared"
    assert not solo_part.exists()

    # Removing the last surviving owner still deletes the partial.
    assert queue.remove("surviving-shared")
    assert not shared_part.exists()


# ---------------------------------------------------------------------------
# Bug 3: A failed full request can erase the partial during Range fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decline",
    [
        "ignore_range",
        "http_416",
    ],
)
@pytest.mark.parametrize(
    "full_error",
    [
        TimeoutError("timed out"),
        URLError("connection reset"),
        http.client.RemoteDisconnected("remote end closed connection"),
    ],
)
def test_full_request_network_failure_after_declined_range_keeps_partial(
    tmp_path: Path, decline: str, full_error: Exception
) -> None:
    data = b"PK\x03\x04" + b"a" * 100

    def resume(_offset: int) -> Any:
        if decline == "http_416":
            raise HTTPError(
                "https://host/file", 416, "Range Not Satisfiable", Message(), None
            )
        return None

    def stream() -> Any:
        raise full_error

    info = FileInfo(
        "game.zip",
        len(data),
        stream,
        resume_factory=resume,
        resume_key="pixeldrain:range-fallback-bug",
    )
    part = _resume_part(tmp_path, info)
    assert part is not None
    part.write_bytes(data[:40])

    with pytest.raises(DownloadError, match="network error"):
        transfer(info, "job", threading.Event(), lambda *_: None, directory=tmp_path)

    assert part.exists() and part.read_bytes() == data[:40]


# ---------------------------------------------------------------------------
# Bug 4: A removed job can still launch its browser resolve
# ---------------------------------------------------------------------------


def test_remove_during_replacement_gap_does_not_launch_resolve_or_leak_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_staging(tmp_path, monkeypatch)
    init_db()

    manager = DownloadManager(limiter=SpeedLimiter())
    worker = HostWorker(manager)
    queue = DownloadQueue([worker], run_thread=False, free_space=lambda: 10**12)

    old_calls: list[str] = []
    old_job = queue.add(
        "host",
        1,
        "Game",
        "1.0",
        _host_source(),
        HostContext(_resolve_ok(calls=old_calls)),
    )
    assert _until_tick(queue, lambda: old_calls == [PIXELDRAIN_URL])
    assert queue.pause(old_job.id)

    removing_old = threading.Event()
    removed_new = threading.Event()
    orig_remove = worker.remove

    def synced_remove(job: Any, *, keep_partial: bool = False) -> None:
        if job.id == old_job.id:
            removing_old.set()
            assert removed_new.wait(5)
        orig_remove(job, keep_partial=keep_partial)

    monkeypatch.setattr(worker, "remove", synced_remove)

    new_calls: list[str] = []
    added_holder: list[Any] = []
    removed_id: list[str] = []

    def do_add() -> None:
        added_holder.append(
            queue.add(
                "host",
                1,
                "Game",
                "1.0",
                _host_source(host="mega"),
                HostContext(_resolve_ok(calls=new_calls)),
                replaces=old_job.id,
            )
        )

    def do_remove() -> None:
        assert removing_old.wait(5)
        current = [j.id for j in queue.jobs() if j.id != old_job.id]
        assert len(current) == 1
        removed_id.append(current[0])
        assert queue.remove(current[0])
        removed_new.set()

    t_add = threading.Thread(target=do_add)
    t_rem = threading.Thread(target=do_remove)
    t_add.start()
    t_rem.start()
    t_add.join(timeout=5)
    t_rem.join(timeout=5)
    assert not t_add.is_alive() and not t_rem.is_alive()

    new_job = added_holder[0]
    assert removed_id == [new_job.id]
    # Give any wrongly-spawned resolver thread a brief moment to run if leaked.
    time.sleep(0.05)
    assert queue.get(new_job.id) is None
    assert list_download_jobs() == []
    assert manager.snapshot(new_job.id) is None
    assert new_calls == []


def test_remove_racing_with_plain_add_cannot_leak_worker_or_launch_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_staging(tmp_path, monkeypatch)
    init_db()

    manager = DownloadManager(limiter=SpeedLimiter())
    worker = HostWorker(manager)
    queue = DownloadQueue([worker], run_thread=False, free_space=lambda: 10**12)

    # Hold the resolve lock so any newly-registered job cannot call resolve()
    # before a winning remove() cancels it, while a leaked prepare() after
    # remove() WILL call resolve() once we release the lock.
    manager._resolve_lock.acquire()
    try:
        new_calls: list[str] = []
        remover_started = threading.Event()
        remover_result: list[bool] = []
        target_id: list[str] = []

        orig_prepare = worker.prepare

        def synced_prepare(job: Any, context: object) -> None:
            target_id.append(job.id)

            def do_remove() -> None:
                remover_started.set()
                remover_result.append(queue.remove(job.id))

            t = threading.Thread(target=do_remove)
            t.start()
            assert remover_started.wait(5)
            # If the queue released its lock before calling prepare(), the
            # remover runs right here and misses the worker record; if the
            # queue holds its lock across registration, the remover waits.
            t.join(timeout=0.05)
            orig_prepare(job, context)

        monkeypatch.setattr(worker, "prepare", synced_prepare)

        job = queue.add(
            "host",
            1,
            "Game",
            "1.0",
            _host_source(),
            HostContext(_resolve_ok(calls=new_calls)),
        )
        assert _wait_for(lambda: len(remover_result) == 1)
        assert remover_result == [True]
    finally:
        manager._resolve_lock.release()

    # Wait briefly for any uncancelled resolver thread to enter resolve().
    time.sleep(0.05)
    assert queue.get(job.id) is None
    assert list_download_jobs() == []
    assert manager.snapshot(job.id) is None
    assert new_calls == []


@pytest.mark.parametrize("keep_partial", [False, True])
def test_removal_during_the_probe_deletes_or_keeps_the_partial(
    tmp_path, monkeypatch, keep_partial
):
    """Removing a job while its link is probed skips transfer(); the partial
    file it would have continued must still go, unless it is kept."""
    from core.downloads.transfer import resume_part_name

    _patch_staging(tmp_path, monkeypatch)
    gate = threading.Event()
    info = _resumable_info(b"x" * 256, gate, [])
    part = tmp_path / resume_part_name(info)
    part.write_bytes(b"x" * 64)
    manager = DownloadManager(limiter=SpeedLimiter())
    probing, release = threading.Event(), threading.Event()

    def probe(_job: Any, _signals: Any) -> FileInfo:
        probing.set()
        assert release.wait(5)
        return info

    monkeypatch.setattr(manager, "_probe", probe)
    manager.add("job", 1, "pixeldrain")
    manager.run("job", _resolve_ok())
    assert probing.wait(5)
    job = manager._jobs["job"]
    manager.forget("job", keep_partial=keep_partial)
    release.set()
    assert job.thread is not None
    job.thread.join(5)
    assert not job.thread.is_alive()
    assert part.exists() is keep_partial
