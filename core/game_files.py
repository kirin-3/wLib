# SPDX-License-Identifier: GPL-3.0-or-later
"""Uninstall and move installed games.

Root detection, the uninstall and move plans, the trash folder with its
device-local records, the move engine and the background move queue. The
bridge methods in `core/api_mixins/library.py` stay thin; the parts that need
the Api (running games, busy games, saves, the library entry) come in as
callables.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import cast

from core.archive_install import (
    NETWORK_IO_WORKERS,
    TRASH_PREFIX,
    ArchiveInstallError,
    InstallCancelled,
    absolute_game_path,
    can_remove_old_folder,
    copy_tree_parallel,
    defer_remove_tree,
    ensure_free_space,
    is_network_filesystem,
    is_within_real,
    old_install_root,
    other_game_paths,
    real_path,
    remove_tree_collect,
    sweep_trash,
    trash_root_for,
    tree_bytes,
)

_log = logging.getLogger(__name__)

JOBS_FILE = "game-file-jobs.json"
MOVE_PREFIX = ".wlib-move-"

UNINSTALL_RUNNING = "Close the game before uninstalling it."
MOVE_RUNNING = "Close the game before moving it."
MOVING_BUSY = "This game is being moved. Wait for the move to finish."
UNINSTALLING_BUSY = "This game is being uninstalled."
NOT_INSTALLED = "This game is not installed."
NOT_FOUND = "Game was not found"
TARGET_EXISTS = "The target folder already exists."
DEST_IS_ROOT = "The destination is the game's own folder."
DEST_INSIDE_ROOT = "The destination is inside the game's folder."
SAME_FOLDER = "The game is already in this folder."
DEST_MISSING = "The destination folder does not exist."


# ── Device-local job records ──

_jobs_lock = threading.Lock()


def _jobs_path() -> str:
    from core.host_platform import get_data_dir

    return os.path.join(get_data_dir(), JOBS_FILE)


def _read_jobs() -> dict[str, list[str]]:
    try:
        with open(_jobs_path(), encoding="utf-8") as handle:
            raw = cast(object, json.load(handle))
    except (OSError, ValueError):
        return {"trash": [], "move": []}
    result: dict[str, list[str]] = {"trash": [], "move": []}
    if isinstance(raw, dict):
        data = cast(dict[str, object], raw)
        for kind in result:
            items = data.get(kind)
            if isinstance(items, list):
                result[kind] = [
                    str(item) for item in cast(list[object], items) if isinstance(item, str) and item
                ]
    return result


def _write_jobs(data: Mapping[str, list[str]]) -> None:
    path = _jobs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(dict(data), handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def add_job(kind: str, path: str) -> None:
    """Record a trash or move folder that wLib made outside the library folders."""
    with _jobs_lock:
        data = _read_jobs()
        items = data.setdefault(kind, [])
        if path not in items:
            items.append(path)
            _write_jobs(data)


def remove_job(kind: str, path: str) -> None:
    with _jobs_lock:
        data = _read_jobs()
        items = data.get(kind, [])
        if path in items:
            items.remove(path)
            _write_jobs(data)


def list_jobs(kind: str) -> list[str]:
    with _jobs_lock:
        return list(_read_jobs().get(kind, []))


# ── Context ──


@dataclass(frozen=True)
class FilesContext:
    library_folders: list[str]
    downloads_folder: str
    pattern: str


def load_context() -> FilesContext:
    from core.database import (
        DEFAULT_LIBRARY_FOLDER_PATTERN,
        get_library_folders,
        get_setting,
    )

    folders, _default = get_library_folders()
    return FilesContext(
        library_folders=[folder for folder in folders if folder],
        downloads_folder=str(get_setting("downloads_folder") or ""),
        pattern=str(get_setting("library_folder_pattern") or DEFAULT_LIBRARY_FOLDER_PATTERN),
    )


@dataclass(frozen=True)
class GameHooks:
    """What the plans need from the Api."""

    running: Callable[[int], bool]
    busy: Callable[[int], str | None]
    saves_folder: Callable[[int], str] = lambda _game_id: ""


def _game_id(game: Mapping[str, object]) -> int | None:
    try:
        return int(str(game.get("id")))
    except (TypeError, ValueError):
        return None


def detect_root(game: Mapping[str, object], ctx: FilesContext) -> tuple[str, str]:
    """(root, root_source) for an installed game; ("", "") when it has no executable.

    root_source is "pattern" when the destination pattern of a library folder
    built the root, and "exe_dir" when wLib fell back to the executable's folder.
    """
    exe = absolute_game_path(game.get("exe_path"))
    if not exe:
        return "", ""
    exe_dir = os.path.dirname(exe)
    root = old_install_root(
        exe,
        game,
        ctx.library_folders,
        ctx.pattern,
        [],
        str(game.get("engine") or ""),
        _game_id(game),
    )
    if real_path(root) != real_path(exe_dir):
        return root, "pattern"
    return exe_dir, "exe_dir"


def coerce_overrides(value: object) -> dict[int, str]:
    """Root overrides from the bridge: {game id: folder}."""
    result: dict[int, str] = {}
    if not isinstance(value, Mapping):
        return result
    for key, path in cast(Mapping[object, object], value).items():
        try:
            game_id = int(str(key))
        except (TypeError, ValueError):
            continue
        if isinstance(path, str) and path.strip():
            result[game_id] = os.path.abspath(os.path.expanduser(path.strip()))
    return result


def _plan_entry(
    game_id: int,
    ctx: FilesContext,
    hooks: GameHooks,
    override: str,
    running_reason: str,
) -> dict[str, object]:
    from core.database import get_game

    entry: dict[str, object] = {
        "game_id": game_id,
        "title": "",
        "root": "",
        "root_source": "",
        "removable": False,
        "reason": "",
        "saves_folder": "",
        "saves_inside_root": False,
        "blocked_reason": "",
    }
    game = get_game(game_id)
    if game is None:
        entry["blocked_reason"] = NOT_FOUND
        return entry
    entry["title"] = str(game.get("title") or "")
    exe = absolute_game_path(game.get("exe_path"))
    if not exe:
        entry["blocked_reason"] = NOT_INSTALLED
        return entry
    if override:
        root, source = override, "override"
    else:
        root, source = detect_root(game, ctx)
    entry["root"] = root
    entry["root_source"] = source
    guard = can_remove_old_folder(
        root,
        "",
        ctx.library_folders,
        ctx.downloads_folder,
        other_game_paths(game_id),
        exe,
    )
    entry["removable"] = bool(guard["removable"])
    entry["reason"] = str(guard["reason"] or "")
    saves = hooks.saves_folder(game_id)
    if saves and os.path.isdir(saves):
        entry["saves_folder"] = saves
        entry["saves_inside_root"] = is_within_real(real_path(saves), real_path(root))
    if hooks.running(game_id):
        entry["blocked_reason"] = running_reason
    else:
        busy = hooks.busy(game_id)
        if busy:
            entry["blocked_reason"] = busy
    return entry


def entry_problem(entry: Mapping[str, object]) -> str:
    """Why a plan entry cannot go ahead, or ""."""
    blocked = str(entry.get("blocked_reason") or "")
    if blocked:
        return blocked
    if not entry.get("removable"):
        return str(entry.get("reason") or "This folder cannot be removed.")
    return str(entry.get("move_reason") or "")


def plan_uninstall(
    ids: Sequence[int],
    root_overrides: Mapping[int, str],
    hooks: GameHooks,
    ctx: FilesContext | None = None,
) -> list[dict[str, object]]:
    context = ctx or load_context()
    return [
        _plan_entry(game_id, context, hooks, root_overrides.get(game_id, ""), UNINSTALL_RUNNING)
        for game_id in ids
    ]


def _same_device(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _free_bytes(path: str) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _gb(size: int) -> str:
    return f"{size / (1024**3):.1f} GB"


def move_problem(root: str, dest_parent: str) -> str:
    """Why `root` cannot move into `dest_parent`, or ""."""
    if not dest_parent or not os.path.isdir(dest_parent):
        return DEST_MISSING
    root_real = real_path(root)
    dest_real = real_path(dest_parent)
    if dest_real == root_real:
        return DEST_IS_ROOT
    if is_within_real(dest_real, root_real):
        return DEST_INSIDE_ROOT
    if real_path(os.path.dirname(root_real)) == dest_real:
        return SAME_FOLDER
    if os.path.lexists(os.path.join(dest_parent, os.path.basename(root_real))):
        return TARGET_EXISTS
    return ""


def plan_move(
    ids: Sequence[int],
    dest_parent: str,
    root_overrides: Mapping[int, str],
    hooks: GameHooks,
    ctx: FilesContext | None = None,
    *,
    with_sizes: bool = True,
) -> list[dict[str, object]]:
    """The uninstall checks plus the move checks, with each game's size.

    The sizes are counted on worker threads, one tree each.
    """
    context = ctx or load_context()
    dest = os.path.abspath(os.path.expanduser(dest_parent)) if dest_parent else ""
    entries = [
        _plan_entry(game_id, context, hooks, root_overrides.get(game_id, ""), MOVE_RUNNING)
        for game_id in ids
    ]
    for entry in entries:
        root = str(entry["root"])
        entry["target"] = os.path.join(dest, os.path.basename(real_path(root))) if root and dest else ""
        entry["same_device"] = bool(root) and _same_device(root, dest)
        entry["move_reason"] = move_problem(root, dest) if root else ""
        entry["size_bytes"] = None
    if with_sizes:
        sized = [entry for entry in entries if entry["root"] and os.path.isdir(str(entry["root"]))]
        if sized:
            with ThreadPoolExecutor(max_workers=min(4, len(sized))) as pool:
                sizes = list(pool.map(tree_bytes, [str(e["root"]) for e in sized]))
            for entry, size in zip(sized, sizes, strict=True):
                entry["size_bytes"] = size
        free = _free_bytes(dest) if dest else None
        for entry in entries:
            size = entry["size_bytes"]
            if (
                not entry["move_reason"]
                and not entry["same_device"]
                and isinstance(size, int)
                and free is not None
                and size > free
            ):
                entry["move_reason"] = (
                    f"Not enough free space: need {_gb(size)}, have {_gb(free)}."
                )
    return entries


# ── Trash ──

_record_lock = threading.Lock()
_record_in_flight: set[str] = set()


def _start_recorded_delete(kind: str, path: str) -> None:
    """Delete a recorded folder on a daemon thread, then drop its record."""
    with _record_lock:
        if path in _record_in_flight:
            return
        _record_in_flight.add(path)

    def _delete() -> None:
        try:
            errors = remove_tree_collect(path)
            if errors:
                _log.warning("Could not fully remove %s: %s", path, errors[0])
            if not os.path.lexists(path):
                remove_job(kind, path)
        finally:
            with _record_lock:
                _record_in_flight.discard(path)

    threading.Thread(target=_delete, name="wlib-game-trash", daemon=True).start()


def _delete_in_place(path: str) -> None:
    def _delete() -> None:
        errors = remove_tree_collect(path)
        if errors:
            _log.warning("Could not fully remove %s: %s", path, ", ".join(errors[:5]))

    threading.Thread(target=_delete, name="wlib-game-delete", daemon=True).start()


def trash_root(root: str, library_folders: Sequence[str]) -> str:
    """Move `root` out of the way and delete it in the background.

    Returns how: "library" (trash in the deepest library folder), "recorded"
    (`<parent>/.wlib-trash-<ns>`, recorded for the next start) or "in_place"
    (the rename failed, so the folder is deleted where it is).
    """
    library_trash = trash_root_for(root, library_folders)
    if library_trash and defer_remove_tree(root, library_trash):
        return "library"
    parent = os.path.dirname(os.path.abspath(root).rstrip(os.sep))
    trash = os.path.join(parent, f"{TRASH_PREFIX}{time.time_ns()}")
    add_job("trash", trash)
    try:
        os.rename(root, trash)
    except OSError:
        remove_job("trash", trash)
        _delete_in_place(root)
        return "in_place"
    _start_recorded_delete("trash", trash)
    return "recorded"


_active_move_temps: set[str] = set()


def sweep_leftovers(library_folders: Sequence[str]) -> int:
    """Restart deletes of trash and move folders left by a crash or a quit.

    Records whose folders are gone are dropped. A record whose parent folder
    is missing (an offline drive) stays for a later start.
    """
    started = 0
    for folder in library_folders:
        if folder:
            started += sweep_trash(folder)
    for kind in ("trash", "move"):
        for path in list_jobs(kind):
            with _record_lock:
                if path in _active_move_temps:
                    continue
            if os.path.isdir(path) and not os.path.islink(path):
                _start_recorded_delete(kind, path)
                started += 1
            elif os.path.isdir(os.path.dirname(path)):
                remove_job(kind, path)
    return started




# ── Uninstall ──


@dataclass(frozen=True)
class UninstallHooks(GameHooks):
    # Atomically check running/busy and mark the game as uninstalling.
    reserve: Callable[[int], str | None] = lambda _game_id: None
    release: Callable[[int], None] = lambda _game_id: None
    # Back up the saves; returns an error, or None (nothing to back up is None).
    backup_saves: Callable[[int], str | None] = lambda _game_id: None
    # The "Remove from library" body, without its running/busy checks.
    remove_entry: Callable[[int], None] = lambda _game_id: None


def run_uninstall(
    ids: Sequence[int],
    root_overrides: Mapping[int, str],
    *,
    backup: bool,
    remove_from_library: bool,
    hooks: UninstallHooks,
    ctx: FilesContext | None = None,
) -> list[dict[str, object]]:
    """Uninstall each game; one result per game. Each game fails on its own."""
    from core.database import clear_game_install

    context = ctx or load_context()
    results: list[dict[str, object]] = []
    for game_id in ids:
        result: dict[str, object] = {"game_id": game_id, "success": False, "error": ""}
        results.append(result)
        entry = _plan_entry(
            game_id, context, hooks, root_overrides.get(game_id, ""), UNINSTALL_RUNNING
        )
        problem = entry_problem(entry)
        if problem:
            result["error"] = problem
            continue
        busy = hooks.reserve(game_id)
        if busy:
            result["error"] = busy
            continue
        try:
            if backup and entry["saves_folder"]:
                error = hooks.backup_saves(game_id)
                if error:
                    result["error"] = f"Save backup failed, so nothing was deleted: {error}"
                    continue
            root = str(entry["root"])
            result["trash"] = trash_root(root, context.library_folders)
            if remove_from_library:
                hooks.remove_entry(game_id)
            else:
                clear_game_install(game_id)
            result["success"] = True
        except Exception as exc:
            result["error"] = str(exc) or type(exc).__name__
        finally:
            hooks.release(game_id)
    return results


# ── Move ──


@dataclass
class MoveJob:
    game_id: int
    dest_parent: str
    root_override: str = ""
    title: str = ""


@dataclass
class MoveOutcome:
    status: str  # moved | skipped | cancelled | failed
    reason: str = ""
    warning: str = ""


@dataclass(frozen=True)
class MoveHooks(GameHooks):
    # Called after a game's paths change (Steam sync, library refresh).
    moved: Callable[[int], None] = lambda _game_id: None


def _copy_folder_meta(src: str, dst: str) -> None:
    try:
        st = os.lstat(src)
        os.chmod(dst, stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass


def run_move_job(
    job: MoveJob,
    cancel: threading.Event,
    on_bytes: Callable[[int], None],
    set_total: Callable[[int], None],
    hooks: MoveHooks,
    ctx: FilesContext | None = None,
) -> MoveOutcome:
    """Move one game. The game stays at its old place unless this returns "moved"."""
    from core.database import repoint_game_paths

    context = ctx or load_context()
    entries = plan_move(
        [job.game_id],
        job.dest_parent,
        {job.game_id: job.root_override} if job.root_override else {},
        hooks,
        context,
        with_sizes=False,
    )
    entry = entries[0]
    job.title = str(entry.get("title") or job.title)
    problem = entry_problem(entry)
    if problem:
        return MoveOutcome("skipped", problem)
    if cancel.is_set():
        return MoveOutcome("cancelled")
    root = real_path(str(entry["root"]))
    dest = os.path.abspath(os.path.expanduser(job.dest_parent))
    final = os.path.join(dest, os.path.basename(root))

    if _same_device(root, dest):
        set_total(0)
        if os.path.lexists(final):
            return MoveOutcome("skipped", TARGET_EXISTS)
        try:
            os.rename(root, final)
        except OSError as exc:
            return MoveOutcome("failed", f"Could not move the folder: {exc}")
        try:
            _ = repoint_game_paths(job.game_id, root, final)
        except Exception as exc:
            try:
                os.rename(final, root)
            except OSError as back_exc:
                _log.error("Could not move %s back to %s: %s", final, root, back_exc)
            return MoveOutcome("failed", f"Could not update the game's paths: {exc}")
        hooks.moved(job.game_id)
        return MoveOutcome("moved")

    size = tree_bytes(root)
    set_total(size)
    try:
        ensure_free_space(dest, size)
    except ArchiveInstallError as exc:
        return MoveOutcome("skipped", str(exc))
    temp = os.path.join(dest, f"{MOVE_PREFIX}{time.time_ns()}")
    with _record_lock:
        _active_move_temps.add(temp)
    add_job("move", temp)
    renamed = False
    try:
        try:
            os.mkdir(temp)
            network = is_network_filesystem(root) or is_network_filesystem(dest)
            workers = NETWORK_IO_WORKERS if network else 1
            _ = copy_tree_parallel(root, temp, workers, cancel, on_bytes)
            if cancel.is_set():
                raise InstallCancelled()
            _copy_folder_meta(root, temp)
            if os.path.lexists(final):
                raise ArchiveInstallError(TARGET_EXISTS)
            os.rename(temp, final)
            renamed = True
        except InstallCancelled:
            _ = remove_tree_collect(temp)
            return MoveOutcome("cancelled")
        except (OSError, ArchiveInstallError) as exc:
            _ = remove_tree_collect(temp)
            return MoveOutcome("failed", f"Could not copy the game: {exc}")
        try:
            _ = repoint_game_paths(job.game_id, root, final)
        except Exception as exc:
            _ = remove_tree_collect(final)
            return MoveOutcome("failed", f"Could not update the game's paths: {exc}")
    finally:
        if renamed or not os.path.lexists(temp):
            remove_job("move", temp)
        with _record_lock:
            _active_move_temps.discard(temp)
    hooks.moved(job.game_id)
    how = trash_root(root, context.library_folders)
    warning = ""
    if how == "in_place":
        warning = "The old folder could not be moved to the trash, so it is deleted in place."
    return MoveOutcome("moved", warning=warning)


_SPEED_WINDOW_SECONDS = 3.0

MoveRunner = Callable[
    [MoveJob, threading.Event, Callable[[int], None], Callable[[int], None]], MoveOutcome
]


@dataclass
class _Totals:
    moved: int = 0
    skipped: int = 0
    cancelled: int = 0
    failed: int = 0
    first_reason: str = ""
    done: int = 0

    def add(self, outcome: MoveOutcome) -> None:
        self.done += 1
        if outcome.status == "moved":
            self.moved += 1
        elif outcome.status == "skipped":
            self.skipped += 1
        elif outcome.status == "cancelled":
            self.cancelled += 1
        else:
            self.failed += 1
        if outcome.status in ("skipped", "failed") and not self.first_reason:
            self.first_reason = outcome.reason


@dataclass
class _Progress:
    bytes_done: int = 0
    bytes_total: int = 0
    samples: deque[tuple[float, int]] = field(default_factory=deque)


class MoveQueue:
    """One worker thread moves the queued games one at a time, in FIFO order."""

    def __init__(
        self,
        runner: MoveRunner,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runner: MoveRunner = runner
        self._clock: Callable[[], float] = clock
        self._lock: threading.Lock = threading.Lock()
        self._queue: deque[MoveJob] = deque()
        self._current: MoveJob | None = None
        self._cancel: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        self._totals: _Totals = _Totals()
        self._summary: dict[str, object] | None = None
        self._progress: _Progress = _Progress()
        self._closed: bool = False

    def enqueue(self, jobs: Sequence[MoveJob]) -> list[int]:
        """Add jobs at the end; a game already queued or moving is left out."""
        added: list[int] = []
        with self._lock:
            if self._closed:
                return added
            busy = {job.game_id for job in self._queue}
            if self._current is not None:
                busy.add(self._current.game_id)
            if self._current is None and not self._queue:
                self._totals = _Totals()
                self._summary = None
            for job in jobs:
                if job.game_id in busy:
                    continue
                busy.add(job.game_id)
                self._queue.append(job)
                added.append(job.game_id)
            if added and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(
                    target=self._work, name="wlib-game-move", daemon=True
                )
                self._thread.start()
        return added

    def state(self, game_id: int) -> str:
        """"moving", "waiting" or ""."""
        with self._lock:
            if self._current is not None and self._current.game_id == game_id:
                return "moving"
            if any(job.game_id == game_id for job in self._queue):
                return "waiting"
        return ""

    def cancel(self, cancel_all: bool = False) -> None:
        with self._lock:
            if cancel_all:
                for _job in self._queue:
                    self._totals.add(MoveOutcome("cancelled"))
                self._queue.clear()
            if self._current is not None:
                self._cancel.set()
            elif cancel_all:
                self._finish_locked()

    def shutdown(self) -> None:
        """Stop at the next file and drop the queue (the app is closing)."""
        with self._lock:
            self._closed = True
            self._queue.clear()
            self._cancel.set()

    def _on_bytes(self, count: int) -> None:
        now = self._clock()
        with self._lock:
            progress = self._progress
            progress.bytes_done += count
            progress.samples.append((now, progress.bytes_done))
            while progress.samples and now - progress.samples[0][0] > _SPEED_WINDOW_SECONDS:
                _ = progress.samples.popleft()

    def _set_total(self, total: int) -> None:
        with self._lock:
            self._progress.bytes_total = total

    def _finish_locked(self) -> None:
        totals = self._totals
        if totals.done:
            self._summary = {
                "moved": totals.moved,
                "skipped": totals.skipped,
                "cancelled": totals.cancelled,
                "failed": totals.failed,
                "first_reason": totals.first_reason,
            }
        self._totals = _Totals()

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._queue or self._closed:
                    self._current = None
                    if not self._closed:
                        self._finish_locked()
                    return
                job = self._queue.popleft()
                self._current = job
                self._cancel = threading.Event()
                self._progress = _Progress(samples=deque([(self._clock(), 0)]))
                cancel = self._cancel
            try:
                outcome = self._runner(job, cancel, self._on_bytes, self._set_total)
            except Exception as exc:
                _log.exception("Move of game %s failed", job.game_id)
                outcome = MoveOutcome("failed", str(exc) or type(exc).__name__)
            with self._lock:
                self._totals.add(outcome)
                self._current = None

    def _speed_locked(self) -> int:
        samples = self._progress.samples
        if len(samples) < 2:
            return 0
        (t0, b0), (t1, b1) = samples[0], samples[-1]
        if t1 <= t0:
            return 0
        return int((b1 - b0) / (t1 - t0))

    def status(self) -> dict[str, object]:
        """JSON snapshot. `summary` shows once after the queue ends."""
        with self._lock:
            current = self._current
            waiting = [job.game_id for job in self._queue]
            running = current is not None or bool(waiting)
            result: dict[str, object] = {
                "running": running,
                "current": None,
                "waiting": waiting,
                "position": self._totals.done + (1 if current is not None else 0),
                "total": self._totals.done + (1 if current is not None else 0) + len(waiting),
            }
            if current is not None:
                result["current"] = {
                    "game_id": current.game_id,
                    "title": current.title,
                    "bytes_done": self._progress.bytes_done,
                    "bytes_total": self._progress.bytes_total,
                    "speed_bps": self._speed_locked(),
                }
            if self._summary is not None and not running:
                result["summary"] = self._summary
                self._summary = None
            return result
