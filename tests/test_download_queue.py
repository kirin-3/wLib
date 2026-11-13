# SPDX-License-Identifier: GPL-3.0-or-later
import time

import pytest

from core.database import init_db, list_download_jobs, update_setting
from core.downloads import queue_settings
from core.downloads.queue import (
    ATTENTION,
    HELD,
    PAUSED,
    PHASE_DONE,
    PHASE_FAILED,
    PHASE_IDLE,
    PHASE_NEEDS_USER,
    PHASE_PREPARED,
    PHASE_PREPARING,
    PHASE_RUNNING,
    PHASE_STOPPED,
    PREPARING,
    QUEUED,
    READY,
    RETRY_WAIT,
    RUNNING,
    DownloadQueue,
    QueueError,
    QueueJob,
    WorkerState,
)


class FakeWorker:
    def __init__(self, kind="host"):
        self.kind = kind
        self.states: dict[str, WorkerState] = {}
        self.prepared: list[str] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.removed: list[str] = []
        self.shares: list[tuple[int, bool]] = []
        self.cleaned: list[list[str]] = []
        self.cannot_continue: set[str] = set()
        self.alive: set[str] | None = None
        self.orphans: list[QueueJob] = []
        self.prepare_phase = PHASE_PREPARED

    def _state(self, job_id):
        return self.states.setdefault(job_id, WorkerState(PHASE_IDLE))

    def prepare(self, job, context):
        self.prepared.append(job.id)
        self._state(job.id).phase = self.prepare_phase

    def start(self, job):
        self.started.append(job.id)
        self._state(job.id).phase = PHASE_RUNNING

    def stop(self, job):
        self.stopped.append(job.id)
        self._state(job.id).phase = PHASE_STOPPED

    def remove(self, job):
        self.removed.append(job.id)
        self._state(job.id).phase = PHASE_STOPPED

    def snapshot(self, job):
        state = self._state(job.id)
        state.can_continue = job.id not in self.cannot_continue
        return state

    def restore(self, job):
        _ = self._state(job.id)
        return self.alive is None or job.id in self.alive

    def unclaimed(self, claimed):
        return [job for job in self.orphans if job.id not in claimed]

    def set_speed_share(self, kib, limited):
        self.shares.append((kib, limited))

    def cleanup(self, jobs):
        self.cleaned.append([job.id for job in jobs])

    # Test helpers
    def finish(self, job_id):
        self._state(job_id).phase = PHASE_DONE

    def fail(self, job_id, message):
        state = self._state(job_id)
        state.phase = PHASE_FAILED
        state.error = message


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def worker():
    return FakeWorker()


def make_queue(worker, clock, *workers, **kwargs):
    init_db()
    options = {"run_thread": False, "clock": clock, "free_space": lambda: 10**12}
    options.update(kwargs)
    return DownloadQueue([worker, *workers], **options)


def add(queue, game_id, kind="host", **kwargs):
    job = queue.add(kind, game_id, f"Game {game_id}", "1.0", {"host": "pixeldrain"}, **kwargs)
    queue.tick()
    return job.id


def states(queue):
    return [(job.id, job.state) for job in queue.jobs()]


def order(queue):
    return [job.id for job in queue.jobs()]


# --- 2.1 -------------------------------------------------------------------


def test_new_job_goes_to_the_end_and_is_prepared(worker, clock):
    queue = make_queue(worker, clock)
    first = add(queue, 1)
    torrent_worker = FakeWorker("torrent")
    queue = make_queue(worker, clock, torrent_worker)
    queue.load()
    second = add(queue, 2, kind="torrent")
    assert order(queue) == [first, second]
    assert torrent_worker.prepared == [second]
    assert [row["id"] for row in list_download_jobs()] == [first, second]


def test_one_unfinished_job_per_game(worker, clock):
    queue = make_queue(worker, clock)
    job_id = add(queue, 1)
    with pytest.raises(QueueError, match="already in the downloads"):
        _ = queue.add("host", 1, "Game", "1.1", {})
    worker.finish(job_id)
    queue.tick()
    assert queue.get(job_id).state == READY
    # A finished download does not block a new one.
    _ = add(queue, 1)


