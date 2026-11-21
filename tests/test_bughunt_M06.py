# SPDX-License-Identifier: GPL-3.0-or-later
"""M06: a pause given while the `.torrent` is fetched must hold once the torrent is added."""

import threading
import time

from test_torrents import (  # type: ignore[import-not-found]
    _queue,
    _ticked,
    data_dir,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    default_seed_mode,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    log_commands,
    make,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    stage,
    torrent_bytes,
    wait_for,
)

from core.downloads.workers import TorrentWorker


def _gated_fetch(gate: threading.Event):
    def fetch() -> bytes:
        _ = gate.wait(5)
        return torrent_bytes()

    return fetch


def test_supervisor_pause_during_fetch_adds_the_torrent_paused(make, tmp_path):  # noqa: F811
    sup = make("stalled")
    gate = threading.Event()
    job_id = sup.start(1, "Game", "1", 7, _gated_fetch(gate))
    assert stage(sup, job_id) == "fetching"
    assert sup.pause(job_id)
    gate.set()
    wait_for(lambda: stage(sup, job_id) == "paused")
    assert wait_for(
        lambda: any(
            c["cmd"] == "add" and c["id"] == job_id for c in log_commands(tmp_path)
        )
    )
    adds = [
        c for c in log_commands(tmp_path) if c["cmd"] == "add" and c["id"] == job_id
    ]
    assert adds[0]["paused"] is True
    time.sleep(0.2)
    assert stage(sup, job_id) == "paused"
    assert not any(c["cmd"] == "resume" for c in log_commands(tmp_path))


def test_supervisor_resume_during_fetch_takes_back_the_pause(make):  # noqa: F811
    sup = make("stalled")
    gate = threading.Event()
    job_id = sup.start(1, "Game", "1", 7, _gated_fetch(gate), paused=True)
    assert sup.resume(job_id)
    gate.set()
    wait_for(lambda: stage(sup, job_id) == "downloading")


def test_queue_pause_of_a_refetching_retry_keeps_the_torrent_paused(make, tmp_path):  # noqa: F811
    from core.downloads.transfer import DownloadError

    sup = make("stalled")
    gate = threading.Event()
    calls = {"n": 0}

    def fetch() -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            raise DownloadError("RPDL could not be reached")
        _ = gate.wait(5)
        return torrent_bytes()

    queue = _queue(sup)
    worker = queue._workers["torrent"]  # pyright: ignore[reportPrivateUsage]
    assert isinstance(worker, TorrentWorker)
    worker._fetch_for = lambda _rpdl_id: fetch  # pyright: ignore[reportPrivateUsage]
    job = queue.add("torrent", 1, "Game", "1", {"rpdl_id": 7}, None)
    assert _ticked(
        queue, lambda: queue.get(job.id).state in ("attention", "retry_wait")
    )
    # The retry fetches the `.torrent` again, started unpaused (it has the slot).
    assert queue.retry(job.id)
    assert _ticked(queue, lambda: queue.get(job.id).state == "running")
    assert stage(sup, job.id) == "fetching"
    assert queue.pause(job.id)
    assert queue.get(job.id).state == "paused"
    gate.set()
    wait_for(lambda: stage(sup, job.id) == "paused")
    # The stage changes before the helper starts; its `ready` event sends `add`.
    assert wait_for(
        lambda: any(
            c["cmd"] == "add" and c["id"] == job.id for c in log_commands(tmp_path)
        )
    )
    queue.tick()
    assert queue.get(job.id).state == "paused" and stage(sup, job.id) == "paused"
    adds = [
        c for c in log_commands(tmp_path) if c["cmd"] == "add" and c["id"] == job.id
    ]
    assert adds[0]["paused"] is True
    assert not any(
        c["cmd"] == "resume" and c["id"] == job.id for c in log_commands(tmp_path)
    )
    # Resume gives it the slot again: now it moves data.
    assert queue.resume(job.id)
    assert _ticked(queue, lambda: stage(sup, job.id) == "downloading")
    assert queue.get(job.id).state == "running"
