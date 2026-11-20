# SPDX-License-Identifier: GPL-3.0-or-later
"""M07: a torrent that finished before the queue saw it reloads as ready."""
from core.database import init_db
from core.downloads.queue import PAUSED, READY, RUNNING, DownloadQueue
from core.downloads.workers import TorrentContext, TorrentWorker
from tests.test_download_queue import Clock


class FakeSupervisor:
    """The parts of `TorrentSupervisor` that `TorrentWorker` calls."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, object]] = {}
        self.resumed: list[str] = []

    def status(self) -> list[dict[str, object]]:
        return [dict(job) for job in self.jobs.values()]

    def start(self, game_id, title, version, rpdl_id, fetch, *, paused=False, job_id=""):
        del fetch
        self.jobs[job_id] = {
            "job_id": job_id, "game_id": game_id, "title": title, "version": version,
            "rpdl_id": rpdl_id, "info_hash": "ab" * 20, "stage": "paused" if paused else "downloading",
            "bytes": 0, "total": 100,
        }
        return job_id

    def resume(self, job_id: str) -> bool:
        self.resumed.append(job_id)
        job = self.jobs.get(job_id)
        if job is None or job["stage"] != "paused":
            return False
        job["stage"] = "downloading"
        return True

    def pause(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job["stage"] != "downloading":
            return False
        job["stage"] = "paused"
        return True

    def cancel(self, job_id: str) -> bool:
        return self.jobs.pop(job_id, None) is not None

    def discard(self, job_id: str) -> bool:
        return self.jobs.pop(job_id, None) is not None

    def set_download_limit(self, kib: int) -> None:
        del kib

    def complete(self, job_id: str) -> None:
        self.jobs[job_id].update(stage="done", bytes=100, seed_state="awaiting_answer")


def _queue(sup: FakeSupervisor, clock: Clock) -> DownloadQueue:
    init_db()
    worker = TorrentWorker(lambda: sup, fetch_for=lambda rpdl_id: lambda: b"")  # pyright: ignore[reportArgumentType]
    return DownloadQueue([worker], run_thread=False, clock=clock, free_space=lambda: 10**12)


def _running_job(sup: FakeSupervisor, clock: Clock) -> str:
    queue = _queue(sup, clock)
    job_id = queue.add("torrent", 1, "Game", "1", {"rpdl_id": 7}, TorrentContext(lambda: b"")).id
    queue.tick()
    queue.tick()
    assert queue.get(job_id).state == RUNNING
    return job_id


def test_saved_running_torrent_that_finished_reloads_as_ready():
    clock = Clock()
    sup = FakeSupervisor()
    job_id = _running_job(sup, clock)
    # The supervisor saved the finish; wLib quit before the queue's next tick.
    sup.complete(job_id)

    restored = _queue(sup, clock)
    restored.load()
    job = restored.get(job_id)
    assert job.state == READY
    assert job.error_code == "" and job.error_text == ""
    assert job.bytes_done == 100
    restored.tick()
    assert restored.get(job_id).state == READY


def test_starting_a_finished_torrent_does_not_report_low_disk():
    clock = Clock()
    sup = FakeSupervisor()
    job_id = _running_job(sup, clock)
    queue = _queue(sup, clock)
    queue.load()
    # The user pauses in the moment between the finish and the next tick.
    assert queue.pause(job_id)
    assert queue.get(job_id).state == PAUSED
    sup.complete(job_id)
    assert queue.resume(job_id)
    job = queue.get(job_id)
    assert job.error_code == "" and job.state == RUNNING
    queue.tick()
    assert queue.get(job_id).state == READY