def test_at_most_max_active_run(worker, clock):
    queue = make_queue(worker, clock)
    ids = [add(queue, game_id) for game_id in (1, 2, 3)]
    assert [queue.get(job_id).state for job_id in ids] == [RUNNING, RUNNING, QUEUED]
    assert worker.started == ids[:2]


def test_preparing_job_waits_for_preparation(worker, clock):
    worker.prepare_phase = PHASE_PREPARING
    queue = make_queue(worker, clock)
    job_id = add(queue, 1)
    assert queue.get(job_id).state == PREPARING and worker.started == []
    worker.states[job_id].phase = PHASE_NEEDS_USER
    queue.tick()
    job = queue.get(job_id)
    assert (job.state, job.reason) == (ATTENTION, "browser_check")
    worker.states[job_id].phase = PHASE_PREPARED
    queue.tick()
    assert queue.get(job_id).state == RUNNING


# --- 2.2 -------------------------------------------------------------------


def test_finished_job_frees_a_slot_for_the_first_waiting(worker, clock):
    queue = make_queue(worker, clock)
    a, b, c, d = (add(queue, game_id) for game_id in (1, 2, 3, 4))
    worker.finish(a)
    queue.tick()
    assert queue.get(a).state == READY
    assert queue.get(c).state == RUNNING and queue.get(d).state == QUEUED
    assert queue.get(b).state == RUNNING


def test_lowered_number_keeps_running_jobs(worker, clock):
    init_db()
    update_setting(queue_settings.MAX_ACTIVE_KEY, "3")
    queue = make_queue(worker, clock)
    ids = [add(queue, game_id) for game_id in (1, 2, 3, 4)]
    assert [queue.get(job_id).state for job_id in ids[:3]] == [RUNNING] * 3
    update_setting(queue_settings.MAX_ACTIVE_KEY, "1")
    queue.apply_settings()
    assert [queue.get(job_id).state for job_id in ids] == [RUNNING, RUNNING, RUNNING, QUEUED]
    assert worker.stopped == []
    worker.finish(ids[0])
    worker.finish(ids[1])
    queue.tick()
    # Still one running: nothing new starts.
    assert queue.get(ids[3]).state == QUEUED
    worker.finish(ids[2])
    queue.tick()
    assert queue.get(ids[3]).state == RUNNING


def test_raised_number_starts_waiting_jobs_at_once(worker, clock):
    queue = make_queue(worker, clock)
    ids = [add(queue, game_id) for game_id in (1, 2, 3, 4)]
    update_setting(queue_settings.MAX_ACTIVE_KEY, "4")
    queue.apply_settings()
    assert all(queue.get(job_id).state == RUNNING for job_id in ids)


# --- 2.3 -------------------------------------------------------------------


def test_reorder_and_move_to_top(worker, clock):
    init_db()
    update_setting(queue_settings.MAX_ACTIVE_KEY, "1")
    queue = make_queue(worker, clock)
    a, b, c, d = (add(queue, game_id) for game_id in (1, 2, 3, 4))
    assert queue.move_to_top(d)
    assert order(queue)[0] == d
    queue.reorder([c, b])
    assert order(queue) == [c, b, d, a]
    assert [row["id"] for row in list_download_jobs()] == [c, b, d, a]
    worker.finish(a)
    queue.tick()
    assert queue.get(c).state == RUNNING


def test_download_now_with_a_free_slot(worker, clock):
    init_db()
    update_setting(queue_settings.MAX_ACTIVE_KEY, "2")
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    b = add(queue, 2)
    assert queue.pause(b)
    assert queue.start_now(b) == "started"
    assert queue.get(b).state == RUNNING and queue.get(a).state == RUNNING


def test_download_now_with_full_slots_yields_the_last_started(worker, clock):
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    clock.now += 1
    b = add(queue, 2)
    c = add(queue, 3)
    d = add(queue, 4)
    assert queue.start_now(d) == "yielded"
    assert queue.get(b).state == QUEUED and worker.stopped == [b]
    assert queue.get(d).state == RUNNING and queue.get(a).state == RUNNING
    # The stopped job is the first waiting job.
    waiting = [job.id for job in queue.jobs() if job.state == QUEUED]
    assert waiting == [b, c]


