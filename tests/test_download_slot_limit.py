# SPDX-License-Identifier: GPL-3.0-or-later
from core.database import init_db, update_setting
from core.downloads import queue_settings
from core.downloads.queue import PHASE_DONE, QUEUED, RUNNING, DownloadQueue
from tests.test_download_queue import FakeWorker


def test_download_now_waits_when_running_count_exceeds_lowered_limit() -> None:
    init_db()
    update_setting(queue_settings.MAX_ACTIVE_KEY, "3")
    worker = FakeWorker()
    queue = DownloadQueue([worker], run_thread=False)
    jobs = [
        queue.add("host", game_id, f"Game {game_id}", "1.0", {})
        for game_id in range(1, 6)
    ]
    queue.tick()
    running_ids = [job.id for job in jobs[:3]]
    assert worker.started == running_ids

    update_setting(queue_settings.MAX_ACTIVE_KEY, "1")
    queue.apply_settings()
    earlier, selected = jobs[3:]
    assert queue.start_now(selected.id) == "first"
    assert worker.stopped == []
    assert worker.started == running_ids
    assert all(job.state == RUNNING for job in jobs[:3])
    assert [job.id for job in queue.jobs() if job.state == QUEUED] == [selected.id, earlier.id]

    for job in jobs[:2]:
        worker.states[job.id].phase = PHASE_DONE
        queue.tick()
        assert worker.started == running_ids

    worker.states[jobs[2].id].phase = PHASE_DONE
    queue.tick()
    assert worker.started == [*running_ids, selected.id]
    assert selected.state == RUNNING
    assert earlier.state == QUEUED
