# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapters that let the download queue drive the two transfer engines.

`TorrentWorker` wraps `TorrentSupervisor`: a waiting torrent is a paused
torrent (design D4). `HostWorker` wraps `DownloadManager` (design D5, D6).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import final

from core.downloads import failures
from core.downloads.queue import (
    ATTENTION,
    PAUSED,
    PHASE_DONE,
    PHASE_FAILED,
    PHASE_IDLE,
    PHASE_NEEDS_USER,
    PHASE_PREPARED,
    PHASE_PREPARING,
    PHASE_RUNNING,
    PHASE_STOPPED,
    READY,
    RUNNING,
    QueueJob,
    WorkerState,
)
from core.downloads.manager import RESUMABLE_HOSTS, DownloadManager, Resolve
from core.downloads.torrents import ACTIVE_STAGES, MSG_LOW_DISK, TorrentSupervisor
from core.downloads.transfer import DownloadError, staging_dir

_TORRENT_DETAIL_KEYS = (
    "stage", "hint", "peers", "upload_speed", "ratio", "ratio_target", "seed_state",
    "archive_path", "folder_path", "payload_kind", "installed", "restarting",
)


def _int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


@dataclass
class TorrentContext:
    """What `prepare` needs for a new torrent job: the `.torrent` fetch."""

    fetch: Callable[[], bytes]


