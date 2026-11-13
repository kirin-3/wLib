# SPDX-License-Identifier: GPL-3.0-or-later
"""File-host download jobs and the staging lifecycle.

A job has two steps (design D5): **resolve** turns the chosen mirror into a
file-host address and runs when the download is added (serialized by one
lock, because it may need the user in the browser); **transfer** moves the
data and runs only when the download queue gives the job a slot. The
resolved address stays in memory only. A job can pause (design D6): the
transfer stops with `DownloadPaused` and keeps a partial file the host can
continue from.
"""
from __future__ import annotations

import errno
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import final
from urllib.parse import urlsplit

from core import extension_bridge
from core.downloads.hosts import buzzheavier, mega, pixeldrain
from core.downloads.pacing import SpeedLimiter, host_limiter
from core.downloads.transfer import (
    DownloadError,
    DownloadPaused,
    FileInfo,
    resume_part_name,
    staging_dir,
    transfer,
)

Resolve = Callable[[Callable[[str], None], threading.Event], dict[str, object]]

MEGA_HOSTS = ("mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz")
# Hosts that can continue a file from a byte position.
RESUMABLE_HOSTS = ("pixeldrain", "buzzheavier")
SPEED_WINDOW_SECONDS = 3.0

# Outcomes (what the queue reads)
IDLE = "idle"
PREPARING = "preparing"
NEEDS_USER = "needs_user"
PREPARED = "prepared"
RUNNING = "running"
STOPPED = "stopped"
DONE = "done"
FAILED = "failed"


def _resolve_failure(error: str) -> str:
    """Turn an extension or bridge resolve error into a user-facing cause."""
    lower = error.lower()
    if 'time' in lower:
        return 'Resolving the link timed out; finish any check in the browser or try another mirror'
    if 'disconnect' in lower:
        return 'The browser extension disconnected; keep the browser open and try again'
    if lower == 'cancelled':
        return 'The browser window was closed before the link resolved'
    return f"Could not resolve the link ({error or 'unknown error'}); try another mirror or open it in the browser"


def _failure_text(exc: BaseException) -> str:
    if isinstance(exc, DownloadError):
        return str(exc)
    if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, getattr(errno, 'EDQUOT', -1)):
        return 'Not enough disk space for the download'
    return 'network error'


@final
class _Signals:
    """Stop signals for one run of a job; a new run gets new ones."""

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.pause = threading.Event()
        # Set together with `cancel` or `pause`; wakes any wait in the transfer.
        self.interrupt = threading.Event()


@final
class _Job:
    def __init__(self, job_id: str, game_id: int, host: str) -> None:
        self.id = job_id
        self.game_id = game_id
        self.host = host
        self.outcome = IDLE
        self.stage = 'waiting'
        self.bytes = 0
        self.total: int | None = None
        self.speed = 0.0
        self.error = ''
        self.archive_path = ''
        self.part_name = ''
        self.can_continue = host in RESUMABLE_HOSTS
        # The resolved file-host address: memory only, never logged or saved.
        self.url = ''
        self.share_url = ''
        self.signals = _Signals()
        self.thread: threading.Thread | None = None
        self.samples: deque[tuple[float, int]] = deque()

    def snapshot(self) -> dict[str, object]:
        return {'job_id': self.id, 'game_id': self.game_id, 'kind': 'host', 'host': self.host,
                'outcome': self.outcome, 'stage': self.stage, 'bytes': self.bytes,
                'total': self.total, 'speed': self.speed, 'error': self.error,
                'archive_path': self.archive_path, 'part_name': self.part_name,
                'can_continue': self.can_continue, 'resolved': bool(self.url)}


