# SPDX-License-Identifier: GPL-3.0-or-later
"""Disk space measurement, game-folder detection and the Storage scan service.

Sizes are size on disk (allocated space), not apparent byte length:
- POSIX: iterative `os.scandir` with `st_blocks * 512`, scan-wide hard-link
  deduplication for `st_nlink > 1`, no symlink following, no mount crossing.
- Windows: iterative folder listing via `GetFileInformationByHandleEx`
  (`FileIdBothDirectoryInfo`), reparse points never entered, WOF fallback to
  `FileStandardInfo`, and per-root `FileId` deduplication.

Device-local files stored in the data folder (never exported or synced):
- `storage-roots.json`: user-picked game folders (`{game_id: abs_folder}`).
- `storage-report.json`: last complete storage report (`{"version": 1, ...}`).
"""

from __future__ import annotations

import ctypes
import json
import logging
import ntpath
import os
import platform
import stat
import struct
import threading
import time
from collections.abc import Callable, Collection, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, TypedDict, cast, final

from core import power
from core.archive_install import (
    absolute_game_path,
    build_destination,
    is_within_real,
    real_path,
)
from core.host_platform import (
    get_data_dir,
    get_default_wine_prefix,
    get_proton_dir,
    is_linux,
    is_windows,
)

_log = logging.getLogger(__name__)

STORAGE_ROOTS_FILE = "storage-roots.json"
STORAGE_REPORT_FILE = "storage-report.json"
STORAGE_REPORT_VERSION = 1
STORAGE_RECOUNT_SETTING = "storage_recount_after_changes"

# Sub-folder names climbed when an executable sits outside every library folder.
MARKER_SUBDIRS = frozenset(
    {"bin", "bin64", "binaries", "win64", "win32", "x64", "x86", "x86_64", "lib"}
)

CATEGORY_ORDER: tuple[tuple[str, str], ...] = (
    ("games", "Games"),
    ("download_archives", "Download archives"),
    ("torrent_data", "Torrent data"),
    ("proton_builds", "Proton builds"),
    ("wine_prefixes", "Wine prefixes"),
    ("shared_wine_prefix", "Shared Wine prefix"),
    ("save_backups", "Save backups"),
    ("web_player", "Built-in player data"),
    ("sandboxes", "Sandboxes"),
    ("removed_game_data", "Data of removed games"),
    ("cover_cache", "Cover cache"),
    ("other_caches", "Other caches"),
    ("catalog", "Catalog"),
    ("logs_and_backups", "Logs and database backups"),
)

CATEGORY_LABELS: dict[str, str] = dict(CATEGORY_ORDER)

GamePart = Literal["game", "saves", "player", "sandbox", "prefix", "torrent"]
RootSource = Literal["picked", "pattern", "climb", "marker", "exe_dir", ""]
ChangeKind = Literal["install", "update", "move", "uninstall", "remove"]


class ScanCancelled(Exception):
    """Raised by `measure()` when its cancel event is set."""


@dataclass(frozen=True)
class Measure:
    """Allocated disk usage for a single measured path."""

    bytes: int
    files: int
    skipped: int


