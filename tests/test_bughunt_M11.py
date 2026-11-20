# SPDX-License-Identifier: GPL-3.0-or-later
"""M11: Download now wakes the queue thread even when the job must wait."""
import pytest

from core.database import init_db, update_setting
from core.downloads import queue_settings
from core.downloads.queue import QUEUED
from tests.test_download_queue import Clock, FakeWorker, add, make_queue


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def worker():
    return FakeWorker()


def test_start_now_under_a_hold_starts_the_queue_thread(worker, clock):
    battery = [True]
    init_db()
    update_setting(queue_settings.PAUSE_ON_BATTERY_KEY, "true")
    queue = make_queue(worker, clock, on_battery=lambda: battery[0])
    a = add(queue, 1)
    assert queue.pause(a)
    queue._run_thread = True  # The paused queue is idle: no thread runs.
    assert not queue.thread_alive()
    try:
        assert queue.start_now(a) == "first"
        assert queue.get(a).state == QUEUED
        # Without a thread, nothing would see the hold clear later.
        assert queue.thread_alive()
    finally:
        queue.close()


def test_start_now_pokes_on_every_waiting_outcome(worker, clock, monkeypatch):
    battery = [True]
    init_db()
    update_setting(queue_settings.PAUSE_ON_BATTERY_KEY, "true")
    queue = make_queue(worker, clock, on_battery=lambda: battery[0])
    a = add(queue, 1)
    assert queue.pause(a)
    pokes: list[int] = []
    monkeypatch.setattr(queue, "poke", lambda: pokes.append(1))
    assert queue.start_now(a) == "first"
    assert pokes == [1]
    battery[0] = False
    queue.tick()
    assert worker.started[-1] == a
