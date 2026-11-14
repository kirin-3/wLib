# SPDX-License-Identifier: GPL-3.0-or-later
"""One ordered queue for every in-app download, file host or torrent.

`DownloadQueue` owns the order, the slots, the holds, the automatic retries
and each job's queue state. It drives the transfer code through a small
`Worker` interface; `core/downloads/workers.py` adapts `DownloadManager` and
`TorrentSupervisor` to it. The queue is saved in the device-local
`download_jobs` table, which holds no link, tracker address or passkey.

States (design D3):

    preparing   resolving the link / fetching the .torrent      no slot
    queued      waiting for a slot                               no slot
    retry_wait  waiting for an automatic retry (`retry_at`)      no slot
    held        stopped by a hold (`reason` game or battery)     no slot
    running     moving data                                      slot
    paused      paused by the user                               no slot
    attention   needs the user (`error_code`)                    no slot
    ready       finished, waiting for its install                no slot

One daemon thread ticks once per second while a job that can change by
itself exists (see `_needs_tick`); an idle queue has no thread.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast, final

from core.downloads import failures, queue_settings
from core.downloads.pacing import split_limit

PREPARING = "preparing"
QUEUED = "queued"
RETRY_WAIT = "retry_wait"
HELD = "held"
RUNNING = "running"
PAUSED = "paused"
ATTENTION = "attention"
READY = "ready"
STATES = (PREPARING, QUEUED, RETRY_WAIT, HELD, RUNNING, PAUSED, ATTENTION, READY)
WAITING_STATES = (PREPARING, QUEUED, RETRY_WAIT, HELD)

# `reason` values
REASON_GAME = "game"
REASON_BATTERY = "battery"
REASON_BROWSER_CHECK = "browser_check"
# A job the user paused while it was still preparing.
REASON_PREPARING = "preparing"

# Worker phases (`WorkerState.phase`)
PHASE_IDLE = "idle"
PHASE_PREPARING = "preparing"
PHASE_NEEDS_USER = "needs_user"
PHASE_PREPARED = "prepared"
PHASE_RUNNING = "running"
PHASE_STOPPED = "stopped"
PHASE_DONE = "done"
PHASE_FAILED = "failed"

RETRY_DELAYS = (30.0, 120.0, 600.0)
STALE_HOST_JOB_SECONDS = 30 * 86400
PROGRESS_SAVE_SECONDS = 15.0
TICK_SECONDS = 1.0
HISTORY_SAMPLES = 300


class QueueError(Exception):
    """A user-visible refusal, such as a second download for one game."""


@dataclass
class WorkerState:
    """What a worker reports about one job."""

    phase: str = PHASE_IDLE
    error: str = ""
    bytes_done: int = 0
    bytes_total: int | None = None
    speed: float = 0.0
    can_continue: bool = True
    # Passed through to the UI: stage, peers, upload_speed, archive_path, ...
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class QueueJob:
    id: str
    kind: str
    game_id: int
    title: str = ""
    version: str = ""
    state: str = QUEUED
    position: int = 0
    reason: str = ""
    error_code: str = ""
    error_text: str = ""
    source: dict[str, object] = field(default_factory=dict)
    bytes_done: int = 0
    bytes_total: int | None = None
    retries: int = 0
    retry_at: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    finished_at: float | None = None
    # In memory only.
    started_at: float = 0.0
    # The failure code before the current start; a worker may read the link again for it.
    previous_error: str = ""
    speed: float = 0.0
    can_continue: bool = True
    details: dict[str, object] = field(default_factory=dict)
    saved_progress_at: float = 0.0

    def row(self) -> dict[str, object]:
        return {
            "id": self.id, "kind": self.kind, "game_id": self.game_id, "title": self.title,
            "version": self.version, "position": self.position, "state": self.state,
            "reason": self.reason, "error_code": self.error_code, "error_text": self.error_text,
            "source": json.dumps(self.source, separators=(",", ":"), sort_keys=True),
            "bytes_done": self.bytes_done, "bytes_total": self.bytes_total,
            "retries": self.retries, "retry_at": self.retry_at,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> QueueJob:
        try:
            source = cast(object, json.loads(str(row.get("source") or "{}")))
        except ValueError:
            source = {}
        state = str(row.get("state") or QUEUED)
        return cls(
            id=str(row.get("id") or ""),
            kind=str(row.get("kind") or ""),
            game_id=_int(row.get("game_id")),
            title=str(row.get("title") or ""),
            version=str(row.get("version") or ""),
            state=state if state in STATES else QUEUED,
            position=_int(row.get("position")),
            reason=str(row.get("reason") or ""),
            error_code=str(row.get("error_code") or ""),
            error_text=str(row.get("error_text") or ""),
            source=cast(dict[str, object], source) if isinstance(source, dict) else {},
            bytes_done=_int(row.get("bytes_done")),
            bytes_total=_opt_int(row.get("bytes_total")),
            retries=_int(row.get("retries")),
            retry_at=_opt_float(row.get("retry_at")),
            created_at=_opt_float(row.get("created_at")) or 0.0,
            updated_at=_opt_float(row.get("updated_at")) or 0.0,
            finished_at=_opt_float(row.get("finished_at")),
        )


class Worker(Protocol):
    """What the queue needs from a transfer engine (design D1)."""

    kind: str

    def prepare(self, job: QueueJob, context: object) -> None:
        """Begin the work that needs no slot (resolve a link, fetch a .torrent)."""
        ...

    def start(self, job: QueueJob) -> None:
        """Move data. Prepares the job first when that was not done."""
        ...

    def stop(self, job: QueueJob) -> None:
        """Stop moving data and keep what can be continued."""
        ...

    def remove(self, job: QueueJob) -> None:
        """Stop and delete the job's data."""
        ...

    def snapshot(self, job: QueueJob) -> WorkerState: ...

    def restore(self, job: QueueJob) -> bool:
        """Re-attach a saved job at startup; False when its data is gone."""
        ...

    def unclaimed(self, claimed: set[str]) -> list[QueueJob]:
        """Jobs this worker holds that the queue has no row for (adoption)."""
        ...

    def set_speed_share(self, kib: int, limited: bool) -> None:
        """Apply this side's share of the shared speed limit; 0 with `limited` False is unlimited."""
        ...

    def cleanup(self, jobs: list[QueueJob]) -> None:
        """Delete staging data that none of `jobs` can continue from."""
        ...