@final
class DownloadManager:
    def __init__(self, limiter: SpeedLimiter | None = None) -> None:
        self._lock = threading.RLock()
        self._resolve_lock = threading.Lock()
        self._limiter = limiter or host_limiter
        self._jobs: dict[str, _Job] = {}
        self._open_archives: set[Path] = set()

    # --- jobs -------------------------------------------------------------

    def add(self, job_id: str, game_id: int, host: str) -> None:
        """Create the in-memory record for a queue job (idempotent)."""
        with self._lock:
            if job_id not in self._jobs:
                self._jobs[job_id] = _Job(job_id, game_id, host)

    def prepare(self, job_id: str, resolve: Resolve) -> None:
        """Resolve the link now, outside any slot."""
        job = self._job(job_id)
        with self._lock:
            job.outcome, job.stage, job.error = PREPARING, 'resolving', ''
        self._spawn(job, lambda signals: self._prepare(job, resolve, signals))

    def run(self, job_id: str, resolve: Resolve | None = None) -> None:
        """Transfer the file; resolves first with `resolve` when no address is held."""
        job = self._job(job_id)
        with self._lock:
            job.outcome, job.error = RUNNING, ''
            job.stage = 'downloading' if job.url else 'resolving'
        self._spawn(job, lambda signals: self._run(job, resolve, signals))

    def pause(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.outcome not in (RUNNING, NEEDS_USER, PREPARING):
                return False
            job.signals.pause.set()
            job.signals.interrupt.set()
            return True

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job.signals.cancel.set()
            job.signals.interrupt.set()
            return True

    def forget(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None:
            job.signals.cancel.set()
            job.signals.interrupt.set()

    def restore_finished(self, job_id: str, game_id: int, host: str, archive_path: str) -> None:
        """Re-create a finished job after a restart, so its archive can be installed."""
        with self._lock:
            job = self._jobs.setdefault(job_id, _Job(job_id, game_id, host))
            job.outcome, job.stage, job.archive_path = DONE, 'done', archive_path

    def set_rate(self, bytes_per_second: float) -> None:
        """The host side's share of the shared speed limit; 0 is unlimited."""
        self._limiter.set_rate(bytes_per_second)

    def snapshot(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job is not None else None

    def has_address(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return job is not None and bool(job.url)

    def drop_address(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.url = job.share_url = ''

    def busy(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return job is not None and job.thread is not None and job.thread.is_alive()

    def _job(self, job_id: str) -> _Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise DownloadError('Download not found')
            return job

    def _spawn(self, job: _Job, work: Callable[[_Signals], None]) -> None:
        with self._lock:
            previous = job.thread
            if previous is not None:
                # The earlier run must stop before this one starts.
                job.signals.pause.set()
                job.signals.interrupt.set()
            signals = _Signals()
            job.signals = signals

            def target() -> None:
                # A paused transfer may still be closing its stream; never
                # run two transfers of one job (they would share a part file).
                if previous is not None:
                    previous.join()
                work(signals)

            thread = threading.Thread(target=target, daemon=True)
            job.thread = thread
        thread.start()

    # --- resolve ----------------------------------------------------------

    def _resolve(self, job: _Job, resolve: Resolve, signals: _Signals) -> None:
        """Turn the mirror into an address held in memory. Raises DownloadError."""
        def update_stage(stage: str) -> None:
            if stage == 'needs_user':
                with self._lock:
                    job.stage = 'needs_user'
                    job.outcome = NEEDS_USER

        while not self._resolve_lock.acquire(timeout=0.1):
            if signals.cancel.is_set():
                raise DownloadError('cancelled')
            if signals.pause.is_set():
                raise DownloadPaused()
        try:
            if signals.cancel.is_set():
                raise DownloadError('cancelled')
            try:
                result = resolve(update_stage, signals.cancel)
            except extension_bridge.BridgeUnavailable as exc:
                raise DownloadError('Open a browser with an up-to-date wLib extension to download in-app') from exc
            except extension_bridge.BridgeFailed as exc:
                if signals.cancel.is_set():
                    raise DownloadError('cancelled') from exc
                raise DownloadError(_resolve_failure(str(exc))) from exc
        finally:
            self._resolve_lock.release()
        if signals.cancel.is_set():
            raise DownloadError('cancelled')
        if result.get('needs_permission'):
            raise DownloadError('Enable Buzzheavier downloads in the wLib extension settings')
        if result.get('ok') is not True:
            raise DownloadError(_resolve_failure(str(result.get('error') or '')))
        url = result.get('url')
        if not isinstance(url, str) or not url:
            raise DownloadError('file invalid')
        share = result.get('share_url')
        with self._lock:
            job.url = url
            job.share_url = share if isinstance(share, str) else ''

    def _prepare(self, job: _Job, resolve: Resolve, signals: _Signals) -> None:
        try:
            self._resolve(job, resolve, signals)
        except Exception as exc:
            self._end(job, exc, signals)
            return
        with self._lock:
            job.outcome, job.stage = PREPARED, 'waiting'

    # --- transfer ---------------------------------------------------------

    def _probe(self, job: _Job, signals: _Signals) -> FileInfo:
        url = job.url
        if pixeldrain.matches(url):
            return pixeldrain.probe(url)
        if urlsplit(url).hostname in MEGA_HOSTS:
            with self._lock:
                job.stage = 'verifying'
            info = mega.probe(url, signals.cancel)
            with self._lock:
                job.stage = 'downloading'
            return info
        if buzzheavier.matches(url):
            return buzzheavier.probe(url, job.share_url)
        raise DownloadError('unsupported host; open in browser')

    def _run(self, job: _Job, resolve: Resolve | None, signals: _Signals) -> None:
        try:
            if not job.url:
                if resolve is None:
                    raise DownloadError('Download link is unavailable; reload the picker')
                self._resolve(job, resolve, signals)
                with self._lock:
                    job.outcome, job.stage = RUNNING, 'downloading'
            if signals.pause.is_set():
                raise DownloadPaused()
            info = self._probe(job, signals)
            part_name = resume_part_name(info)
            with self._lock:
                job.part_name = part_name
                job.can_continue = bool(part_name)
                job.stage = 'downloading'
                job.samples.clear()

            def progress(current: int, total: int | None) -> None:
                now = time.monotonic()
                with self._lock:
                    job.bytes = current
                    job.total = total
                    job.samples.append((now, current))
                    while job.samples and now - job.samples[0][0] > SPEED_WINDOW_SECONDS:
                        _ = job.samples.popleft()
                    first_time, first_bytes = job.samples[0]
                    job.speed = (current - first_bytes) / max(now - first_time, 0.25) if len(job.samples) > 1 else 0.0

            def verify() -> None:
                with self._lock:
                    job.stage = 'verifying'

            path = transfer(info, job.id, signals.cancel, progress, verify,
                            pause=signals.pause, limiter=self._limiter, interrupt=signals.interrupt)
        except Exception as exc:
            self._end(job, exc, signals)
            return
        with self._lock:
            job.archive_path = str(path)
            if job.signals is signals:
                job.outcome, job.stage, job.speed = DONE, 'done', 0.0

    def _end(self, job: _Job, exc: BaseException, signals: _Signals) -> None:
        with self._lock:
            if job.signals is not signals:
                return  # A newer run owns the job now.
            job.speed = 0.0
            if signals.cancel.is_set() or str(exc) == 'cancelled':
                job.outcome, job.stage, job.error = STOPPED, 'cancelled', ''
            elif isinstance(exc, DownloadPaused) or signals.pause.is_set():
                job.outcome, job.stage, job.error = STOPPED, 'paused', ''
            else:
                job.outcome, job.stage, job.error = FAILED, 'failed', _failure_text(exc)

    # --- staging ----------------------------------------------------------

    def mark_open(self, path: str) -> None:
        with self._lock:
            self._open_archives.add(Path(path).resolve())

    def release(self, path: str) -> None:
        with self._lock:
            self._open_archives.discard(Path(path).resolve())

    def delete_part(self, name: str) -> None:
        """Delete one partial file of a removed download."""
        if not name or '/' in name or '\\' in name or not name.endswith('.part'):
            return
        try:
            (staging_dir() / name).unlink(missing_ok=True)
        except OSError:
            pass

    def delete_archive(self, path: str) -> bool:
        """Delete a finished download's archive, when it is in staging and not open."""
        if not path:
            return False
        try:
            target = Path(path).resolve()
            if target.parent != staging_dir().resolve():
                return False
            with self._lock:
                if target in self._open_archives:
                    return False
            target.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def cleanup(self, keep_parts: Iterable[str] = (), *, now: float | None = None) -> None:
        """Delete partial files no queued download can continue from, and old archives.

        `keep_parts` names the partial files of waiting, paused or failed
        downloads. Completed archives older than 7 days go unless an install
        dialog holds them. The torrent area (`downloads/torrents/`) is a folder
        and is never visited.
        """
        now = now if now is not None else time.time()
        keep = set(keep_parts)
        with self._lock:
            protected = set(self._open_archives)
            keep.update(job.part_name for job in self._jobs.values() if job.part_name)
        for path in staging_dir().iterdir():
            try:
                if not path.is_file() or path.is_symlink() or path.resolve() in protected:
                    continue
                if path.suffix == '.part':
                    if path.name not in keep:
                        path.unlink()
                elif path.suffix.lower() in ('.zip','.rar','.7z') and path.stat().st_mtime < now - 7 * 86400:
                    path.unlink()
            except OSError:
                continue


manager = DownloadManager()
