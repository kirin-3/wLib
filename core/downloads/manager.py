# SPDX-License-Identifier: GPL-3.0-or-later
"""Background download jobs and staging lifecycle."""
from __future__ import annotations

import errno
import threading
import time
import uuid
from urllib.parse import urlsplit
from pathlib import Path
from typing import final
from collections.abc import Callable

from core import extension_bridge
from core.downloads.hosts import buzzheavier, mega, pixeldrain
from core.downloads.transfer import RESUME_KEEP_SECONDS, RESUME_PREFIX, DownloadError, FileInfo, staging_dir, transfer


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


@final
class _Job:
    def __init__(self, game_id: int) -> None:
        self.id = uuid.uuid4().hex
        self.game_id = game_id
        self.stage = 'resolving'
        self.bytes = 0
        self.total: int | None = None
        self.speed = 0.0
        self.error = ''
        self.archive_path = ''
        self.cancel = threading.Event()

    def snapshot(self) -> dict[str, object]:
        return {'job_id':self.id, 'game_id':self.game_id, 'stage':self.stage,
                'bytes':self.bytes, 'total':self.total, 'speed':self.speed,
                'error':self.error, 'archive_path':self.archive_path}


@final
class DownloadManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._transfers = threading.Semaphore(2)
        self._resolve_lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._open_archives: set[Path] = set()

    def start(self, game_id: int, resolve: Callable[[Callable[[str], None], threading.Event], dict[str, object]]) -> str:
        with self._lock:
            if any(job.game_id == game_id and job.stage in ('resolving','needs_user','downloading','verifying') for job in self._jobs.values()):
                raise DownloadError('A download is already running for this game')
            job = _Job(game_id)
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, resolve), daemon=True).start()
        return job.id

    def _run(self, job: _Job, resolve: Callable[[Callable[[str], None], threading.Event], dict[str, object]]) -> None:
        def update_stage(stage: str) -> None:
            if stage == 'needs_user':
                with self._lock:
                    job.stage = 'needs_user'
        try:
            while not self._resolve_lock.acquire(timeout=0.1):
                if job.cancel.is_set():
                    raise DownloadError('cancelled')
            try:
                if job.cancel.is_set():
                    raise DownloadError('cancelled')
                try:
                    result = resolve(update_stage, job.cancel)
                except extension_bridge.BridgeUnavailable as exc:
                    raise DownloadError('Open a browser with an up-to-date wLib extension to download in-app') from exc
                except extension_bridge.BridgeFailed as exc:
                    if job.cancel.is_set():
                        raise DownloadError('cancelled') from exc
                    raise DownloadError(_resolve_failure(str(exc))) from exc
            finally:
                self._resolve_lock.release()
            if job.cancel.is_set():
                raise DownloadError('cancelled')
            if result.get('needs_permission'):
                raise DownloadError('Enable Buzzheavier downloads in the wLib extension settings')
            if result.get('ok') is not True:
                raise DownloadError(_resolve_failure(str(result.get('error') or '')))
            url = result.get('url')
            if not isinstance(url, str):
                raise DownloadError('file invalid')
            while not self._transfers.acquire(timeout=0.1):
                if job.cancel.is_set():
                    raise DownloadError('cancelled')
            try:
                if job.cancel.is_set():
                    raise DownloadError('cancelled')
                with self._lock:
                    job.stage = 'downloading'
                info: FileInfo
                if pixeldrain.matches(url):
                    info = pixeldrain.probe(url)
                elif urlsplit(url).hostname in ("mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz"):
                    with self._lock:
                        job.stage = 'verifying'
                    info = mega.probe(url, job.cancel)
                    with self._lock:
                        job.stage = 'downloading' 
                elif buzzheavier.matches(url):
                    share = result.get('share_url')
                    info = buzzheavier.probe(url, share if isinstance(share, str) else '')
                else:
                    raise DownloadError('unsupported host; open in browser')
                started = time.monotonic()
                def progress(current: int, total: int | None) -> None:
                    with self._lock:
                        job.bytes = current
                        job.total = total
                        job.speed = current / max(time.monotonic() - started, 0.001)
                def verify() -> None:
                    with self._lock:
                        job.stage = 'verifying'
                path = transfer(info, job.id, job.cancel, progress, verify)
                with self._lock:
                    job.archive_path = str(path)
                    job.stage = 'done'
            finally:
                self._transfers.release()
        except Exception as exc:
            with self._lock:
                job.stage = 'cancelled' if job.cancel.is_set() or str(exc) == 'cancelled' else 'failed'
                if job.stage == 'cancelled':
                    job.error = ''
                elif isinstance(exc, DownloadError):
                    job.error = str(exc)
                elif isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, getattr(errno, 'EDQUOT', -1)):
                    job.error = 'Not enough disk space for the download'
                else:
                    job.error = 'network error'


    def status(self) -> list[dict[str, object]]:
        from core.downloads.torrents import supervisor

        with self._lock:
            jobs = [job.snapshot() for job in self._jobs.values()]
        return jobs + supervisor.status()

    def cancel(self, job_id: str) -> bool:
        from core.downloads.torrents import supervisor

        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                if job.stage not in ('resolving','needs_user','downloading','verifying'):
                    return False
                job.cancel.set()
                return True
        return supervisor.cancel(job_id)

    def mark_open(self, path: str) -> None:
        with self._lock:
            self._open_archives.add(Path(path).resolve())

    def release(self, path: str) -> None:
        with self._lock:
            self._open_archives.discard(Path(path).resolve())

    def cleanup(self, *, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            protected = set(self._open_archives)
        for path in staging_dir().iterdir():
            try:
                if not path.is_file() or path.is_symlink() or path.resolve() in protected:
                    continue
                if path.suffix == '.part':
                    # A resumable partial file waits a week for a retry of the
                    # same download; one named after its job can never resume.
                    if not path.name.startswith(RESUME_PREFIX) or path.stat().st_mtime < now - RESUME_KEEP_SECONDS:
                        path.unlink()
                elif path.suffix.lower() in ('.zip','.rar','.7z') and path.stat().st_mtime < now - 7 * 86400:
                    path.unlink()
            except OSError:
                continue


manager = DownloadManager()