def test_download_now_when_no_running_job_can_continue(worker, clock):
    init_db()
    update_setting(queue_settings.MAX_ACTIVE_KEY, "1")
    queue = make_queue(worker, clock)
    mega = add(queue, 1)
    worker.cannot_continue.add(mega)
    queue.tick()
    b = add(queue, 2)
    c = add(queue, 3)
    assert queue.start_now(c) == "first"
    assert queue.get(mega).state == RUNNING and worker.stopped == []
    waiting = [job.id for job in queue.jobs() if job.state == QUEUED]
    assert waiting == [c, b]


# --- 2.4 -------------------------------------------------------------------


def test_pause_frees_a_slot(worker, clock):
    queue = make_queue(worker, clock)
    a, b, c = (add(queue, game_id) for game_id in (1, 2, 3))
    assert queue.pause(a)
    assert queue.get(a).state == PAUSED and worker.stopped == [a]
    assert queue.get(c).state == RUNNING


def test_resume_waits_for_a_slot(worker, clock):
    queue = make_queue(worker, clock)
    a, b, c = (add(queue, game_id) for game_id in (1, 2, 3))
    assert queue.pause(a)
    assert queue.resume(a)
    assert queue.get(a).state == QUEUED
    worker.finish(b)
    queue.tick()
    assert queue.get(a).state == RUNNING


def test_pause_all_and_resume_all(worker, clock):
    queue = make_queue(worker, clock)
    ids = [add(queue, game_id) for game_id in (1, 2, 3, 4)]
    assert queue.pause_all() == 4
    queue.tick()
    assert all(queue.get(job_id).state == PAUSED for job_id in ids)
    assert not [job for job in queue.jobs() if job.state == RUNNING]
    assert queue.resume_all() == 4
    assert [queue.get(job_id).state for job_id in ids] == [RUNNING, RUNNING, QUEUED, QUEUED]


def test_pause_while_preparing_keeps_the_pause(worker, clock):
    worker.prepare_phase = PHASE_PREPARING
    queue = make_queue(worker, clock)
    job_id = add(queue, 1)
    assert queue.pause(job_id)
    worker.states[job_id].phase = PHASE_PREPARED
    queue.tick()
    assert queue.get(job_id).state == PAUSED and worker.started == []
    assert queue.resume(job_id)
    queue.tick()
    queue.tick()
    assert queue.get(job_id).state == RUNNING


# --- 2.5 -------------------------------------------------------------------


def test_queue_survives_a_restart(worker, clock):
    init_db()
    update_setting(queue_settings.MAX_ACTIVE_KEY, "1")
    queue = make_queue(worker, clock)
    a, b, c, d = (add(queue, game_id) for game_id in (1, 2, 3, 4))
    queue.reorder([a, d, c, b])
    assert queue.pause(c)
    assert queue.get(a).state == RUNNING
    before = order(queue)

    worker2 = FakeWorker()
    restored = make_queue(worker2, clock)
    restored.load()
    assert order(restored) == before
    assert restored.get(a).state == RUNNING and worker2.started == [a]
    assert restored.get(c).state == PAUSED
    assert restored.get(d).state == QUEUED and restored.get(b).state == QUEUED


def test_running_job_starts_first_after_a_restart(worker, clock):
    queue = make_queue(worker, clock)
    a, b, c = (add(queue, game_id) for game_id in (1, 2, 3))
    # c is first in order, but a and b were running: they start first after a restart.
    queue.reorder([c])
    assert order(queue)[0] == c
    worker2 = FakeWorker()
    restored = make_queue(worker2, clock)
    restored.load()
    assert worker2.started == [a, b]
    assert order(restored) == [a, b, c]
    assert restored.get(c).state == QUEUED


# --- 2.6 -------------------------------------------------------------------


def test_row_whose_data_is_gone_is_deleted(worker, clock):
    queue = make_queue(worker, clock)
    _, b = add(queue, 1), add(queue, 2)
    worker2 = FakeWorker()
    worker2.alive = {b}
    restored = make_queue(worker2, clock)
    restored.load()
    assert order(restored) == [b]
    assert [row["id"] for row in list_download_jobs()] == [b]


