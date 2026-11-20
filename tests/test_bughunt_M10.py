# SPDX-License-Identifier: GPL-3.0-or-later
"""M10: the battery hold applies before startup and user actions start a transfer."""
import pytest

from core.database import init_db, update_setting
from core.downloads import queue_settings
from core.downloads.queue import ATTENTION, HELD, QUEUED, RETRY_WAIT, RUNNING
from tests.test_download_queue import Clock, FakeWorker, add, make_queue


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def worker():
    return FakeWorker()


def _battery_queue(worker, clock, battery):
    init_db()
    update_setting(queue_settings.PAUSE_ON_BATTERY_KEY, "true")
    return make_queue(worker, clock, on_battery=lambda: battery[0])


def test_startup_holds_a_saved_running_job_on_battery(worker, clock):
    battery = [False]
    queue = _battery_queue(worker, clock, battery)
    a = add(queue, 1)
    b = add(queue, 2)
    assert queue.get(a).state == RUNNING
    battery[0] = True

    worker2 = FakeWorker()
    restored = make_queue(worker2, clock, on_battery=lambda: battery[0])
    restored.load()
    assert worker2.started == []
    assert restored.get(a).state == HELD and restored.get(a).reason == "battery"
    assert restored.get(b).state == HELD
    battery[0] = False
    restored.tick()
    assert worker2.started == [a, b]


def test_resume_does_not_start_when_the_hold_began_since_the_last_tick(worker, clock):
    battery = [False]
    queue = _battery_queue(worker, clock, battery)
    a = add(queue, 1)
    assert queue.pause(a)
    worker.started.clear()
    battery[0] = True  # No tick runs between the unplug and the click.
    assert queue.resume(a)
    assert worker.started == []
    assert queue.get(a).state == QUEUED


def test_retry_does_not_start_when_the_hold_began_since_the_last_tick(worker, clock):
    battery = [False]
    queue = _battery_queue(worker, clock, battery)
    a = add(queue, 1)
    worker.fail(a, "boom")
    queue.tick()
    assert queue.get(a).state in (ATTENTION, RETRY_WAIT)
    worker.started.clear()
    battery[0] = True
    assert queue.retry(a)
    assert worker.started == []
    assert queue.get(a).state == QUEUED


def test_start_now_waits_when_the_hold_began_since_the_last_tick(worker, clock):
    battery = [False]
    queue = _battery_queue(worker, clock, battery)
    a = add(queue, 1)
    assert queue.pause(a)
    worker.started.clear()
    battery[0] = True
    assert queue.start_now(a) == "first"
    assert worker.started == []
    assert queue.get(a).state == QUEUED