def _log(message: str) -> None:
    # Never log links, tracker addresses or passkeys here.
    print(f"[downloads] {message}", file=sys.stderr, flush=True)


@final
class DownloadQueue:
    def __init__(
        self,
        workers: Iterable[Worker],
        *,
        running_games: Callable[[], Iterable[int]] = lambda: (),
        on_battery: Callable[[], bool | None] = lambda: None,
        free_space: Callable[[], int] | None = None,
        clock: Callable[[], float] = time.time,
        run_thread: bool = True,
        store: QueueStore | None = None,
    ) -> None:
        self._workers: dict[str, Worker] = {worker.kind: worker for worker in workers}
        self._running_games = running_games
        self._on_battery = on_battery
        self._free_space = free_space or _staging_free_space
        self._clock = clock
        self._run_thread = run_thread
        self._store = store or QueueStore()
        self._lock = threading.RLock()
        self._jobs: dict[str, QueueJob] = {}
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._closing = False
        self._max_active = queue_settings.max_active()
        self._limit_kib = queue_settings.limit_kib()
        self._pause_while_playing = queue_settings.pause_while_playing()
        self._pause_on_battery = queue_settings.pause_on_battery()
        self._hold = ""
        self._hold_override = False
        self._ticks = 0
        self._history: deque[float] = deque(maxlen=HISTORY_SAMPLES)
        self._side_speed: dict[str, deque[float]] = {}
        self._shares: dict[str, int] = {}

    # --- helpers ----------------------------------------------------------

    def _ordered(self) -> list[QueueJob]:
        return sorted(self._jobs.values(), key=lambda job: (job.position, job.created_at, job.id))

    def _worker(self, job: QueueJob) -> Worker:
        return self._workers[job.kind]

    def _save(self, job: QueueJob, *fields: str) -> None:
        job.updated_at = self._clock()
        row = job.row()
        values = {key: row[key] for key in (*fields, "updated_at")}
        self._store.update(job.id, values)

    def _set_state(self, job: QueueJob, state: str, reason: str = "") -> None:
        job.state = state
        job.reason = reason
        if state not in (RUNNING,):
            job.speed = 0.0
        self._save(job, "state", "reason")

    def _renumber(self) -> None:
        ordered = self._ordered()
        for index, job in enumerate(ordered):
            job.position = index
        self._store.reorder([job.id for job in ordered])

    def _move_to_top_of_waiting(self, jobs: list[QueueJob]) -> None:
        """Put `jobs` before every other job, keeping their own order."""
        ids = {job.id for job in jobs}
        rest = [job for job in self._ordered() if job.id not in ids]
        for index, job in enumerate([*jobs, *rest]):
            job.position = index
        self._store.reorder([job.id for job in [*jobs, *rest]])

    def _running(self) -> list[QueueJob]:
        return [job for job in self._jobs.values() if job.state == RUNNING]

    @staticmethod
    def _transient_attention(job: QueueJob) -> bool:
        return job.state == ATTENTION and job.reason == REASON_BROWSER_CHECK

    def _needs_tick(self) -> bool:
        return any(
            job.state in (PREPARING, QUEUED, RETRY_WAIT, HELD, RUNNING)
            or self._transient_attention(job)
            or (job.state == PAUSED and job.reason == REASON_PREPARING)
            for job in self._jobs.values()
        )

    # --- thread -----------------------------------------------------------

    def _ensure_thread(self) -> None:
        """Start the tick thread when a job can change by itself. Call with the lock held."""
        if not self._run_thread or self._closing or self._thread is not None:
            return
        if not self._needs_tick():
            return
        thread = threading.Thread(target=self._loop, name="wlib-download-queue", daemon=True)
        self._thread = thread
        thread.start()

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # A tick must never kill the thread silently.
                _log(f"queue tick failed: {type(exc).__name__}")
            with self._lock:
                if self._closing or not self._needs_tick():
                    self._thread = None
                    return
            _ = self._wake.wait(TICK_SECONDS)
            self._wake.clear()

    def thread_alive(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def poke(self) -> None:
        """Run the next tick now (a worker finished, a setting changed)."""
        with self._lock:
            self._ensure_thread()
        self._wake.set()

    def close(self) -> None:
        with self._lock:
            self._closing = True
        self._wake.set()

    # --- tick -------------------------------------------------------------

    def tick(self) -> None:
        with self._lock:
            now = self._clock()
            self._sync_workers(now)
            self._check_retries(now)
            self._evaluate_holds()
            self._fill_slots(now)
            self._ticks += 1
            self._sample_speed()
            if self._ticks % 2 == 0:
                self._apply_shares()

    def _sync_workers(self, now: float) -> None:
        for job in self._ordered():
            if job.state in (READY, RETRY_WAIT, QUEUED, HELD) or (
                job.state == ATTENTION and not self._transient_attention(job)
            ):
                continue
            if job.state == PAUSED and job.reason != REASON_PREPARING:
                continue
            state = self._worker(job).snapshot(job)
            self._take_progress(job, state, now)
            phase = state.phase
            if phase == PHASE_DONE:
                self._finish(job, now)
            elif phase == PHASE_FAILED:
                self._fail(job, state.error, now)
            elif job.state == PAUSED:
                # Paused while preparing: preparation finished, the pause stays.
                if phase in (PHASE_PREPARED, PHASE_STOPPED, PHASE_IDLE):
                    self._set_state(job, PAUSED)
            elif phase == PHASE_NEEDS_USER:
                if job.state != ATTENTION:
                    self._set_state(job, ATTENTION, REASON_BROWSER_CHECK)
            elif phase == PHASE_PREPARED:
                if job.state in (PREPARING, ATTENTION):
                    self._set_state(job, QUEUED)
            elif phase == PHASE_RUNNING:
                if job.state != RUNNING:
                    job.started_at = job.started_at or now
                    self._set_state(job, RUNNING)
            elif phase == PHASE_PREPARING:
                if job.state == ATTENTION:
                    self._set_state(job, PREPARING)
            elif phase == PHASE_STOPPED and job.state == RUNNING:
                # The worker stopped by itself without a result; wait again.
                self._set_state(job, QUEUED)

    def _take_progress(self, job: QueueJob, state: WorkerState, now: float) -> None:
        job.can_continue = state.can_continue
        job.details = dict(state.details)
        job.speed = state.speed if state.phase == PHASE_RUNNING else 0.0
        changed = state.bytes_done != job.bytes_done or state.bytes_total != job.bytes_total
        if state.bytes_done or state.bytes_total is not None:
            job.bytes_done = state.bytes_done
            job.bytes_total = state.bytes_total if state.bytes_total is not None else job.bytes_total
        if changed and now - job.saved_progress_at >= PROGRESS_SAVE_SECONDS:
            job.saved_progress_at = now
            self._save(job, "bytes_done", "bytes_total", "source")

    def _finish(self, job: QueueJob, now: float) -> None:
        job.finished_at = now
        job.error_code = job.error_text = ""
        job.retries = 0
        job.retry_at = None
        if job.bytes_total:
            job.bytes_done = job.bytes_total
        self._set_state(job, READY)
        self._save(job, "finished_at", "error_code", "error_text", "retries", "retry_at",
                   "bytes_done", "bytes_total", "source")

    def _fail(self, job: QueueJob, message: str, now: float) -> None:
        code = failures.classify(message)
        job.error_code, job.error_text = code, message
        if failures.retryable(code) and job.retries < len(RETRY_DELAYS):
            job.retry_at = now + RETRY_DELAYS[job.retries]
            job.retries += 1
            self._set_state(job, RETRY_WAIT)
        else:
            job.retry_at = None
            self._set_state(job, ATTENTION)
        self._save(job, "error_code", "error_text", "retries", "retry_at", "bytes_done", "bytes_total", "source")

    def _check_retries(self, now: float) -> None:
        for job in self._ordered():
            if job.state == RETRY_WAIT and (job.retry_at or 0) <= now:
                job.retry_at = None
                self._set_state(job, QUEUED)
                self._save(job, "retry_at")

    # --- holds (D9) -------------------------------------------------------

    def _hold_reason(self) -> str:
        if self._pause_while_playing:
            try:
                if any(True for _ in self._running_games()):
                    return REASON_GAME
            except Exception:
                pass
        if self._pause_on_battery:
            try:
                if self._on_battery() is True:
                    return REASON_BATTERY
            except Exception:
                pass
        return ""

    def _hold_active(self) -> bool:
        return bool(self._hold) and not self._hold_override

    def _evaluate_holds(self) -> None:
        reason = self._hold_reason()
        if not reason:
            self._hold_override = False
        self._hold = reason
        if self._hold_active():
            for job in self._running():
                if job.can_continue:
                    self._worker(job).stop(job)
                    self._set_state(job, HELD, reason)
            for job in self._jobs.values():
                if job.state == HELD and job.reason != reason:
                    self._set_state(job, HELD, reason)
            return
        held = [job for job in self._ordered() if job.state == HELD]
        if held:
            for job in held:
                self._set_state(job, QUEUED)
            self._move_to_top_of_waiting(held)

    def hold_keeps_running(self) -> list[str]:
        with self._lock:
            if not self._hold_active():
                return []
            return [job.id for job in self._ordered() if job.state == RUNNING and not job.can_continue]

    # --- slots ------------------------------------------------------------

    def _fill_slots(self, now: float) -> None:
        if self._hold_active():
            return
        running = len(self._running())
        for job in self._ordered():
            if running >= self._max_active:
                break
            if job.state != QUEUED:
                continue
            self._start(job, now)
            running += 1

    def _start(self, job: QueueJob, now: float) -> None:
        job.started_at = now
        job.previous_error = job.error_code or job.previous_error
        job.error_code = job.error_text = ""
        self._set_state(job, RUNNING)
        self._save(job, "error_code", "error_text")
        try:
            self._worker(job).start(job)
        except Exception as exc:
            self._fail(job, str(exc) or type(exc).__name__, now)
        finally:
            job.previous_error = ""
        # A side that had no running job holds the smallest share; give the
        # new job its share now, not on the next even tick.
        self._apply_shares()

    # --- speed (D8, D12) --------------------------------------------------

    def _sample_speed(self) -> None:
        total = 0.0
        sides: dict[str, float] = {}
        for job in self._running():
            total += job.speed
            sides[job.kind] = sides.get(job.kind, 0.0) + job.speed
        self._history.append(total)
        for kind in self._workers:
            self._side_speed.setdefault(kind, deque(maxlen=2)).append(sides.get(kind, 0.0))

    def _apply_shares(self) -> None:
        running = self._running()
        host_n = sum(1 for job in running if job.kind == "host")
        torrent_n = sum(1 for job in running if job.kind == "torrent")

        def used(kind: str) -> float | None:
            samples = self._side_speed.get(kind)
            if not samples or len(samples) < 2:
                return None
            return sum(samples) / len(samples) / 1024

        host_share, torrent_share = split_limit(
            self._limit_kib, host_n, torrent_n, used("host"), used("torrent")
        )
        shares = {"host": host_share, "torrent": torrent_share}
        limited = self._limit_kib > 0
        for kind, worker in self._workers.items():
            share = shares.get(kind, 0)
            if self._shares.get(kind) == share and kind in self._shares:
                continue
            self._shares[kind] = share
            try:
                worker.set_speed_share(share, limited)
            except Exception as exc:
                _log(f"could not set the {kind} speed share: {type(exc).__name__}")

    # --- public: adding ---------------------------------------------------

    def add(
        self,
        kind: str,
        game_id: int,
        title: str,
        version: str,
        source: Mapping[str, object],
        context: object = None,
        *,
        replaces: str | None = None,
    ) -> QueueJob:
        """Add a job at the end (or in the place of `replaces`) and prepare it."""
        if kind not in self._workers:
            raise QueueError("Unknown download kind")
        with self._lock:
            replaced = self._jobs.get(replaces) if replaces else None
            if replaces and replaced is None:
                raise QueueError("The download to replace was not found")
            if any(
                job.game_id == game_id and job.state != READY and job is not replaced
                for job in self._jobs.values()
            ):
                raise QueueError("This game is already in the downloads")
            now = self._clock()
            job = QueueJob(
                id=uuid.uuid4().hex, kind=kind, game_id=game_id, title=title, version=version,
                state=PREPARING, source=dict(source), created_at=now, updated_at=now,
            )
            ordered = self._ordered()
            if replaced is not None:
                index = ordered.index(replaced)
                ordered[index] = job
            else:
                ordered.append(job)
            for index, item in enumerate(ordered):
                item.position = index
            row = job.row()
            self._store.insert(row)
            self._jobs[job.id] = job
            if replaced is not None:
                _ = self._jobs.pop(replaced.id, None)
                self._store.delete(replaced.id)
            self._store.reorder([item.id for item in ordered])
        if replaced is not None:
            self._remove_data(replaced)
        try:
            self._worker(job).prepare(job, context)
        except Exception as exc:
            with self._lock:
                self._fail(job, str(exc) or type(exc).__name__, self._clock())
        self.poke()
        return job

    # --- public: user actions ---------------------------------------------

    def reorder(self, job_ids: list[str]) -> None:
        """Put the named jobs first, in this order; the rest keep their order after them."""
        with self._lock:
            named = [self._jobs[job_id] for job_id in dict.fromkeys(job_ids) if job_id in self._jobs]
            self._move_to_top_of_waiting(named)
        self.poke()

    def move_to_top(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            self._move_to_top_of_waiting([job])
        self.poke()
        return True

    def start_now(self, job_id: str) -> str:
        """Start a waiting or paused job at once (design: the yield rule).

        Returns "started", "yielded" (a running job gave up its slot),
        or "first" (no job could give up its slot; this job is next).
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise QueueError("The download was not found")
            if job.state not in (QUEUED, RETRY_WAIT, HELD, PAUSED):
                raise QueueError("The download cannot start now")
            if job.state == PAUSED and job.reason == REASON_PREPARING:
                self._set_state(job, PREPARING)
                self._move_to_top_of_waiting([job])
                return "first"
            now = self._clock()
            job.retry_at = None
            self._move_to_top_of_waiting([job])
            self._set_state(job, QUEUED)
            if self._hold_active():
                return "first"
            running = sorted(self._running(), key=lambda item: item.started_at, reverse=True)
            if len(running) < self._max_active:
                self._start(job, now)
                outcome = "started"
            else:
                victim = next((item for item in running if item.can_continue), None)
                if victim is None:
                    return "first"
                self._worker(victim).stop(victim)
                self._set_state(victim, QUEUED)
                self._start(job, now)
                # The stopped job becomes the first waiting job.
                rest = [item for item in self._ordered() if item is not job and item is not victim]
                self._move_to_top_of_waiting([job, victim, *rest])
                outcome = "yielded"
        self.poke()
        return outcome

    def pause(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if not self._pause(job):
                return False
            self._fill_slots(self._clock())
        self.poke()
        return True

    def _pause(self, job: QueueJob) -> bool:
        if job.state == RUNNING:
            self._worker(job).stop(job)
            self._set_state(job, PAUSED)
            return True
        if job.state == PREPARING:
            self._set_state(job, PAUSED, REASON_PREPARING)
            return True
        if job.state in (QUEUED, RETRY_WAIT, HELD):
            job.retry_at = None
            self._set_state(job, PAUSED)
            self._save(job, "retry_at")
            return True
        return False

    def resume(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != PAUSED:
                return False
            self._set_state(job, PREPARING if job.reason == REASON_PREPARING else QUEUED)
            self._fill_slots(self._clock())
        self.poke()
        return True

    def pause_all(self) -> int:
        with self._lock:
            count = sum(1 for job in self._ordered() if self._pause(job))
        self.poke()
        return count

    def resume_all(self) -> int:
        with self._lock:
            paused = [job for job in self._ordered() if job.state == PAUSED]
            for job in paused:
                self._set_state(job, PREPARING if job.reason == REASON_PREPARING else QUEUED)
            self._fill_slots(self._clock())
        self.poke()
        return len(paused)

    def retry(self, job_id: str) -> bool:
        """Retry a failed job, or one that waits for an automatic retry, at once."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state not in (ATTENTION, RETRY_WAIT):
                return False
            job.retries = 0
            job.retry_at = None
            job.previous_error = job.error_code
            job.error_code = job.error_text = ""
            self._set_state(job, QUEUED)
            self._save(job, "retries", "retry_at", "error_code", "error_text")
            self._fill_slots(self._clock())
        self.poke()
        return True

    def resume_now(self) -> bool:
        """Override the current hold until its reason ends."""
        with self._lock:
            if not self._hold:
                return False
            self._hold_override = True
            self._evaluate_holds()
            self._fill_slots(self._clock())
        self.poke()
        return True

    def remove(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if job is None:
                return False
            self._store.delete(job.id)
            self._renumber()
            self._fill_slots(self._clock())
        self._remove_data(job)
        self.poke()
        return True

    def forget_game(self, game_id: int) -> None:
        """Remove every job of a deleted game, with its data."""
        with self._lock:
            jobs = [job for job in self._jobs.values() if job.game_id == game_id]
            for job in jobs:
                _ = self._jobs.pop(job.id, None)
                self._store.delete(job.id)
            if jobs:
                self._renumber()
                self._fill_slots(self._clock())
        for job in jobs:
            self._remove_data(job)
        if jobs:
            self.poke()

    def _remove_data(self, job: QueueJob) -> None:
        try:
            self._worker(job).remove(job)
        except Exception as exc:
            _log(f"could not delete the data of a {job.kind} download: {type(exc).__name__}")

    def installed(self, path: str) -> list[str]:
        """Drop ready jobs whose archive or folder `path` is; the data stays with its worker."""
        target = _resolved(path)
        if target is None:
            return []
        removed: list[str] = []
        with self._lock:
            for job in list(self._jobs.values()):
                if job.state != READY:
                    continue
                paths = [_resolved(str(job.details.get(key) or "")) for key in ("archive_path", "folder_path")]
                if any(item is not None and (item == target or item in target.parents) for item in paths):
                    _ = self._jobs.pop(job.id, None)
                    self._store.delete(job.id)
                    removed.append(job.id)
            if removed:
                self._renumber()
        return removed

    def apply_settings(self) -> None:
        """Re-read the four settings and apply them to the running queue."""
        with self._lock:
            self._max_active = queue_settings.max_active()
            self._limit_kib = queue_settings.limit_kib()
            self._pause_while_playing = queue_settings.pause_while_playing()
            self._pause_on_battery = queue_settings.pause_on_battery()
            self._shares.clear()
            self._apply_shares()
            self._evaluate_holds()
            self._fill_slots(self._clock())
        self.poke()

    # --- public: reading --------------------------------------------------

    def get(self, job_id: str) -> QueueJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def jobs(self) -> list[QueueJob]:
        with self._lock:
            return self._ordered()

    def refresh(self) -> None:
        """Read the workers once (without starting anything), for a fresh status."""
        with self._lock:
            now = self._clock()
            for job in self._ordered():
                if job.state in (RUNNING, PREPARING, READY) or self._transient_attention(job):
                    state = self._worker(job).snapshot(job)
                    if job.state == READY:
                        job.details = dict(state.details)
                    else:
                        self._take_progress(job, state, now)

    def status(self, include_history: bool = False) -> dict[str, object]:
        self.refresh()
        with self._lock:
            now = self._clock()
            jobs = [self._job_payload(job, now) for job in self._ordered()]
            speed = sum(job.speed for job in self._running())
            peak = max(self._history, default=0.0)
            queue: dict[str, object] = {
                "max_active": self._max_active,
                "active": len(self._running()),
                "hold": self._hold if self._hold_active() else "",
                "hold_reason": self._hold,
                "hold_override": self._hold_override,
                "hold_keeps_running": [
                    job.id for job in self._ordered() if job.state == RUNNING and not job.can_continue
                ] if self._hold_active() else [],
                "speed": speed,
                "peak": max(peak, speed),
                "forecast": self._forecast(),
                "settings": queue_settings.values(),
            }
            if include_history:
                queue["history"] = list(self._history)
        return {"jobs": jobs, "queue": queue}

    def _job_payload(self, job: QueueJob, now: float) -> dict[str, object]:
        eta: int | None = None
        if job.state == RUNNING and job.speed > 0 and job.bytes_total:
            eta = int(max(0, job.bytes_total - job.bytes_done) / job.speed)
        retry_in = max(0, int((job.retry_at or now) - now)) if job.state == RETRY_WAIT else None
        return {
            "job_id": job.id, "kind": job.kind, "game_id": job.game_id, "title": job.title,
            "version": job.version, "state": job.state, "reason": job.reason,
            "position": job.position, "error_code": job.error_code, "error_text": job.error_text,
            "bytes_done": job.bytes_done, "bytes_total": job.bytes_total, "speed": job.speed,
            "eta": eta, "retries": job.retries, "retry_at": job.retry_at, "retry_in": retry_in,
            "created_at": job.created_at, "finished_at": job.finished_at,
            "can_continue": job.can_continue, "source": _public_source(job),
            "details": dict(job.details),
        }

    def _forecast(self) -> dict[str, object]:
        needed = 0
        unknown = 0
        for job in self._jobs.values():
            if job.state == READY:
                continue
            if job.bytes_total is None or job.bytes_total <= 0:
                unknown += 1
            else:
                needed += max(0, job.bytes_total - job.bytes_done)
        try:
            free: int | None = self._free_space()
        except OSError:
            free = None
        return {
            "needed": needed,
            "free": free,
            "unknown": unknown,
            "fits": free is None or needed <= free,
            "missing": max(0, needed - free) if free is not None else 0,
        }

    # --- startup (2.5, 2.6) -----------------------------------------------

    def load(self) -> None:
        """Restore the saved queue, adopt worker jobs without a row, and clean up."""
        _ = queue_settings.migrate_legacy_limit()
        now = self._clock()
        saved: list[QueueJob] = []
        for row in self._store.list():
            job = QueueJob.from_row(row)
            if not job.id or job.kind not in self._workers:
                self._store.delete(job.id)
                continue
            if (
                job.kind == "host"
                and job.state in (PAUSED, ATTENTION, RETRY_WAIT)
                and job.updated_at < now - STALE_HOST_JOB_SECONDS
            ):
                self._store.delete(job.id)
                self._remove_data(job)
                continue
            saved.append(job)
        # Before restoring: a finished job whose archive this deletes leaves the queue below.
        for worker in self._workers.values():
            try:
                worker.cleanup([job for job in saved if job.kind == worker.kind])
            except Exception as exc:
                _log(f"{worker.kind} staging cleanup failed: {type(exc).__name__}")
        with self._lock:
            self._limit_kib = queue_settings.limit_kib()
            restarted: list[QueueJob] = []
            for job in saved:
                worker = self._workers[job.kind]
                try:
                    alive = worker.restore(job)
                except Exception as exc:
                    _log(f"could not restore a {job.kind} download: {type(exc).__name__}")
                    alive = False
                if not alive:
                    self._store.delete(job.id)
                    continue
                before = (job.state, job.reason)
                if job.state in (RUNNING, HELD):
                    job.state, job.reason = QUEUED, ""
                    restarted.append(job)
                elif job.state == PREPARING or self._transient_attention(job):
                    job.state, job.reason = QUEUED, ""
                elif job.state == PAUSED and job.reason == REASON_PREPARING:
                    job.reason = ""
                self._jobs[job.id] = job
                # Save only a change: a save sets `updated_at`, and an untouched
                # failed host job must still age out after 30 days.
                if (job.state, job.reason) != before:
                    self._save(job, "state", "reason")
            claimed = set(self._jobs)
            adopted: list[QueueJob] = []
            for worker in self._workers.values():
                try:
                    adopted.extend(worker.unclaimed(claimed))
                except Exception as exc:
                    _log(f"could not adopt {worker.kind} downloads: {type(exc).__name__}")
            adopted.sort(key=lambda job: job.created_at)
            last = max((job.position for job in self._jobs.values()), default=-1)
            for job in adopted:
                if job.id in self._jobs:
                    continue
                last += 1
                job.position = last
                job.updated_at = now
                was_running = job.state == RUNNING
                if was_running:
                    job.state = QUEUED
                self._store.insert(job.row())
                self._jobs[job.id] = job
                if was_running:
                    restarted.append(job)
            restarted.sort(key=lambda job: (job.position, job.created_at))
            self._move_to_top_of_waiting(restarted)
            self._apply_shares()
            self._fill_slots(self._clock())
            self._ensure_thread()


@final
class QueueStore:
    """The `download_jobs` table; failures are logged, never raised into the queue."""

    def list(self) -> list[dict[str, object]]:
        from core import database

        try:
            return database.list_download_jobs()
        except (OSError, sqlite3.Error) as exc:
            _log(f"could not read the download queue: {type(exc).__name__}")
            return []

    def insert(self, row: Mapping[str, object]) -> None:
        from core import database

        try:
            database.insert_download_job(row)
        except (OSError, sqlite3.Error) as exc:
            _log(f"could not save a download: {type(exc).__name__}")

    def update(self, job_id: str, values: Mapping[str, object]) -> None:
        from core import database

        try:
            _ = database.update_download_job(job_id, values)
        except (OSError, sqlite3.Error) as exc:
            _log(f"could not save a download: {type(exc).__name__}")

    def reorder(self, job_ids: list[str]) -> None:
        from core import database

        try:
            database.reorder_download_jobs(job_ids)
        except (OSError, sqlite3.Error) as exc:
            _log(f"could not save the download order: {type(exc).__name__}")

    def delete(self, job_id: str) -> None:
        from core import database

        try:
            _ = database.delete_download_job(job_id)
        except (OSError, sqlite3.Error) as exc:
            _log(f"could not remove a download: {type(exc).__name__}")


_PUBLIC_SOURCE_KEYS = ("label", "section", "platform", "host", "part", "rpdl_id")


def _public_source(job: QueueJob) -> dict[str, object]:
    return {key: job.source[key] for key in _PUBLIC_SOURCE_KEYS if key in job.source}


def _staging_free_space() -> int:
    from core.downloads.transfer import staging_dir

    return shutil.disk_usage(staging_dir()).free


def _resolved(path: str) -> Path | None:
    if not path:
        return None
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError):
        return None


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _opt_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _opt_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