def test_worker_job_without_a_row_is_adopted_at_the_end(worker, clock):
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    torrent = FakeWorker("torrent")
    torrent.orphans = [
        QueueJob(id="t2", kind="torrent", game_id=3, state=PAUSED, created_at=20.0),
        QueueJob(id="t1", kind="torrent", game_id=2, state=READY, created_at=10.0),
    ]
    restored = make_queue(FakeWorker(), clock, torrent)
    restored.load()
    assert order(restored)[0] == a
    assert order(restored)[1:] == ["t1", "t2"]
    assert {row["id"] for row in list_download_jobs()} == {a, "t1", "t2"}


def test_upgrade_with_existing_torrents_and_no_rows(worker, clock):
    torrent = FakeWorker("torrent")
    torrent.orphans = [
        QueueJob(id="old", kind="torrent", game_id=1, state=PAUSED, created_at=5.0),
        QueueJob(id="was-running", kind="torrent", game_id=2, state=RUNNING, created_at=6.0),
        QueueJob(id="done", kind="torrent", game_id=3, state=READY, created_at=7.0),
    ]
    queue = make_queue(FakeWorker(), clock, torrent)
    queue.load()
    assert queue.get("old").state == PAUSED
    assert queue.get("was-running").state == RUNNING and torrent.started == ["was-running"]
    assert queue.get("done").state == READY
    assert order(queue)[0] == "was-running"


def test_stale_host_job_is_removed_after_30_days(worker, clock):
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    worker.fail(a, "link expired or removed")
    queue.tick()
    assert queue.get(a).state == ATTENTION
    clock.now += 31 * 86400
    worker2 = FakeWorker()
    restored = make_queue(worker2, clock)
    restored.load()
    assert restored.jobs() == [] and worker2.removed == [a]


def test_cleanup_gets_the_restored_jobs(worker, clock):
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    worker2 = FakeWorker()
    restored = make_queue(worker2, clock)
    restored.load()
    assert worker2.cleaned == [[a]]


# --- 2.7 -------------------------------------------------------------------


def test_remove_deletes_the_job_and_its_data(worker, clock):
    queue = make_queue(worker, clock)
    a, b, c = (add(queue, game_id) for game_id in (1, 2, 3))
    assert queue.remove(c)
    assert worker.removed == [c] and c not in order(queue)
    assert queue.remove(a)
    # The freed slot is used at once.
    assert queue.get(b).state == RUNNING
    assert not queue.remove("missing")
    assert [row["id"] for row in list_download_jobs()] == [b]


def test_forget_game_removes_its_jobs(worker, clock):
    queue = make_queue(worker, clock)
    a, b = add(queue, 1), add(queue, 2)
    assert queue.pause(a)
    queue.forget_game(1)
    assert order(queue) == [b] and worker.removed == [a]
    assert [row["id"] for row in list_download_jobs()] == [b]


def test_installed_drops_the_ready_job(worker, clock, tmp_path):
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    archive = tmp_path / "game.zip"
    archive.write_bytes(b"PK")
    worker.finish(a)
    worker.states[a].details = {"archive_path": str(archive)}
    queue.tick()
    queue.refresh()
    assert queue.installed(str(archive)) == [a]
    assert queue.jobs() == [] and worker.removed == []


# --- 2.8 -------------------------------------------------------------------


