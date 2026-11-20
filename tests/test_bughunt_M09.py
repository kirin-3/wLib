# SPDX-License-Identifier: GPL-3.0-or-later
"""M09: a job back from a browser check waits for a slot before it transfers."""
import io
import threading
import time

from core.downloads.queue import (
    ATTENTION,
    PHASE_NEEDS_USER,
    PHASE_PREPARED,
    QUEUED,
    RUNNING,
)
from core.downloads.transfer import FileInfo
from tests.test_download_queue import Clock, FakeWorker, add, make_queue, order
from tests.test_downloads import PIXELDRAIN_URL


def _manager(tmp_path, monkeypatch, probes):
    from core.downloads import manager as manager_module
    from core.downloads.manager import DownloadManager
    from core.downloads.pacing import SpeedLimiter

    def probe(url):
        probes.append(url)
        return FileInfo('update.zip', 4, lambda: io.BytesIO(b'PK\x03\x04'))

    monkeypatch.setattr('core.downloads.transfer.staging_dir', lambda: tmp_path)
    monkeypatch.setattr(manager_module, 'staging_dir', lambda: tmp_path)
    monkeypatch.setattr(manager_module.pixeldrain, 'probe', probe)
    return DownloadManager(limiter=SpeedLimiter())


def _wait_outcome(manager, job_id, outcomes, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(job_id)
        if snapshot and snapshot['outcome'] in outcomes:
            return snapshot['outcome']
        time.sleep(0.01)
    return None


def test_run_that_needed_a_browser_check_waits_for_a_new_slot(tmp_path, monkeypatch):
    probes: list[str] = []
    manager = _manager(tmp_path, monkeypatch, probes)
    finish_check = threading.Event()

    def resolve(progress, _cancel):
        progress('needs_user')  # The queue frees the job's slot here.
        assert finish_check.wait(3)
        return {'ok': True, 'url': PIXELDRAIN_URL}

    manager.add('a', 1, 'pixeldrain')
    manager.run('a', resolve)
    assert _wait_outcome(manager, 'a', ('needs_user',)) == 'needs_user'
    finish_check.set()
    assert _wait_outcome(manager, 'a', ('prepared', 'running', 'done')) == 'prepared'
    assert probes == [] and manager.has_address('a')
    # The queue gives it a slot again: now it transfers without a new resolve.
    manager.run('a', None)
    assert _wait_outcome(manager, 'a', ('done', 'failed')) == 'done'
    assert probes == [PIXELDRAIN_URL]


def test_run_that_resolved_without_a_check_transfers_at_once(tmp_path, monkeypatch):
    probes: list[str] = []
    manager = _manager(tmp_path, monkeypatch, probes)
    manager.add('a', 1, 'pixeldrain')
    manager.run('a', lambda _progress, _cancel: {'ok': True, 'url': PIXELDRAIN_URL})
    assert _wait_outcome(manager, 'a', ('done', 'failed', 'prepared')) == 'done'
    assert probes == [PIXELDRAIN_URL]


def test_queue_puts_a_job_back_from_a_check_first_when_it_had_a_slot():
    clock = Clock()
    worker = FakeWorker()
    queue = make_queue(worker, clock)
    a, b, c = (add(queue, game_id) for game_id in (1, 2, 3))
    worker.states[a].phase = PHASE_NEEDS_USER
    queue.tick()
    assert queue.get(a).state == ATTENTION and queue.get(c).state == RUNNING
    worker.states[a].phase = PHASE_PREPARED
    queue.tick()
    assert [queue.get(job_id).state for job_id in (a, b, c)] == [QUEUED, RUNNING, RUNNING]
    assert order(queue)[0] == a and worker.stopped == []
    worker.finish(b)
    queue.tick()
    assert queue.get(a).state == RUNNING and worker.started.count(a) == 2


def test_queue_restarts_a_running_job_whose_check_ended_between_ticks():
    clock = Clock()
    worker = FakeWorker()
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    assert queue.get(a).state == RUNNING
    # The check came and went before a tick saw it.
    worker.states[a].phase = PHASE_PREPARED
    queue.tick()
    assert queue.get(a).state == RUNNING and worker.started.count(a) == 2
