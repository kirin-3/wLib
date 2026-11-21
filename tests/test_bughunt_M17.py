# SPDX-License-Identifier: GPL-3.0-or-later
"""M17: a torrent that finishes after a pause or a stop becomes ready to install."""

from core.database import init_db, insert_download_job
from core.downloads import torrent_settings
from core.downloads.queue import PAUSED, QUEUED, READY, RUNNING, DownloadQueue, QueueJob
from core.downloads.torrents import TorrentSupervisor, _Job  # pyright: ignore[reportPrivateUsage]
from core.downloads.workers import TorrentContext, TorrentWorker
from tests.test_bughunt_M07 import FakeSupervisor, _queue, _running_job
from tests.test_download_queue import Clock


class LateFinishSupervisor(FakeSupervisor):
    """The helper's `finished` arrives between the worker's snapshot and its resume."""

    def __init__(self) -> None:
        super().__init__()
        self.late = False

    def resume(self, job_id: str) -> bool:
        if not self.late:
            return super().resume(job_id)
        self.resumed.append(job_id)
        self.complete(job_id)
        return False


def test_paused_torrent_that_finishes_becomes_ready_without_resume():
    clock = Clock()
    sup = FakeSupervisor()
    job_id = _running_job(sup, clock)
    queue = _queue(sup, clock)
    queue.load()
    assert queue.pause(job_id)
    sup.resumed.clear()
    # The last piece arrived before the pause; `finished` comes after it.
    sup.complete(job_id)
    queue.tick()
    job = queue.get(job_id)
    assert job.state == READY
    assert job.bytes_done == 100
    assert sup.resumed == []


def test_stopped_waiting_torrent_that_finishes_becomes_ready():
    clock = Clock()
    sup = FakeSupervisor()
    queue = _queue(sup, clock)
    queue._max_active = 1  # pyright: ignore[reportPrivateUsage]
    first = queue.add(
        "torrent", 1, "One", "1", {"rpdl_id": 7}, TorrentContext(lambda: b"")
    ).id
    second = queue.add(
        "torrent", 2, "Two", "1", {"rpdl_id": 8}, TorrentContext(lambda: b"")
    ).id
    queue.tick()
    queue.tick()
    assert queue.get(first).state == RUNNING
    # The second job takes the slot; the first one waits, stopped.
    assert queue.start_now(second) == "yielded"
    assert queue.get(first).state == QUEUED
    sup.complete(first)
    queue.tick()
    assert queue.get(first).state == READY
    assert queue.get(second).state == RUNNING


def test_paused_torrent_that_is_not_done_stays_paused():
    clock = Clock()
    sup = FakeSupervisor()
    job_id = _running_job(sup, clock)
    queue = _queue(sup, clock)
    queue.load()
    assert queue.pause(job_id)
    queue.tick()
    assert queue.get(job_id).state == PAUSED


def test_torrent_that_finishes_just_before_its_resume_is_not_a_failure():
    clock = Clock()
    sup = LateFinishSupervisor()
    job_id = _running_job(sup, clock)
    queue = _queue(sup, clock)
    queue.load()
    assert queue.pause(job_id)
    sup.late = True
    assert queue.resume(job_id)
    job = queue.get(job_id)
    assert job.error_code == "" and job.error_text == ""
    queue.tick()
    assert queue.get(job_id).state == READY


def test_status_shows_a_paused_torrent_that_finished_as_ready():
    # With only paused jobs no tick thread runs: the status read must see it.
    clock = Clock()
    sup = FakeSupervisor()
    job_id = _running_job(sup, clock)
    queue = _queue(sup, clock)
    queue.load()
    assert queue.pause(job_id)
    sup.complete(job_id)
    jobs = queue.status()["jobs"]
    assert isinstance(jobs, list)
    assert [
        job["state"]
        for job in jobs
        if isinstance(job, dict) and job.get("job_id") == job_id
    ] == [READY]


def test_paused_torrents_do_not_read_the_entire_supervisor_for_each_row(monkeypatch):
    init_db()
    supervisor = TorrentSupervisor()
    for game_id in range(1, 6):
        torrent = _Job(game_id, "Game", "1", game_id)
        torrent.stage = "paused"
        supervisor._jobs[torrent.id] = torrent  # pyright: ignore[reportPrivateUsage]
        job = QueueJob(id=torrent.id, kind="torrent", game_id=game_id, state=PAUSED)
        insert_download_job(job.row())
    queue = DownloadQueue(
        [TorrentWorker(lambda: supervisor)], run_thread=False, clock=Clock()
    )
    queue.load()
    assert len(queue.jobs()) == 5

    reads = []
    monkeypatch.setattr(torrent_settings, "seed_ratio", lambda: reads.append("read"))
    queue.refresh()
    assert len(reads) <= 5