@dataclass
class MeasureBudget:
    """Shared state for one measurement or full scan."""

    cancel: threading.Event | None = None
    seen_inodes: set[tuple[int, int]] = field(default_factory=set)
    on_progress: Callable[[int, int], None] | None = None

    def check_cancelled(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise ScanCancelled()


# ── Low CPU and I/O priority ─────────────────────────────────────────────

_IOPRIO_WHO_PROCESS = 1
_IOPRIO_CLASS_IDLE = 3
_IOPRIO_CLASS_SHIFT = 13
_IOPRIO_IDLE_VALUE = _IOPRIO_CLASS_IDLE << _IOPRIO_CLASS_SHIFT

_LINUX_SYS_IOPRIO_SET: dict[str, int] = {
    "x86_64": 251,
    "amd64": 251,
    "aarch64": 30,
    "arm64": 30,
    "riscv64": 30,
    "i386": 289,
    "i686": 289,
    "armv7l": 314,
    "armv6l": 314,
}

_WIN_THREAD_MODE_BACKGROUND_BEGIN = 0x00010000
_WIN_THREAD_MODE_BACKGROUND_END = 0x00020000


def _win_last_error() -> int:
    fn = cast(Callable[[], int] | None, getattr(ctypes, "get_last_error", None))
    return fn() if fn is not None else 0


def _lower_linux_priority() -> bool:
    """Lower the current thread's I/O class to idle and CPU niceness to 19."""
    tid = threading.get_native_id()
    ok = True

    nr = _LINUX_SYS_IOPRIO_SET.get(platform.machine().lower())
    if nr is not None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            syscall_fn = libc.syscall
            syscall_fn.restype = ctypes.c_long
            res = cast(
                int,
                syscall_fn(
                    ctypes.c_long(nr),
                    ctypes.c_int(_IOPRIO_WHO_PROCESS),
                    ctypes.c_int(tid),
                    ctypes.c_int(_IOPRIO_IDLE_VALUE),
                ),
            )
            if res != 0:
                err = ctypes.get_errno()
                _log.debug("ioprio_set failed for tid %s (errno=%s)", tid, err)
                ok = False
        except (OSError, AttributeError, ValueError) as exc:
            _log.debug("ioprio_set unavailable: %s", exc)
            ok = False
    else:
        _log.debug("ioprio_set syscall number unknown for %s", platform.machine())
        ok = False

    try:
        setpriority = cast(
            Callable[[int, int, int], None] | None, getattr(os, "setpriority", None)
        )
        prio_process = cast(int | None, getattr(os, "PRIO_PROCESS", None))
        if setpriority is not None and prio_process is not None:
            setpriority(prio_process, tid, 19)
        else:
            ok = False
    except OSError as exc:
        _log.debug("setpriority(19) failed for tid %s: %s", tid, exc)
        ok = False

    return ok


def _set_windows_background_mode(enable: bool) -> bool:
    try:
        load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
        kernel32 = load_dll("kernel32", use_last_error=True)
        get_thread = kernel32.GetCurrentThread
        get_thread.restype = ctypes.c_void_p
        set_prio = kernel32.SetThreadPriority
        set_prio.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_prio.restype = ctypes.c_int
        mode = (
            _WIN_THREAD_MODE_BACKGROUND_BEGIN
            if enable
            else _WIN_THREAD_MODE_BACKGROUND_END
        )
        ok = bool(cast(int, set_prio(get_thread(), mode)))
        if not ok:
            _log.debug(
                "SetThreadPriority(%s) failed (err=%s)",
                "BEGIN" if enable else "END",
                _win_last_error(),
            )
        return ok
    except (OSError, AttributeError, ValueError) as exc:
        _log.debug("Windows background thread mode unavailable: %s", exc)
        return False


@contextmanager
def low_priority_thread() -> Generator[None]:
    """Lower CPU and disk I/O priority for the current worker thread."""
    win_entered = False
    if is_linux():
        _ = _lower_linux_priority()
    elif is_windows():
        win_entered = _set_windows_background_mode(True)
    try:
        yield
    finally:
        if win_entered:
            _ = _set_windows_background_mode(False)


# ── POSIX measurement back end ───────────────────────────────────────────


def _stat_allocated_bytes(st: os.stat_result) -> int:
    blocks = cast(int | None, getattr(st, "st_blocks", None))
    if blocks is not None:
        return blocks * 512
    return int(st.st_size)


def _measure_posix(path: str, budget: MeasureBudget) -> Measure:
    budget.check_cancelled()
    try:
        root_st = os.lstat(path)
    except FileNotFoundError:
        return Measure(0, 0, 0)
    except OSError:
        return Measure(0, 0, 1)

    if not stat.S_ISDIR(root_st.st_mode):
        if stat.S_ISREG(root_st.st_mode) and root_st.st_nlink > 1:
            key = (root_st.st_dev, root_st.st_ino)
            if key in budget.seen_inodes:
                return Measure(0, 0, 0)
            budget.seen_inodes.add(key)
        size = _stat_allocated_bytes(root_st)
        if budget.on_progress is not None:
            budget.on_progress(1, size)
        return Measure(size, 1, 0)

    root_dev = root_st.st_dev
    # Folders use blocks too, and deleting them frees those blocks (`du` counts them).
    total_bytes = _stat_allocated_bytes(root_st)
    total_files = 0
    skipped = 0
    stack: list[str] = [path]
    seen_inodes = budget.seen_inodes
    on_progress = budget.on_progress

    while stack:
        budget.check_cancelled()
        current = stack.pop()
        dir_bytes = 0
        dir_files = 0
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        skipped += 1
                        continue
                    if st.st_dev != root_dev:
                        # Do not cross into another mounted filesystem.
                        continue
                    mode = st.st_mode
                    if stat.S_ISDIR(mode):
                        dir_bytes += _stat_allocated_bytes(st)
                        stack.append(entry.path)
                        continue
                    if stat.S_ISREG(mode) and st.st_nlink > 1:
                        key = (st.st_dev, st.st_ino)
                        if key in seen_inodes:
                            continue
                        seen_inodes.add(key)
                    dir_bytes += _stat_allocated_bytes(st)
                    dir_files += 1
        except FileNotFoundError:
            continue
        except OSError:
            skipped += 1
            continue

        if dir_files or dir_bytes:
            total_bytes += dir_bytes
            total_files += dir_files
            if on_progress is not None:
                on_progress(dir_files, dir_bytes)

    return Measure(total_bytes, total_files, skipped)


# ── Windows measurement back end ─────────────────────────────────────────

_WIN_FILE_LIST_DIRECTORY = 0x0001
_WIN_FILE_READ_ATTRIBUTES = 0x0080
_WIN_FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_WIN_OPEN_EXISTING = 3
_WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WIN_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WIN_FILE_STANDARD_INFO = 1
_WIN_FILE_ID_BOTH_DIR_INFO = 10
_WIN_ERROR_NO_MORE_FILES = 18
_WIN_ERROR_FILE_NOT_FOUND = 2
_WIN_ERROR_PATH_NOT_FOUND = 3
_WIN_DIR_BUFFER_BYTES = 256 * 1024

_WIN_DIR_HEADER_SIZE = 104
_WIN_DIR_FIELDS_STRUCT = struct.Struct("<qqII")  # at +40: eof, alloc, attrs, name_len
_WIN_DIR_NEXT_STRUCT = struct.Struct("<I")  # at +0: NextEntryOffset
_WIN_DIR_FILE_ID_STRUCT = struct.Struct("<q")  # at +96: FileId
_WIN_STD_INFO_STRUCT = struct.Struct("<qqIBB2x")


def _win_extended_path(path: str) -> str:
    """Prefix long Windows paths with \\\\?\\ so deep game folders stay readable."""
    normalized = os.path.abspath(path)
    if normalized.startswith("\\\\?\\"):
        return normalized
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized[2:]
    if len(normalized) >= 240:
        return "\\\\?\\" + normalized
    return normalized


def _parse_win_dir_buffer(
    buf: bytes | bytearray | memoryview,
) -> Iterator[tuple[str, int, int, int, int]]:
    """Yield (name, end_of_file, allocation_size, attributes, file_id) from a
    `FILE_ID_BOTH_DIR_INFO` buffer. Pure Python so Linux tests can verify offsets.
    """
    view = memoryview(buf)
    size = len(view)
    offset = 0
    while offset + _WIN_DIR_HEADER_SIZE <= size:
        next_tuple = cast(tuple[int], _WIN_DIR_NEXT_STRUCT.unpack_from(view, offset))
        next_offset = next_tuple[0]
        fields_tuple = cast(
            tuple[int, int, int, int],
            _WIN_DIR_FIELDS_STRUCT.unpack_from(view, offset + 40),
        )
        end_of_file, alloc_size, attrs, name_len = fields_tuple
        fid_tuple = cast(
            tuple[int], _WIN_DIR_FILE_ID_STRUCT.unpack_from(view, offset + 96)
        )
        file_id = fid_tuple[0]
        name_start = offset + _WIN_DIR_HEADER_SIZE
        name_end = name_start + name_len
        if name_end > size:
            break
        name = bytes(view[name_start:name_end]).decode(
            "utf-16-le", errors="surrogatepass"
        )
        yield name, end_of_file, alloc_size, attrs, file_id
        if next_offset == 0:
            break
        offset += next_offset


def _measure_windows(path: str, budget: MeasureBudget) -> Measure:
    budget.check_cancelled()
    load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
    kernel32 = load_dll("kernel32", use_last_error=True)

    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p

    get_info_ex = kernel32.GetFileInformationByHandleEx
    get_info_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_info_ex.restype = ctypes.c_int

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    invalid_handle = ctypes.c_void_p(-1).value
    std_buf = ctypes.create_string_buffer(_WIN_STD_INFO_STRUCT.size)

    def _query_standard_info(
        target: str, *, flags: int = _WIN_FILE_FLAG_OPEN_REPARSE_POINT
    ) -> tuple[int, int, bool] | None:
        handle = cast(
            int | None,
            create_file(
                _win_extended_path(target),
                _WIN_FILE_READ_ATTRIBUTES,
                _WIN_FILE_SHARE_ALL,
                None,
                _WIN_OPEN_EXISTING,
                flags,
                None,
            ),
        )
        if not handle or handle == invalid_handle:
            return None
        try:
            ok = cast(
                int,
                get_info_ex(
                    handle,
                    _WIN_FILE_STANDARD_INFO,
                    std_buf,
                    _WIN_STD_INFO_STRUCT.size,
                ),
            )
            if not ok:
                return None
            unpacked = cast(
                tuple[int, int, int, int, int],
                _WIN_STD_INFO_STRUCT.unpack(std_buf.raw),
            )
            alloc, eof, _nlinks, _del_pending, is_dir = unpacked
            return alloc, eof, bool(is_dir)
        finally:
            _ = cast(int, close_handle(handle))

    root_info = _query_standard_info(
        path,
        flags=_WIN_FILE_FLAG_BACKUP_SEMANTICS | _WIN_FILE_FLAG_OPEN_REPARSE_POINT,
    )
    if root_info is None:
        err = _win_last_error()
        if err in (_WIN_ERROR_FILE_NOT_FOUND, _WIN_ERROR_PATH_NOT_FOUND):
            return Measure(0, 0, 0)
        return Measure(0, 0, 1)

    root_alloc, _root_eof, root_is_dir = root_info
    if not root_is_dir:
        if budget.on_progress is not None:
            budget.on_progress(1, root_alloc)
        return Measure(root_alloc, 1, 0)

    get_attrs = kernel32.GetFileAttributesW
    get_attrs.argtypes = [ctypes.c_wchar_p]
    get_attrs.restype = ctypes.c_uint32
    root_attrs = cast(int, get_attrs(_win_extended_path(path)))
    if root_attrs != 0xFFFFFFFF and (root_attrs & _WIN_FILE_ATTRIBUTE_REPARSE_POINT):
        if budget.on_progress is not None:
            budget.on_progress(1, root_alloc)
        return Measure(root_alloc, 1, 0)

    dir_buf = ctypes.create_string_buffer(_WIN_DIR_BUFFER_BYTES)
    total_bytes = 0
    total_files = 0
    skipped = 0
    # Per-root FileId set keeps memory bounded by the largest measured root.
    seen_file_ids: set[int] = set()
    stack: list[str] = [path]
    on_progress = budget.on_progress

    while stack:
        budget.check_cancelled()
        current = stack.pop()
        handle = cast(
            int | None,
            create_file(
                _win_extended_path(current),
                _WIN_FILE_LIST_DIRECTORY | _WIN_FILE_READ_ATTRIBUTES,
                _WIN_FILE_SHARE_ALL,
                None,
                _WIN_OPEN_EXISTING,
                _WIN_FILE_FLAG_BACKUP_SEMANTICS | _WIN_FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            ),
        )
        if not handle or handle == invalid_handle:
            err = _win_last_error()
            if err not in (_WIN_ERROR_FILE_NOT_FOUND, _WIN_ERROR_PATH_NOT_FOUND):
                skipped += 1
            continue

        dir_bytes = 0
        dir_files = 0
        try:
            while True:
                ok = cast(
                    int,
                    get_info_ex(
                        handle,
                        _WIN_FILE_ID_BOTH_DIR_INFO,
                        dir_buf,
                        _WIN_DIR_BUFFER_BYTES,
                    ),
                )
                if not ok:
                    err = _win_last_error()
                    if err != _WIN_ERROR_NO_MORE_FILES:
                        skipped += 1
                    break
                for name, eof, alloc, attrs, file_id in _parse_win_dir_buffer(
                    memoryview(dir_buf)
                ):
                    if name in (".", ".."):
                        continue
                    is_reparse = bool(attrs & _WIN_FILE_ATTRIBUTE_REPARSE_POINT)
                    is_dir = bool(attrs & _WIN_FILE_ATTRIBUTE_DIRECTORY)
                    if is_dir and not is_reparse:
                        stack.append(os.path.join(current, name))
                        continue
                    if file_id:
                        if file_id in seen_file_ids:
                            continue
                        seen_file_ids.add(file_id)
                    if not is_reparse and alloc == 0 and eof > 0:
                        # WOF-compressed files report AllocationSize == 0 in
                        # directory listings; fall back to FileStandardInfo.
                        child_info = _query_standard_info(os.path.join(current, name))
                        if child_info is not None:
                            size = child_info[0]
                        else:
                            size = eof
                            skipped += 1
                    else:
                        size = alloc
                    dir_bytes += max(0, size)
                    dir_files += 1
        finally:
            _ = cast(int, close_handle(handle))

        if dir_files or dir_bytes:
            total_bytes += dir_bytes
            total_files += dir_files
            if on_progress is not None:
                on_progress(dir_files, dir_bytes)

    return Measure(total_bytes, total_files, skipped)


def measure(
    path: str | os.PathLike[str], budget: MeasureBudget | None = None
) -> Measure:
    """Measure allocated disk usage of `path` using the platform back end."""
    active_budget = budget if budget is not None else MeasureBudget()
    target = os.fspath(path)
    if is_windows():
        return _measure_windows(target, active_budget)
    return _measure_posix(target, active_budget)


# ── Atomic JSON helpers & picked roots ───────────────────────────────────


def _write_json_atomic(path: str, payload: object) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temporary = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def storage_roots_path(data_dir: str | None = None) -> str:
    return os.path.join(data_dir or get_data_dir(), STORAGE_ROOTS_FILE)


def storage_report_path(data_dir: str | None = None) -> str:
    return os.path.join(data_dir or get_data_dir(), STORAGE_REPORT_FILE)


_roots_lock = threading.Lock()


def load_picked_roots(
    *,
    data_dir: str | None = None,
    valid_game_ids: Collection[int] | None = None,
) -> dict[int, str]:
    """Load device-local picked folders; drop IDs no longer in `valid_game_ids`."""
    path = storage_roots_path(data_dir)
    with _roots_lock:
        try:
            with open(path, encoding="utf-8") as handle:
                raw = cast(object, json.load(handle))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        data = cast(dict[object, object], raw)
        result: dict[int, str] = {}
        dropped = False
        for key, value in data.items():
            try:
                game_id = int(str(key))
            except (TypeError, ValueError):
                dropped = True
                continue
            if game_id <= 0 or not isinstance(value, str) or not value.strip():
                dropped = True
                continue
            if valid_game_ids is not None and game_id not in valid_game_ids:
                dropped = True
                continue
            result[game_id] = os.path.abspath(os.path.expanduser(value.strip()))
        if dropped and valid_game_ids is not None:
            try:
                _write_json_atomic(path, {str(k): v for k, v in sorted(result.items())})
            except OSError:
                pass
        return result


def save_picked_roots(roots: Mapping[int, str], *, data_dir: str | None = None) -> None:
    path = storage_roots_path(data_dir)
    payload = {str(gid): folder for gid, folder in sorted(roots.items()) if folder}
    with _roots_lock:
        _write_json_atomic(path, payload)


def clear_picked_root(game_id: int, *, data_dir: str | None = None) -> bool:
    """Remove `game_id` from `storage-roots.json`; returns True when it existed."""
    path = storage_roots_path(data_dir)
    with _roots_lock:
        try:
            with open(path, encoding="utf-8") as handle:
                raw = cast(object, json.load(handle))
        except (OSError, ValueError):
            return False
        if not isinstance(raw, dict):
            return False
        data = {
            str(k): str(v)
            for k, v in cast(dict[object, object], raw).items()
            if isinstance(v, str) and v.strip()
        }
        key = str(game_id)
        if key not in data:
            return False
        del data[key]
        _write_json_atomic(path, data)
        return True


# ── Game root detection and guard (Decision 5) ───────────────────────────


@dataclass(frozen=True)
class RootContext:
    library_folders: list[str]
    pattern: str
    all_game_exes: dict[int, str]  # game_id -> real_path(absolute exe)
    picked_roots: dict[int, str] = field(default_factory=dict)
    home_dir: str = field(default_factory=lambda: os.path.expanduser("~"))


@dataclass(frozen=True)
class RootResult:
    root: str
    source: RootSource
    uncertain: bool = False
    unsafe: bool = False
    unavailable: bool = False
    reason: str = ""


def _is_fs_or_drive_root(path: str) -> bool:
    if not path:
        return True
    cleaned = os.path.abspath(path)
    if os.path.dirname(cleaned) == cleaned:
        return True
    # Windows drive root check (also safe on Linux when testing Windows-style paths).
    drive, tail = ntpath.splitdrive(cleaned)
    return bool(drive) and tail in ("", "\\", "/")


def _has_engine_subdir(folder: str) -> bool:
    """True when `folder` contains an `Engine` subfolder (case-insensitive)."""
    direct = os.path.join(folder, "Engine")
    if os.path.isdir(direct) and not os.path.islink(direct):
        return True
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.lower() == "engine" and entry.is_dir(
                    follow_symlinks=False
                ):
                    return True
    except OSError:
        return False
    return False


def _guard_reason(
    candidate: str,
    exe_real: str,
    library_reals: Sequence[str],
    home_real: str,
    other_exe_reals: Sequence[str],
) -> str:
    """Return why `candidate` cannot be used as a game's storage root, or ""."""
    if not candidate:
        return "The folder is unknown."
    cand_real = real_path(candidate)
    if _is_fs_or_drive_root(cand_real):
        return "A file system or drive root cannot be used as a game folder."
    if home_real and (cand_real == home_real or is_within_real(home_real, cand_real)):
        return "Your home folder cannot be used as a game folder."
    for lib_real in library_reals:
        if cand_real == lib_real or is_within_real(lib_real, cand_real):
            return "A library folder cannot be used as a game folder."
    for other_real in other_exe_reals:
        # Another game pointing at the exact same executable shares the game folder
        # rather than being a different game in a shared parent container.
        if other_real != exe_real and is_within_real(other_real, cand_real):
            return "This folder holds another game's executable."
    return ""


def _game_id_of(game: Mapping[str, object]) -> int | None:
    raw = game.get("id")
    if isinstance(raw, bool):
        return None
    try:
        gid = int(str(raw))
    except (TypeError, ValueError):
        return None
    return gid if gid > 0 else None


def _pattern_root(
    game: Mapping[str, object],
    exe_real: str,
    library_folders: Sequence[str],
    pattern: str,
    library_reals: Sequence[str],
    home_real: str,
    other_exe_reals: Sequence[str],
) -> str:
    title = str(game.get("title") or "").strip()
    if not title:
        return ""
    creator = str(game.get("developer") or "")
    version = str(game.get("version") or "")
    engine = str(game.get("engine") or "")
    best = ""
    best_real = ""
    for folder in library_folders:
        if not folder:
            continue
        candidate = build_destination(folder, pattern, creator, title, version, engine)
        cand_real = real_path(candidate)
        if not is_within_real(exe_real, cand_real):
            continue
        if _guard_reason(
            candidate, exe_real, library_reals, home_real, other_exe_reals
        ):
            continue
        if len(cand_real) > len(best_real):
            best, best_real = candidate, cand_real
    return best


def _containing_library(
    exe_real: str, library_folders: Sequence[str]
) -> tuple[str, str] | None:
    """Deepest configured library folder strictly containing `exe_real`."""
    best: tuple[str, str] | None = None
    for folder in library_folders:
        if not folder:
            continue
        lib_real = real_path(folder)
        if exe_real != lib_real and is_within_real(exe_real, lib_real):
            if best is None or len(lib_real) > len(best[1]):
                best = (folder, lib_real)
    return best


def _root_unavailable(root: str, exe: str) -> bool:
    """True when `root` (or its drive) is not currently present on disk."""
    if not root:
        return False
    if not os.path.isdir(root):
        return True
    # An unmounted mount-point can appear as an empty directory where `exe` is missing.
    if exe and not os.path.exists(exe):
        try:
            with os.scandir(root) as it:
                if next(it, None) is None:
                    return True
        except OSError:
            return True
    return False


def storage_root(game: Mapping[str, object], ctx: RootContext) -> RootResult:
    """Determine a game's storage folder for the Storage page (read-only)."""
    exe = absolute_game_path(game.get("exe_path"))
    if not exe:
        return RootResult("", "")

    game_id = _game_id_of(game)
    exe_dir = os.path.dirname(exe)
    exe_real = real_path(exe)
    library_reals = [real_path(folder) for folder in ctx.library_folders if folder]
    home_real = real_path(ctx.home_dir)
    other_exe_reals = [
        path for gid, path in ctx.all_game_exes.items() if gid != game_id
    ]

    candidate = ""
    source: RootSource = ""
    uncertain = False

    # 1. Picked folder wins when it still holds the executable and passes the guard.
    picked = ctx.picked_roots.get(game_id) if game_id is not None else None
    if picked:
        picked_real = real_path(picked)
        if is_within_real(exe_real, picked_real) and not _guard_reason(
            picked, exe_real, library_reals, home_real, other_exe_reals
        ):
            candidate = picked
            source = "picked"

    # 2. Library folder pattern match (holds the executable and no other game).
    if not candidate:
        pat = _pattern_root(
            game,
            exe_real,
            ctx.library_folders,
            ctx.pattern,
            library_reals,
            home_real,
            other_exe_reals,
        )
        if pat:
            candidate = pat
            source = "pattern"

    # 3. Executable inside a library folder L: climb while parent is below L
    #    and holds no other game's executable.
    if not candidate:
        containing = _containing_library(exe_real, ctx.library_folders)
        if containing is not None:
            _lib_raw, lib_real = containing
            cur = exe_dir
            cur_real = real_path(cur)
            if cur_real != lib_real and is_within_real(cur_real, lib_real):
                while True:
                    parent = os.path.dirname(cur)
                    parent_real = real_path(parent)
                    if (
                        not parent
                        or parent_real == cur_real
                        or parent_real == lib_real
                        or not is_within_real(parent_real, lib_real)
                    ):
                        break
                    if _guard_reason(
                        parent, exe_real, library_reals, home_real, other_exe_reals
                    ):
                        break
                    cur = parent
                    cur_real = parent_real
                candidate = cur
                source = "climb"
            else:
                # Executable sits directly in the library folder -> guard will refuse it.
                candidate = exe_dir
                source = "exe_dir"

    # 4. Executable outside every library folder: climb only through known
    #    sub-folder marker names, plus one more step when the parent holds `Engine`.
    if not candidate:
        cur = exe_dir
        climbed = False
        while True:
            base = os.path.basename(cur).lower()
            if base not in MARKER_SUBDIRS:
                break
            parent = os.path.dirname(cur)
            if not parent or real_path(parent) == real_path(cur):
                break
            cur = parent
            climbed = True

        parent = os.path.dirname(cur)
        if (
            parent
            and real_path(parent) != real_path(cur)
            and _has_engine_subdir(parent)
        ):
            cur = parent
            climbed = True

        if climbed:
            candidate = cur
            source = "marker"
        else:
            # 5. Fallback to executable's folder, marked as uncertain.
            candidate = exe_dir
            source = "exe_dir"
            uncertain = True

    reason = _guard_reason(
        candidate, exe_real, library_reals, home_real, other_exe_reals
    )
    if reason:
        return RootResult("", source, uncertain=False, unsafe=True, reason=reason)

    unavailable = _root_unavailable(candidate, exe)
    return RootResult(
        candidate,
        source,
        uncertain=uncertain,
        unsafe=False,
        unavailable=unavailable,
    )


def validate_picked_folder(
    game: Mapping[str, object],
    folder: str,
    ctx: RootContext,
) -> tuple[str, str]:
    """Validate a user-picked folder for `game`. Returns `(normalized_path, error)`."""
    exe = absolute_game_path(game.get("exe_path"))
    if not exe:
        return "", "This game has no executable path."
    raw = folder.strip()
    if not raw:
        return "", "Choose a folder."
    normalized = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(normalized):
        return "", "The chosen folder does not exist."

    game_id = _game_id_of(game)
    exe_real = real_path(exe)
    cand_real = real_path(normalized)
    if not is_within_real(exe_real, cand_real):
        return "", "The chosen folder does not hold this game's executable."

    library_reals = [real_path(item) for item in ctx.library_folders if item]
    home_real = real_path(ctx.home_dir)
    other_exe_reals = [
        path for gid, path in ctx.all_game_exes.items() if gid != game_id
    ]
    reason = _guard_reason(
        normalized, exe_real, library_reals, home_real, other_exe_reals
    )
    if reason:
        return "", reason
    return normalized, ""


# ── Scan plan (Decision 4) ───────────────────────────────────────────────


@dataclass
class ScanUnit:
    """One de-duplicated filesystem path to measure during a scan."""

    category: str
    path: str
    real_key: str
    label: str
    owners: list[tuple[int, GamePart]] = field(default_factory=list)


@dataclass
class GamePlanEntry:
    game_id: int
    title: str
    exe_path: str
    root_result: RootResult
    picked: bool
    shared_with_ids: list[int] = field(default_factory=list)
    shared_with: list[str] = field(default_factory=list)


@dataclass
class ScanPlan:
    units: list[ScanUnit]
    games: dict[int, GamePlanEntry]


@dataclass(frozen=True)
class PlanInput:
    games: list[dict[str, object]]
    library_folders: list[str]
    pattern: str
    wine_prefix_path: str
    data_dir: str
    picked_roots: dict[int, str]
    torrent_folders: dict[str, int] = field(
        default_factory=dict
    )  # real_path -> game_id
    home_dir: str = field(default_factory=lambda: os.path.expanduser("~"))


def _norm_real_key(path: str) -> str:
    return os.path.normcase(real_path(path))


def _discover_torrent_folders(data_dir: str) -> dict[str, int]:
    """Map `<data_dir>/downloads/torrents/<hash>` real paths to game IDs via state.json."""
    result: dict[str, int] = {}
    torrents_root = os.path.join(data_dir, "downloads", "torrents")
    if not os.path.isdir(torrents_root):
        return result
    try:
        entries = sorted(os.scandir(torrents_root), key=lambda e: e.name)
    except OSError:
        return result
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        gid = 0
        state_path = os.path.join(entry.path, "state.json")
        try:
            with open(state_path, encoding="utf-8") as handle:
                raw = cast(object, json.load(handle))
            if isinstance(raw, dict):
                val = cast(dict[object, object], raw).get("game_id")
                if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                    gid = val
        except (OSError, ValueError):
            pass
        result[entry.path] = gid
    return result


def build_root_context(
    games: Sequence[Mapping[str, object]],
    library_folders: Sequence[str],
    pattern: str,
    picked_roots: Mapping[int, str],
    *,
    home_dir: str | None = None,
) -> RootContext:
    all_exes: dict[int, str] = {}
    for game in games:
        gid = _game_id_of(game)
        if gid is None:
            continue
        exe = absolute_game_path(game.get("exe_path"))
        if exe:
            all_exes[gid] = real_path(exe)
    return RootContext(
        library_folders=[folder for folder in library_folders if folder],
        pattern=pattern,
        all_game_exes=all_exes,
        picked_roots=dict(picked_roots),
        home_dir=home_dir if home_dir is not None else os.path.expanduser("~"),
    )


def build_scan_plan(plan_input: PlanInput) -> ScanPlan:
    """Build the de-duplicated unit plan from games, settings and data folders."""
    data_dir = plan_input.data_dir
    ctx = build_root_context(
        plan_input.games,
        plan_input.library_folders,
        plan_input.pattern,
        plan_input.picked_roots,
        home_dir=plan_input.home_dir,
    )

    units_by_key: dict[str, ScanUnit] = {}
    units_order: list[ScanUnit] = []
    game_entries: dict[int, GamePlanEntry] = {}
    games_by_id: dict[int, Mapping[str, object]] = {}

    def add_unit(
        category: str,
        path: str,
        label: str,
        owner: tuple[int, GamePart] | None = None,
    ) -> ScanUnit | None:
        if not path:
            return None
        key = _norm_real_key(path)
        existing = units_by_key.get(key)
        if existing is not None:
            if owner is not None and owner not in existing.owners:
                existing.owners.append(owner)
            return existing
        unit = ScanUnit(
            category=category,
            path=path,
            real_key=key,
            label=label,
            owners=[owner] if owner is not None else [],
        )
        units_by_key[key] = unit
        units_order.append(unit)
        return unit

    # Shared Wine prefix real key so per-game custom prefixes pointing at it
    # are not double-counted as game-owned prefixes.
    shared_prefix = (
        os.path.abspath(os.path.expanduser(plan_input.wine_prefix_path.strip()))
        if plan_input.wine_prefix_path.strip()
        else os.path.join(data_dir, "prefix")
    )
    shared_prefix_key = _norm_real_key(shared_prefix)

    # Group custom prefixes across all games to distinguish 1-game vs shared prefixes.
    prefix_users: dict[str, tuple[str, list[int]]] = {}
    for game in plan_input.games:
        gid = _game_id_of(game)
        if gid is None:
            continue
        games_by_id[gid] = game
        raw_prefix = str(game.get("custom_prefix") or "").strip()
        if not raw_prefix:
            continue
        expanded = os.path.abspath(os.path.expanduser(raw_prefix))
        pkey = _norm_real_key(expanded)
        if pkey == shared_prefix_key:
            continue
        entry = prefix_users.get(pkey)
        if entry is None:
            prefix_users[pkey] = (expanded, [gid])
        elif gid not in entry[1]:
            entry[1].append(gid)

    # Track game-root units so we can populate `shared_with` on rows sharing a folder.
    game_root_units: dict[str, list[int]] = {}

    # 1. Per-game units: game folder, saves, player, sandbox, single-game custom prefix.
    for game in plan_input.games:
        gid = _game_id_of(game)
        if gid is None:
            continue
        title = str(game.get("title") or f"Game {gid}")
        exe = absolute_game_path(game.get("exe_path"))
        root_res = storage_root(game, ctx)
        picked = gid in plan_input.picked_roots and root_res.source == "picked"

        has_any_part = bool(exe)
        if root_res.root and not root_res.unsafe and not root_res.unavailable:
            unit = add_unit("games", root_res.root, title, (gid, "game"))
            if unit is not None:
                game_root_units.setdefault(unit.real_key, []).append(gid)

        saves_dir = os.path.join(data_dir, "save-backups", str(gid))
        if os.path.isdir(saves_dir) and not os.path.islink(saves_dir):
            _ = add_unit("save_backups", saves_dir, f"{title} (saves)", (gid, "saves"))
            has_any_part = True

        player_dir = os.path.join(data_dir, "web-player", str(gid))
        if os.path.isdir(player_dir) and not os.path.islink(player_dir):
            _ = add_unit("web_player", player_dir, f"{title} (player)", (gid, "player"))
            has_any_part = True

        sandbox_dir = os.path.join(data_dir, "sandbox", str(gid))
        if os.path.isdir(sandbox_dir) and not os.path.islink(sandbox_dir):
            _ = add_unit(
                "sandboxes", sandbox_dir, f"{title} (sandbox)", (gid, "sandbox")
            )
            has_any_part = True

        if has_any_part:
            game_entries[gid] = GamePlanEntry(
                game_id=gid,
                title=title,
                exe_path=exe,
                root_result=root_res,
                picked=picked,
            )

    # Custom Wine prefixes: single-game attaches to that game, multi-game goes to category only.
    for _pkey, (prefix_path, user_ids) in prefix_users.items():
        if not os.path.isdir(prefix_path):
            continue
        if len(user_ids) == 1:
            gid = user_ids[0]
            game = games_by_id.get(gid)
            title = str(game.get("title") or f"Game {gid}") if game else f"Game {gid}"
            _ = add_unit(
                "wine_prefixes", prefix_path, f"{title} (Wine prefix)", (gid, "prefix")
            )
            if gid not in game_entries and game is not None:
                game_entries[gid] = GamePlanEntry(
                    game_id=gid,
                    title=title,
                    exe_path=absolute_game_path(game.get("exe_path")),
                    root_result=RootResult("", ""),
                    picked=False,
                )
        else:
            _ = add_unit(
                "wine_prefixes",
                prefix_path,
                f"Shared prefix ({os.path.basename(prefix_path)})",
            )

    # Populate shared_with for games whose game folder resolved to the same real path.
    for _unit_key, owner_ids in game_root_units.items():
        if len(owner_ids) <= 1:
            continue
        for gid in owner_ids:
            entry = game_entries.get(gid)
            if entry is None:
                continue
            other_ids = [oid for oid in owner_ids if oid != gid]
            entry.shared_with_ids = other_ids
            entry.shared_with = [
                game_entries[oid].title if oid in game_entries else f"Game {oid}"
                for oid in other_ids
            ]

    # 2. Torrent data folders.
    torrent_map = (
        dict(plan_input.torrent_folders)
        if plan_input.torrent_folders
        else _discover_torrent_folders(data_dir)
    )
    for folder, gid in torrent_map.items():
        if not os.path.isdir(folder):
            continue
        owner: tuple[int, GamePart] | None = None
        label = "Torrent data"
        if gid > 0 and gid in games_by_id:
            owner = (gid, "torrent")
            title = str(games_by_id[gid].get("title") or f"Game {gid}")
            label = f"{title} (torrent)"
            if gid not in game_entries:
                game_entries[gid] = GamePlanEntry(
                    game_id=gid,
                    title=title,
                    exe_path=absolute_game_path(games_by_id[gid].get("exe_path")),
                    root_result=RootResult("", ""),
                    picked=False,
                )
        _ = add_unit("torrent_data", folder, label, owner)

    # 3. Orphan / removed save backup folders under `save-backups/`.
    save_backups_root = os.path.join(data_dir, "save-backups")
    if os.path.isdir(save_backups_root):
        try:
            for entry in sorted(os.scandir(save_backups_root), key=lambda e: e.name):
                if entry.is_symlink():
                    continue
                if _norm_real_key(entry.path) not in units_by_key:
                    _ = add_unit("save_backups", entry.path, "Save backups")
        except OSError:
            pass

    # 4. Shared Wine prefix.
    if os.path.isdir(shared_prefix):
        _ = add_unit("shared_wine_prefix", shared_prefix, "Shared Wine prefix")

    # 5. Proton builds in wLib's proton folder.
    proton_root = os.path.join(data_dir, "proton")
    if os.path.isdir(proton_root):
        try:
            for entry in sorted(os.scandir(proton_root), key=lambda e: e.name):
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                _ = add_unit("proton_builds", entry.path, f"Proton ({entry.name})")
        except OSError:
            pass

    # 6. Download staging files (excluding `torrents/` subfolder).
    downloads_root = os.path.join(data_dir, "downloads")
    if os.path.isdir(downloads_root):
        try:
            for entry in sorted(os.scandir(downloads_root), key=lambda e: e.name):
                if entry.name == "torrents":
                    continue
                if entry.is_symlink():
                    continue
                _ = add_unit("download_archives", entry.path, "Download archives")
        except OSError:
            pass

    # 7. Data of removed games.
    for sub in ("sandbox", "web-player"):
        removed = os.path.join(data_dir, sub, "removed")
        if os.path.isdir(removed) and not os.path.islink(removed):
            _ = add_unit("removed_game_data", removed, "Data of removed games")

    # 8. Cover cache (`covers/cache/`, `covers/cards/`).
    for sub in ("cache", "cards"):
        folder = os.path.join(data_dir, "covers", sub)
        if os.path.isdir(folder) and not os.path.islink(folder):
            _ = add_unit("cover_cache", folder, "Cover cache")

    # 9. Other caches (`bepinex-cache/`, `install-scratch/`, `webview/`).
    for name in ("bepinex-cache", "install-scratch", "webview"):
        folder = os.path.join(data_dir, name)
        if os.path.isdir(folder) and not os.path.islink(folder):
            _ = add_unit("other_caches", folder, "Other caches")

    # 10. Catalog (`catalog.db`, `discover-index.db` plus WAL/SHM).
    for name in (
        "catalog.db",
        "catalog.db-wal",
        "catalog.db-shm",
        "discover-index.db",
        "discover-index.db-wal",
        "discover-index.db-shm",
    ):
        file_path = os.path.join(data_dir, name)
        if os.path.lexists(file_path) and not os.path.islink(file_path):
            _ = add_unit("catalog", file_path, "Catalog")

    # 11. Logs and database backups (`logs/`, `db-backups/`).
    for name in ("logs", "db-backups"):
        folder = os.path.join(data_dir, name)
        if os.path.isdir(folder) and not os.path.islink(folder):
            _ = add_unit("logs_and_backups", folder, "Logs and database backups")

    return ScanPlan(units=units_order, games=game_entries)


# ── Report structure & helpers ───────────────────────────────────────────


class CategoryReport(TypedDict):
    id: str
    label: str
    bytes: int
    files: int
    percent: float


class GamePartsReport(TypedDict):
    game: int
    saves: int
    player: int
    sandbox: int
    prefix: int


class GameStorageReport(TypedDict):
    game_id: int
    title: str
    root: str
    root_source: str
    exe_path: str
    picked: bool
    uncertain: bool
    unsafe: bool
    unsafe_reason: str
    unavailable: bool
    changed: bool
    shared_with: list[str]
    shared_with_ids: list[int]
    parts: GamePartsReport
    torrent_bytes: int
    total_bytes: int


class ActionSummary(TypedDict):
    bytes: int
    count: int


class StorageReport(TypedDict):
    version: int
    scanned_at: str
    scanned_at_epoch: float
    total_bytes: int
    total_files: int
    skipped_entries: int
    categories: list[CategoryReport]
    games: dict[str, GameStorageReport]
    actions: dict[str, ActionSummary]


def recalculate_category_percents(report: StorageReport) -> None:
    total = sum(max(0, int(cat["bytes"])) for cat in report["categories"])
    report["total_bytes"] = total
    for cat in report["categories"]:
        size = max(0, int(cat["bytes"]))
        cat["bytes"] = size
        cat["percent"] = round((size / total) * 100.0, 1) if total > 0 else 0.0


def load_saved_report(data_dir: str | None = None) -> StorageReport | None:
    path = storage_report_path(data_dir)
    try:
        with open(path, encoding="utf-8") as handle:
            raw = cast(object, json.load(handle))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    data = cast(dict[str, object], raw)
    if data.get("version") != STORAGE_REPORT_VERSION:
        return None
    if not isinstance(data.get("categories"), list) or not isinstance(
        data.get("games"), dict
    ):
        return None
    return cast(StorageReport, cast(object, data))


def save_report(report: StorageReport, data_dir: str | None = None) -> None:
    _write_json_atomic(storage_report_path(data_dir), report)


# ── Scan service ─────────────────────────────────────────────────────────


def _default_load_plan_input() -> PlanInput:
    from core.database import (
        DEFAULT_LIBRARY_FOLDER_PATTERN,
        get_all_games,
        get_library_folders,
        get_setting,
    )

    games = get_all_games()
    valid_ids = {gid for g in games if (gid := _game_id_of(g)) is not None}
    folders, _default = get_library_folders()
    pattern = str(
        get_setting("library_folder_pattern") or DEFAULT_LIBRARY_FOLDER_PATTERN
    )
    wine_prefix = str(get_setting("wine_prefix_path") or "")
    data_dir = get_data_dir()
    picked = load_picked_roots(data_dir=data_dir, valid_game_ids=valid_ids)
    return PlanInput(
        games=games,
        library_folders=folders,
        pattern=pattern,
        wine_prefix_path=wine_prefix,
        data_dir=data_dir,
        picked_roots=picked,
    )


def _default_recount_enabled() -> bool:
    from core.database import get_setting

    return get_setting(STORAGE_RECOUNT_SETTING) == "true"


@final
class StorageScanService:
    """Background disk-usage scanner, saved report manager and single-game recounter."""

    def __init__(
        self,
        *,
        running_games: Callable[[], Sequence[int]] = lambda: (),
        load_plan_input: Callable[[], PlanInput] | None = None,
        measure_actions: Callable[[MeasureBudget], dict[str, ActionSummary]]
        | None = None,
        recount_enabled: Callable[[], bool] | None = None,
        on_battery: Callable[[], bool | None] | None = None,
        data_dir: Callable[[], str] | None = None,
    ) -> None:
        self._running_games = running_games
        self._load_plan_input = load_plan_input or _default_load_plan_input
        self._measure_actions = measure_actions
        self._recount_enabled = recount_enabled or _default_recount_enabled
        self._on_battery = on_battery or power.on_battery
        self._data_dir = data_dir or get_data_dir

        # Shared task lock: serializes full scan, single-game recount and cleanup.
        self.task_lock: threading.Lock = threading.Lock()
        self._state_lock: threading.RLock = threading.RLock()
        self._task: Literal["idle", "scan", "recount", "cleanup"] = "idle"
        self._cancel: threading.Event = threading.Event()
        self._worker: threading.Thread | None = None
        self._completed_units: int = 0
        self._total_units: int = 0
        self._files_counted: int = 0
        self._bytes_counted: int = 0
        self._current_label: str = ""
        self._stopped_reason: str = ""
        self._last_error: str = ""
        self._report: StorageReport | None = load_saved_report(self._data_dir())

    def set_action_measurer(
        self, fn: Callable[[MeasureBudget], dict[str, ActionSummary]] | None
    ) -> None:
        self._measure_actions = fn

    # --- State reading ----------------------------------------------------

    def get_report(self) -> StorageReport | None:
        with self._state_lock:
            if self._report is None:
                self._report = load_saved_report(self._data_dir())
            if self._report is None:
                return None
            return cast(StorageReport, json.loads(json.dumps(self._report)))

    def replace_report(self, report: StorageReport) -> None:
        """Persist an updated report (used by cleanup after deleting targets)."""
        with self._state_lock:
            self._report = report
            save_report(report, self._data_dir())

    def is_busy(self) -> bool:
        with self._state_lock:
            return self._task != "idle"

    def current_task(self) -> str:
        with self._state_lock:
            return self._task

    @contextmanager
    def acquire_cleanup_task(self) -> Generator[None]:
        """Reserve the shared storage task slot for a cleanup run."""
        with self._state_lock:
            if self._task != "idle":
                if self._task in ("scan", "recount"):
                    raise RuntimeError(
                        "A storage scan is running. Wait for it to finish."
                    )
                raise RuntimeError("Another cleanup is already running.")
            self._task = "cleanup"
        try:
            with self.task_lock:
                yield
        finally:
            with self._state_lock:
                if self._task == "cleanup":
                    self._task = "idle"

    def state(self) -> dict[str, object]:
        running = list(self._running_games())
        with self._state_lock:
            if self._report is None:
                self._report = load_saved_report(self._data_dir())
            report_copy = (
                cast(dict[str, object], json.loads(json.dumps(self._report)))
                if self._report is not None
                else None
            )
            return {
                "task": self._task,
                "scanning": self._task in ("scan", "recount"),
                "cleanup_running": self._task == "cleanup",
                "game_running": bool(running),
                "completed_units": self._completed_units,
                "total_units": self._total_units,
                "files_counted": self._files_counted,
                "bytes_counted": self._bytes_counted,
                "current_label": self._current_label,
                "stopped_reason": self._stopped_reason,
                "last_error": self._last_error,
                "report": report_copy,
            }

    # --- Start / stop scan ------------------------------------------------

    def start_scan(self) -> dict[str, object]:
        if self._running_games():
            return {
                "success": False,
                "code": "game_running",
                "error": "Close the running game before scanning storage.",
            }
        with self._state_lock:
            if self._task != "idle":
                return {
                    "success": False,
                    "code": "task_running",
                    "error": "A storage task is already running.",
                }
            self._task = "scan"
            self._cancel = threading.Event()
            self._completed_units = 0
            self._total_units = 0
            self._files_counted = 0
            self._bytes_counted = 0
            self._current_label = "Preparing scan"
            self._stopped_reason = ""
            self._last_error = ""
            cancel = self._cancel
            thread = threading.Thread(
                target=self._run_full_scan,
                args=(cancel,),
                name="wlib-storage-scan",
                daemon=True,
            )
            self._worker = thread
        thread.start()
        return {"success": True}

    def stop_scan(self, reason: str = "user") -> dict[str, object]:
        with self._state_lock:
            if self._task not in ("scan", "recount"):
                return {"success": True, "stopped": False}
            if not self._stopped_reason:
                self._stopped_reason = reason
            self._cancel.set()
            return {"success": True, "stopped": True}

    def on_game_starting(self) -> None:
        """Stop any running scan or recount immediately when a game starts."""
        _ = self.stop_scan(reason="game")

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Wait for the current worker thread to finish (for tests)."""
        with self._state_lock:
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)
        return not self.is_busy()

    def _run_full_scan(self, cancel: threading.Event) -> None:
        try:
            with self.task_lock, low_priority_thread():
                cancel_check = MeasureBudget(
                    cancel=cancel, on_progress=self._add_progress
                )
                cancel_check.check_cancelled()
                plan_input = self._load_plan_input()
                plan = build_scan_plan(plan_input)
                with self._state_lock:
                    self._total_units = len(plan.units)
                    prev_report = self._report

                report = self._execute_plan(plan, cancel_check, prev_report)
                cancel_check.check_cancelled()
                save_report(report, plan_input.data_dir)
                with self._state_lock:
                    self._report = report
                    self._stopped_reason = ""
        except ScanCancelled:
            with self._state_lock:
                if not self._stopped_reason:
                    self._stopped_reason = "user"
        except Exception as exc:
            _log.exception("Storage scan failed")
            with self._state_lock:
                self._last_error = str(exc) or "Storage scan failed"
        finally:
            with self._state_lock:
                if self._task == "scan":
                    self._task = "idle"
                self._current_label = ""

    def _add_progress(self, files_delta: int, bytes_delta: int) -> None:
        with self._state_lock:
            self._files_counted += files_delta
            self._bytes_counted += bytes_delta

    def _execute_plan(
        self,
        plan: ScanPlan,
        budget: MeasureBudget,
        prev_report: StorageReport | None,
    ) -> StorageReport:
        cat_bytes: dict[str, int] = {cid: 0 for cid, _ in CATEGORY_ORDER}
        cat_files: dict[str, int] = {cid: 0 for cid, _ in CATEGORY_ORDER}
        total_skipped = 0

        # Prepare per-game accumulators.
        prev_games = prev_report["games"] if prev_report is not None else {}
        game_rows: dict[str, GameStorageReport] = {}
        for gid, entry in plan.games.items():
            prev_row = prev_games.get(str(gid))
            parts: GamePartsReport = {
                "game": 0,
                "saves": 0,
                "player": 0,
                "sandbox": 0,
                "prefix": 0,
            }
            if entry.root_result.unavailable and prev_row is not None:
                prev_game_bytes = int(prev_row["parts"]["game"])
                parts["game"] = prev_game_bytes
                cat_bytes["games"] += prev_game_bytes

            game_rows[str(gid)] = {
                "game_id": gid,
                "title": entry.title,
                "root": entry.root_result.root,
                "root_source": entry.root_result.source,
                "exe_path": entry.exe_path,
                "picked": entry.picked,
                "uncertain": entry.root_result.uncertain,
                "unsafe": entry.root_result.unsafe,
                "unsafe_reason": entry.root_result.reason,
                "unavailable": entry.root_result.unavailable,
                "changed": False,
                "shared_with": list(entry.shared_with),
                "shared_with_ids": list(entry.shared_with_ids),
                "parts": parts,
                "torrent_bytes": 0,
                "total_bytes": parts["game"],
            }

        for index, unit in enumerate(plan.units):
            budget.check_cancelled()
            with self._state_lock:
                self._current_label = unit.label
            result = measure(unit.path, budget)
            cat_bytes[unit.category] = cat_bytes.get(unit.category, 0) + result.bytes
            cat_files[unit.category] = cat_files.get(unit.category, 0) + result.files
            total_skipped += result.skipped

            for gid, part in unit.owners:
                row = game_rows.get(str(gid))
                if row is None:
                    continue
                if part == "torrent":
                    row["torrent_bytes"] += result.bytes
                else:
                    row["parts"][part] += result.bytes

            with self._state_lock:
                self._completed_units = index + 1

        for row in game_rows.values():
            parts = row["parts"]
            row["total_bytes"] = (
                parts["game"]
                + parts["saves"]
                + parts["player"]
                + parts["sandbox"]
                + parts["prefix"]
            )

        actions: dict[str, ActionSummary] = {}
        if self._measure_actions is not None:
            budget.check_cancelled()
            with self._state_lock:
                self._current_label = "Checking cleanup actions"
            actions = self._measure_actions(budget)

        now_epoch = time.time()
        categories: list[CategoryReport] = [
            {
                "id": cid,
                "label": label,
                "bytes": cat_bytes[cid],
                "files": cat_files[cid],
                "percent": 0.0,
            }
            for cid, label in CATEGORY_ORDER
        ]
        report: StorageReport = {
            "version": STORAGE_REPORT_VERSION,
            "scanned_at": datetime.fromtimestamp(now_epoch, tz=UTC).isoformat(),
            "scanned_at_epoch": now_epoch,
            "total_bytes": 0,
            "total_files": sum(cat_files.values()),
            "skipped_entries": total_skipped,
            "categories": categories,
            "games": game_rows,
            "actions": actions,
        }
        recalculate_category_percents(report)
        return report

    # --- Game change notifications & single-game recount (Tasks 3.4, 3.5) ---

    def game_changed(self, game_id: int, kind: ChangeKind) -> bool:
        """React to install, update, move, uninstall or removal of `game_id`.

        Install, update, move and uninstall mark the row changed (uninstall also
        drops the deleted game folder from the sizes). Removal drops the row.
        Returns True if a background single-game recount was started.
        """
        if kind == "remove":
            _ = clear_picked_root(game_id, data_dir=self._data_dir())

        with self._state_lock:
            if self._report is None:
                self._report = load_saved_report(self._data_dir())
            report = self._report
            if report is None:
                return False

            key = str(game_id)
            if kind == "remove":
                removed = report["games"].pop(key, None)
                if removed is not None:
                    self._apply_game_removal(report, game_id, removed)
                    recalculate_category_percents(report)
                    save_report(report, self._data_dir())
                return False

            row = report["games"].get(key)
            if row is not None:
                row["changed"] = True
                if kind == "uninstall":
                    self._apply_game_uninstall(report, row)
                    recalculate_category_percents(report)
                save_report(report, self._data_dir())
            if kind == "uninstall":
                return False
            label = row["title"] if row is not None else f"Game {game_id}"

        # Read the recount conditions without the state lock: they take other
        # locks (settings, the launcher) that a game launch may hold.
        if not self._recount_enabled():
            return False
        if self._on_battery() is True:
            return False
        if self._running_games():
            return False

        with self._state_lock:
            if self._task != "idle":
                # A full scan already includes this game; a running cleanup/recount keeps it marked.
                return False
            self._task = "recount"
            self._cancel = threading.Event()
            self._completed_units = 0
            self._total_units = 0
            self._files_counted = 0
            self._bytes_counted = 0
            self._current_label = label
            self._stopped_reason = ""
            cancel = self._cancel
            thread = threading.Thread(
                target=self._run_single_game_recount,
                args=(game_id, cancel),
                name=f"wlib-storage-recount-{game_id}",
                daemon=True,
            )
            self._worker = thread

        thread.start()
        return True

    @staticmethod
    def _adjust_category(report: StorageReport, cat_id: str, delta: int) -> None:
        for cat in report["categories"]:
            if cat["id"] == cat_id:
                cat["bytes"] = max(0, cat["bytes"] + delta)
                return

    @classmethod
    def _apply_game_uninstall(
        cls, report: StorageReport, row: GameStorageReport
    ) -> None:
        """The game folder is deleted; saves, player data, sandbox and prefix stay."""
        parts = row["parts"]
        # A folder that another game still shares stays on disk.
        if not row["shared_with_ids"]:
            cls._adjust_category(report, "games", -parts["game"])
        parts["game"] = 0
        row["total_bytes"] = (
            parts["saves"] + parts["player"] + parts["sandbox"] + parts["prefix"]
        )

    @classmethod
    def _apply_game_removal(
        cls, report: StorageReport, game_id: int, row: GameStorageReport
    ) -> None:
        """Match what the next scan shows after a game leaves the library.

        Its folder and own prefix stop counting, its sandbox and player data
        move to the removed-game data, and its save backups and torrent data
        stay where they are.
        """
        parts = row["parts"]
        if not row["shared_with_ids"]:
            cls._adjust_category(report, "games", -parts["game"])
        cls._adjust_category(report, "wine_prefixes", -parts["prefix"])
        cls._adjust_category(report, "sandboxes", -parts["sandbox"])
        cls._adjust_category(report, "web_player", -parts["player"])
        cls._adjust_category(
            report, "removed_game_data", parts["sandbox"] + parts["player"]
        )
        for other in report["games"].values():
            if game_id in other["shared_with_ids"]:
                idx = other["shared_with_ids"].index(game_id)
                del other["shared_with_ids"][idx]
                if idx < len(other["shared_with"]):
                    del other["shared_with"][idx]

    def _run_single_game_recount(self, game_id: int, cancel: threading.Event) -> None:
        try:
            with self.task_lock, low_priority_thread():
                budget = MeasureBudget(cancel=cancel, on_progress=self._add_progress)
                budget.check_cancelled()
                plan_input = self._load_plan_input()
                plan = build_scan_plan(plan_input)
                entry = plan.games.get(game_id)
                if entry is None:
                    return

                game_units = [
                    u for u in plan.units if any(gid == game_id for gid, _ in u.owners)
                ]
                with self._state_lock:
                    self._total_units = len(game_units)

                new_parts: GamePartsReport = {
                    "game": 0,
                    "saves": 0,
                    "player": 0,
                    "sandbox": 0,
                    "prefix": 0,
                }
                new_torrent = 0
                for idx, unit in enumerate(game_units):
                    budget.check_cancelled()
                    with self._state_lock:
                        self._current_label = unit.label
                    res = measure(unit.path, budget)
                    for gid, part in unit.owners:
                        if gid != game_id:
                            continue
                        if part == "torrent":
                            new_torrent += res.bytes
                        else:
                            new_parts[part] += res.bytes
                    with self._state_lock:
                        self._completed_units = idx + 1

                budget.check_cancelled()
                with self._state_lock:
                    report = self._report
                    if report is None:
                        return
                    key = str(game_id)
                    old_row = report["games"].get(key)
                    if entry.root_result.unavailable and old_row is not None:
                        new_parts["game"] = old_row["parts"]["game"]

                    cat_map = {cat["id"]: cat for cat in report["categories"]}
                    if old_row is not None:
                        old_parts = old_row["parts"]
                        if not old_row["shared_with_ids"] and "games" in cat_map:
                            cat_map["games"]["bytes"] = max(
                                0, cat_map["games"]["bytes"] - old_parts["game"]
                            )
                        for cat_id, old_val in (
                            ("save_backups", old_parts["saves"]),
                            ("web_player", old_parts["player"]),
                            ("sandboxes", old_parts["sandbox"]),
                            ("wine_prefixes", old_parts["prefix"]),
                            ("torrent_data", old_row["torrent_bytes"]),
                        ):
                            if cat_id in cat_map:
                                cat_map[cat_id]["bytes"] = max(
                                    0, cat_map[cat_id]["bytes"] - old_val
                                )

                    if not entry.shared_with_ids and "games" in cat_map:
                        cat_map["games"]["bytes"] += new_parts["game"]
                    elif (
                        entry.shared_with_ids and old_row is None and "games" in cat_map
                    ):
                        # Already counted under the other game sharing it.
                        pass
                    for cat_id, new_val in (
                        ("save_backups", new_parts["saves"]),
                        ("web_player", new_parts["player"]),
                        ("sandboxes", new_parts["sandbox"]),
                        ("wine_prefixes", new_parts["prefix"]),
                        ("torrent_data", new_torrent),
                    ):
                        if cat_id in cat_map:
                            cat_map[cat_id]["bytes"] += new_val

                    report["games"][key] = {
                        "game_id": game_id,
                        "title": entry.title,
                        "root": entry.root_result.root,
                        "root_source": entry.root_result.source,
                        "exe_path": entry.exe_path,
                        "picked": entry.picked,
                        "uncertain": entry.root_result.uncertain,
                        "unsafe": entry.root_result.unsafe,
                        "unsafe_reason": entry.root_result.reason,
                        "unavailable": entry.root_result.unavailable,
                        "changed": False,
                        "shared_with": list(entry.shared_with),
                        "shared_with_ids": list(entry.shared_with_ids),
                        "parts": new_parts,
                        "torrent_bytes": new_torrent,
                        "total_bytes": (
                            new_parts["game"]
                            + new_parts["saves"]
                            + new_parts["player"]
                            + new_parts["sandbox"]
                            + new_parts["prefix"]
                        ),
                    }
                    recalculate_category_percents(report)
                    save_report(report, plan_input.data_dir)
        except ScanCancelled:
            with self._state_lock:
                if not self._stopped_reason:
                    self._stopped_reason = "user"
        except Exception as exc:
            _log.exception("Single-game recount failed for game %s", game_id)
            with self._state_lock:
                self._last_error = str(exc) or "Recount failed"
        finally:
            with self._state_lock:
                if self._task == "recount":
                    self._task = "idle"
                self._current_label = ""


def resolve_default_proton_dir() -> str:
    return get_proton_dir()


def resolve_default_wine_prefix() -> str:
    return get_default_wine_prefix()


def notify_storage_game_starting(owner: object) -> None:
    """Stop any active storage scan on `owner.storage` when present."""
    service = getattr(owner, "storage", None)
    if isinstance(service, StorageScanService):
        service.on_game_starting()


def notify_storage_game_changed(owner: object, game_id: int, kind: ChangeKind) -> None:
    """Forward a game change event to `owner.storage` when present."""
    service = getattr(owner, "storage", None)
    if isinstance(service, StorageScanService):
        _ = service.game_changed(game_id, kind)
