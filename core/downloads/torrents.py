# SPDX-License-Identifier: GPL-3.0-or-later
"""Torrent downloads: jobs in this process, the engine in a helper process.

The helper (`wlib --torrent-helper`, see torrent_helper.py) speaks JSON lines
on its stdin and stdout. This module owns the job state, the per-torrent
folder layout and the helper's lifecycle. It never loads libtorrent.

Layout: `<staging>/torrents/<info-hash>/` holds `meta.torrent`, `resume.dat`,
`state.json` and `data/`. Tracker secrets live only in those files.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import IO, cast, final

from core.downloads import seeding, torrent_settings
from core.downloads.bencode import TorrentMeta, parse_torrent
from core.downloads.transfer import DownloadError, staging_dir

LOW_DISK_BYTES = 512 * 1024 * 1024
PEER_HINT_SECONDS = 20.0
NO_DATA_HINT_SECONDS = 120.0
RESTART_BACKOFF = (1.0, 5.0, 15.0)
RESTART_LIMIT = 3
RESTART_WINDOW = 600.0
PAUSED_KEEP_SECONDS = 30 * 86400
DISK_CHECK_SECONDS = 5.0
STATE_SAVE_SECONDS = 15.0
REMOVE_WAIT_SECONDS = 3.0
ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z")
# Names that can be an archive or one part of a multi-part archive.
_VOLUME_NAME = re.compile(r"\.(?:zip|rar|7z|r\d{2,3}|z\d{2,3}|\d{3})$")
# Files a release may carry next to its archive without being part of the game.
EXTRA_SUFFIXES = (".txt", ".nfo", ".url", ".md", ".sfv", ".jpg", ".jpeg", ".png", ".webp")

MSG_UNAVAILABLE = "Torrent downloads are unavailable in this build"
MSG_KEEPS_STOPPING = "Torrent engine keeps stopping"
MSG_RESTARTING = "Torrent engine stopped, restarting"
MSG_INVALID = "Invalid torrent"
MSG_LOW_DISK = "Free disk space, then resume"

ACTIVE_STAGES = ("fetching", "downloading", "paused")


def torrents_dir() -> Path:
    path = staging_dir() / "torrents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def free_space(path: Path) -> int:
    return shutil.disk_usage(path).free


def _size_text(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"


def find_payload(data_dir: Path) -> tuple[str, str]:
    """('archive', path) when the torrent holds one archive, else ('folder', game folder).

    It counts as an archive only when every file is a part of that one archive
    or a small extra such as a readme. A game folder that ships its own `.zip`
    (a data pack, a bundled library) stays a folder.
    """
    from core.archive_volumes import volume_set

    first_volumes: set[str] = set()
    other_files = False
    for root, _dirs, names in os.walk(data_dir):
        for name in names:
            path = os.path.join(root, name)
            lower = name.lower()
            if _VOLUME_NAME.search(lower):
                volumes = volume_set(path)
                if volumes is not None and volumes.first:
                    first_volumes.add(os.path.normcase(volumes.first))
                    continue
                if lower.endswith(ARCHIVE_SUFFIXES):
                    first_volumes.add(os.path.normcase(path))
                    continue
            if not lower.endswith(EXTRA_SUFFIXES):
                other_files = True
    if len(first_volumes) == 1 and not other_files:
        return "archive", next(iter(first_volumes))
    entries = [entry for entry in data_dir.iterdir()] if data_dir.is_dir() else []
    if len(entries) == 1 and entries[0].is_dir():
        return "folder", str(entries[0])
    return "folder", str(data_dir)


@final
class _Job:
    def __init__(self, game_id: int, title: str, version: str, rpdl_id: int) -> None:
        self.id = uuid.uuid4().hex
        self.game_id = game_id
        self.title = title
        self.version = version
        self.rpdl_id = rpdl_id
        self.stage = "fetching"
        self.phase = "fetching"
        self.error = ""
        self.folder: Path | None = None
        self.info_hash = ""
        self.total = 0
        self.bytes = 0
        self.speed = 0
        self.upload_speed = 0
        self.peers = 0
        self.uploaded = 0
        self.seeding_seconds = 0
        self.paused = False
        self.auto_paused = False
        self.seed_state = ""
        self.restarting = False
        self.created = time.time()
        self.cancel = threading.Event()
        self.removed = threading.Event()
        self.added = False
        self.waiting_since = time.monotonic()
        self.idle_since: float | None = None
        self.peerless_since: float | None = time.monotonic()
        self.saved_at = 0.0
        self.installed = False
        self.payload: tuple[str, str] | None = None
        self.last_status_at: float | None = None
        self.seed_mode_note = ""

    def eta(self) -> int | None:
        if self.stage != "downloading" or self.speed <= 0 or self.total <= 0:
            return None
        return int(max(0, self.total - self.bytes) / self.speed)

    def hint(self, now: float) -> str:
        if self.restarting:
            return MSG_RESTARTING
        if self.stage == "fetching":
            return "Fetching the torrent"
        if self.stage != "downloading" or self.bytes >= self.total > 0:
            return ""
        if self.peers == 0 and self.peerless_since is not None and now - self.peerless_since >= PEER_HINT_SECONDS:
            return "Looking for peers"
        if self.peers > 0 and self.idle_since is not None and now - self.idle_since >= NO_DATA_HINT_SECONDS:
            return "Connected, but no data is arriving"
        if self.peers == 0:
            return "Finding peers"
        return ""

    def snapshot(self, now: float) -> dict[str, object]:
        archive_path = folder_path = ""
        payload_kind = ""
        if self.stage == "done" and self.folder is not None and self.folder.is_dir():
            if self.payload is None:
                self.payload = find_payload(self.folder / "data")
            payload_kind, path = self.payload
            archive_path, folder_path = (path, "") if payload_kind == "archive" else ("", path)
        return {
            "job_id": self.id, "game_id": self.game_id, "kind": "torrent", "stage": self.stage,
            "title": self.title, "version": self.version,
            "bytes": self.bytes, "total": self.total or None, "speed": self.speed,
            "error": self.error, "archive_path": archive_path, "folder_path": folder_path,
            "peers": self.peers, "upload_speed": self.upload_speed,
            "ratio": (self.uploaded / self.total) if self.total else 0.0,
            "seed_state": self.seed_state, "paused": self.paused, "payload_kind": payload_kind,
            "installed": self.installed,
            "ratio_target": torrent_settings.seed_ratio() or 0,
            "hint": self.hint(now), "eta": self.eta(),
        }


def _default_command() -> tuple[list[str], dict[str, str]]:
    from core import launcher

    return launcher.self_command("--torrent-helper"), launcher.self_env(os.environ.copy())


def _log(message: str) -> None:
    # The app log copies stderr. Messages here never carry tokens, tracker URLs or magnet links.
    print(f"[torrents] {message}", file=sys.stderr, flush=True)


@final
class TorrentSupervisor:
    def __init__(
        self,
        command: Callable[[], tuple[list[str], dict[str, str]]] = _default_command,
        backoff: tuple[float, ...] = RESTART_BACKOFF,
        clock: Callable[[], float] = time.monotonic,
        free_space_fn: Callable[[Path], int] | None = None,
        options: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        self._command = command
        self._backoff = backoff
        self._clock = clock
        self._free_space = free_space_fn or free_space
        self._options: Callable[[], dict[str, object]] = options or torrent_settings.helper_options
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._proc: subprocess.Popen[str] | None = None
        self._ready = False
        self._closing = False
        self._gave_up = False
        self._engine_missing = False
        self._restarts: list[float] = []
        self._open: set[Path] = set()
        self._last_disk_check = 0.0

    # --- helper lifecycle -------------------------------------------------

    def _spawn(self) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            if self._engine_missing:
                return False
            try:
                argv, env = self._command()
                proc = subprocess.Popen(
                    argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                    bufsize=1, env=env, encoding="utf-8",
                )
            except (OSError, ValueError):
                _log("helper could not start")
                return False
            self._proc = proc
            self._ready = False
            threading.Thread(target=self._read, args=(proc,), daemon=True).start()
            hello: dict[str, object] = {"cmd": "hello"}
            hello.update(self._options())
            _ = self._send(hello)
            return True

    def _send(self, command: dict[str, object]) -> bool:
        with self._lock:
            proc = self._proc
            stdin: IO[str] | None = proc.stdin if proc is not None else None
            if proc is None or stdin is None or proc.poll() is not None:
                return False
            try:
                _ = stdin.write(json.dumps(command, separators=(",", ":")) + "\n")
                stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def _read(self, proc: subprocess.Popen[str]) -> None:
        stream = proc.stdout
        if stream is not None:
            for line in cast(IO[str], stream):
                try:
                    event = cast(object, json.loads(line))
                except ValueError:
                    continue
                if isinstance(event, dict):
                    self._on_event(cast(dict[str, object], event))
        _ = proc.wait()
        self._on_exit(proc)

    def _on_exit(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            if proc is not self._proc:
                return
            self._proc = None
            self._ready = False
            for job in self._jobs.values():
                job.added = False
            if self._closing:
                return
            _log(f"helper exited with code {proc.returncode}")
            if self._engine_missing:
                return
            now = self._clock()
            self._restarts = [t for t in self._restarts if now - t < RESTART_WINDOW]
            self._restarts.append(now)
            active = [j for j in self._jobs.values() if self._wants_helper(j)]
            if len(self._restarts) > RESTART_LIMIT:
                self._gave_up = True
                for job in active:
                    if job.stage == "done":
                        self._end_seeding(job)
                    else:
                        self._fail(job, MSG_KEEPS_STOPPING)
                return
            for job in active:
                job.restarting = True
            delay = self._backoff[min(len(self._restarts) - 1, len(self._backoff) - 1)]
        if active:
            timer = threading.Timer(delay, self._restart)
            timer.daemon = True
            timer.start()

    def _restart(self) -> None:
        with self._lock:
            if self._closing or self._gave_up:
                return
            _ = self._spawn()

    def _on_event(self, event: dict[str, object]) -> None:
        name = event.get("event")
        with self._lock:
            if name == "ready":
                self._ready = True
                for job in self._jobs.values():
                    job.restarting = False
                    if self._wants_helper(job) and job.folder is not None and not job.added:
                        self._add_to_helper(job)
            elif name == "status":
                self._on_status(event)
            elif name == "finished":
                job = self._jobs.get(str(event.get("id") or ""))
                if job is not None:
                    self._on_finished(job)
            elif name == "error":
                self._on_error(event)
            elif name in ("removed", "stopped"):
                job = self._jobs.get(str(event.get("id") or ""))
                if job is not None:
                    job.removed.set()

    @staticmethod
    def _wants_helper(job: _Job) -> bool:
        if job.stage in ("downloading", "paused"):
            return True
        return job.stage == "done" and job.seed_state in ("seeding", "awaiting_answer")

    def _add_to_helper(self, job: _Job) -> None:
        assert job.folder is not None
        job.removed.clear()
        job.added = self._send({"cmd": "add", "id": job.id, "dir": str(job.folder), "paused": job.paused})
        job.waiting_since = self._clock()
        job.peerless_since = self._clock()
        job.idle_since = None

    def _on_status(self, event: dict[str, object]) -> None:
        raw_items = event.get("torrents")
        if not isinstance(raw_items, list):
            return
        now = self._clock()
        for raw in cast(list[object], raw_items):
            if not isinstance(raw, dict):
                continue
            item = cast(dict[str, object], raw)
            job = self._jobs.get(str(item.get("id") or ""))
            if job is None:
                continue
            if job.stage == "done":
                self._on_seeding_status(job, item, now)
                continue
            if job.stage not in ("downloading", "paused"):
                continue
            job.total = _int(item.get("size")) or job.total
            job.bytes = _int(item.get("downloaded"))
            job.speed = _int(item.get("down"))
            job.upload_speed = _int(item.get("up"))
            job.peers = _int(item.get("peers"))
            job.uploaded = _int(item.get("uploaded"))
            job.seeding_seconds = _int(item.get("seeding_seconds"))
            if job.peers == 0:
                job.peerless_since = job.peerless_since or now
            else:
                job.peerless_since = None
            if job.peers > 0 and job.speed == 0:
                job.idle_since = job.idle_since or now
            else:
                job.idle_since = None
            if item.get("state") == "error" and not job.paused:
                self._fail(job, "Files could not be written")
                continue
            if now - job.saved_at >= STATE_SAVE_SECONDS:
                self._save_state(job)
        self._check_disk(now)

    def _check_disk(self, now: float) -> None:
        if now - self._last_disk_check < DISK_CHECK_SECONDS:
            return
        self._last_disk_check = now
        try:
            free = self._free_space(torrents_dir())
        except OSError:
            return
        if free >= LOW_DISK_BYTES:
            return
        for job in self._jobs.values():
            if job.stage == "downloading" and not job.paused and job.bytes < job.total:
                self._set_paused(job, True)
                job.auto_paused = True
                job.error = MSG_LOW_DISK
                self._save_state(job)

    def _on_finished(self, job: _Job) -> None:
        if job.stage not in ("downloading", "paused"):
            return
        job.bytes = job.total
        job.stage = "done"
        job.phase = "finished"
        job.speed = 0
        job.error = ""
        job.last_status_at = None
        decision = seeding.decide_after_finish(torrent_settings.seed_mode())
        if decision == seeding.ASK:
            job.seed_state, job.phase = "awaiting_answer", "awaiting_seed_answer"
            # An unanswered question must not seed in the meantime.
            job.paused = True
            if job.added:
                _ = self._send({"cmd": "pause", "id": job.id})
        elif decision == seeding.SEED:
            job.seed_state, job.phase = "seeding", "seeding"
        else:
            self._end_seeding(job)
            return
        self._save_state(job)

    def _on_seeding_status(self, job: _Job, item: dict[str, object], now: float) -> None:
        if job.seed_state not in ("seeding", "awaiting_answer"):
            return
        job.upload_speed = _int(item.get("up"))
        job.peers = _int(item.get("peers"))
        job.uploaded = max(job.uploaded, _int(item.get("uploaded")))
        if item.get("state") == "seeding" and job.last_status_at is not None:
            # Only time spent seeding counts; a gap (helper restart) adds at most a few seconds.
            job.seeding_seconds += int(min(max(now - job.last_status_at, 0.0), 5.0) + 0.5)
        job.last_status_at = now
        if job.seed_state == "seeding" and seeding.limit_reached(
            job.uploaded, job.total, job.seeding_seconds, torrent_settings.seed_ratio(), torrent_settings.seed_hours()
        ):
            self._end_seeding(job)
        elif now - job.saved_at >= STATE_SAVE_SECONDS:
            self._save_state(job)

    def _end_seeding(self, job: _Job) -> None:
        """Stop sharing. The files go once no install is using them."""
        job.seed_state, job.phase = "ended", "ended"
        job.upload_speed = job.peers = 0
        if job.added:
            _ = self._send({"cmd": "stop_seeding", "id": job.id})
            job.added = False
        self._save_state(job)
        self._delete_if_unused(job)

    def _delete_if_unused(self, job: _Job) -> None:
        """Delete an ended torrent's folder after its install released it (or none was needed)."""
        if job.folder is None or job.seed_state != "ended" or not job.installed:
            return
        folder = job.folder.resolve()
        if any(p == folder or folder in p.parents for p in self._open):
            return
        self._delete_folder(job)

    def _on_error(self, event: dict[str, object]) -> None:
        code = str(event.get("code") or "")
        if code == "engine_missing":
            self._engine_missing = True
            for job in self._jobs.values():
                if job.stage in ("fetching", "downloading", "paused"):
                    self._fail(job, MSG_UNAVAILABLE)
            return
        job = self._jobs.get(str(event.get("id") or ""))
        if job is None or job.stage in ("failed", "cancelled"):
            # A late answer, such as to the `remove` that `_fail` sent.
            return
        # An error with an id means the helper does not hold this torrent.
        job.added = False
        if code in ("unsafe_paths", "bad_torrent"):
            self._fail(job, MSG_INVALID)
            if code == "unsafe_paths":
                self._delete_folder(job)
        else:
            self._fail(job, str(event.get("message") or "Torrent download failed"))

    def _fail(self, job: _Job, message: str) -> None:
        if job.added:
            # Drop it from the helper so a retry can add it again.
            _ = self._send({"cmd": "remove", "id": job.id})
            job.added = False
        job.stage = "failed"
        job.phase = "failed"
        job.error = message
        job.restarting = False
        job.speed = job.upload_speed = job.peers = 0
        self._save_state(job)

    # --- state.json -------------------------------------------------------

    def _save_state(self, job: _Job) -> None:
        if job.folder is None or not job.folder.is_dir():
            return
        job.saved_at = self._clock()
        state = {
            "job_id": job.id, "game_id": job.game_id, "rpdl_id": job.rpdl_id, "title": job.title,
            "version": job.version, "info_hash": job.info_hash, "size": job.total,
            "phase": job.phase, "paused": job.paused, "error": job.error,
            "downloaded": job.bytes, "uploaded": job.uploaded,
            "seeding_seconds": job.seeding_seconds, "seed_state": job.seed_state, "installed": job.installed,
            "created": job.created, "updated": time.time(),
        }
        tmp = job.folder / "state.json.tmp"
        try:
            _ = tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, job.folder / "state.json")
        except OSError:
            pass

    def _delete_folder(self, job: _Job) -> None:
        folder = job.folder
        if folder is None:
            return
        root = torrents_dir().resolve()
        try:
            if folder.resolve().parent == root:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass
        job.folder = None

    # --- public API -------------------------------------------------------

    def start(
        self, game_id: int, title: str, version: str, rpdl_id: int, fetch: Callable[[], bytes]
    ) -> str:
        """Start a job: `fetch` returns the `.torrent` bytes (it may raise DownloadError)."""
        with self._lock:
            if any(j.game_id == game_id and j.stage in ACTIVE_STAGES for j in self._jobs.values()):
                raise DownloadError("A download is already running for this game")
            job = _Job(game_id, title, version, rpdl_id)
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, fetch), daemon=True).start()
        return job.id

    def _run(self, job: _Job, fetch: Callable[[], bytes]) -> None:
        try:
            data = fetch()
            if job.cancel.is_set():
                raise DownloadError("cancelled")
            try:
                meta = parse_torrent(data)
            except ValueError as exc:
                raise DownloadError(MSG_INVALID) from exc
            self._prepare(job, meta, data)
        except DownloadError as exc:
            with self._lock:
                if job.cancel.is_set() or str(exc) == "cancelled":
                    job.stage, job.phase, job.error = "cancelled", "cancelled", ""
                else:
                    self._fail(job, str(exc))
        except OSError:
            with self._lock:
                self._fail(job, "Files could not be written")
                self._delete_folder(job)

    def _prepare(self, job: _Job, meta: TorrentMeta, data: bytes) -> None:
        root = torrents_dir()
        free = self._free_space(root)
        if free < meta.size:
            raise DownloadError(
                f"Not enough disk space: {_size_text(meta.size)} needed, {_size_text(free)} free"
            )
        with self._lock:
            # Every job of one torrent uses the folder named after its hash, so
            # two live jobs would share (and later delete) each other's files.
            same = [j for j in self._jobs.values() if j is not job and j.info_hash == meta.info_hash]
            if any(self._holds_torrent(j) for j in same):
                raise DownloadError(
                    "This torrent is already in your downloads. Install, retry or remove that download first."
                )
            # A failed or finished job that nothing uses hands its folder (and
            # any data already in it) over to this job.
            for old in same:
                _ = self._jobs.pop(old.id, None)
            job.info_hash = meta.info_hash
        folder = root / meta.info_hash
        (folder / "data").mkdir(parents=True, exist_ok=True)
        _ = (folder / "meta.torrent").write_bytes(data)
        with self._lock:
            job.folder = folder
            job.info_hash = meta.info_hash
            job.total = meta.size
            job.stage = "downloading"
            job.phase = "downloading"
            self._save_state(job)
            if not self._spawn():
                self._fail(job, MSG_UNAVAILABLE)
                return
            if self._ready and not job.added:
                self._add_to_helper(job)
            # Otherwise the helper's `ready` event adds every active job.

    def _holds_torrent(self, job: _Job) -> bool:
        """Whether `job` still uses its torrent: active, sharing, or open in an install."""
        if job.stage in ACTIVE_STAGES or job.added:
            return True
        if job.stage == "done" and job.seed_state in ("seeding", "awaiting_answer"):
            return True
        if job.folder is None:
            return False
        folder = job.folder.resolve()
        return any(p == folder or folder in p.parents for p in self._open)

    def status(self) -> list[dict[str, object]]:
        now = self._clock()
        with self._lock:
            return [job.snapshot(now) for job in self._jobs.values()]

    def _set_paused(self, job: _Job, paused: bool) -> None:
        job.paused = paused
        job.stage = "paused" if paused else "downloading"
        job.phase = job.stage
        job.speed = job.upload_speed = 0
        if job.added:
            # A job the helper does not hold yet gets `paused` with its `add`.
            _ = self._send({"cmd": "pause" if paused else "resume", "id": job.id})

    def pause(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.stage != "downloading":
                return False
            job.auto_paused = False
            self._set_paused(job, True)
            self._save_state(job)
            return True

    def resume(self, job_id: str) -> bool:
        """Resume a paused job, or retry a failed one that still has its folder."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.folder is None:
                return False
            if job.stage == "paused":
                if self._free_space(torrents_dir()) < LOW_DISK_BYTES:
                    job.error = MSG_LOW_DISK
                    return False
                job.auto_paused = False
                job.error = ""
                self._set_paused(job, False)
                self._save_state(job)
                return True
            if job.stage == "failed" and job.info_hash:
                self._gave_up = False
                self._restarts.clear()
                job.error = ""
                job.paused = False
                job.stage = job.phase = "downloading"
                if not self._spawn():
                    self._fail(job, MSG_UNAVAILABLE)
                    return False
                if self._ready and not job.added:
                    self._add_to_helper(job)
                self._save_state(job)
                return True
            return False

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            # A failed job keeps its partial data until cancelled.
            if job is None or job.stage not in (*ACTIVE_STAGES, "failed"):
                return False
            job.cancel.set()
            job.stage = job.phase = "cancelled"
            job.error = ""
            job.speed = job.upload_speed = job.peers = 0
            removing = job.added and self._send({"cmd": "remove", "id": job.id})
        if removing:
            _ = job.removed.wait(REMOVE_WAIT_SECONDS)
        with self._lock:
            self._delete_folder(job)
        return True

    def forget_game(self, game_id: int) -> None:
        """Stop and delete every torrent of a removed game, seeding ones included."""
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.game_id == game_id]
        for job in jobs:
            with self._lock:
                active = job.stage in ACTIVE_STAGES
            if active:
                _ = self.cancel(job.id)
            else:
                with self._lock:
                    if job.added:
                        _ = self._send({"cmd": "remove", "id": job.id})
                    self._delete_folder(job)
                    job.stage = job.phase = "cancelled"
        with self._lock:
            for job_id in [j.id for j in jobs]:
                _ = self._jobs.pop(job_id, None)

    def mark_open(self, path: str) -> None:
        with self._lock:
            self._open.add(Path(path).resolve())

    def release(self, path: str, installed: bool = False) -> None:
        """Let go of a folder an install dialog held; `installed` marks a finished install."""
        with self._lock:
            resolved = Path(path).resolve()
            self._open.discard(resolved)
            if not installed:
                return
            for job in self._jobs.values():
                if job.folder is not None and job.folder.resolve() in (resolved, *resolved.parents):
                    job.installed = True
                    self._save_state(job)
                    self._delete_if_unused(job)

    def is_seeding_path(self, path: str) -> bool:
        """Whether `path` belongs to a torrent that is still sharing its files."""
        with self._lock:
            resolved = Path(path).resolve()
            return any(
                job.folder is not None
                and job.seed_state in ("seeding", "awaiting_answer")
                and job.folder.resolve() in (resolved, *resolved.parents)
                for job in self._jobs.values()
            )

    def answer_seed(self, job_id: str, seed: bool, remember: bool = False) -> bool:
        """Settle the seeding question; `remember` turns the answer into the setting."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.seed_state != "awaiting_answer":
                return False
            if remember:
                from core import database

                database.update_setting(
                    torrent_settings.SEED_MODE_KEY, seeding.MODE_ALWAYS if seed else seeding.MODE_NEVER
                )
            if not seed:
                self._end_seeding(job)
                return True
            job.seed_state, job.phase = "seeding", "seeding"
            job.last_status_at = None
            job.paused = False
            if job.folder is not None:
                if not self._spawn():
                    self._fail_seeding(job)
                elif job.added:
                    _ = self._send({"cmd": "resume", "id": job.id})
                elif self._ready:
                    self._add_to_helper(job)
            self._save_state(job)
            return True

    def _fail_seeding(self, job: _Job) -> None:
        self._end_seeding(job)

    def stop_seeding(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.seed_state not in ("seeding", "awaiting_answer"):
                return False
            self._end_seeding(job)
            return True

    def apply_limits(self) -> None:
        """Push the current speed limits to a running helper."""
        download, upload = torrent_settings.limits_kib()
        with self._lock:
            _ = self._send({"cmd": "set_limits", "download_kib": download, "upload_kib": upload})

    def shutdown(self) -> None:
        """Ask the helper to save and exit; used when wLib quits."""
        with self._lock:
            self._closing = True
            proc = self._proc
            _ = self._send({"cmd": "shutdown"})
        if proc is not None:
            try:
                _ = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # --- startup ----------------------------------------------------------

    def load(self) -> None:
        """Re-create jobs from `state.json` files and resume unfinished ones."""
        root = torrents_dir()
        resumed = False
        for folder in sorted(root.iterdir()):
            state = _read_state(folder)
            if state is None:
                continue
            job = _Job(_int(state.get("game_id")), str(state.get("title") or ""), str(state.get("version") or ""), _int(state.get("rpdl_id")))
            job.id = str(state.get("job_id") or job.id)
            job.folder = folder
            job.info_hash = folder.name
            job.total = _int(state.get("size"))
            job.bytes = _int(state.get("downloaded"))
            job.uploaded = _int(state.get("uploaded"))
            job.seeding_seconds = _int(state.get("seeding_seconds"))
            job.created = _float(state.get("created")) or time.time()
            job.error = str(state.get("error") or "")
            job.seed_state = str(state.get("seed_state") or "")
            job.paused = bool(state.get("paused"))
            job.installed = bool(state.get("installed"))
            phase = str(state.get("phase") or "")
            job.phase = phase
            if phase in ("downloading", "paused"):
                job.stage = "paused" if job.paused else "downloading"
                resumed = True
            elif phase in ("finished", "ended"):
                job.stage = "done"
                job.seed_state = "ended" if phase == "ended" else ""
            elif phase in ("awaiting_seed_answer", "seeding"):
                job.stage = "done"
                job.seed_state = "awaiting_answer" if phase == "awaiting_seed_answer" else "seeding"
                # An unanswered question must not seed in the meantime.
                job.paused = phase == "awaiting_seed_answer"
                resumed = True
            elif phase == "failed":
                job.stage = "failed"
            else:
                job.stage = phase if phase in ("done",) else "failed"
                if job.stage == "failed" and not job.error:
                    job.error = "Download was interrupted"
            with self._lock:
                self._jobs[job.id] = job
        with self._lock:
            for job in list(self._jobs.values()):
                self._delete_if_unused(job)
        if resumed:
            _ = self._spawn()

    def cleanup(self, *, now: float | None = None) -> None:
        """Delete orphaned torrent folders and paused or failed ones untouched for 30 days."""
        now = now if now is not None else time.time()
        root = torrents_dir()
        with self._lock:
            known = {j.folder.resolve() for j in self._jobs.values() if j.folder is not None}
            protected = set(self._open)
        for folder in root.iterdir():
            try:
                if folder.is_symlink() or not folder.is_dir():
                    continue
                resolved = folder.resolve()
                if resolved in protected or any(p == resolved or resolved in p.parents for p in protected):
                    continue
                state = _read_state(folder)
                if resolved not in known or state is None:
                    shutil.rmtree(folder, ignore_errors=True)
                    continue
                touched = _float(state.get("updated"))
                if state.get("phase") in ("paused", "failed", "ended") and touched < now - PAUSED_KEEP_SECONDS:
                    shutil.rmtree(folder, ignore_errors=True)
                    with self._lock:
                        for job_id in [j.id for j in self._jobs.values() if j.folder == folder]:
                            _ = self._jobs.pop(job_id, None)
            except OSError:
                continue


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _read_state(folder: Path) -> dict[str, object] | None:
    try:
        value = cast(object, json.loads((folder / "state.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    return cast(dict[str, object], value) if isinstance(value, dict) else None


supervisor = TorrentSupervisor()