def _wait_for(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_no_thread_for_an_empty_queue(worker):
    init_db()
    queue = DownloadQueue([worker], free_space=lambda: 0)
    queue.load()
    assert not queue.thread_alive()
    queue.poke()
    assert not queue.thread_alive()


def test_thread_runs_only_while_an_unfinished_job_exists(worker):
    init_db()
    queue = DownloadQueue([worker], free_space=lambda: 0)
    queue.load()
    job = queue.add("host", 1, "Game", "1.0", {})
    assert queue.thread_alive()
    assert _wait_for(lambda: queue.get(job.id).state == RUNNING)
    worker.finish(job.id)
    assert _wait_for(lambda: queue.get(job.id).state == READY)
    assert _wait_for(lambda: not queue.thread_alive())
    queue.close()


def test_no_thread_for_finished_jobs_after_a_restart(worker, clock):
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    worker.finish(a)
    queue.tick()
    restored = DownloadQueue([FakeWorker()], free_space=lambda: 0)
    restored.load()
    assert restored.get(a).state == READY
    assert not restored.thread_alive()


# --- failures and retries (5.2-5.4) ------------------------------------------


def test_network_failure_retries_three_times_then_needs_the_user(worker, clock):
    init_db()
    update_setting(queue_settings.MAX_ACTIVE_KEY, "1")
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    b = add(queue, 2)
    for delay in (30, 120, 600):
        worker.states[a].bytes_done = 43
        worker.fail(a, "network error")
        queue.tick()
        job = queue.get(a)
        assert job.state == RETRY_WAIT and job.retry_at == clock.now + delay
        # The slot is free during the wait.
        assert queue.get(b).state == RUNNING
        clock.now += delay - 1
        queue.tick()
        assert queue.get(a).state == RETRY_WAIT
        clock.now += 1
        queue.tick()
        assert queue.get(a).state == QUEUED
        # Let a run again.
        worker.finish(b)
        queue.tick()
        assert queue.get(a).state == RUNNING
        b = add(queue, 10 + delay)
    worker.fail(a, "network error")
    queue.tick()
    job = queue.get(a)
    assert (job.state, job.error_code, job.bytes_done) == (ATTENTION, "network", 43)


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("link expired or removed", "removed"),
        ("transfer quota or rate limit reached", "quota"),
        ("Not enough disk space: 10 bytes needed", "disk"),
        ("Open a browser with an up-to-date wLib extension to download in-app", "extension"),
    ],
)
def test_other_failures_need_the_user_at_once(worker, clock, message, code):
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    worker.states[a].bytes_done = 500
    worker.states[a].bytes_total = 1000
    worker.fail(a, message)
    queue.tick()
    job = queue.get(a)
    assert (job.state, job.error_code, job.error_text) == (ATTENTION, code, message)
    payload = queue.status()["jobs"][0]
    assert payload["bytes_done"] == 500 and payload["bytes_total"] == 1000


def test_manual_retry_during_the_wait(worker, clock):
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    worker.fail(a, "network error")
    queue.tick()
    assert queue.get(a).state == RETRY_WAIT
    assert queue.retry(a)
    assert queue.get(a).state == RUNNING and queue.get(a).retries == 0


def test_replacement_takes_the_failed_jobs_place(worker, clock):
    queue = make_queue(worker, clock)
    a, b, c = (add(queue, game_id) for game_id in (1, 2, 3))
    worker.fail(b, "link expired or removed")
    queue.tick()
    new = queue.add("host", 2, "Game 2", "1.0", {"host": "mega"}, replaces=b)
    assert order(queue) == [a, new.id, c]
    assert worker.removed == [b] and queue.get(b) is None
    with pytest.raises(QueueError):
        _ = queue.add("host", 3, "Game 3", "1.0", {}, replaces="missing")


# --- holds (9.4) ------------------------------------------------------------


def test_hold_while_playing(worker, clock):
    games: list[int] = []
    queue = make_queue(worker, clock, running_games=lambda: games)
    a, b, c = (add(queue, game_id) for game_id in (1, 2, 3))
    worker.cannot_continue.add(b)
    queue.tick()
    games.append(99)
    queue.tick()
    assert queue.get(a).state == HELD and queue.get(a).reason == "game"
    assert queue.get(b).state == RUNNING
    assert queue.hold_keeps_running() == [b]
    assert queue.status()["queue"]["hold"] == "game"
    worker.finish(b)
    queue.tick()
    # No job starts during a hold.
    assert queue.get(c).state == QUEUED
    games.clear()
    queue.tick()
    assert queue.get(a).state == RUNNING and queue.get(c).state == RUNNING


def test_user_paused_job_stays_paused_through_a_hold(worker, clock):
    games: list[int] = []
    queue = make_queue(worker, clock, running_games=lambda: games)
    a = add(queue, 1)
    assert queue.pause(a)
    games.append(1)
    queue.tick()
    games.clear()
    queue.tick()
    assert queue.get(a).state == PAUSED