@final
class TorrentWorker:
    kind = "torrent"

    def __init__(
        self,
        supervisor: Callable[[], TorrentSupervisor],
        fetch_for: Callable[[int], Callable[[], bytes]] | None = None,
    ) -> None:
        self._supervisor = supervisor
        # Builds the `.torrent` fetch for an RPDL id after a restart or a retry.
        self._fetch_for = fetch_for

    def _snapshot(self, job_id: str) -> dict[str, object] | None:
        return next((item for item in self._supervisor().status() if item.get("job_id") == job_id), None)

    def _rpdl_id(self, job: QueueJob) -> int:
        return _int(job.source.get("rpdl_id"))

    def prepare(self, job: QueueJob, context: object) -> None:
        fetch = context.fetch if isinstance(context, TorrentContext) else None
        if fetch is None:
            if self._fetch_for is None:
                raise DownloadError("Torrent downloads are unavailable in this build")
            fetch = self._fetch_for(self._rpdl_id(job))
        _ = self._supervisor().start(
            job.game_id, job.title, job.version, self._rpdl_id(job),
            fetch, paused=True, job_id=job.id,
        )

    def start(self, job: QueueJob) -> None:
        supervisor = self._supervisor()
        snapshot = self._snapshot(job.id)
        if snapshot is not None and snapshot.get("stage") == "failed" and not snapshot.get("info_hash"):
            # The `.torrent` never arrived: fetch it again.
            _ = supervisor.cancel(job.id)
            snapshot = None
        if snapshot is None or snapshot.get("stage") == "cancelled":
            if self._fetch_for is None:
                raise DownloadError("Torrent downloads are unavailable in this build")
            _ = supervisor.start(
                job.game_id, job.title, job.version, self._rpdl_id(job),
                self._fetch_for(self._rpdl_id(job)), paused=False, job_id=job.id,
            )
            return
        if snapshot.get("stage") == "fetching":
            return
        if not supervisor.resume(job.id):
            refreshed = self._snapshot(job.id) or {}
            if refreshed.get("stage") != "downloading":
                raise DownloadError(str(refreshed.get("error") or "") or MSG_LOW_DISK)

    def stop(self, job: QueueJob) -> None:
        _ = self._supervisor().pause(job.id)

    def remove(self, job: QueueJob) -> None:
        supervisor = self._supervisor()
        snapshot = self._snapshot(job.id)
        if snapshot is None:
            return
        stage = snapshot.get("stage")
        if stage in (*ACTIVE_STAGES, "failed"):
            _ = supervisor.cancel(job.id)
        elif stage == "done":
            # A seeding torrent keeps its files; it leaves with "Stop seeding".
            _ = supervisor.discard(job.id)

    def snapshot(self, job: QueueJob) -> WorkerState:
        snapshot = self._snapshot(job.id)
        if snapshot is None:
            if job.state == READY:
                return WorkerState(PHASE_DONE)
            return WorkerState(PHASE_IDLE, bytes_done=job.bytes_done, bytes_total=job.bytes_total)
        info_hash = str(snapshot.get("info_hash") or "")
        if info_hash and job.source.get("info_hash") != info_hash:
            job.source["info_hash"] = info_hash
        details = {key: snapshot.get(key) for key in _TORRENT_DETAIL_KEYS if key in snapshot}
        total = snapshot.get("total")
        state = WorkerState(
            bytes_done=_int(snapshot.get("bytes")),
            bytes_total=_int(total) if isinstance(total, (int, float)) and total else None,
            speed=float(_int(snapshot.get("speed"))),
            can_continue=True,
            details=details,
        )
        stage = snapshot.get("stage")
        if stage == "fetching":
            state.phase = PHASE_PREPARING
        elif stage == "downloading":
            state.phase = PHASE_RUNNING
        elif stage == "paused":
            if snapshot.get("auto_paused"):
                state.phase = PHASE_FAILED
                state.error = str(snapshot.get("error") or MSG_LOW_DISK)
            else:
                state.phase = PHASE_STOPPED if job.state == RUNNING else PHASE_PREPARED
        elif stage == "done":
            state.phase = PHASE_DONE
        elif stage == "failed":
            state.phase = PHASE_FAILED
            state.error = str(snapshot.get("error") or "Torrent download failed")
        else:
            state.phase = PHASE_STOPPED
        return state

    def restore(self, job: QueueJob) -> bool:
        if self._snapshot(job.id) is not None:
            return True
        # A torrent whose `.torrent` never arrived has no folder; it fetches
        # again at its turn. One that had a folder lost its data.
        return not job.source.get("info_hash") and self._rpdl_id(job) > 0 and job.state != READY

    def unclaimed(self, claimed: set[str]) -> list[QueueJob]:
        adopted: list[QueueJob] = []
        for snapshot in self._supervisor().status():
            job_id = str(snapshot.get("job_id") or "")
            if not job_id or job_id in claimed:
                continue
            stage = snapshot.get("stage")
            error = str(snapshot.get("error") or "")
            if stage == "paused":
                state = PAUSED if snapshot.get("loaded_paused") else RUNNING
            elif stage == "downloading":
                state = RUNNING
            elif stage == "failed":
                state = ATTENTION
            elif stage == "done" and not snapshot.get("installed"):
                state = READY
            else:
                continue
            total = snapshot.get("total")
            job = QueueJob(
                id=job_id,
                kind=self.kind,
                game_id=_int(snapshot.get("game_id")),
                title=str(snapshot.get("title") or ""),
                version=str(snapshot.get("version") or ""),
                state=state,
                source={
                    "rpdl_id": _int(snapshot.get("rpdl_id")),
                    "info_hash": str(snapshot.get("info_hash") or ""),
                },
                bytes_done=_int(snapshot.get("bytes")),
                bytes_total=_int(total) if isinstance(total, (int, float)) and total else None,
                created_at=float(_int(snapshot.get("created"))),
            )
            if state == ATTENTION:
                job.error_code, job.error_text = failures.classify(error), error
            adopted.append(job)
        return adopted

    def set_speed_share(self, kib: int, limited: bool) -> None:
        # libtorrent reads 0 as unlimited; a side with no share gets the smallest limit.
        self._supervisor().set_download_limit(max(kib, 1) if limited else 0)

    def cleanup(self, jobs: list[QueueJob]) -> None:
        # The supervisor cleans its own folders at startup (`TorrentSupervisor.cleanup`).
        del jobs



# A failure that says the held address is no good: the next start reads the thread again.
_LINK_FAILURES = frozenset({
    failures.REMOVED, failures.CHECK_TIMEOUT, failures.EXTENSION, failures.PERMISSION,
    failures.MIRROR_GONE, failures.CHALLENGE, failures.INVALID, failures.SIGN_IN,
})

_HOST_PHASES = {
    "idle": PHASE_IDLE,
    "preparing": PHASE_PREPARING,
    "needs_user": PHASE_NEEDS_USER,
    "prepared": PHASE_PREPARED,
    "running": PHASE_RUNNING,
    "stopped": PHASE_STOPPED,
    "done": PHASE_DONE,
    "failed": PHASE_FAILED,
}


@dataclass
class HostContext:
    """What `prepare` needs for a new file-host job; kept in memory only."""

    resolve: Resolve