def test_resume_now_lasts_until_the_hold_ends(worker, clock):
    games: list[int] = [1]
    queue = make_queue(worker, clock, running_games=lambda: games)
    a = add(queue, 5)
    assert queue.get(a).state == QUEUED
    assert queue.resume_now()
    assert queue.get(a).state == RUNNING
    queue.tick()
    assert queue.get(a).state == RUNNING
    games.clear()
    queue.tick()
    games.append(2)
    queue.tick()
    assert queue.get(a).state == HELD


def test_battery_hold_and_settings_off(worker, clock):
    battery = [True]
    queue = make_queue(worker, clock, running_games=lambda: [1], on_battery=lambda: battery[0])
    update_setting(queue_settings.PAUSE_WHILE_PLAYING_KEY, "false")
    queue.apply_settings()
    a = add(queue, 7)
    # Both settings off (battery is off by default): runs.
    assert queue.get(a).state == RUNNING
    update_setting(queue_settings.PAUSE_ON_BATTERY_KEY, "true")
    queue.apply_settings()
    assert queue.get(a).state == HELD and queue.get(a).reason == "battery"
    battery[0] = False
    queue.tick()
    assert queue.get(a).state == RUNNING


def test_unknown_power_state_counts_as_mains(worker, clock):
    queue = make_queue(worker, clock, on_battery=lambda: None)
    update_setting(queue_settings.PAUSE_ON_BATTERY_KEY, "true")
    queue.apply_settings()
    a = add(queue, 1)
    assert queue.get(a).state == RUNNING


# --- forecast and history (9.5) ---------------------------------------------


def test_forecast(worker, clock):
    queue = make_queue(worker, clock, free_space=lambda: 100)
    a, b, c = (add(queue, game_id) for game_id in (1, 2, 3))
    worker.states[a].bytes_total = 80
    worker.states[a].bytes_done = 30
    worker.states[b].bytes_total = 40
    forecast = queue.status()["queue"]["forecast"]
    assert forecast == {"needed": 90, "free": 100, "unknown": 1, "fits": True, "missing": 0}
    worker.states[b].bytes_total = 100
    forecast = queue.status()["queue"]["forecast"]
    assert forecast["fits"] is False and forecast["missing"] == 50


def test_history_only_when_asked(worker, clock):
    queue = make_queue(worker, clock)
    a = add(queue, 1)
    worker.states[a].speed = 1000.0
    for _ in range(400):
        queue.tick()
    assert "history" not in queue.status()["queue"]
    status = queue.status(include_history=True)["queue"]
    assert len(status["history"]) == 300 and status["peak"] == 1000.0
    assert status["speed"] == 1000.0


def test_status_says_nothing_runs_when_idle(worker, clock):
    queue = make_queue(worker, clock)
    status = queue.status(include_history=True)
    assert status["jobs"] == [] and status["queue"]["speed"] == 0
    assert status["queue"]["forecast"]["needed"] == 0


def test_shares_follow_the_limit(worker, clock):
    torrent = FakeWorker("torrent")
    queue = make_queue(worker, clock, torrent)
    update_setting(queue_settings.LIMIT_KEY, "1000")
    queue.apply_settings()
    _ = add(queue, 1)
    queue.tick()
    queue.tick()
    assert worker.shares[-1] == (1000, True)
    assert torrent.shares[-1] == (0, True)
    update_setting(queue_settings.LIMIT_KEY, "0")
    queue.apply_settings()
    assert worker.shares[-1] == (0, False)


def test_a_starting_job_gets_its_share_at_once(worker, clock):
    torrent = FakeWorker("torrent")
    queue = make_queue(worker, clock, torrent)
    update_setting(queue_settings.LIMIT_KEY, "1000")
    queue.apply_settings()
    _ = add(queue, 1, kind="torrent")
    queue.tick()
    assert torrent.shares[-1] == (1000, True)
    # The host side held the smallest share; its first job must not wait for the next even tick.
    _ = add(queue, 2)
    assert worker.shares[-1][0] >= 100
    assert worker.shares[-1][0] + torrent.shares[-1][0] == 1000