@final
class HostWorker:
    kind = "host"

    def __init__(
        self,
        manager: DownloadManager,
        relink: Callable[[QueueJob], Resolve] | None = None,
    ) -> None:
        self._manager = manager
        # Reads the thread again and returns a resolve for the saved build and mirror.
        self._relink = relink

    def _host(self, job: QueueJob) -> str:
        return str(job.source.get("host") or "")

    def _ensure(self, job: QueueJob) -> None:
        self._manager.add(job.id, job.game_id, self._host(job))

    def _lazy_relink(self, job: QueueJob) -> Resolve | None:
        relink = self._relink
        if relink is None:
            return None

        def resolve(progress: Callable[[str], None], cancel: threading.Event) -> dict[str, object]:
            # Runs in the job's thread: reading the thread may wait for the browser.
            return relink(job)(progress, cancel)

        return resolve

    def prepare(self, job: QueueJob, context: object) -> None:
        self._ensure(job)
        if isinstance(context, HostContext):
            self._manager.prepare(job.id, context.resolve)
            return
        relink = self._lazy_relink(job)
        if relink is None:
            raise DownloadError("Download link is unavailable; reload the picker")
        self._manager.prepare(job.id, relink)

    def start(self, job: QueueJob) -> None:
        self._ensure(job)
        if job.previous_error in _LINK_FAILURES:
            self._manager.drop_address(job.id)
        resolve = None if self._manager.has_address(job.id) else self._lazy_relink(job)
        self._manager.run(job.id, resolve)

    def stop(self, job: QueueJob) -> None:
        _ = self._manager.pause(job.id)

    def remove(self, job: QueueJob) -> None:
        snapshot = self._manager.snapshot(job.id) or {}
        part = str(snapshot.get("part_name") or job.source.get("part_file") or "")
        archive = str(snapshot.get("archive_path") or "")
        if not archive and job.source.get("archive"):
            archive = str(staging_dir() / str(job.source.get("archive")))
        busy = self._manager.busy(job.id)
        self._manager.forget(job.id)
        # A running transfer deletes its own partial file when cancelled.
        if part and not busy:
            self._manager.delete_part(part)
        if job.state == READY and archive:
            _ = self._manager.delete_archive(archive)

    def snapshot(self, job: QueueJob) -> WorkerState:
        snapshot = self._manager.snapshot(job.id)
        if snapshot is None:
            return WorkerState(PHASE_IDLE, bytes_done=job.bytes_done, bytes_total=job.bytes_total,
                               can_continue=self._host(job) in RESUMABLE_HOSTS)
        part = str(snapshot.get("part_name") or "")
        if part and job.source.get("part_file") != part:
            job.source["part_file"] = part
        archive = str(snapshot.get("archive_path") or "")
        if archive and job.source.get("archive") != Path(archive).name:
            job.source["archive"] = Path(archive).name
        total = snapshot.get("total")
        phase = _HOST_PHASES.get(str(snapshot.get("outcome") or ""), PHASE_IDLE)
        return WorkerState(
            phase=phase,
            error=str(snapshot.get("error") or ""),
            bytes_done=_int(snapshot.get("bytes")),
            bytes_total=_int(total) if isinstance(total, int) and total > 0 else None,
            speed=_float(snapshot.get("speed")),
            can_continue=bool(snapshot.get("can_continue")),
            details={"stage": snapshot.get("stage"), "host": self._host(job), "archive_path": archive},
        )

    def restore(self, job: QueueJob) -> bool:
        if job.state == READY:
            name = str(job.source.get("archive") or "")
            path = staging_dir() / name if name else None
            if path is None or not path.is_file():
                return False
            self._manager.restore_finished(job.id, job.game_id, self._host(job), str(path))
            return True
        self._ensure(job)
        return True

    def unclaimed(self, claimed: set[str]) -> list[QueueJob]:
        del claimed  # File-host jobs exist only as queue rows.
        return []

    def set_speed_share(self, kib: int, limited: bool) -> None:
        self._manager.set_rate((max(kib, 1) * 1024) if limited else 0)

    def cleanup(self, jobs: list[QueueJob]) -> None:
        keep = [str(job.source.get("part_file") or "") for job in jobs if job.state != READY]
        self._manager.cleanup([name for name in keep if name])
