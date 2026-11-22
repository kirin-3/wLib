# SPDX-License-Identifier: GPL-3.0-or-later
"""Install games from downloaded archives (.zip/.7z/.rar and tar archives)."""

from __future__ import annotations

import errno
import importlib
import io
import json
import logging
import ntpath
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
import zlib
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from typing import Self, cast, override

from core.archive_formats import ARCHIVE_SUFFIXES, TAR_COMPRESSION_MODULES
from core.archive_formats import archive_family as _archive_family
from core.archive_formats import archive_suffix as archive_suffix
from core.archive_formats import split_archive_name as _split_archive_name
from core.archive_volumes import RAR_OLD_RE as _RAR_OLD_RE
from core.archive_volumes import RAR_PART_RE as _RAR_PART_RE
from core.archive_volumes import SPLIT_RE as _SPLIT_RE
from core.archive_volumes import ZIP_SPAN_RE as _ZIP_SPAN_RE
from core.archive_volumes import VolumeSet as VolumeSet
from core.archive_volumes import volume_set as volume_set
from core.database import DEFAULT_LIBRARY_FOLDER_PATTERN
from core.game_saves import (
    INFOLDER_SAVE_DIRS as INFOLDER_SAVE_DIRS,
    LOOSE_SAVE_PATTERNS as LOOSE_SAVE_PATTERNS,
    copy_folder_contents,
    is_within,
    loose_save_files,
)
from core.host_platform import host_command, host_which, is_flatpak, no_console_window
from core.versions import has_update, normalize_version

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_INVALID_SEGMENT_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

ARCHIVE_EXTENSIONS = ARCHIVE_SUFFIXES
UNSUPPORTED_FORMAT_MESSAGE = (
    "Unsupported archive format. Choose a .zip, .7z, .rar, .tar, .tar.gz, "
    ".tgz, .tar.bz2, .tbz2, .tbz, .tar.xz or .txz file."
)
STAGING_PREFIX = ".wlib-staging-"
TRASH_PREFIX = ".wlib-trash-"
# Every wLib version names trash `TRASH_PREFIX + str(time.time_ns())`, which
# is 19 digits until the year 2286. The sweep deletes only names of this form,
# so a user's own `.wlib-trash-backup` folder stays.
_TRASH_NAME_RE = re.compile(re.escape(TRASH_PREFIX) + r"[0-9]{19}")
SCRATCH_PREFIX = ".wlib-scratch-"
# Local scratch must leave this much free so an install never fills the home disk.
SCRATCH_RESERVE_BYTES = 1024**3
# Without a listed size, assume an archive unpacks to at most this many times
# its own size before trusting local disk with it.
SCRATCH_UNKNOWN_SIZE_FACTOR = 4
# How often a scratch extraction checks that local disk still has the reserve.
SCRATCH_WATCH_SECONDS = 0.25
BUNDLED_7ZZ = "/app/bin/7zz"

# Network filesystems pay a server round trip (and, with sync exports, a disk
# commit) for every file create, close and attribute change. Installs into a
# library on one of these extract in parallel, stage 7z/rar output on local
# disk first, and remove the old version in the background.
NETWORK_FS_TYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "smbfs",
        "9p",
        "afs",
        "ceph",
        "glusterfs",
        "fuse.glusterfs",
        "fuse.sshfs",
        "fuse.rclone",
        "fuse.davfs2",
        "fuse.gvfsd-fuse",
        "fuse.smbnetfs",
        "davfs",
    }
)
NETWORK_IO_WORKERS = 16
# Zip members inflate in zlib, which releases the GIL, so local installs scale
# with cores too. The cap keeps huge machines from opening dozens of handles.
ZIP_MAX_WORKERS = 32
_COPY_CHUNK = 1024 * 1024
# os.open() is text mode on Windows unless asked otherwise.
_O_BINARY: int = getattr(os, "O_BINARY", 0)
# os.utime takes a file descriptor on POSIX only.
_UTIME_FD = os.utime in os.supports_fd

_log = logging.getLogger("wlib.archive")


class ArchiveInstallError(Exception):
    """Base failure for archive installs (message is user-facing)."""


class ArchivePasswordRequired(ArchiveInstallError):
    """Extraction failed because the archive needs a password and none was given."""

    error_code: str = "archive_password_required"

    def __init__(
        self,
        message: str = "This archive needs a password. Enter it in the install dialog and try again.",
    ) -> None:
        super().__init__(message)


class ArchivePasswordWrong(ArchiveInstallError):
    """Extraction failed because the given archive password is wrong."""

    error_code: str = "archive_password_wrong"

    def __init__(self, message: str = "The archive password is wrong.") -> None:
        super().__init__(message)


class InstallCancelled(ArchiveInstallError):
    """The user cancelled the install before the new version moved into place."""

    def __init__(self) -> None:
        super().__init__("The install was cancelled. Nothing was changed.")


class NoLauncherChosenError(ArchiveInstallError):
    """An update's archive holds launch files, but none could be picked.

    `candidates` lists them as paths relative to the extracted archive, so the
    dialog can offer a choice and install again with `exe_relpath`.
    """

    def __init__(self, candidates: list[str]) -> None:
        super().__init__(
            "No launchable file could be picked automatically. Choose the file to launch."
        )
        self.candidates: list[str] = candidates


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise InstallCancelled()


def tree_bytes(root: str) -> int:
    """Bytes of the regular files under root (symlinks not followed)."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                info = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


class _ExtractMeter:
    """Report how many bytes have landed in staging while extraction runs.

    Measuring the folder works the same for zipfile, 7-Zip, unrar and
    bsdtar, none of which needs to report progress itself.
    """

    def __init__(
        self, root: str, total: int | None, report: Callable[[int, int], None] | None
    ) -> None:
        self._root: str = root
        self._total: int = total if total and total > 0 else 0
        self._report: Callable[[int, int], None] | None = report
        self._stop: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _ExtractMeter:
        if self._report is not None:
            self._thread = threading.Thread(
                target=self._run, name="extract-meter", daemon=True
            )
            self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        interval = 1.0
        while not self._stop.wait(interval):
            started = time.monotonic()
            done = _tree_bytes(self._root)
            # Walking a large tree, or one on a network share, takes time the
            # extractor needs; keep the walks to a fifth of the wall clock.
            interval = max(1.0, (time.monotonic() - started) * 4)
            if self._report is not None:
                try:
                    # Never claim done before the extractor exits.
                    self._report(
                        min(done, self._total - 1) if self._total else done, self._total
                    )
                except Exception:
                    pass


class ZipMethodUnsupportedError(ArchiveInstallError):
    """A zip member uses a compression method Python cannot inflate."""


class ArchiveSafetyError(ArchiveInstallError):
    """Archive tried to write outside the destination."""


def sanitize_path_segment(segment: str) -> str:
    """Sanitize one path segment for Windows + Linux."""
    cleaned = _INVALID_SEGMENT_CHARS_RE.sub("_", segment)
    cleaned = cleaned.strip().strip(". ")
    cleaned = cleaned.strip()
    if not cleaned:
        return ""
    base = cleaned.split(".")[0].upper()
    if base in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip()


def build_destination(
    library_folder: str,
    pattern: str,
    creator: str = "",
    title: str = "",
    version: str = "",
    engine: str = "",
) -> str:
    """Compute `<library>/<pattern>` with token substitution + sanitizing."""
    base = os.path.abspath(os.path.expanduser((library_folder or "").strip()))
    raw_pattern = (pattern or "").strip() or DEFAULT_LIBRARY_FOLDER_PATTERN
    parts = re.split(r"[/\\]+", raw_pattern)
    tokens = {
        "creator": (creator or "").strip(),
        "title": (title or "").strip(),
        "version": (version or "").strip(),
        "engine": (engine or "").strip(),
    }
    segments: list[str] = []
    for part in parts:
        substituted = part
        for key, value in tokens.items():
            substituted = substituted.replace("{" + key + "}", value)
        substituted = re.sub(r"\{[^{}]*\}", "", substituted)
        for sub in re.split(r"[/\\]+", substituted):
            cleaned = sanitize_path_segment(sub)
            if cleaned:
                segments.append(cleaned)
    if not segments:
        fallback = sanitize_path_segment(tokens["title"])
        segments.append(fallback or "game")
    if base:
        return os.path.join(base, *segments)
    return os.path.join(*segments)


def suggest_library_folder(
    old_exe: str,
    pattern: str,
    creator: str,
    title: str,
    version: str,
    engine: str,
    downloads_folder: str = "",
) -> str:
    """Library folder to offer for a game installed outside every library folder.

    Strips the folder pattern's segments from the previous install path, so
    `.../InProgress/Dev/Title/0.4/Game.exe` with `{creator}/{title}/{version}`
    gives `.../InProgress`. When the path does not follow the pattern, the
    parent of the executable's folder is used. A drive root, the home folder,
    and the Downloads folder are never suggested ("" then).
    """
    if not old_exe:
        return ""
    sentinel = os.path.abspath(os.sep + "wlib-base")
    relative = os.path.relpath(
        build_destination(sentinel, pattern, creator, title, version, engine), sentinel
    )
    segments = [part for part in re.split(r"[/\\]+", relative) if part and part != "."]
    exe_dir = os.path.dirname(os.path.abspath(old_exe))
    suggestion = ""
    if segments:
        current = exe_dir
        while True:
            parts = [p for p in re.split(r"[/\\]+", current) if p]
            if len(parts) > len(segments) and [
                p.casefold() for p in parts[-len(segments) :]
            ] == [seg.casefold() for seg in segments]:
                suggestion = current
                for _ in segments:
                    suggestion = os.path.dirname(suggestion)
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    if not suggestion:
        suggestion = os.path.dirname(exe_dir)
    suggestion_real = _real(suggestion)
    refused = {_real("~"), _real(os.path.join("~", "Downloads"))}
    if downloads_folder:
        refused.add(_real(downloads_folder))
    if _is_root(suggestion_real) or suggestion_real in refused:
        return ""
    return suggestion


def check_volume_set(volumes: VolumeSet) -> None:
    """Refuse a multi-part archive with parts missing, naming them."""
    if volumes.complete:
        return
    if volumes.ambiguous:
        names = ", ".join(os.path.basename(part) for part in volumes.ambiguous[:4])
        more = "…" if len(volumes.ambiguous) > 4 else ""
        raise ArchiveInstallError(
            f"Some parts of this multi-part archive have names that differ only in letter case ({names}{more}). Keep only one set of parts in the folder first."
        )
    numbers = ", ".join(str(n) for n in volumes.missing[:5])
    more = "…" if len(volumes.missing) > 5 else ""
    noun = "Part" if len(volumes.missing) == 1 else "Parts"
    verb = "is" if len(volumes.missing) == 1 else "are"
    raise ArchiveInstallError(
        f"{noun} {numbers}{more} of this multi-part archive {verb} missing. Download every part into the same folder first."
    )


def archive_format(archive_path: str) -> str:
    """'zip' | '7z' | 'rar' | 'tar' | '' based on the file extension.

    Split archives (`.7z.001`, `.zip.001`) and spanned zips (`.zip` with
    `.z01`...) need 7-Zip, so they report '7z'. Any part of a spanned zip
    or an old-style `.rar` + `.r00` set reports the format of its set, found
    the same letter-case-blind way as `volume_set`.
    """
    lower = (archive_path or "").lower()
    name = os.path.basename(lower)
    split = _SPLIT_RE.match(name)
    if split:
        return "rar" if split.group("base").endswith(".rar") else "7z"
    family = _archive_family(lower)
    if family == "zip" or (
        not family and (_ZIP_SPAN_RE.match(name) or _RAR_OLD_RE.match(name))
    ):
        volumes = volume_set(archive_path)
        if volumes is not None and volumes.kind == "zip_span":
            return "7z"
        if volumes is not None and volumes.kind == "rar_old":
            return "rar"
    return family


_PART_SUFFIX_RE = re.compile(r"\.part\d+$", re.IGNORECASE)


def archive_stem(archive_path: str) -> str:
    """File name without the archive extension or multi-part suffix.

    `Game-1.0.part1.rar` is the first part of `Game-1.0.rar` and
    `Game-1.0.7z.001` the first of `Game-1.0.7z`; the part number is not part
    of the game's name or version.
    """
    name = os.path.basename(str(archive_path or ""))
    split = _SPLIT_RE.match(name.lower())
    if split:
        name = name[: len(split.group("base"))]
    stem, suffix = _split_archive_name(name)
    if not suffix:
        stem = os.path.splitext(name)[0]
    return _PART_SUFFIX_RE.sub("", stem)


def _is_windows(platform: str | None = None) -> bool:
    if platform is not None:
        return platform.lower().startswith("win")
    return sys.platform.startswith("win")


def _writable_in_sandbox(folder: str) -> bool:
    """True when `folder`, or its nearest existing parent, is writable here.

    The library folder may not exist yet; installs create it.
    """
    current = os.path.abspath(os.path.expanduser(folder))
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent
    return os.access(current, os.W_OK)


def library_folder_status(path: str, is_default: bool) -> dict[str, object]:
    available = _writable_in_sandbox(path)
    free: int | None = None
    if available:
        parent = os.path.abspath(os.path.expanduser(path))
        while not os.path.exists(parent):
            parent = os.path.dirname(parent)
        try:
            free = shutil.disk_usage(parent).free
        except OSError:
            available = False
    return {
        "path": path,
        "is_default": is_default,
        "available": available,
        "free_bytes": free,
    }


def detect_archive_tool(
    archive_path: str,
    *,
    platform: str | None = None,
    library_folder: str = "",
    password: str = "",
) -> tuple[str | None, str | None]:
    """Return (tool, error) for an archive.

    `.zip` and tar archives need no tool → (None, None). Flatpak prefers its bundled 7zz
    when the archive and destination are accessible in the sandbox. Otherwise search
    `7zz`/`7z`/`7za`, then `bsdtar`, then `unrar` (rar only), plus the
    Windows `Program Files\\7-Zip` paths. Uses `host_which` and
    `os.path.isfile` so tests can monkeypatch them.
    """
    fmt = archive_format(archive_path)
    if fmt in ("zip", "tar"):
        return None, None
    if fmt not in ("7z", "rar"):
        return None, f"Unsupported archive format: {archive_path or '(none)'}"

    if (
        is_flatpak()
        and os.path.isfile(BUNDLED_7ZZ)
        and os.path.isfile(archive_path)
        and (not library_folder or _writable_in_sandbox(library_folder))
    ):
        return BUNDLED_7ZZ, None

    for candidate in ("7zz", "7z", "7za"):
        found = host_which(candidate)
        if found:
            return found, None
    # bsdtar opens one file; it cannot follow the parts of a multi-part set
    # and libarchive cannot decrypt password-protected .7z or .rar archives.
    multi_part = volume_set(archive_path) is not None
    bsdtar = None if multi_part else host_which("bsdtar")
    if bsdtar and not password:
        return bsdtar, None
    if fmt == "rar":
        found = host_which("unrar")
        if found:
            return found, None
    if _is_windows(platform):
        found = _windows_7zip()
        if found:
            return found, None
        return None, "Install 7-Zip to extract .7z/.rar archives."
    if password and bsdtar:
        return (
            None,
            "Install 7-Zip (7z) to extract password-protected .7z or .rar archives.",
        )
    if multi_part:
        return None, "Install 7-Zip (7z) to extract archives split into several parts."
    if fmt == "rar":
        return None, "Install 7-Zip (7z), bsdtar, or unrar to extract .rar archives."
    return None, "Install 7-Zip (7z) or bsdtar to extract .7z archives."


def _windows_7zip() -> str | None:
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for base in (program_files, program_files_x86):
        candidate = os.path.join(base, "7-Zip", "7z.exe")
        try:
            if os.path.isfile(candidate):
                return candidate
        except OSError:
            continue
    return None


def find_7zip_tool(
    archive_path: str, *, platform: str | None = None, library_folder: str = ""
) -> str | None:
    """A 7-Zip binary for zips Python can't inflate (Deflate64, LZMA, ...)."""
    if (
        is_flatpak()
        and os.path.isfile(BUNDLED_7ZZ)
        and os.path.isfile(archive_path)
        and (not library_folder or _writable_in_sandbox(library_folder))
    ):
        return BUNDLED_7ZZ
    for candidate in ("7zz", "7z", "7za"):
        found = host_which(candidate)
        if found:
            return found
    return _windows_7zip() if _is_windows(platform) else None


def _is_absolute_entry(entry: str) -> bool:
    normalized = entry.replace("\\", "/")
    if os.path.isabs(entry) or os.path.isabs(normalized):
        return True
    if normalized.startswith("/"):
        return True
    # Windows drive-absolute (C:/, C:\), drive-relative (C:foo), or UNC (//server).
    if re.match(r"^[a-zA-Z]:", entry) or re.match(r"^[a-zA-Z]:", normalized):
        return True
    return False


def check_archive_entry_safe(entry: str) -> None:
    """Raise ArchiveSafetyError for absolute paths, `..` components, or device names."""
    if not entry or entry.strip() == "":
        raise ArchiveSafetyError(f"Archive entry has an empty name: {entry!r}")
    if "\x00" in entry:
        raise ArchiveSafetyError(f"Archive entry contains null byte: {entry!r}")
    normalized = entry.replace("\\", "/")
    if _is_absolute_entry(entry) or _is_absolute_entry(normalized):
        raise ArchiveSafetyError(f"Archive entry is unsafe (absolute path): {entry!r}")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts:
        raise ArchiveSafetyError(f"Archive entry is unsafe: {entry!r}")
    if any(p == ".." for p in parts):
        raise ArchiveSafetyError(f"Archive entry is unsafe: {entry!r}")
    # Windows strips trailing dots and spaces from every path component before
    # it resolves `..`, so ".. " (or "..." / ".. .") leaves the destination
    # just like "..". Reject components made only of dots and spaces.
    if any(not p.strip(". ") for p in parts):
        raise ArchiveSafetyError(f"Archive entry is unsafe: {entry!r}")
    for part in parts:
        # A colon starts a device stream on Windows (CON:foo, COM1:data), so
        # the reserved-name check must look at the part before it as well.
        base = part.split(".")[0].split(":")[0].rstrip(" ").upper()
        if base in _WINDOWS_RESERVED_NAMES:
            raise ArchiveSafetyError(
                f"Archive entry uses reserved device name: {entry!r}"
            )


def _target_inside_staging(staging: str, entry: str) -> str:
    normalized = entry.replace("\\", "/")
    # Strip a trailing slash for directories; join handles the rest.
    target = os.path.abspath(os.path.join(staging, normalized))
    staging_real = os.path.abspath(staging)
    if target != staging_real and not target.startswith(staging_real + os.sep):
        raise ArchiveSafetyError(f"Archive entry is unsafe: {entry!r}")
    return target


def build_host_open_env() -> dict[str, str]:
    """Env for archive subprocesses (no AppImage library paths)."""
    clean_env: dict[str, str] = os.environ.copy()
    for var in (
        "APPIMAGE",
        "APPDIR",
        "ARGV0",
        "APPIMAGE_SILENT_INSTALL",
        "OWD",
        "APPIMAGE_EXTRACT_AND_RUN",
    ):
        _ = clean_env.pop(var, None)
    original_library_path = str(clean_env.get("LD_LIBRARY_PATH_ORIG") or "").strip()
    if original_library_path:
        clean_env["LD_LIBRARY_PATH"] = original_library_path
    else:
        _ = clean_env.pop("LD_LIBRARY_PATH", None)
    return clean_env


def _unescape_mountinfo(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def filesystem_type(path: str, *, mountinfo: str = "/proc/self/mountinfo") -> str:
    """Filesystem type of the mount holding `path` ("" when unknown).

    Uses the deepest mount point containing the nearest existing ancestor, so
    a library folder that does not exist yet still resolves to its drive.
    """
    if _is_windows():
        return ""
    probe = os.path.abspath(os.path.expanduser(path or ""))
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    probe = os.path.realpath(probe)
    best, best_type = "", ""
    try:
        with open(mountinfo, encoding="utf-8", errors="replace") as f:
            for line in f:
                pre, sep, post = line.partition(" - ")
                fields = pre.split()
                if not sep or len(fields) < 5 or not post.split():
                    continue
                mount_point = _unescape_mountinfo(fields[4])
                if mount_point != "/" and not (
                    probe == mount_point
                    or probe.startswith(mount_point.rstrip("/") + "/")
                ):
                    continue
                # Later lines are mounted over earlier ones at the same point.
                if len(mount_point) >= len(best):
                    best, best_type = mount_point, post.split()[0]
    except OSError:
        return ""
    return best_type


def is_network_filesystem(path: str) -> bool:
    return filesystem_type(path).lower() in NETWORK_FS_TYPES


def usable_cpu_count() -> int:
    """CPU cores this process may run on (affinity-aware on Linux)."""
    if sys.platform == "linux":
        try:
            return max(1, len(os.sched_getaffinity(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def _fchmod(fd: int, mode: int) -> None:
    """os.fchmod, which Windows Python 3.12 lacks (callers skip Windows)."""
    if sys.platform != "win32":
        os.fchmod(fd, mode)


def _windows_name_key(target: str) -> str:
    """The file Windows actually opens for `target`.

    Windows matches names case-insensitively, drops trailing dots and spaces
    from each component and treats `name:stream` as a stream of `name`, so
    entries that differ only that way land on one file.
    """
    parts = target.replace("\\", "/").split("/")
    return "/".join(p.split(":")[0].rstrip(". ").casefold() for p in parts)


def zip_extract_workers(network: bool) -> int:
    """Parallel zip writers: one per usable core.

    Network shares keep at least NETWORK_IO_WORKERS, because there the
    workers mostly wait on server round trips rather than use a core.
    """
    workers = min(usable_cpu_count(), ZIP_MAX_WORKERS)
    return max(workers, NETWORK_IO_WORKERS) if network else workers


def _exec_mode(name: str, head: bytes, mode: int) -> int:
    """apply_execute_bits()'s rule, decided from bytes already in hand."""
    if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        return mode
    lower = os.path.basename(name).lower()
    if not (lower.endswith((".sh", ".x86", ".x86_64")) or head[:4] == b"\x7fELF"):
        return mode
    new_mode = mode | stat.S_IXUSR
    if mode & stat.S_IRGRP:
        new_mode |= stat.S_IXGRP
    if mode & stat.S_IROTH:
        new_mode |= stat.S_IXOTH
    return new_mode


def _empty_dir(path: str) -> None:
    """Remove everything inside `path`, keeping the folder itself."""
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _interleave_by_folder[T](items: list[T], key: Callable[[T], str]) -> list[T]:
    """Round-robin across folders.

    The kernel serialises creates inside one folder, so parallel workers that
    all write into the same folder would queue on its lock.
    """
    buckets: dict[str, list[T]] = {}
    for item in items:
        buckets.setdefault(os.path.dirname(key(item)), []).append(item)
    queues = [list(reversed(b)) for b in buckets.values()]
    out: list[T] = []
    while queues:
        for q in queues:
            out.append(q.pop())
        queues = [q for q in queues if q]
    return out


def _run_parallel[T](func: Callable[[T], None], items: list[T], workers: int) -> None:
    """Run func over items; the first failure stops new work and is raised."""
    if workers <= 1 or len(items) <= 1:
        for item in items:
            func(item)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(func, item) for item in items]
        done, pending = wait(futures, return_when=FIRST_EXCEPTION)
        for fut in pending:
            _ = fut.cancel()
        for fut in done:
            exc = fut.exception()
            if exc is not None:
                raise exc


def copy_tree_parallel(
    src_root: str,
    dst_root: str,
    workers: int,
    cancel: threading.Event | None = None,
    on_bytes: Callable[[int], None] | None = None,
    *,
    hard_links: bool = False,
) -> int:
    """Copy an extracted tree into an existing empty folder.

    Modes are set when each file is created (no separate chmod round trip)
    and modification times are kept, as direct extraction would leave them.
    Symlinks are recreated as links; install_archive() re-checks them in
    staging afterwards. Returns the number of files copied.

    `on_bytes(n)`, when given, is called from the worker threads after each
    chunk is written, with the size of that chunk.

    With `hard_links`, names in the tree that share one file are linked again
    in the copy, as direct extraction would leave them, so the copy takes no
    more room than the source. A name that cannot be linked is copied.
    """
    dirs: list[str] = []
    files: list[str] = []
    # Later names of a hard-linked file -> the first name, which is copied.
    linked: list[tuple[str, str]] = []
    first_names: dict[tuple[int, int], str] = {}

    def _walk_failed(exc: OSError) -> None:
        # A folder that cannot be listed would arrive empty; a move then
        # deletes the original, so stop instead.
        raise exc

    for root, dirnames, filenames in os.walk(src_root, onerror=_walk_failed):
        rel_root = os.path.relpath(root, src_root)
        rel_root = "" if rel_root == "." else rel_root
        for d in dirnames:
            path = os.path.join(root, d)
            if os.path.islink(path):
                files.append(os.path.join(rel_root, d))
            else:
                dirs.append(os.path.join(rel_root, d))
        for f in filenames:
            rel = os.path.join(rel_root, f)
            if hard_links:
                st = os.lstat(os.path.join(root, f))
                if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                    first = first_names.setdefault((st.st_dev, st.st_ino), rel)
                    if first != rel:
                        linked.append((rel, first))
                        continue
            files.append(rel)
    for rel in dirs:  # os.walk is top-down, so parents come first
        mode = stat.S_IMODE(os.lstat(os.path.join(src_root, rel)).st_mode)
        os.mkdir(os.path.join(dst_root, rel), mode | stat.S_IRWXU)

    def _copy(rel: str) -> None:
        _check_cancel(cancel)
        src = os.path.join(src_root, rel)
        dst = os.path.join(dst_root, rel)
        st = os.lstat(src)
        if stat.S_ISLNK(st.st_mode):
            os.symlink(os.readlink(src), dst)
            return
        if not stat.S_ISREG(st.st_mode):
            return  # extractors don't produce devices or FIFOs we want to keep
        mode = stat.S_IMODE(st.st_mode)
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, mode)
        try:
            with open(src, "rb") as f:
                while chunk := f.read(_COPY_CHUNK):
                    _write_all(fd, chunk)
                    if on_bytes is not None:
                        on_bytes(len(chunk))
            # Shares that don't store modes or times (some SMB, rclone) may
            # refuse these; 7-Zip extracting there ignores that too.
            try:
                if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
                    _fchmod(fd, mode)  # create mode is filtered by the umask
                if _UTIME_FD:
                    os.utime(fd, ns=(st.st_atime_ns, st.st_mtime_ns))
            except OSError:
                pass
        finally:
            os.close(fd)
        if not _UTIME_FD:  # Windows can't set times through a file descriptor
            try:
                os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
            except OSError:
                pass

    _run_parallel(_copy, _interleave_by_folder(files, key=lambda r: r), workers)
    for rel, first in linked:
        _check_cancel(cancel)
        try:
            os.link(os.path.join(dst_root, first), os.path.join(dst_root, rel))
        except OSError:
            _copy(rel)  # the share cannot store hard links
    # Folder times last: creating their entries above bumped them.
    for rel in reversed(dirs):
        st = os.lstat(os.path.join(src_root, rel))
        try:
            os.utime(os.path.join(dst_root, rel), ns=(st.st_atime_ns, st.st_mtime_ns))
        except OSError:
            pass
    return len(files) + len(linked)


_trash_lock = threading.Lock()
_trash_in_flight: set[str] = set()


def _start_trash_delete(trash: str) -> None:
    """Delete a trash folder on a daemon thread, at most once at a time."""
    with _trash_lock:
        if trash in _trash_in_flight:
            return
        _trash_in_flight.add(trash)

    def _delete() -> None:
        try:
            errors = remove_tree_collect(trash)
            if errors:
                _log.warning(
                    "Could not fully remove old version %s: %s", trash, errors[0]
                )
        finally:
            with _trash_lock:
                _trash_in_flight.discard(trash)

    threading.Thread(target=_delete, name="wlib-trash", daemon=True).start()


def defer_remove_tree(path: str, trash_root: str) -> bool:
    """Rename `path` into `trash_root` and delete it on a background thread.

    Returns False (nothing renamed) when the rename is impossible, e.g. the
    folders sit on different filesystems; callers then delete synchronously.
    Leftovers from an interrupted delete are swept by sweep_trash().
    """
    if not trash_root:
        return False
    trash = os.path.join(trash_root, f"{TRASH_PREFIX}{time.time_ns()}")
    try:
        os.rename(path, trash)
    except OSError:
        return False
    _start_trash_delete(trash)
    return True


def sweep_trash(library_folder: str) -> int:
    """Restart background deletes of `.wlib-trash-<ns>` folders left by a crash.

    Only names that wLib makes (the prefix and a 19-digit time) count; other
    folders that start with the prefix are not wLib's and are kept.
    """
    started = 0
    try:
        base = os.path.abspath(os.path.expanduser(library_folder))
        if not os.path.isdir(base):
            return 0
        for name in os.listdir(base):
            if not _TRASH_NAME_RE.fullmatch(name):
                continue
            candidate = os.path.join(base, name)
            if os.path.isdir(candidate) and not os.path.islink(candidate):
                _start_trash_delete(candidate)
                started += 1
    except OSError:
        return started
    return started


def trash_root_for(path: str, library_folders: Sequence[str]) -> str:
    """Deepest library folder holding `path` ("" when none does).

    Trash only goes where sweep_trash() looks, so an old version outside every
    library folder is deleted synchronously instead.
    """
    real = _real(path)
    best = ""
    for folder in library_folders:
        folder_real = _real(folder) if folder else ""
        if (
            folder_real
            and real != folder_real
            and _is_within(real, folder_real)
            and len(folder_real) > len(best)
        ):
            best = folder_real
    return best


def _scratch_parent() -> str:
    from core.host_platform import get_data_subdir

    return get_data_subdir("install-scratch")


def _sweep_stale_scratch(parent: str) -> None:
    """Remove scratch left behind by a crashed install a day or more ago."""
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        if not name.startswith(SCRATCH_PREFIX):
            continue
        old = os.path.join(parent, name)
        try:
            stale = time.time() - os.lstat(old).st_mtime > 86400
        except OSError:
            continue
        if stale:
            cleanup_staging_dir(old)


def _scratch_free_bytes(parent: str) -> int | None:
    try:
        return shutil.disk_usage(parent).free
    except OSError:
        return None


def _extract_tool_via_local_scratch(
    extract: Callable[[str, threading.Event], object],
    archive_path: str,
    staging: str,
    workers: int,
    required_bytes: int | None,
    cancel: threading.Event | None = None,
) -> bool:
    """Run `extract(folder, stop)` into local scratch, then copy into staging.

    Used for the extractors that write one file at a time (7-Zip, unrar and
    tarfile), which are slow on a network share. `extract` must stop with
    InstallCancelled when `stop` is set.

    Returns False without extracting when local disk can't hold the archive;
    the caller then extracts straight into staging. An archive whose unpacked
    size is unknown is budgeted at a multiple of its own size. That budget
    is only a guess, so extraction is stopped when local disk falls below
    the reserve (or fills up); staging is still empty then, and False sends
    the caller to direct extraction too.
    """
    parent = _scratch_parent()
    try:
        needed = required_bytes
        if not needed or needed <= 0:
            needed = os.path.getsize(archive_path) * SCRATCH_UNKNOWN_SIZE_FACTOR
        os.makedirs(parent, exist_ok=True)
        _sweep_stale_scratch(parent)
        if shutil.disk_usage(parent).free < needed + SCRATCH_RESERVE_BYTES:
            return False
        scratch = tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=parent)
    except (OSError, ArchiveInstallError):
        return False
    stop = threading.Event()
    low_space = threading.Event()
    done = threading.Event()

    def _watch() -> None:
        while not done.wait(SCRATCH_WATCH_SECONDS):
            if cancel is not None and cancel.is_set():
                stop.set()
                return
            free = _scratch_free_bytes(parent)
            if free is not None and free < SCRATCH_RESERVE_BYTES:
                low_space.set()
                stop.set()
                return

    watcher = threading.Thread(target=_watch, name="wlib-scratch-watch", daemon=True)
    try:
        watcher.start()
        try:
            _ = extract(scratch, stop)
        except ArchiveSafetyError:
            raise
        except ArchiveInstallError:
            _check_cancel(cancel)
            free = _scratch_free_bytes(parent)
            if not low_space.is_set() and (
                free is None or free >= SCRATCH_RESERVE_BYTES
            ):
                raise
            _log.warning(
                "Local install scratch ran low on space; extracting %s on the library drive instead",
                os.path.basename(archive_path),
            )
            return False
        finally:
            done.set()
            watcher.join()
        try:
            _ = apply_execute_bits(scratch)
        except OSError:
            pass
        _check_cancel(cancel)
        _ = copy_tree_parallel(scratch, staging, workers, cancel, hard_links=True)
    finally:
        cleanup_staging_dir(scratch)
    return True


def _zip_raw_name(info: zipfile.ZipInfo) -> bytes | None:
    """Stored name bytes of an entry whose name zipfile read as cp437.

    None when the name needs no second decoding: the UTF-8 flag (bit 11) is
    set, or a valid Info-ZIP Unicode Path extra field (0x7075) gave zipfile
    the real name already. That name can hold letters cp437 does not have,
    so it must never be encoded back to cp437.
    """
    if info.flag_bits & 0x800:
        return None
    raw = info.orig_filename.encode("cp437")
    extra = info.extra
    while len(extra) >= 4:
        kind = int.from_bytes(extra[0:2], "little")
        size = int.from_bytes(extra[2:4], "little")
        data = extra[4 : 4 + size]
        if (
            kind == 0x7075
            and len(data) >= 5
            and data[0] == 1
            and int.from_bytes(data[1:5], "little") == zlib.crc32(raw)
        ):
            return None
        extra = extra[4 + size :]
    # zipfile also ends the name at the first null byte.
    return raw.split(b"\0", 1)[0]


def _zip_name_encoding(infos: list[zipfile.ZipInfo]) -> str:
    """Encoding of the names stored without the UTF-8 flag (bit 11).

    zipfile reads those as cp437. Most such archives were made with UTF-8
    names anyway; Japanese Windows tools write Shift-JIS (cp932). One encoding
    is chosen for the whole archive so a cp437 name is never half-converted.
    """
    raw = [
        name
        for info in infos
        if (name := _zip_raw_name(info)) is not None and not name.isascii()
    ]
    for encoding in ("utf-8", "cp932"):
        try:
            for name in raw:
                _ = name.decode(encoding)
        except UnicodeError:
            continue
        return encoding
    return "cp437"


def _zip_entry_name(info: zipfile.ZipInfo, encoding: str = "utf-8") -> str:
    """Entry name, decoding names stored without the UTF-8 flag with `encoding`."""
    name = info.filename
    raw = _zip_raw_name(info) if encoding != "cp437" else None
    if raw is not None:
        try:
            name = raw.decode(encoding)
        except UnicodeError:
            pass
    return name.replace("\\", "/")


def extract_zip(
    archive_path: str,
    staging_dir: str,
    *,
    workers: int = 1,
    exec_bits: bool = False,
    cancel: threading.Event | None = None,
    password: str = "",
) -> int:
    """Extract a zip into staging with per-entry validation.

    Returns the number of entries extracted. Raises ArchiveSafetyError for
    traversal entries and ArchiveInstallError for corrupt or encrypted archives.

    Each file's mode is set as it is created rather than with a later chmod.
    With `exec_bits`, the apply_execute_bits() rule is applied from the bytes
    being written, so no second pass has to reopen every file. `workers` > 1
    inflates and writes files concurrently, which uses spare cores locally and
    hides per-file latency on network filesystems.
    """
    os.makedirs(staging_dir, exist_ok=True)
    count = 0
    encrypted = False
    pwd_bytes = password.encode("utf-8") if password else None
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            infos = zf.infolist()
            name_encoding = _zip_name_encoding(infos)
            # Pre-validate every entry before writing anything.
            names: list[str] = []
            for info in infos:
                if info.flag_bits & 0x1:
                    encrypted = True
                    if not password:
                        raise ArchivePasswordRequired()
                name = _zip_entry_name(info, name_encoding)
                if name == "" or (name.endswith("/") and name.strip("/") == ""):
                    raise ArchiveSafetyError(f"Archive entry is unsafe: {name!r}")
                check_archive_entry_safe(name)
                _ = _target_inside_staging(staging_dir, name)
                names.append(name)
            check_archive_entries_no_collisions(names)
            # Folders first (sorted, so parents precede children), then files.
            folders: set[str] = set()
            files: list[tuple[zipfile.ZipInfo, str]] = []
            for info, name in zip(infos, names, strict=True):
                target = _target_inside_staging(staging_dir, name)
                if name.endswith("/"):
                    folders.add(target)
                    count += 1
                    continue
                folders.add(os.path.dirname(target) or staging_dir)
                files.append((info, target))
            folder_keys = {os.path.normcase(folder).casefold() for folder in folders}
            for _info, target in files:
                if os.path.normcase(target).casefold() in folder_keys:
                    name = os.path.relpath(target, staging_dir)
                    raise ArchiveSafetyError(
                        f"Archive has a file and a folder with the same name: {name!r}"
                    )
            for folder in sorted(folders):
                os.makedirs(folder, exist_ok=True)

            windows = _is_windows()
            if windows and workers > 1:
                # Entries the collision check allows can still name one file
                # on Windows ("a.txt" and "a.txt."). Writing those from two
                # threads would interleave them; one worker keeps the old
                # last-one-wins result.
                keys = {_windows_name_key(target) for _info, target in files}
                if len(keys) != len(files):
                    workers = 1
            # All workers share one ZipFile: reopening it per thread would
            # parse the whole central directory again in every worker. Reads
            # of the shared file are serialized by zipfile's own lock, and
            # inflating runs in parallel. Opening and closing members update
            # an unlocked reference count, so those two steps take this lock.
            member_lock = threading.Lock()

            def _write(item: tuple[zipfile.ZipInfo, str]) -> None:
                _check_cancel(cancel)
                info, target = item
                mode = (info.external_attr >> 16) & 0o7777
                # Symlink entries are written as plain files holding the link
                # text, so a zip can't plant links (deliberate, see design).
                with member_lock:
                    src = zf.open(info, "r", pwd=pwd_bytes)
                try:
                    chunk = src.read(_COPY_CHUNK)
                    head = chunk[:4]
                    fd = os.open(
                        target,
                        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BINARY,
                        mode if (mode and not windows) else 0o666,
                    )
                    try:
                        while chunk:
                            _write_all(fd, chunk)
                            # A cancel also stops inside one large member.
                            _check_cancel(cancel)
                            chunk = src.read(_COPY_CHUNK)
                        if not windows:
                            current = stat.S_IMODE(os.fstat(fd).st_mode)
                            wanted = mode or current
                            if exec_bits:
                                wanted = _exec_mode(target, head, wanted)
                            if wanted != current:
                                try:
                                    _fchmod(fd, wanted)
                                except OSError:
                                    pass
                    finally:
                        os.close(fd)
                finally:
                    with member_lock:
                        src.close()

            order = (
                _interleave_by_folder(files, key=lambda it: it[1])
                if workers > 1
                else files
            )
            _run_parallel(_write, order, workers)
            count += len(files)
    except NotImplementedError as exc:
        detail = _sanitize_error_detail(str(exc), password)
        raise ZipMethodUnsupportedError(
            f"Archive uses a compression method wLib can't read: {detail}"
        ) from exc
    except (zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
        msg = str(exc)
        if _PASSWORD_ERROR_RE.search(msg):
            raise _password_failure(password) from exc
        if encrypted and password:
            raise ArchivePasswordWrong() from exc
        detail = _sanitize_error_detail(msg, password)
        raise ArchiveInstallError(
            f"Archive is corrupt or incomplete: {detail}"
        ) from exc
    check_symlinks_inside_staging(staging_dir)
    return count


class _TarReader(io.RawIOBase):
    """The compressed archive file as tarfile reads it.

    Each read reports (compressed bytes read, archive size) and stops with
    InstallCancelled once `cancel` is set, so a cancel works inside one large
    member too.
    """

    def __init__(
        self,
        raw: io.BufferedReader,
        size: int,
        progress: Callable[[int, int], None] | None,
        cancel: threading.Event | None,
    ) -> None:
        super().__init__()
        self._raw: io.BufferedReader = raw
        self._size: int = size
        self._progress: Callable[[int, int], None] | None = progress
        self._cancel: threading.Event | None = cancel
        self._done: int = 0
        self._last_report: float = 0.0

    @override
    def readable(self) -> bool:
        return True

    @override
    def readinto(self, buffer: memoryview | bytearray) -> int:  # pyright: ignore[reportIncompatibleMethodOverride]
        _check_cancel(self._cancel)
        count = self._raw.readinto(buffer)
        self._done += count
        if self._progress is not None:
            now = time.monotonic()
            if self._done >= self._size or now - self._last_report >= 0.5:
                self._last_report = now
                try:
                    self._progress(min(self._done, self._size), self._size)
                except Exception:
                    pass
        return count


def _tar_codec_error(archive_path: str) -> str:
    """The error for a compressed tar whose codec this Python lacks, or ""."""
    suffix = archive_suffix(archive_path)
    module = TAR_COMPRESSION_MODULES.get(suffix)
    if not module:
        return ""
    try:
        _ = importlib.import_module(module)
    except ImportError:
        return f"This build of wLib cannot read {suffix} archives."
    return ""


def _tar_decompressor(raw: io.RawIOBase, magic: bytes) -> io.BufferedIOBase | None:
    """A checksum-checking reader for a gzip, bzip2 or xz tar, or None.

    tarfile's stream mode never checks the gzip CRC and stops at the tar end
    marker, before a bzip2 or xz stream's own check. These readers verify the
    check when they are read to the end, which extract_tar() does. None means
    plain tar, or a codec this Python lacks (tarfile then reports it).
    """
    # Each BufferedReader is made only when used: dropping one closes `raw`.
    try:
        if magic.startswith(b"\x1f\x8b"):
            import gzip

            return gzip.GzipFile(fileobj=io.BufferedReader(raw, _COPY_CHUNK), mode="rb")
        if magic.startswith(b"BZh"):
            import bz2

            return bz2.BZ2File(io.BufferedReader(raw, _COPY_CHUNK), "rb")
        if magic.startswith(b"\xfd7zXZ\x00"):
            import lzma

            return lzma.LZMAFile(io.BufferedReader(raw, _COPY_CHUNK), "rb")
    except ImportError:
        return None
    return None


def _disk_full(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in (
        errno.ENOSPC,
        getattr(errno, "EDQUOT", -1),
    )


_DISK_FULL_MESSAGE = "Not enough free space: the library drive filled up while the archive was extracted."


# The parameter of TarInfo.fromtarfile hides the module name inside it.
_TarHeaderError = tarfile.HeaderError
_TarReadError = tarfile.ReadError


class _StrictTarInfo(tarfile.TarInfo):
    """Fail on a broken member header instead of ending the archive there.

    After the first member, tarfile takes a header with a bad checksum or a
    cut-off header as the end of the archive, so the extraction would report
    success with only the earlier members. Zero blocks and the end of the
    data still end the archive normally.
    """

    @classmethod
    @override
    def fromtarfile(cls, tarfile: tarfile.TarFile) -> Self:
        try:
            return super().fromtarfile(tarfile)
        except _TarHeaderError as exc:
            # The subclasses are not in the type stubs.
            if type(exc).__name__ in ("InvalidHeaderError", "TruncatedHeaderError"):
                raise _TarReadError(
                    f"Archive has a broken member header: {exc}"
                ) from exc
            raise


def extract_tar(
    archive_path: str,
    staging_dir: str,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> int:
    """Extract a tar archive (plain, gzip, bzip2 or xz) into staging.

    Reads the archive once, in stream mode, so a compressed archive is not
    decompressed again for each member. Each member is checked before it is
    written: the same name rules as the other formats, no two members that
    land on one file, no special files, and hard links only to a file that
    was extracted earlier. The tarfile "data" filter then refuses links out
    of staging and clears setuid and group/other write bits. Returns the
    number of members extracted.
    """
    codec_error = _tar_codec_error(archive_path)
    if codec_error:
        raise ArchiveInstallError(codec_error)
    os.makedirs(staging_dir, exist_ok=True)
    windows = _is_windows()
    real_staging = os.path.realpath(staging_dir)

    def _resolved_key(path: str) -> str:
        # Key by where the member lands after earlier symlinks in staging
        # are followed (the data filter allows links that stay inside), so
        # "d/f" and "link-to-d/f" collide. The last part is not followed.
        target = os.path.join(staging_dir, *path.split("/"))
        parent = os.path.realpath(os.path.dirname(target))
        landed = os.path.join(parent, os.path.basename(target))
        rel = os.path.relpath(landed, real_staging).replace(os.sep, "/")
        return _windows_name_key(rel) if windows else rel

    # Key -> True for a folder, False for anything else.
    seen: dict[str, bool] = {}
    files: set[str] = set()
    # Symlinks Windows refused, made as copies once every member is out.
    copies: list[tuple[str, str]] = []
    count = 0
    try:
        size = os.path.getsize(archive_path)
        with open(archive_path, "rb") as raw:
            magic = raw.read(6)
            _ = raw.seek(0)
            reader = _TarReader(raw, size, progress, cancel)
            unpacked = _tar_decompressor(reader, magic)
            source: io.IOBase = unpacked if unpacked is not None else reader
            tar_mode = "r|" if unpacked is not None else "r|*"
            with tarfile.open(
                fileobj=source, mode=tar_mode, tarinfo=_StrictTarInfo
            ) as tf:
                for member in tf:
                    _check_cancel(cancel)
                    name = member.name
                    # `tar -C dir .` stores the root itself as "./"; it names
                    # staging, so there is nothing to write for it.
                    if member.isdir() and not [
                        p
                        for p in name.replace("\\", "/").split("/")
                        if p not in ("", ".")
                    ]:
                        continue
                    check_archive_entry_safe(name)
                    _ = _target_inside_staging(staging_dir, name)
                    path = posixpath.normpath(name.replace("\\", "/"))
                    key = _resolved_key(path)
                    is_dir = member.isdir()
                    earlier = seen.get(key)
                    if earlier is not None and not (earlier and is_dir):
                        raise ArchiveSafetyError(
                            f"Archive has duplicate or colliding entry: {name!r}"
                        )
                    seen[key] = is_dir
                    if not (
                        is_dir or member.isreg() or member.issym() or member.islnk()
                    ):
                        raise ArchiveSafetyError(
                            f"Archive entry is a device or special file: {name!r}"
                        )
                    if member.islnk():
                        link = member.linkname
                        check_archive_entry_safe(link)
                        link_path = posixpath.normpath(link.replace("\\", "/"))
                        link_key = _resolved_key(link_path)
                        if link_key not in files:
                            raise ArchiveSafetyError(
                                f"Archive hard link does not point to an earlier file in the archive: {name!r}"
                            )
                    if member.isreg() or member.islnk():
                        files.add(key)
                    if windows and member.issym():
                        _extract_windows_symlink(member, staging_dir, copies)
                    else:
                        tf.extract(member, staging_dir, filter="data")
                    count += 1
            if unpacked is not None:
                # Read to the end so the gzip CRC (or bzip2/xz check) is
                # verified; a corrupt archive must not install.
                while unpacked.read(_COPY_CHUNK):
                    pass
        _copy_refused_symlinks(copies, staging_dir)
    except tarfile.FilterError as exc:
        raise ArchiveSafetyError(f"Archive entry is unsafe: {exc}") from exc
    except ArchiveInstallError:
        raise
    except tarfile.CompressionError as exc:
        raise ArchiveInstallError(
            f"This build of wLib cannot read this archive's compression: {exc}"
        ) from exc
    except OSError as exc:
        if _disk_full(exc):
            raise ArchiveInstallError(_DISK_FULL_MESSAGE) from exc
        raise ArchiveInstallError(f"Extraction failed: {exc}") from exc
    except (tarfile.TarError, EOFError, KeyError, zlib.error, ValueError) as exc:
        raise ArchiveInstallError(f"Extraction failed: {exc}") from exc
    except Exception as exc:
        # lzma.LZMAError; lzma itself may be missing from the build.
        if type(exc).__name__ == "LZMAError":
            raise ArchiveInstallError(f"Extraction failed: {exc}") from exc
        raise
    check_symlinks_inside_staging(staging_dir)
    return count


def _extract_windows_symlink(
    member: tarfile.TarInfo, staging_dir: str, copies: list[tuple[str, str]]
) -> None:
    """Make a tar symlink on Windows, or note it to copy later.

    Windows makes symlinks only for an admin or in Developer Mode. tarfile
    then copies the link target, which a stream read cannot reach again
    ("seeking backwards is not allowed"). The data filter checks the link
    first, as for the other members.
    """
    checked = tarfile.data_filter(member, staging_dir)
    target = os.path.join(staging_dir, *checked.name.replace("\\", "/").split("/"))
    if os.path.lexists(target):
        raise ArchiveSafetyError(
            f"Archive has duplicate or colliding entry: {member.name!r}"
        )
    os.makedirs(os.path.dirname(target), exist_ok=True)
    link = checked.linkname.replace("/", os.sep)
    source = os.path.normpath(os.path.join(os.path.dirname(target), link))
    try:
        os.symlink(link, target, target_is_directory=os.path.isdir(source))
    except OSError:
        copies.append((target, source))


def _copy_refused_symlinks(copies: list[tuple[str, str]], staging_dir: str) -> None:
    """Copy each refused symlink's target to the link's place.

    Repeats while copies succeed, so a link to a link that is copied later
    still gets its file. A link whose target is not in the archive is left
    out, as it would point nowhere.
    """
    staging_real = os.path.realpath(staging_dir)
    pending = list(copies)
    while pending:
        waiting: list[tuple[str, str]] = []
        for target, source in pending:
            source_real = os.path.realpath(source)
            if not is_within_real(source_real, staging_real):
                raise ArchiveSafetyError(
                    f"Archive link points outside the archive: {target!r}"
                )
            target_real = os.path.realpath(target)
            if os.path.isfile(source_real):
                if os.path.lexists(target):
                    raise ArchiveSafetyError(
                        f"Archive has duplicate or colliding entry: {target!r}"
                    )
                _ = shutil.copy2(source_real, target)
            elif os.path.isdir(source_real):
                if is_within_real(target_real, source_real) or is_within_real(
                    source_real, target_real
                ):
                    raise ArchiveSafetyError(
                        f"Archive link points into itself: {target!r}"
                    )
                # Later members may have gone "through" the link name into a
                # real folder; both names then get every file, as with a link.
                written_through = os.path.isdir(target)
                _ = shutil.copytree(
                    source_real, target, symlinks=True, dirs_exist_ok=True
                )
                if written_through:
                    _ = shutil.copytree(
                        target, source_real, symlinks=True, dirs_exist_ok=True
                    )
            else:
                waiting.append((target, source))
        if len(waiting) == len(pending):
            for target, _source in waiting:
                _log.warning(
                    "Archive link %s points to no file; it was left out", target
                )
            return
        pending = waiting


_PASSWORD_ERROR_RE = re.compile(
    r"wrong\s+password|incorrect\s+password|bad\s+password|encrypted|password|passphrase",
    re.I,
)


def _sanitize_error_detail(detail: str, password: str) -> str:
    if password and password in detail:
        return "command failed"
    return detail


def _password_failure(password: str) -> ArchiveInstallError:
    if password:
        return ArchivePasswordWrong()
    return ArchivePasswordRequired()


def _tool_failure(
    completed: subprocess.CompletedProcess[str],
    what: str,
    *,
    password: str = "",
) -> ArchiveInstallError:
    raw_detail = (
        (completed.stderr or "").strip()
        or (completed.stdout or "").strip()
        or f"exit {completed.returncode}"
    )
    if _PASSWORD_ERROR_RE.search(raw_detail):
        return _password_failure(password)
    detail = _sanitize_error_detail(raw_detail, password)
    return ArchiveInstallError(f"{what} ({detail})")


def _tool_basename(tool: str) -> str:
    return os.path.basename(tool).lower()


def _tool_argv(
    tool: str, args: Sequence[str], *, env: dict[str, str] | None = None
) -> list[str]:
    """Run the bundled extractor in the sandbox and other tools on the host."""
    cmd = [tool, *args]
    return cmd if tool == BUNDLED_7ZZ else host_command(cmd, env=env)


# 7-Zip builds that refused -sccUTF-8 (very old releases): run them without it.
_NO_UTF8_SWITCH: set[str] = set()
_SEVEN_ZIP_COMMAND_LINE_ERROR = 7


def _windows_seven_zip(tool: str) -> bool:
    base = _tool_basename(tool)
    return (
        _is_windows()
        and "7z" in base
        and "unrar" not in base
        and tool not in _NO_UTF8_SWITCH
    )


def _tool_args(tool: str, args: Sequence[str]) -> list[str]:
    """Make 7-Zip on Windows print names as UTF-8.

    Its console output is otherwise in the OEM code page, where names like
    'ア.txt' and 'イ.txt' both become '?.txt' and look like duplicates.
    """
    if not args or not _windows_seven_zip(tool):
        return list(args)
    return [args[0], "-sccUTF-8", *args[1:]]


def _tool_encoding(tool: str) -> str | None:
    return "utf-8" if _windows_seven_zip(tool) else None


def _run_tool(
    tool: str, args: Sequence[str], env: dict[str, str] | None
) -> subprocess.CompletedProcess[str]:
    """Run an archive tool and decode its output; undecodable bytes never raise."""

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            _tool_argv(tool, _tool_args(tool, args), env=env),
            capture_output=True,
            encoding=_tool_encoding(tool),
            errors="replace",
            check=False,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=no_console_window(),
        )

    completed = run()
    if completed.returncode == _SEVEN_ZIP_COMMAND_LINE_ERROR and _windows_seven_zip(
        tool
    ):
        # A 7-Zip too old to know -scc rejects the whole command line.
        _NO_UTF8_SWITCH.add(tool)
        completed = run()
    return completed


def _seven_zip_member_lines(output: str) -> list[str]:
    """`7z l -slt` lines that describe members, not the archive itself.

    7-Zip prints one header block per archive layer (a split set has two,
    both naming the inner archive) before a line of ten dashes.
    """
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "----------":
            return lines[index + 1 :]
    return lines


def _unrar_links(
    tool: str,
    archive_path: str,
    env: dict[str, str] | None,
    *,
    password: str = "",
) -> list[tuple[str, str, bool]]:
    """Best-effort link list from `unrar lt` (Name/Type/Target blocks)."""
    pw_arg = f"-p{password}" if password else "-p-"
    try:
        completed = _run_tool(tool, ["lt", pw_arg, archive_path], env)
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    links: list[tuple[str, str, bool]] = []
    name = ""
    is_link = False
    is_hard = False
    for raw in (completed.stdout or "").splitlines():
        key, sep, value = raw.strip().partition(": ")
        if not sep:
            continue
        if key == "Name":
            name, is_link, is_hard = value, False, False
        elif key == "Type":
            is_link = "link" in value.lower()
            is_hard = "hard" in value.lower()
        elif key == "Target" and is_link and name:
            links.append((name, value, is_hard))
    return links


def list_tool_entries(
    tool: str,
    archive_path: str,
    links: list[tuple[str, str, bool]] | None = None,
    *,
    password: str = "",
) -> list[str]:
    """List archive entries via the external tool (for the path pre-check).

    When ``links`` is given, link members the listing reports are appended to
    it as ``(path, target, is_hard_link)``. bsdtar is not asked: it refuses to extract
    through or to escaping links on its own.
    """
    base = _tool_basename(tool)
    env = None if tool == BUNDLED_7ZZ else build_host_open_env()
    if "unrar" in base:
        pw_arg = f"-p{password}" if password else "-p-"
        completed = _run_tool(tool, ["lb", pw_arg, archive_path], env)
        if completed.returncode != 0:
            raise _tool_failure(
                completed, "Could not list archive contents", password=password
            )
        if links is not None:
            links.extend(_unrar_links(tool, archive_path, env, password=password))
        return [
            line.strip()
            for line in (completed.stdout or "").splitlines()
            if line.strip()
        ]
    if "bsdtar" in base or base == "tar":
        if password and archive_format(archive_path) != "zip":
            raise ArchiveInstallError(
                "Install 7-Zip (7z) to extract password-protected .7z or .rar archives."
            )
        list_args = (
            ["--passphrase", password, "-tf", archive_path]
            if password
            else ["-tf", archive_path]
        )
        completed = _run_tool(tool, list_args, env)
        if completed.returncode != 0:
            raise _tool_failure(
                completed, "Could not list archive contents", password=password
            )
        entries: list[str] = []
        for line in (completed.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("./"):
                line = line[2:]
            if line:
                entries.append(line)
        return entries
    # 7z/7zz/7za (also a raw 7z.exe path on Windows).
    completed = _run_tool(tool, ["l", "-slt", f"-p{password}", archive_path], env)
    if completed.returncode != 0:
        raise _tool_failure(
            completed, "Could not list archive contents", password=password
        )
    entries = []
    archive_real = os.path.abspath(archive_path)
    current = ""
    for line in _seven_zip_member_lines(completed.stdout or ""):
        if line.startswith("Path = "):
            path = line[len("Path = ") :].strip()
            current = ""
            # 7z lists the archive itself as the first entry; it is never a member.
            if os.path.abspath(path) == archive_real:
                continue
            current = path
            entries.append(path)
        elif links is not None and current:
            for field, is_hard in (
                ("Symbolic Link = ", False),
                ("Hard Link = ", True),
                ("Link = ", False),
            ):
                if line.startswith(field):
                    target = line[len(field) :].strip()
                    if target:
                        links.append((current, target, is_hard))
                    break
    return entries


def check_archive_links_safe(
    links: list[tuple[str, str, bool]], entries: list[str]
) -> None:
    """Reject listed links that escape, or that other members are written through.

    Runs before an external tool writes anything: an older extractor could
    follow such a link and write outside staging before the post-extraction
    check sees it.
    """
    link_paths: set[str] = set()
    for path, target, is_hard in links:
        member = posixpath.normpath(path.replace("\\", "/")).lstrip("/")
        link_paths.add(member)
        normalized = target.replace("\\", "/")
        if _is_absolute_entry(target) or _is_absolute_entry(normalized):
            raise ArchiveSafetyError(
                f"Archive link points outside the destination: {path!r}"
            )
        # A hard link names another member from the archive root; a symlink
        # resolves from its own folder.
        base = "" if is_hard else posixpath.dirname(member)
        resolved = posixpath.normpath(posixpath.join(base, normalized))
        if resolved == ".." or resolved.startswith("../"):
            raise ArchiveSafetyError(
                f"Archive link points outside the destination: {path!r}"
            )
    if not link_paths:
        return
    for entry in entries:
        parts = posixpath.normpath(entry.replace("\\", "/")).lstrip("/").split("/")
        for depth in range(1, len(parts)):
            if "/".join(parts[:depth]) in link_paths:
                raise ArchiveSafetyError(
                    f"Archive entry is written through a link: {entry!r}"
                )


def _make_dirs_traversable(staging_dir: str) -> None:
    """Give every staged folder u+rwx so the link check can list it.

    Extractors keep archive modes, so a mode-000 folder would otherwise hide
    its links from the check and still reach the install.
    """

    def _fix(path: str) -> None:
        try:
            st = os.lstat(path)
            if stat.S_ISDIR(st.st_mode) and (st.st_mode & stat.S_IRWXU) != stat.S_IRWXU:
                os.chmod(path, stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
        except OSError:
            pass

    _fix(staging_dir)
    # Top-down: each folder is fixed before os.walk tries to list it.
    for root, dirs, _files in os.walk(staging_dir, followlinks=False):
        for name in dirs:
            _fix(os.path.join(root, name))


def _unreadable_staging(path: str, staging_dir: str) -> ArchiveSafetyError:
    return ArchiveSafetyError(
        f"Archive folder could not be checked: {os.path.relpath(path, staging_dir)!r}"
    )


def check_symlinks_inside_staging(staging_dir: str) -> None:
    """Reject symlinks and hard links whose resolved target escapes staging.

    A folder that cannot be listed is rejected too: links inside it could not
    be checked.
    """
    staging_real = os.path.realpath(os.path.abspath(staging_dir))
    inode_counts: dict[tuple[int, int], int] = {}
    file_records: list[tuple[str, os.stat_result]] = []
    _make_dirs_traversable(staging_dir)

    def _walk_error(exc: OSError) -> None:
        filename = cast(object, exc.filename)
        path = filename if isinstance(filename, str) and filename else staging_dir
        raise _unreadable_staging(path, staging_dir) from exc

    for root, _dirs, files in os.walk(
        staging_dir, followlinks=False, onerror=_walk_error
    ):
        candidates: list[str] = []
        try:
            with os.scandir(root) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            candidates.append(entry.path)
                    except OSError as exc:
                        raise _unreadable_staging(entry.path, staging_dir) from exc
        except OSError as exc:
            raise _unreadable_staging(root, staging_dir) from exc
        # os.walk already yields files; also check dir symlinks via scandir above.
        for path in candidates:
            target_real = os.path.realpath(path)
            if target_real != staging_real and not target_real.startswith(
                staging_real + os.sep
            ):
                raise ArchiveSafetyError(
                    f"Archive link points outside the destination: {os.path.relpath(path, staging_dir)!r}"
                )
        # Belt and braces: files list from walk for platforms where scandir missed.
        for name in files:
            path = os.path.join(root, name)
            try:
                st = os.lstat(path)
                if stat.S_ISLNK(st.st_mode):
                    target_real = os.path.realpath(path)
                    if target_real != staging_real and not target_real.startswith(
                        staging_real + os.sep
                    ):
                        raise ArchiveSafetyError(
                            f"Archive link points outside the destination: {os.path.relpath(path, staging_dir)!r}"
                        )
                else:
                    key = (st.st_dev, st.st_ino)
                    inode_counts[key] = inode_counts.get(key, 0) + 1
                    file_records.append((path, st))
            except OSError as exc:
                raise _unreadable_staging(path, staging_dir) from exc

    # A hard link with more names than staging holds also lives outside it.
    for path, st in file_records:
        if st.st_nlink > 1:
            key = (st.st_dev, st.st_ino)
            if st.st_nlink > inode_counts.get(key, 0):
                raise ArchiveSafetyError(
                    f"Archive link points outside the destination: {os.path.relpath(path, staging_dir)!r}"
                )


def check_archive_entries_no_collisions(entries: list[str]) -> None:
    """Reject duplicate or case-insensitive-colliding archive members.

    Two entries that differ only by case (`Game.exe` vs `game.exe`) coexist
    on Linux but silently overwrite each other on Windows/macOS, losing data.
    Exact duplicates (`a.txt` twice) mean the first copy is silently dropped.
    """
    seen_exact: set[str] = set()
    seen_fold: dict[str, str] = {}
    for raw in entries:
        # normpath folds `./a`, `a//b`, `a/./b` and a trailing `/` into the
        # one path extraction writes to.
        key = posixpath.normpath(raw.replace("\\", "/"))
        if key in seen_exact:
            raise ArchiveSafetyError(f"Archive has duplicate entry: {raw!r}")
        seen_exact.add(key)
        folded = key.casefold()
        first = seen_fold.get(folded)
        if first is not None and first != key:
            raise ArchiveSafetyError(
                f"Archive entries collide (differ only by case): {first!r} and {raw!r}"
            )
        _ = seen_fold.setdefault(folded, key)


def extract_with_tool(
    tool: str,
    archive_path: str,
    staging_dir: str,
    cancel: threading.Event | None = None,
    *,
    password: str = "",
) -> int:
    """Extract via an external tool after a listing-based safety pre-check."""
    base = _tool_basename(tool)
    if (
        ("bsdtar" in base or base == "tar")
        and password
        and archive_format(archive_path) != "zip"
    ):
        raise ArchiveInstallError(
            "Install 7-Zip (7z) to extract password-protected .7z or .rar archives."
        )
    os.makedirs(staging_dir, exist_ok=True)
    links: list[tuple[str, str, bool]] = []
    entries = list_tool_entries(tool, archive_path, links, password=password)
    meaningful: list[str] = []
    for entry in entries:
        # 7z lists the archive itself as "." or empty in some outputs; skip those.
        if entry.strip() in ("", ".", "./"):
            continue
        check_archive_entry_safe(entry)
        meaningful.append(entry)
    check_archive_entries_no_collisions(meaningful)
    check_archive_links_safe(links, meaningful)
    env = None if tool == BUNDLED_7ZZ else build_host_open_env()
    if "unrar" in base:
        pw_arg = f"-p{password}" if password else "-p-"
        args = ["x", "-o+", pw_arg, archive_path, staging_dir + os.sep]
    elif "bsdtar" in base or base == "tar":
        args = (
            ["--passphrase", password, "-xf", archive_path, "-C", staging_dir]
            if password
            else ["-xf", archive_path, "-C", staging_dir]
        )
    else:
        args = ["x", archive_path, f"-o{staging_dir}", "-y", f"-p{password}"]
    argv = _tool_argv(tool, _tool_args(tool, args), env=env)
    if cancel is None:
        try:
            completed = _run_tool(tool, args, env)
        except OSError as exc:
            detail = _sanitize_error_detail(str(exc), password)
            raise ArchiveInstallError(f"Extraction failed: {detail}") from exc
        if completed.returncode != 0:
            raise _tool_failure(completed, "Extraction failed", password=password)
        check_symlinks_inside_staging(staging_dir)
        return len(meaningful)
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding=_tool_encoding(tool),
            errors="replace",
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=no_console_window(),
        )
    except OSError as exc:
        detail = _sanitize_error_detail(str(exc), password)
        raise ArchiveInstallError(f"Extraction failed: {detail}") from exc
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set():
                process.kill()
                _ = process.communicate()
                raise InstallCancelled() from None
    completed = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    if completed.returncode != 0:
        raise _tool_failure(completed, "Extraction failed", password=password)
    check_symlinks_inside_staging(staging_dir)
    return len(meaningful)


def apply_execute_bits(root: str) -> int:
    """Linux execute-bit pass: .sh/.x86/.x86_64 or ELF files gain +x.

    Returns the number of files made executable. Uses `is_elf` so zips made
    on Windows (no mode data) still launch.
    """
    if _is_windows():
        return 0
    from core.launcher import is_elf

    changed = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if os.path.islink(path):
                    continue
                mode = os.stat(path).st_mode
                # FIFOs/sockets/devices would block is_elf on open; only
                # regular files can be launchers.
                if not stat.S_ISREG(mode):
                    continue
            except OSError:
                continue
            if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                continue
            lower = name.lower()
            needs_exec = lower.endswith((".sh", ".x86", ".x86_64")) or is_elf(path)
            if not needs_exec:
                continue
            new_mode = mode | stat.S_IXUSR
            if mode & stat.S_IRGRP:
                new_mode |= stat.S_IXGRP
            if mode & stat.S_IROTH:
                new_mode |= stat.S_IXOTH
            try:
                os.chmod(path, new_mode)
                changed += 1
            except OSError:
                continue
    return changed


def _single_wrapper_name(staging_dir: str) -> str:
    """Name of the only top-level folder (ignoring junk), or "" when there is not one."""
    junk = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}
    try:
        names = [n for n in os.listdir(staging_dir) if n.lower() not in junk]
    except OSError:
        return ""
    if len(names) != 1:
        return ""
    path = os.path.join(staging_dir, names[0])
    return names[0] if os.path.isdir(path) and not os.path.islink(path) else ""


def flatten_single_wrapper(staging_dir: str) -> bool:
    """Move a single top-level wrapper folder's contents up one level."""
    try:
        entries = os.listdir(staging_dir)
    except OSError:
        return False
    # Clean up and ignore metadata/junk entries (__MACOSX, .DS_Store, Thumbs.db)
    junk = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}
    meaningful: list[str] = []
    for entry in entries:
        candidate = os.path.join(staging_dir, entry)
        if entry.lower() in junk:
            try:
                if os.path.isdir(candidate) and not os.path.islink(candidate):
                    shutil.rmtree(candidate, ignore_errors=True)
                elif os.path.isfile(candidate) or os.path.islink(candidate):
                    os.remove(candidate)
            except OSError:
                pass
        else:
            meaningful.append(entry)

    if len(meaningful) != 1:
        return False
    wrapper = os.path.join(staging_dir, meaningful[0])
    if not os.path.isdir(wrapper) or os.path.islink(wrapper):
        return False
    try:
        inner = os.listdir(wrapper)
    except OSError:
        return False
    for name in inner:
        dst = os.path.join(staging_dir, name)
        if os.path.lexists(dst):
            return False
    try:
        for name in inner:
            _ = shutil.move(
                os.path.join(wrapper, name), os.path.join(staging_dir, name)
            )
        os.rmdir(wrapper)
    except OSError:
        return False
    return True


def create_staging_dir(library_folder: str) -> str:
    """Create `<library>/.wlib-staging-<random>/` (same filesystem for rename)."""
    base = os.path.abspath(os.path.expanduser(library_folder))
    os.makedirs(base, exist_ok=True)
    path = tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=base)
    if not _is_windows():
        # mkdtemp is 0o700 and staging is renamed into the game folder.
        os.chmod(path, 0o755)
    return path


def _long_path_safe(path: str) -> str:
    """On Windows, the extended-length form that reaches past 260 characters."""
    if not _is_windows() or not path:
        return path
    absolute = ntpath.abspath(path)
    if absolute.startswith("\\\\"):
        return absolute
    return "\\\\?\\" + absolute


def _windows_long_path_error(exc: BaseException) -> str:
    """A message for a Windows path-length failure, or ""."""
    if not _is_windows() or not isinstance(exc, OSError):
        return ""
    raw_names = (cast(object, exc.filename), cast(object, exc.filename2))
    names = [str(name) for name in raw_names if name]
    # Folders stop at 248 characters, files at 260.
    if not any(len(name) >= 248 for name in names):
        return ""
    return (
        "A file in this archive would have a path longer than Windows allows "
        + "(260 characters). Choose a library folder with a shorter path, or turn "
        + "on Windows long path support, then install again."
    )


def cleanup_staging_dir(path: str) -> None:
    """Delete a staging or scratch tree, best effort; leftovers are logged.

    An extractor that failed partway can leave folders without owner write
    or search access (archive modes), and their files cannot be deleted
    then. Real folders get u+rwx back first; links are not followed.
    """
    try:
        if path and os.path.isdir(path) and not os.path.islink(path):
            # A tree with paths past 260 characters only goes in extended form.
            safe = _long_path_safe(path)
            _make_dirs_traversable(safe)
            errors: list[str] = []

            def _onerror(_func: object, p: str, _exc: object) -> None:
                errors.append(str(p))

            shutil.rmtree(safe, onexc=_onerror)
            if errors:
                _log.warning(
                    "Could not fully remove %s (%d leftovers, first: %s)",
                    path,
                    len(errors),
                    errors[0],
                )
    except OSError as exc:
        _log.warning("Could not remove %s: %s", path, exc)


# Beside a staging folder that holds a moved torrent download: where the
# download came from, so a crashed install puts it back instead of deleting
# the only copy.
MOVED_RECORD_SUFFIX = ".moved.json"


def _moved_record_path(staging: str) -> str:
    return staging.rstrip(os.sep) + MOVED_RECORD_SUFFIX


def write_moved_record(
    staging: str, source: str, wrapper: str = "", flattened: bool = False
) -> None:
    """Record (atomically) that `staging` holds the download moved from `source`."""
    path = _moved_record_path(staging)
    temp = f"{path}.tmp"
    data = {"source": source, "wrapper": wrapper, "flattened": flattened}
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def remove_moved_record(staging: str) -> None:
    for path in (_moved_record_path(staging), f"{_moved_record_path(staging)}.tmp"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            _log.warning("Could not remove %s: %s", path, exc)


def _read_moved_record(staging: str) -> dict[str, object] | None:
    try:
        with open(_moved_record_path(staging), encoding="utf-8") as handle:
            data = cast(object, json.load(handle))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # A record that cannot be read still marks the folder as someone's data.
        return {}
    if not isinstance(data, dict):
        return {}
    return cast(dict[str, object], data)


def restore_moved_staging(staging: str, record: Mapping[str, object]) -> bool:
    """Put a moved download back at its source, in its own layout.

    Returns False (and changes nothing) when the source cannot take it back.
    """
    source = str(record.get("source") or "")
    wrapper = str(record.get("wrapper") or "") if record.get("flattened") else ""
    if not source or not os.path.isabs(source):
        return False
    if os.path.lexists(source):
        try:
            if not os.listdir(staging):
                # Stopped before the rename: the download never left.
                os.rmdir(staging)
                return True
        except OSError:
            return False
        # The torrent may have made an empty folder again; anything more is
        # data to keep, so the staging folder waits for the user.
        try:
            os.rmdir(source)
        except OSError:
            return False
    # The parent is not made again: a torrent folder that is gone was removed,
    # and its cleanup would delete a recreated one without a state file.
    try:
        if wrapper:
            os.mkdir(source)
            try:
                os.replace(staging, os.path.join(source, wrapper))
            except OSError:
                try:
                    os.rmdir(source)
                except OSError:
                    pass
                raise
        else:
            os.replace(staging, source)
    except OSError as exc:
        _log.warning("Could not put %s back at %s: %s", staging, source, exc)
        return False
    return True


def cleanup_stale_staging(library_folder: str) -> int:
    """Remove leftover `.wlib-staging-*` folders from crashed installs.

    A folder that holds a moved torrent download goes back to the download's
    folder instead; when that fails it is kept, never deleted.
    """
    removed = 0
    try:
        base = os.path.abspath(os.path.expanduser(library_folder))
        if not os.path.isdir(base):
            return 0
        names = os.listdir(base)
        for name in names:
            if not name.startswith(STAGING_PREFIX):
                continue
            candidate = os.path.join(base, name)
            try:
                if os.path.islink(candidate) or not os.path.isdir(candidate):
                    continue
                record = _read_moved_record(candidate)
                if record is not None:
                    if restore_moved_staging(candidate, record):
                        remove_moved_record(candidate)
                        _log.info(
                            "Put the moved download %s back", record.get("source")
                        )
                    else:
                        _log.warning(
                            "Kept %s: it holds a moved download that could not be put back",
                            candidate,
                        )
                    continue
                shutil.rmtree(_long_path_safe(candidate), ignore_errors=True)
                removed += 1
            except OSError:
                continue
        # Records whose staging folder is gone (the install finished or the
        # folder was put back) are only clutter.
        for name in names:
            if not name.startswith(STAGING_PREFIX):
                continue
            for suffix in (MOVED_RECORD_SUFFIX, MOVED_RECORD_SUFFIX + ".tmp"):
                if name.endswith(suffix):
                    staging = os.path.join(base, name[: -len(suffix)])
                    if not os.path.lexists(staging):
                        try:
                            os.remove(os.path.join(base, name))
                        except OSError:
                            pass
                    break
    except OSError:
        return removed
    return removed


def _existing_ancestor(path: str) -> str:
    current = os.path.abspath(os.path.expanduser(path))
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def _same_drive(a: str, b: str) -> bool:
    """Whether `a` and `b` (or `b`'s nearest existing folder) share a filesystem."""
    try:
        return os.stat(a).st_dev == os.stat(_existing_ancestor(b)).st_dev
    except OSError:
        return False


def ensure_free_space(library_folder: str, required_bytes: int | None) -> None:
    """Fail early when the library drive cannot hold the uncompressed size."""
    if not required_bytes or required_bytes <= 0:
        return
    try:
        base = os.path.abspath(os.path.expanduser(library_folder))
        os.makedirs(base, exist_ok=True)
        free = shutil.disk_usage(base).free
    except OSError:
        return
    if free < required_bytes:
        required_gb = required_bytes / (1024**3)
        free_gb = free / (1024**3)
        raise ArchiveInstallError(
            f"Not enough free space: need {required_gb:.1f} GB, have {free_gb:.1f} GB."
        )


def uncompressed_size(
    archive_path: str, *, library_folder: str = "", password: str = ""
) -> int | None:
    """Best-effort uncompressed size in bytes (None when unknown)."""

    fmt = archive_format(archive_path)
    if fmt == "zip":
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                return sum(info.file_size for info in zf.infolist())
        except (OSError, zipfile.BadZipFile):
            # Extraction reports a broken zip with a clear message.
            return None
    if fmt == "tar":
        # Only a plain tar lists its sizes without full decompression.
        if archive_suffix(archive_path) != ".tar":
            return None
        try:
            # "r:" refuses compressed data, so this reads the headers only.
            with tarfile.open(archive_path, "r:") as tf:
                total = sum(m.size for m in tf.getmembers() if m.isreg())
        except (OSError, tarfile.TarError, EOFError):
            return None
        return total if total > 0 else None
    if fmt in ("7z", "rar"):
        tool, _err = detect_archive_tool(
            archive_path, library_folder=library_folder, password=password
        )
        if tool and "7z" in _tool_basename(tool):
            try:
                env = None if tool == BUNDLED_7ZZ else build_host_open_env()
                completed = _run_tool(
                    tool, ["l", "-slt", f"-p{password}", archive_path], env
                )
                if completed.returncode == 0:
                    total = 0
                    # The header blocks repeat the archive's own size.
                    for line in _seven_zip_member_lines(completed.stdout or ""):
                        if line.startswith("Size = "):
                            try:
                                total += int(line[len("Size = ") :].strip())
                            except ValueError:
                                pass
                    return total if total > 0 else None
            except OSError:
                return None
    return None


def infolder_save_relpaths(root: str) -> list[str]:
    """Relative in-folder save paths under root (dirs + loose save files)."""
    found: list[str] = []
    for rel in INFOLDER_SAVE_DIRS:
        if os.path.isdir(os.path.join(root, rel)):
            found.append(rel)
    for path in loose_save_files(root):
        found.append(os.path.relpath(path, root))
    return found


def copy_infolder_saves(
    old_root: str,
    new_root: str,
    *,
    removed_root: str = "",
    skipped: list[str] | None = None,
    copy_function: Callable[[str, str], object] = shutil.copy2,
) -> list[str]:
    """Copy in-folder saves from old_root into new_root, overwriting.

    A save folder that is a link to a folder inside `removed_root` (the tree
    that old-folder cleanup deletes; default old_root) is copied from its
    target, so the update does not delete the only copy. A link whose target
    holds the link itself (for example the install folder) cannot be copied;
    its relative path goes into `skipped` so the caller keeps the old folder.
    A link that leaves `removed_root` is left alone: cleanup removes only the
    link, not its target.

    `copy_function(src, dst)` writes each file (see _SaveCarryUndo).

    Returns the copied relative paths. Raises ArchiveInstallError on failure
    so the caller can roll back before touching the library.
    """
    copied: list[str] = []
    bound_real = real_path(removed_root or old_root)
    for rel in infolder_save_relpaths(old_root):
        src = os.path.join(old_root, rel)
        dst = os.path.join(new_root, rel)
        try:
            if os.path.isdir(src) and os.path.islink(src):
                target_real = real_path(src)
                if not is_within_real(target_real, bound_real):
                    continue
                if is_within_real(real_path(os.path.dirname(src)), target_real):
                    if skipped is not None:
                        skipped.append(rel)
                    continue
                os.makedirs(os.path.dirname(dst) or new_root, exist_ok=True)
                _ = shutil.copytree(
                    target_real, dst, dirs_exist_ok=True, copy_function=copy_function
                )
                copied.append(rel)
            elif os.path.isdir(src):
                os.makedirs(os.path.dirname(dst) or new_root, exist_ok=True)
                _ = shutil.copytree(
                    src, dst, dirs_exist_ok=True, copy_function=copy_function
                )
                copied.append(rel)
            elif os.path.isfile(src):
                os.makedirs(os.path.dirname(dst) or new_root, exist_ok=True)
                _ = copy_function(src, dst)
                copied.append(rel)
        except OSError as exc:
            raise ArchiveInstallError(
                f"Could not carry saves into the new version: {exc}"
            ) from exc
    return copied


def override_relpath(override: str, old_root: str) -> str | None:
    """Path of a saves-folder override relative to the old install folder.

    None when there is no override or it lies outside `old_root`; "." when it
    is the install folder itself.
    """
    if not override.strip() or not old_root:
        return None
    if not is_within(override, old_root):
        return None
    return os.path.relpath(os.path.realpath(override), os.path.realpath(old_root))


def copy_override_saves(old_override: str, rel: str, staging: str) -> int:
    """Copy a saves-folder override from the old install into staging.

    The install folder itself ("."): nothing to copy, the caller repoints it.
    Raises ArchiveInstallError on failure so the caller can roll back.
    """
    if rel == "." or not os.path.isdir(old_override):
        return 0
    try:
        return copy_folder_contents(old_override, os.path.join(staging, rel))
    except OSError as exc:
        raise ArchiveInstallError(
            f"Could not carry saves into the new version: {exc}"
        ) from exc


def real_path(path: str) -> str:
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    except OSError:
        return os.path.abspath(os.path.expanduser(path))


def absolute_game_path(value: object) -> str:
    """Absolute path for a stored game path, expanding `~` like the launcher.

    Add Game stores the typed value, so a game added as `~/games/Game.exe`
    keeps the tilde. Every consumer expands it (the launcher, save detection,
    `in_library` matching); the update path must do the same or it looks for
    the executable under the app's working directory.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    return os.path.abspath(os.path.expanduser(raw))


def _is_root(path_real: str) -> bool:
    # Filesystem/drive root: parent == itself (posix "/") or drive root (nt "C:\\").
    parent = os.path.dirname(path_real.rstrip(os.sep))
    if parent == path_real.rstrip(os.sep):
        return True
    drive, _tail = os.path.splitdrive(path_real)
    if drive and path_real.rstrip(os.sep).lower() == drive.lower():
        return True
    return False


def is_within_real(child_real: str, parent_real: str) -> bool:
    if child_real == parent_real:
        return True
    return child_real.startswith(parent_real.rstrip(os.sep) + os.sep)


def can_remove_old_folder(
    candidate: str,
    new_destination: str,
    library_folders: list[str] | None = None,
    downloads_folder: str = "",
    other_paths: list[str] | tuple[str, ...] | None = None,
    old_exe: str = "",
) -> dict[str, object]:
    """Guard for deleting the previous install folder.

    Returns {"removable": bool, "reason": str}; reason is "" when removable.
    """
    cand_real = _real(candidate) if candidate else ""
    if not candidate or not cand_real:
        return {"removable": False, "reason": "The previous folder is unknown."}
    if _is_root(cand_real):
        return {"removable": False, "reason": "Refusing to remove a drive root."}
    # A mounted drive's root is the filesystem root for that drive: deleting it
    # removes every unrelated file on the mount (rmtree would empty it and then
    # fail to remove the mount point).
    if os.path.ismount(cand_real):
        return {
            "removable": False,
            "reason": "Refusing to remove a mounted drive root.",
        }
    home_real = _real("~")
    if cand_real == home_real:
        return {"removable": False, "reason": "Refusing to remove your home folder."}
    if _is_within(home_real, cand_real):
        return {
            "removable": False,
            "reason": "Refusing to remove a folder that contains your home folder.",
        }
    library_reals = [_real(folder) for folder in library_folders or [] if folder]
    if any(cand_real == folder for folder in library_reals):
        return {"removable": False, "reason": "Refusing to remove the library folder."}
    # A game added by hand straight into a parent folder (~/Games/Foo.sh) has
    # that parent as its folder; removing it would take the library with it.
    if any(_is_within(folder, cand_real) for folder in library_reals):
        return {
            "removable": False,
            "reason": "Refusing to remove a folder that contains a library folder.",
        }
    if downloads_folder:
        downloads_real = _real(downloads_folder)
        if cand_real == downloads_real:
            return {
                "removable": False,
                "reason": "Refusing to remove the Downloads folder.",
            }
        if _is_within(downloads_real, cand_real):
            return {
                "removable": False,
                "reason": "Refusing to remove a folder that contains the Downloads folder.",
            }
    if new_destination:
        new_real = _real(new_destination)
        # Equal folders = in-place replace: what gets removed is the renamed
        # .wlib-old-* backup, not the destination itself.
        if cand_real != new_real and _is_within(new_real, cand_real):
            return {
                "removable": False,
                "reason": "Refusing to remove a parent of the new install.",
            }
    if old_exe:
        old_exe_path = _absolute_game_path(old_exe)
        old_exe_real = _real(old_exe_path)
        try:
            exe_exists = os.path.isfile(old_exe_path)
        except OSError:
            exe_exists = False
        if not exe_exists or not _is_within(old_exe_real, cand_real):
            return {
                "removable": False,
                "reason": "The old folder no longer contains the game's executable.",
            }
    else:
        # Without an exe to anchor on, refuse empty/missing candidates only.
        if not os.path.isdir(candidate):
            return {"removable": False, "reason": "The previous folder is missing."}
    for other in other_paths or []:
        if not other:
            continue
        other_real = _real(str(other))
        if _is_within(other_real, cand_real):
            return {
                "removable": False,
                "reason": "The folder contains another library game.",
            }
    # Last: off Linux this walks the tree. A mount inside the folder (a bind
    # mount of a shared folder) would be emptied by the delete.
    nested = mount_points_within(cand_real)
    if nested:
        return {
            "removable": False,
            "reason": f"Refusing to remove a folder that contains a mounted folder ({nested[0]}).",
        }
    return {"removable": True, "reason": ""}


def relative_inside(path: str, root: str) -> str | None:
    """Relative path of `path` inside `root`, or None when outside."""
    path_real = _real(path)
    root_real = _real(root)
    if path_real == root_real:
        return ""
    prefix = root_real.rstrip(os.sep) + os.sep
    if path_real.startswith(prefix):
        return os.path.relpath(path_real, root_real)
    return None


def relocate_launch_targets(
    old_root: str,
    new_root: str,
    targets: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Repoint targets inside old_root to the same relative path in new_root.

    Returns (updated, unmoved): updated holds the new target dicts (with
    exe_path rewritten when the file exists in the new install); unmoved
    holds the targets left unchanged because the file is missing there.
    Targets outside old_root are left unchanged and not reported.
    """
    updated: list[dict[str, object]] = []
    unmoved: list[dict[str, object]] = []
    for target in targets:
        exe = str(target.get("exe_path") or "")
        rel = _relative_inside(exe, old_root) if old_root and exe else None
        if rel is None or rel == "":
            updated.append(dict(target))
            continue
        candidate = os.path.join(new_root, rel)
        if os.path.isfile(candidate):
            next_target = dict(target)
            next_target["exe_path"] = candidate
            updated.append(next_target)
        else:
            updated.append(dict(target))
            unmoved.append(dict(target))
    return updated, unmoved


def _point_missing_at_backup(
    updated: list[dict[str, object]],
    unmoved: list[dict[str, object]],
    destination: str,
    old_backup: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """After an in-place swap, repoint targets the new tree lacks at the backup.

    A target whose path is inside `destination` but whose file is not in the
    new version points at the same relative path in `old_backup`, and is
    listed as needing attention with the backup folder.
    """
    result: list[dict[str, object]] = []
    repointed: list[dict[str, object]] = []
    for target in updated:
        exe = str(target.get("exe_path") or "")
        rel = _relative_inside(exe, destination) if exe else None
        if rel and not os.path.isfile(exe):
            backup_path = os.path.join(old_backup, rel)
            if os.path.isfile(backup_path):
                next_target = dict(target)
                next_target["exe_path"] = backup_path
                result.append(next_target)
                repointed.append({**next_target, "backup_folder": old_backup})
                continue
        result.append(target)
    if not repointed:
        return updated, unmoved
    moved_ids = {t.get("id") for t in repointed if t.get("id") is not None}
    kept = [t for t in unmoved if t.get("id") is None or t.get("id") not in moved_ids]
    # The default target has no id; match it on its old path instead.
    repointed_default = any(t.get("id") is None for t in repointed)
    if repointed_default:
        kept = [t for t in kept if t.get("id") is not None]
    return result, kept + repointed


_MOUNTINFO = "/proc/self/mountinfo"
_MOUNTINFO_ESCAPE_RE = re.compile(r"\\([0-7]{3})")


def _linux_mount_points() -> list[str] | None:
    """Every mount point of this process on Linux, or None when unknown."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        with open(_MOUNTINFO, encoding="utf-8", errors="surrogateescape") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    points: list[str] = []
    for line in lines:
        fields = line.split(" ")
        if len(fields) > 4:
            # The kernel writes space, tab, newline and backslash as \ooo.
            points.append(
                _MOUNTINFO_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 8)), fields[4])
            )
    return points


def mount_points_within(path: str) -> list[str]:
    """Mount points at or below `path`, sorted ([] when there are none).

    shutil.rmtree goes into a mounted folder like any other folder, so a bind
    mount inside a game folder would lose the shared folder's files. On Linux
    the mount table is read (a bind mount on the same filesystem looks like a
    plain folder to os.path.ismount). Elsewhere the tree is walked without
    going into mounts. Windows rmtree does not go into mounted volumes, so
    only `path` itself is checked there.
    """
    root = real_path(path)
    points = _linux_mount_points()
    if points is not None:
        return sorted({point for point in points if is_within_real(point, root)})
    found: list[str] = []
    if os.path.ismount(root):
        found.append(root)
    if _is_windows():
        return found
    for dirpath, dirnames, _files in os.walk(root, followlinks=False):
        keep: list[str] = []
        for name in dirnames:
            child = os.path.join(dirpath, name)
            if os.path.ismount(child):
                found.append(child)
            else:
                keep.append(name)
        dirnames[:] = keep
    return sorted(found)


def remove_tree_collect(path: str) -> list[str]:
    """Remove a tree, collecting errors instead of aborting halfway.

    A tree with a mount point in it is not touched: deleting it would erase
    the mounted folder's files, which belong to something else.
    """
    mounts = mount_points_within(path)
    if mounts:
        return [f"{mount} is a mounted folder; unmount it first" for mount in mounts]
    errors: list[str] = []

    def _onerror(_func: object, p: str, _exc: object) -> None:
        errors.append(str(p))

    try:
        shutil.rmtree(path, onexc=_onerror)
    except OSError as exc:
        errors.append(str(exc))
    return errors


def other_game_paths(exclude_id: int | None = None) -> list[str]:
    """Paths that other library games need: executables, launch targets,
    save-folder overrides, custom Wine prefixes and the shared Wine prefix.

    A folder that holds one of these must not be removed or moved for a
    different game.
    """
    from core.database import get_all_games, get_setting, list_launch_targets_for_games

    paths: list[str] = []
    try:
        games = get_all_games()
    except OSError:
        return []
    ids: list[int] = []
    for game in games:
        try:
            gid = int(str(game.get("id", 0)))
        except (TypeError, ValueError):
            continue
        if exclude_id is not None and gid == exclude_id:
            continue
        ids.append(gid)
        for field in ("exe_path", "save_path_override", "custom_prefix"):
            value = str(game.get(field) or "")
            if field != "exe_path":
                value = value.strip()
            if value:
                paths.append(value)
    try:
        shared_prefix = str(get_setting("wine_prefix_path") or "").strip()
    except OSError:
        shared_prefix = ""
    if shared_prefix:
        paths.append(shared_prefix)
    if ids:
        try:
            by_game = list_launch_targets_for_games(ids)
            for targets in by_game.values():
                for target in targets:
                    exe = str(target.get("exe_path") or "")
                    if exe:
                        paths.append(exe)
        except OSError:
            pass
    return paths


def old_install_root(
    old_exe: str,
    old_game: Mapping[str, object],
    library_folders: Sequence[str],
    pattern: str,
    names: Sequence[tuple[str, str]],
    engine: str,
    game_id: int | None,
) -> str:
    """Find the folder the previous version was installed into.

    An archive that wraps the game in a subfolder leaves the executable one
    level below the install folder, so its own folder is not the install
    root. Rebuild the old destination from the stored version and use it
    when it holds the executable and no other game; otherwise fall back to
    the executable's folder.
    """
    exe_dir = os.path.dirname(os.path.abspath(old_exe))
    exe_real = _real(old_exe)
    library_reals = {_real(folder) for folder in library_folders if folder}
    old_version = str(old_game.get("version") or "")
    candidates = [
        *names,
        (str(old_game.get("developer") or ""), str(old_game.get("title") or "")),
    ]
    others = [_real(path) for path in other_game_paths(game_id)]
    best = best_real = ""
    for folder in library_folders:
        if not folder:
            continue
        for creator, title in candidates:
            if not title.strip():
                continue
            root = build_destination(
                folder, pattern, creator, title, old_version, engine
            )
            root_real = _real(root)
            if (
                root_real in library_reals
                or _is_root(root_real)
                or not _is_within(exe_real, root_real)
                or any(_is_within(other, root_real) for other in others)
            ):
                continue
            if len(root_real) > len(best_real):
                best, best_real = root, root_real
    if best and best_real != _real(exe_dir):
        return best
    return exe_dir


def _pick_exe(
    destination: str,
    old_exe: str = "",
    old_root: str = "",
    preferred_title: str = "",
) -> str:
    from core.game_scan import find_launcher

    if old_exe and old_root:
        rel = _relative_inside(old_exe, old_root)
        if rel:
            candidate = os.path.join(destination, rel)
            if os.path.isfile(candidate):
                return candidate
    # Archives often nest the game a few single folders deep (Game/Game v1/...).
    return find_launcher(destination, depth=4, preferred_title=preferred_title)


_MAX_LAUNCH_CANDIDATES = 50
_MAX_CANDIDATE_DEPTH = 4


def list_launch_candidates(root: str) -> list[str]:
    """Launch files under `root` (depth 4, at most 50) as relative paths.

    Uses the same file filter as `find_launcher`, so it never offers a file
    the automatic pick would skip (crash reporters, installers, documents).
    """
    from core.game_scan import list_launcher_files

    found: list[str] = []

    def walk(folder: str, depth: int) -> None:
        if len(found) >= _MAX_LAUNCH_CANDIDATES:
            return
        for name in list_launcher_files(folder):
            if len(found) >= _MAX_LAUNCH_CANDIDATES:
                return
            found.append(os.path.relpath(os.path.join(folder, name), root))
        if depth >= _MAX_CANDIDATE_DEPTH:
            return
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name.lower())
        except OSError:
            return
        for entry in entries:
            if (
                not entry.is_symlink()
                and entry.is_dir(follow_symlinks=False)
                and entry.name.lower() not in ("__macosx", ".git")
            ):
                walk(entry.path, depth + 1)

    walk(root, 1)
    return [path.replace(os.sep, "/") for path in found]


def _checked_exe_relpath(exe_relpath: str) -> str:
    """Refuse an absolute or parent-escaping relative path."""
    rel = str(exe_relpath or "").strip().replace("\\", "/")
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if (
        not parts
        or rel.startswith("/")
        or os.path.isabs(rel)
        or re.match(r"^[A-Za-z]:", rel)
        or any(part == ".." for part in parts)
    ):
        raise ArchiveInstallError("The chosen launch file path is not valid.")
    return "/".join(parts)


def _torrent_area() -> str:
    from core.downloads.torrents import torrents_dir

    return _real(str(torrents_dir()))


def in_torrent_area(path: str) -> bool:
    """Whether `path` lies inside wLib's torrent staging area."""
    try:
        root = _torrent_area()
        real = _real(path)
    except OSError:
        return False
    return real != root and _is_within(real, root)


def _checked_torrent_folder(path: str) -> str:
    folder = os.path.abspath(os.path.expanduser(str(path or "").strip()))
    if not folder or not os.path.isdir(folder) or os.path.islink(folder):
        raise ArchiveInstallError("The downloaded folder was not found.")
    if not in_torrent_area(folder):
        raise ArchiveInstallError(
            "Only folders downloaded by wLib's torrent downloads can be installed this way."
        )
    return folder


def _torrent_seeding(path: str) -> bool:
    from core.downloads.torrents import supervisor

    return supervisor.is_seeding_path(path)


def _place_folder(
    source: str,
    staging: str,
    *,
    seeding: bool,
    workers: int,
    cancel: threading.Event | None,
    progress: Callable[[str], None],
    required_bytes: int | None = None,
) -> str:
    """Fill `staging` from a downloaded folder; returns the source path when it was moved.

    A seeding torrent keeps its files, so they are copied. Otherwise the folder
    is moved when both sit on one drive, and copied when they do not.
    """
    if not seeding:
        try:
            # Recorded first: after a crash the staging folder holds the only
            # copy, and the next install's cleanup must put it back.
            write_moved_record(staging, source)
            os.rmdir(staging)
            os.replace(source, staging)
            return source
        except OSError:
            remove_moved_record(staging)
            os.makedirs(staging, exist_ok=True)
        # A rename that failed means a copy after all, so it needs the room.
        ensure_free_space(staging, required_bytes)
    progress("copying")
    _ = copy_tree_parallel(source, staging, workers, cancel)
    return ""


class _SaveCarryUndo:
    """Makes the save copies into a moved download reversible.

    A download that was moved (not copied) into staging is the only copy of
    its files. Before a carried save replaces one of them, the original is
    renamed into a hidden folder inside staging; rollback() puts the
    originals back and removes the files the copies added, so a cancelled or
    failed install returns the download as it was.
    """

    def __init__(self, root: str) -> None:
        self.root: str = os.path.abspath(root)
        self.name: str = f".wlib-undo-{time.time_ns()}"
        self._seen: set[str] = set()
        self._created: list[str] = []
        self._saved: list[str] = []

    def _rel(self, path: str) -> str | None:
        rel = os.path.relpath(os.path.normpath(os.path.abspath(path)), self.root)
        if rel == "." or rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return None
        return rel

    def before_write(self, dst: str) -> None:
        rel = self._rel(dst)
        if rel is None or rel in self._seen:
            return
        path = os.path.join(self.root, rel)
        if os.path.isdir(path) and not os.path.islink(path):
            return
        self._seen.add(rel)
        if os.path.lexists(path):
            saved = os.path.join(self.root, self.name, rel)
            os.makedirs(os.path.dirname(saved), exist_ok=True)
            os.rename(path, saved)
            self._saved.append(rel)
        else:
            self._created.append(rel)

    def before_tree(self, source: str, destination: str) -> None:
        """Prepare for copy_folder_contents(source, destination)."""
        if not os.path.isdir(source):
            return
        for current, dirnames, filenames in os.walk(source, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if not os.path.islink(os.path.join(current, name))
            ]
            rel_dir = os.path.relpath(current, source)
            for name in filenames:
                full = os.path.join(current, name)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                self.before_write(os.path.join(destination, rel_dir, name))

    def copy(self, src: str, dst: str) -> object:
        if os.path.isdir(dst) and not os.path.islink(dst):
            dst = os.path.join(dst, os.path.basename(src))
        self.before_write(dst)
        return shutil.copy2(src, dst)

    def rollback(self) -> bool:
        """Undo the copies; False when something could not be put back."""
        ok = True
        for rel in reversed(self._created):
            path = os.path.join(self.root, rel)
            try:
                if os.path.lexists(path) and not (
                    os.path.isdir(path) and not os.path.islink(path)
                ):
                    os.remove(path)
            except OSError as exc:
                _log.warning("Could not remove carried save %s: %s", path, exc)
                ok = False
                continue
            # Folders the copies made. A torrent holds no empty folders.
            parent = os.path.dirname(path)
            while parent != self.root and self._rel(parent) is not None:
                try:
                    os.rmdir(parent)
                except OSError:
                    break
                parent = os.path.dirname(parent)
        for rel in reversed(self._saved):
            path = os.path.join(self.root, rel)
            saved = os.path.join(self.root, self.name, rel)
            try:
                if os.path.lexists(path) and not (
                    os.path.isdir(path) and not os.path.islink(path)
                ):
                    os.remove(path)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.rename(saved, path)
            except OSError as exc:
                _log.warning("Could not put back %s: %s", path, exc)
                ok = False
        if ok:
            shutil.rmtree(os.path.join(self.root, self.name), ignore_errors=True)
        return ok

    def discard(self, root: str) -> None:
        """Drop the kept originals once the tree moved to `root` for good."""
        shutil.rmtree(os.path.join(root, self.name), ignore_errors=True)


def install_archive(
    archive_path: str,
    *,
    library_folder: str,
    pattern: str,
    title: str,
    version: str = "",
    creator: str = "",
    engine: str = "",
    cover_image: str = "",
    tags: str | list[str] = "",
    rating: str = "",
    lc_url: str = "",
    lc_dismissed: str = "",
    screenshots: Sequence[str] = (),
    game_id: int | None = None,
    target_id: int | None = None,
    remove_old: bool = True,
    delete_archive: bool = False,
    progress_cb: Callable[[str], None] | None = None,
    bytes_cb: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    source_kind: str = "archive",
    exe_relpath: str = "",
    password: str = "",
) -> dict[str, object]:
    """Full install orchestration per design §7 (filesystem + DB).

    `source_kind="folder"` installs a folder downloaded by wLib's torrent
    downloads (refused anywhere else) instead of extracting an archive.

    `bytes_cb(done, total)` reports extracted bytes about once a second
    (total 0 when the archive does not say). Setting `cancel` stops the
    install with InstallCancelled at any point before the new version moves
    into place; after that it has no effect.

    `exe_relpath` is the launch file inside the extracted archive that an
    update uses when the automatic pick found none (see NoLauncherChosenError).

    Returns {"game_id", "exe_path", "destination", "unmoved_targets",
    "kept_old_folder_reason", "removed_old", "old_folder"}.
    Raises ArchiveInstallError on failure; staging is always cleaned and the
    previous install is untouched until the new tree is in place.
    """
    import time

    from core.database import (
        get_all_games,
        list_game_launch_targets,
        update_game_launch_target,
    )

    def _progress(stage: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(stage)
            except Exception:
                pass

    folder_source = source_kind == "folder"
    chosen_relpath = _checked_exe_relpath(exe_relpath) if exe_relpath.strip() else ""
    archive_path = os.path.expanduser(str(archive_path or "").strip())
    volumes: VolumeSet | None = None
    if folder_source:
        archive_path = _checked_torrent_folder(archive_path)
        fmt = "folder"
    else:
        if not archive_path or not os.path.isfile(archive_path):
            raise ArchiveInstallError("Archive file was not found.")
        volumes = volume_set(archive_path)
        if volumes is not None:
            check_volume_set(volumes)
            # Any part may be chosen; extraction always starts from the first.
            archive_path = volumes.first
        fmt = archive_format(archive_path)
        if fmt not in ("zip", "7z", "rar", "tar"):
            raise ArchiveInstallError(UNSUPPORTED_FORMAT_MESSAGE)
        if fmt == "tar":
            codec_error = _tar_codec_error(archive_path)
            if codec_error:
                raise ArchiveInstallError(codec_error)
    if not title.strip():
        raise ArchiveInstallError("A title is required.")
    if not library_folder.strip():
        raise ArchiveInstallError("Choose a library folder first.")

    default_exe = ""
    old_exe = ""
    # The chosen target's stored path, as stored, for a rollback.
    old_target_exe = ""
    old_root = ""
    # The folder the previous version was installed into. It differs from
    # old_root (the executable's folder) when the archive wrapped the game in
    # a subfolder; saves and targets stay relative to old_root.
    old_install = ""
    old_override = ""
    old_game: dict[str, object] = {}
    game_found = game_id is None
    if game_id is not None:
        for game in get_all_games():
            try:
                if int(str(game.get("id"))) == int(game_id):
                    old_exe = _absolute_game_path(game.get("exe_path"))
                    old_override = _absolute_game_path(game.get("save_path_override"))
                    # Updates keep the stored engine unless the caller sent one.
                    if not engine.strip():
                        engine = str(game.get("engine") or "")
                    old_game = game
                    game_found = True
                    break
            except (TypeError, ValueError):
                continue
        if not game_found:
            raise ArchiveInstallError("The game to update was not found.")
        default_exe = old_exe
        if target_id is not None:
            chosen = next(
                (t for t in list_game_launch_targets(game_id) if t["id"] == target_id),
                None,
            )
            if chosen is None:
                raise ArchiveInstallError(
                    "The launch target does not belong to this game."
                )
            old_exe = _absolute_game_path(chosen["exe_path"])
            old_target_exe = str(chosen["exe_path"] or "")
    elif target_id is not None:
        raise ArchiveInstallError("A game is required when choosing a launch target.")

    from core.database import get_setting, get_library_folders

    library_folders, _default = get_library_folders()
    if library_folder not in library_folders:
        library_folders.append(library_folder)
    if old_exe:
        old_root = os.path.dirname(os.path.abspath(old_exe))
        old_install = _old_install_root(
            old_exe,
            old_game,
            library_folders,
            pattern,
            [(creator, title)],
            engine,
            game_id,
        )
    # The previous-install root is ambiguous when it is a parent of the
    # executable's folder and also holds another target of this game.
    root_ambiguous = False
    if old_install and old_root and _real(old_install) != _real(old_root):
        own_paths: list[str] = []
        if target_id is not None and default_exe:
            own_paths.append(default_exe)
        try:
            own_paths.extend(
                _absolute_game_path(t["exe_path"])
                for t in list_game_launch_targets(int(str(game_id)))
                if t["id"] != target_id
            )
        except (OSError, TypeError, ValueError):
            pass
        install_real = _real(old_install)
        exe_dir_real = _real(old_root)
        root_ambiguous = any(
            path
            and _is_within(_real(path), install_real)
            and not _is_within(_real(path), exe_dir_real)
            for path in own_paths
        )
    downloads_folder = ""
    try:
        downloads_folder = str(get_setting("downloads_folder") or "")
    except OSError:
        downloads_folder = ""

    def _resolve_destination(
        resolved_engine: str,
    ) -> tuple[str, str, dict[str, object]]:
        destination = build_destination(
            library_folder, pattern, creator, title, version, resolved_engine
        )
        dest_real = _real(destination)
        if os.path.lexists(destination) and dest_real != old_real:
            raise ArchiveInstallError(
                f"The destination folder already exists: {destination}. Change the version or destination."
            )
        if old_root and dest_real == old_real and os.path.lexists(destination):
            # An in-place update renames the whole folder aside, which would
            # break any other library game stored inside it.
            for other in other_game_paths(game_id):
                if other and _is_within(_real(other), dest_real):
                    raise ArchiveInstallError(
                        f"{destination} also contains another library game. Change the destination pattern so the update goes to a new folder."
                    )
        guard: dict[str, object] = (
            can_remove_old_folder(
                old_install,
                destination,
                library_folders,
                downloads_folder,
                other_game_paths(game_id),
                old_exe,
            )
            if old_root
            else {"removable": False, "reason": ""}
        )
        return destination, dest_real, guard

    old_real = _real(old_install) if old_install else ""
    destination, dest_real, guard = _resolve_destination(engine)

    _progress("preparing")
    _ = cleanup_stale_staging(library_folder)
    # Resume background deletes only where this install could have left one,
    # so a fresh install never touches unrelated (possibly offline) shares.
    _ = sweep_trash(library_folder)
    if old_install:
        old_trash_root = _trash_root_for(old_install, library_folders)
        if old_trash_root and old_trash_root != _real(library_folder):
            _ = sweep_trash(old_trash_root)
    tool: str | None = None
    seeding = False
    if folder_source:
        required_bytes: int | None = _tree_bytes(archive_path)
        seeding = _torrent_seeding(archive_path)
    else:
        tool, tool_error = detect_archive_tool(
            archive_path, library_folder=library_folder, password=password
        )
        if tool_error:
            raise ArchiveInstallError(tool_error)
        required_bytes = uncompressed_size(
            archive_path, library_folder=library_folder, password=password
        )
    # A compressed tar does not record its unpacked size; it needs at least
    # its own size.
    min_free = required_bytes
    if fmt == "tar" and not required_bytes:
        try:
            min_free = os.path.getsize(archive_path)
        except OSError:
            min_free = None
    # A download that is not seeding and sits on the library's drive is only
    # renamed into place, which takes no room; _place_folder checks the room
    # itself if that rename fails and it copies after all.
    if not (
        folder_source and not seeding and _same_drive(archive_path, library_folder)
    ):
        ensure_free_space(library_folder, min_free)
    network = is_network_filesystem(library_folder)
    workers = NETWORK_IO_WORKERS if network else 1
    _check_cancel(cancel)
    staging = create_staging_dir(library_folder)
    staging_path = staging
    moved_from = ""
    # The wrapper folder that flattening removed, put back if a moved
    # download is restored.
    wrapper_name = ""
    # Set when saves are carried into a moved download, to undo them.
    save_undo: _SaveCarryUndo | None = None
    try:
        if not folder_source:
            _progress("extracting")
        # tar reports progress itself, as the part of the archive read so far.
        with _ExtractMeter(staging, required_bytes, None if fmt == "tar" else bytes_cb):
            if folder_source:
                moved_from = _place_folder(
                    archive_path,
                    staging,
                    seeding=seeding,
                    workers=workers,
                    cancel=cancel,
                    progress=_progress,
                    required_bytes=required_bytes,
                )
                _progress("fixing permissions")
                try:
                    _ = apply_execute_bits(staging)
                except OSError:
                    pass
            elif fmt == "zip":
                try:
                    # Execute bits are decided while writing; no second pass needed.
                    _ = extract_zip(
                        archive_path,
                        staging,
                        workers=zip_extract_workers(network),
                        exec_bits=True,
                        cancel=cancel,
                        password=password,
                    )
                except ZipMethodUnsupportedError as exc:
                    # Deflate64 (Windows Explorer's choice for large files), AES
                    # encryption, and other methods zipfile lacks: 7-Zip reads them.
                    fallback = find_7zip_tool(
                        archive_path, library_folder=library_folder
                    )
                    if fallback is None:
                        raise ArchiveInstallError(
                            f"{exc}. Install 7-Zip (7z) to extract this archive."
                        ) from exc
                    _empty_dir(staging)
                    _ = extract_with_tool(
                        fallback, archive_path, staging, cancel, password=password
                    )
                    _progress("fixing permissions")
                    try:
                        _ = apply_execute_bits(staging)
                    except OSError:
                        pass
            elif (
                fmt == "tar"
                and network
                and _extract_tool_via_local_scratch(
                    lambda folder, stop: extract_tar(
                        archive_path, folder, progress=bytes_cb, cancel=stop
                    ),
                    archive_path,
                    staging,
                    workers,
                    required_bytes,
                    cancel,
                )
            ):
                # tarfile writes one file at a time, like 7-Zip and unrar below.
                pass
            elif fmt == "tar":
                _ = extract_tar(archive_path, staging, progress=bytes_cb, cancel=cancel)
                _progress("fixing permissions")
                try:
                    _ = apply_execute_bits(staging)
                except OSError:
                    pass
            elif network and _extract_tool_via_local_scratch(
                lambda folder, stop: extract_with_tool(
                    cast(str, tool), archive_path, folder, stop, password=password
                ),
                archive_path,
                staging,
                workers,
                required_bytes,
                cancel,
            ):
                # 7-Zip/unrar write one file at a time. On a network share every
                # file waits for the server, so they extract on local disk, modes
                # are fixed there, and the finished tree is copied over in parallel.
                pass
            else:
                assert tool is not None
                _ = extract_with_tool(
                    tool, archive_path, staging, cancel, password=password
                )
                _progress("fixing permissions")
                try:
                    _ = apply_execute_bits(staging)
                except OSError:
                    pass
        _check_cancel(cancel)
        single_wrapper = _single_wrapper_name(staging)
        if moved_from and single_wrapper:
            write_moved_record(staging, moved_from, single_wrapper, flattened=False)
        if flatten_single_wrapper(staging):
            wrapper_name = single_wrapper
            if moved_from and wrapper_name:
                write_moved_record(staging, moved_from, wrapper_name, flattened=True)
        # Flattening moves a wrapper's entries up one level, which changes
        # what relative symlinks resolve to (staging/Game/link -> ../x means
        # something different at staging/link). Re-check after the move.
        check_symlinks_inside_staging(staging)

        # The archive root is not always the game folder: an archive can keep
        # the game one level down (e.g. readme.txt + Game/). Saves must land in
        # the folder the new executable will run from, so pick that first.
        staging_install_dir = staging
        picked_exe = ""
        if chosen_relpath:
            chosen_path = os.path.join(staging, *chosen_relpath.split("/"))
            if (
                _relative_inside(chosen_path, staging) is None
                or os.path.islink(chosen_path)
                or not os.path.isfile(chosen_path)
            ):
                raise ArchiveInstallError(
                    "The chosen launch file is not in the archive."
                )
            picked_exe = chosen_path
        elif old_root or ("{engine}" in pattern and not engine.strip()):
            picked_exe = _pick_exe(staging, old_exe, old_root, title)
        if old_root and not picked_exe:
            # An update never changes the library or the old folder without a
            # launch file: list what the archive holds so the user can choose.
            candidates = list_launch_candidates(staging)
            if candidates:
                raise NoLauncherChosenError(candidates)
            raise ArchiveInstallError(
                "No launchable file was found in the archive. Nothing was changed."
            )
        if picked_exe:
            picked_dir = os.path.dirname(picked_exe)
            if picked_dir and _relative_inside(picked_dir, staging) is not None:
                staging_install_dir = picked_dir
        if root_ambiguous and picked_exe:
            rel_to_root = _relative_inside(old_exe, old_install) or ""
            rel_to_exedir = os.path.basename(old_exe)
            picked_rel_now = _relative_inside(picked_exe, staging) or ""
            # A wrapper folder that flattening removed still counts as the
            # archive's own layout (`Bin/Game.exe` is a root-level `Bin`).
            archive_rels = {
                picked_rel_now.replace(os.sep, "/"),
                f"{wrapper_name}/{picked_rel_now}".replace(os.sep, "/")
                if wrapper_name
                else "",
            }
            rel_to_root_posix = rel_to_root.replace(os.sep, "/")
            if (
                rel_to_root_posix not in archive_rels
                and picked_rel_now == rel_to_exedir
            ):
                # The new version has the executable where the target's own
                # folder had it, so only that folder is the previous install.
                old_install = old_root
                old_real = _real(old_install)
                destination, dest_real, guard = _resolve_destination(engine)

        if "{engine}" in pattern and not engine.strip():
            from core.game_scan import detect_engine

            engine = detect_engine(staging, picked_exe)
            if not engine and picked_exe:
                engine = detect_engine(os.path.dirname(picked_exe), picked_exe)
            destination, dest_real, guard = _resolve_destination(engine)

        override_rel: str | None = None
        # Whether override_rel is relative to the install folder (the archive
        # root) rather than the executable's folder.
        override_from_install = False
        if old_root and os.path.isdir(old_root):
            _check_cancel(cancel)
            _progress("carrying saves")
            # Save folders linked to a target that cleanup would delete but
            # that cannot be copied keep the old folder on disk.
            skipped_saves: list[str] = []
            save_copy: Callable[[str, str], object] = shutil.copy2
            if moved_from:
                save_undo = _SaveCarryUndo(staging)
                save_copy = save_undo.copy
            _ = copy_infolder_saves(
                old_root,
                staging_install_dir,
                removed_root=old_install or old_root,
                skipped=skipped_saves,
                copy_function=save_copy,
            )
            # The executable can sit below the install folder (Game/bin/x.exe);
            # saves kept beside that subfolder are deleted with the old
            # install, so carry them relative to the install folder too.
            wraps_exe = bool(
                old_install
                and _real(old_install) != _real(old_root)
                and os.path.isdir(old_install)
            )
            if wraps_exe:
                _ = copy_infolder_saves(
                    old_install, staging, skipped=skipped_saves, copy_function=save_copy
                )
            override_rel = override_relpath(old_override, old_root)
            if override_rel is None and wraps_exe:
                override_rel = override_relpath(old_override, old_install)
                override_from_install = override_rel is not None
            if override_rel is not None:
                override_into = (
                    staging if override_from_install else staging_install_dir
                )
                if save_undo is not None and override_rel != ".":
                    try:
                        save_undo.before_tree(
                            old_override, os.path.join(override_into, override_rel)
                        )
                    except OSError as exc:
                        raise ArchiveInstallError(
                            f"Could not carry saves into the new version: {exc}"
                        ) from exc
                _ = copy_override_saves(old_override, override_rel, override_into)
            if override_rel == ".":
                # The saves folder is the game folder itself. Its save files
                # cannot be told apart from game files, so only the known
                # patterns were carried; keep the old folder for the rest.
                guard = {
                    "removable": False,
                    "reason": "The saves folder is the game folder itself, so the old folder was kept in case it holds other saves.",
                }
            elif skipped_saves:
                guard = {
                    "removable": False,
                    "reason": f'The save folder "{skipped_saves[0]}" is a link that could not be copied, so the old folder was kept.',
                }
            check_symlinks_inside_staging(staging)

        picked_rel = (
            _relative_inside(picked_exe, staging) if picked_exe else None
        ) or ""
        # Last point to cancel: from here on the library changes.
        _check_cancel(cancel)
        _progress("moving into place")
        # A cancel that came in before the stage above was published was
        # accepted, so check again: no later cancel is accepted.
        _check_cancel(cancel)
        old_backup = ""
        try:
            os.makedirs(os.path.dirname(destination) or destination, exist_ok=True)
        except OSError:
            pass
        if old_root and dest_real == old_real and os.path.lexists(destination):
            # Pattern without {version}: swap via .wlib-old-<ts>. When the old
            # folder was deleted or moved away there is nothing to swap, and
            # the new version simply moves into the empty destination below.
            old_backup = f"{destination}.wlib-old-{int(time.time())}"
            try:
                os.rename(destination, old_backup)
            except OSError as exc:
                if getattr(exc, "winerror", None) in (5, 32):
                    # Windows cannot rename a folder while a program has a
                    # file in it open or runs from it.
                    raise ArchiveInstallError(
                        f"{destination} is in use. Close the game and any program "
                        + "or File Explorer window using files in it, then install "
                        + "again. The current version was not changed."
                    ) from exc
                raise ArchiveInstallError(
                    f"Could not replace the current folder: {exc}"
                ) from exc
            old_root_for_targets = old_backup
            try:
                os.rename(staging, destination)
            except OSError as exc:
                try:
                    os.rename(old_backup, destination)
                except OSError:
                    pass
                raise ArchiveInstallError(
                    f"Could not move the new version into place: {exc}"
                ) from exc
            staging = ""  # moved
        else:
            try:
                os.rename(staging, destination)
            except OSError as exc:
                raise ArchiveInstallError(
                    f"Could not move the new version into place: {exc}"
                ) from exc
            staging = ""
            old_root_for_targets = old_root
            old_backup = ""
        if moved_from:
            # The download now is the installed game folder.
            remove_moved_record(staging_path)
            if save_undo is not None:
                save_undo.discard(destination)
    except Exception as exc:
        if staging:
            if moved_from and os.path.isdir(staging):
                # Saves carried into the download are taken out again first.
                if save_undo is not None:
                    _ = save_undo.rollback()
                # A moved download must survive a failed install, in its own
                # layout: a retry's chosen launch file and the torrent's file
                # list are both relative to it. When it cannot go back now,
                # the record keeps it for the next install's cleanup.
                if restore_moved_staging(
                    staging,
                    {"source": moved_from, "wrapper": wrapper_name, "flattened": True},
                ):
                    remove_moved_record(staging)
            else:
                cleanup_staging_dir(staging)
                remove_moved_record(staging)
        long_path = _windows_long_path_error(exc)
        if long_path:
            raise ArchiveInstallError(long_path) from exc
        raise

    # Destination is in place from here on; failures below keep the update.
    kept_reason = ""
    unmoved: list[dict[str, object]] = []
    library_updated = False
    target_rewritten = False
    try:
        _progress("updating library")
        from core.game_scan import detect_engine as _detect_engine

        # Reuse the executable picked in staging: carried saves can add
        # folders beside a wrapper folder, which would stop the scan from
        # descending into it.
        exe_path = ""
        if picked_rel:
            candidate = os.path.join(destination, picked_rel)
            if os.path.isfile(candidate):
                exe_path = candidate
        if not exe_path:
            exe_path = _pick_exe(destination, old_exe, old_root, title)
        # A new game is still added with the details the user entered; the
        # dialog then asks for the executable inside the extracted folder.
        # The same goes for a wishlisted game, which has no executable to keep.
        # An update keeps raising: its row must keep pointing at a working exe.
        needs_executable = not exe_path and (
            game_id is None or (not default_exe and target_id is None)
        )
        if not exe_path and not needs_executable:
            raise ArchiveInstallError(
                "No launchable file was found. The extracted folder was kept; choose the executable manually."
            )
        # Where the game actually runs from. For archives that keep the game
        # below the archive root this is not `destination`, and saves, the
        # saves override and launch targets are relative to it.
        new_install_dir = (os.path.dirname(exe_path) if exe_path else "") or destination
        detected_engine = ""
        try:
            detected_engine = _detect_engine(
                os.path.dirname(exe_path) if exe_path else destination, exe_path
            )
        except OSError:
            detected_engine = ""

        from core import database as _db

        if game_id is None:
            new_id = _db.add_game(
                title.strip(),
                exe_path,
                version=version.strip(),
                developer=creator.strip(),
                engine=engine.strip() or detected_engine,
                cover_image=cover_image,
                tags=tags,
                rating=rating,
                lc_url=lc_url,
                lc_dismissed=lc_dismissed,
                screenshots=screenshots,
            )
            game_id = int(str(new_id)) if new_id is not None else None
            library_updated = game_id is not None
        else:
            fields: dict[str, object] = {"version": version.strip()}
            if target_id is None:
                fields["exe_path"] = exe_path
            else:
                _ = update_game_launch_target(target_id, {"exe_path": exe_path})
                # Written before the game row; a failed row update puts it back.
                target_rewritten = True
            if engine.strip():
                fields["engine"] = engine.strip()
            elif detected_engine:
                # Only fill engine when the row has none (don't clobber manual edits).
                for game in get_all_games():
                    try:
                        if int(str(game.get("id"))) == int(game_id):
                            if not str(game.get("engine") or "").strip():
                                fields["engine"] = detected_engine
                            break
                    except (TypeError, ValueError):
                        continue
            if override_rel is not None:
                new_override = os.path.normpath(
                    os.path.join(
                        destination if override_from_install else new_install_dir,
                        override_rel,
                    )
                )
                if new_override != os.path.normpath(old_override):
                    fields["save_path_override"] = new_override
            _ = _db.update_game(int(game_id), fields)
            library_updated = True
            if old_root_for_targets and os.path.isdir(destination):
                try:
                    targets = list_game_launch_targets(int(game_id))
                except OSError:
                    targets = []
                target_dicts: list[dict[str, object]] = [
                    {"label": t["label"], "exe_path": t["exe_path"], "id": t["id"]}
                    for t in targets
                    if t["id"] != target_id
                ]
                if target_id is not None and default_exe:
                    target_dicts.append(
                        {
                            "id": None,
                            "label": str(
                                old_game.get("default_target_label") or ""
                            ).strip()
                            or "Default",
                            "exe_path": default_exe,
                        }
                    )
                updated_targets, unmoved = relocate_launch_targets(
                    old_root, new_install_dir, target_dicts
                )
                if old_backup:
                    updated_targets, unmoved = _point_missing_at_backup(
                        updated_targets, unmoved, destination, old_backup
                    )
                for before, after in zip(target_dicts, updated_targets, strict=True):
                    if before["exe_path"] != after["exe_path"]:
                        try:
                            if before["id"] is None:
                                _ = _db.update_game(
                                    int(game_id), {"exe_path": str(after["exe_path"])}
                                )
                            else:
                                _ = update_game_launch_target(
                                    int(str(before["id"])),
                                    {"exe_path": str(after["exe_path"])},
                                )
                        except (OSError, ValueError):
                            unmoved.append(dict(before))
                # Only unresolved targets block cleanup. In-place updates retain
                # their old backup when a target was absent from the new tree,
                # including targets outside the executable's folder that
                # relocation leaves as they were.
                remaining = (
                    [
                        t
                        for t in updated_targets
                        if not os.path.isfile(str(t.get("exe_path") or ""))
                    ]
                    if old_backup
                    else updated_targets + unmoved
                )

                def _target_inside(t: dict[str, object], root: str) -> bool:
                    exe = str(t.get("exe_path") or "")
                    return bool(exe) and _is_within(_real(exe), _real(root))

                inside = next(
                    (t for t in remaining if _target_inside(t, old_install)), None
                )
                if inside is None and old_backup:
                    # A target repointed into the kept backup keeps it on disk.
                    inside = next(
                        (t for t in updated_targets if _target_inside(t, old_backup)),
                        None,
                    )
                if inside is not None:
                    guard = {
                        "removable": False,
                        "reason": f'The folder still contains this game\'s "{inside["label"]}" target.',
                    }

        _progress("cleaning up")
        removed_old = False
        if game_id is not None and old_root and remove_old:
            if guard.get("removable"):
                cleanup_root = old_backup or old_install
                if _real(cleanup_root) != dest_real:
                    cleanup_trash = _trash_root_for(cleanup_root, library_folders)
                    if _is_windows() and cleanup_trash and os.path.isdir(cleanup_root):
                        # Windows refuses the rename while a program has a file
                        # open in the folder, often a game that wLib did not
                        # start. Deleting in place would remove only the files
                        # it is not using, so the folder is kept instead.
                        if defer_remove_tree(cleanup_root, cleanup_trash):
                            removed_old = True
                        else:
                            kept_reason = (
                                f"{cleanup_root} could not be moved to the trash, so it "
                                + "was kept. A program may still use it: close it, "
                                + "then delete the folder."
                            )
                    else:
                        errors: list[str] = []
                        # On a network share deleting thousands of files takes
                        # as long as extracting them; move the old version
                        # aside (instant) and delete it in the background.
                        if not (
                            is_network_filesystem(cleanup_root)
                            and defer_remove_tree(cleanup_root, cleanup_trash)
                        ):
                            errors = remove_tree_collect(cleanup_root)
                        if errors:
                            kept_reason = (
                                f"Could not fully remove {cleanup_root}: {errors[0]}"
                            )
                        else:
                            removed_old = True
                else:
                    kept_reason = "The old and new folders are the same."
            else:
                kept_reason = str(guard.get("reason") or "")
        elif old_backup:
            # Same-folder update with remove unticked: the old contents were
            # already replaced, so report where the renamed backup was kept.
            kept_reason = f"Previous contents kept at {old_backup}."

        # Archive deletion is always last and only on success.
        # A torrent's files go with the torrent (see TorrentSupervisor), never here.
        if delete_archive and not folder_source and not in_torrent_area(archive_path):
            try:
                # Every part of a multi-part set goes, not only the first.
                for part in volumes.parts if volumes is not None else (archive_path,):
                    os.remove(part)
            except OSError as exc:
                kept_reason = (
                    f"{kept_reason}; could not delete archive: {exc}"
                    if kept_reason
                    else f"Could not delete archive: {exc}"
                )

        return {
            "success": True,
            "game_id": game_id,
            "exe_path": exe_path,
            "destination": destination,
            "unmoved_targets": unmoved,
            "kept_old_folder_reason": kept_reason,
            "removed_old": removed_old,
            "old_folder": old_install,
            "needs_executable": needs_executable,
        }
    except Exception as exc:
        # Step 5 failure after the move: the previous install was renamed aside
        # for an in-place update and the library still points into it. Restore
        # it so the game stays usable, and keep the extracted tree beside it
        # for the manual-executable flow the message offers.
        def _restore_target() -> None:
            # The target was pointed at the new version before the game row
            # failed; the previous version is what stays installed.
            if not (target_rewritten and target_id is not None and old_target_exe):
                return
            try:
                _ = update_game_launch_target(target_id, {"exe_path": old_target_exe})
            except Exception as target_exc:
                _log.error(
                    "Could not restore launch target %s to %s: %s",
                    target_id,
                    old_target_exe,
                    target_exc,
                )

        if old_backup and not library_updated:
            kept_new = f"{destination}.wlib-new-{time.time_ns()}"
            try:
                os.rename(destination, kept_new)
                os.rename(old_backup, destination)
            except OSError:
                # Put the new tree back so the state matches the original error.
                try:
                    if not os.path.lexists(destination):
                        os.rename(kept_new, destination)
                except OSError:
                    pass
                raise
            _restore_target()
            raise ArchiveInstallError(
                f"{str(exc) or 'The update could not be completed.'} The extracted folder was kept at {kept_new} and the previous version was restored."
            ) from exc
        if not library_updated:
            # The previous folder was left as it was, so the target goes back to it.
            _restore_target()
        # Step 6 failures are reported in the result, not raised; nothing else
        # after the library update is allowed to abandon the new version.
        raise


def _squash_key(value: str) -> str:
    # Keep letters and digits from every script. Dropping non-ASCII characters
    # made every non-Latin title compare equal to every other one.
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _dismissal_key(path: str, size: int, mtime: float) -> str:
    return f"{path}|{size}|{mtime}"


def _load_dismissals() -> list[str]:
    import json

    from core.database import get_setting

    try:
        raw = get_setting("dismissed_download_archives") or "[]"
    except OSError:
        return []
    try:
        raw_items: object = cast(object, json.loads(raw))
    except (ValueError, TypeError):
        return []
    if not isinstance(raw_items, list):
        return []
    items = cast(list[object], raw_items)
    return [str(item) for item in items if isinstance(item, (str, int, float))]


def dismiss_download_offer(key: str) -> None:
    """Persist a dismissal key, capped at the 200 most recent."""
    import json

    from core.database import update_setting

    key = (key or "").strip()
    if not key:
        return
    items = [k for k in _load_dismissals() if k != key]
    items.insert(0, key)
    try:
        update_setting("dismissed_download_archives", json.dumps(items[:200]))
    except OSError:
        pass


def _volume_set_finished(volumes: VolumeSet, in_progress: set[str], now: float) -> bool:
    """True when no part of the set is still arriving.

    A set looks complete while its last part is still downloading under a
    browser's temporary name, so a part being written (temporary file of the
    next number, or a part changed in the last 15 seconds) holds the offer.
    Sets whose parts all have the first part's size probably continue, so they
    wait too; only the last part of a split archive is shorter.
    """
    sizes: list[int] = []
    for part in volumes.parts:
        try:
            info = os.stat(part)
        except OSError:
            return False
        if now - info.st_mtime < 15:
            return False
        sizes.append(info.st_size)
    lowered = [os.path.basename(part).lower() for part in volumes.parts]
    for candidate in in_progress:
        if any(_same_set(candidate, name) for name in lowered):
            return False
    return volumes.kind == "zip_span" or len(set(sizes)) > 1


def _same_set(candidate: str, part_name: str) -> bool:
    """Whether a file name is another part of the set `part_name` is in."""
    for pattern in (_RAR_PART_RE, _SPLIT_RE, _RAR_OLD_RE, _ZIP_SPAN_RE):
        left, right = pattern.match(candidate), pattern.match(part_name)
        if left and right:
            return left.group("base") == right.group("base")
    return False


def find_download_offers(*, now: float | None = None) -> list[dict[str, object]]:
    """Scan the Downloads folder for archives matching pending updates."""
    import time as _time

    from core.database import get_all_games, get_setting
    from core.game_scan import guess_title_and_version

    try:
        enabled = (get_setting("download_detection_enabled") or "true") == "true"
    except OSError:
        enabled = True
    if not enabled:
        return []
    try:
        folder = (get_setting("downloads_folder") or "").strip()
    except OSError:
        return []
    if not folder or not os.path.isdir(folder):
        return []

    try:
        names = os.listdir(folder)
    except OSError:
        return []
    dismissals = set(_load_dismissals())
    current = now if now is not None else _time.time()

    pending: list[dict[str, object]] = []
    try:
        for game in get_all_games():
            # A game without an executable is wishlisted: it has no installed
            # version to update, and the library treats it as having no update.
            if not str(game.get("exe_path") or "").strip():
                continue
            installed = str(game.get("version") or "")
            latest = str(game.get("latest_version") or "")
            if not has_update(installed, latest, game.get("skipped_version")):
                continue
            key = _squash_key(str(game.get("title") or ""))
            if not key:
                continue
            try:
                gid = int(str(game.get("id", 0)))
            except (TypeError, ValueError):
                continue
            pending.append(
                {
                    "id": gid,
                    "title": str(game.get("title") or ""),
                    "key": key,
                    "version": installed,
                    "latest_version": latest,
                }
            )
    except OSError:
        return []
    if not pending:
        return []
    by_key: dict[str, list[dict[str, object]]] = {}
    for game in pending:
        by_key.setdefault(str(game["key"]), []).append(game)

    offers: list[dict[str, object]] = []
    # Browser downloads in progress, by the name they will finish with.
    in_progress = {
        re.sub(r"\.(?:crdownload|part|download|opdownload)$", "", name.lower())
        for name in names
        if re.search(r"\.(?:crdownload|part|download|opdownload)$", name.lower())
    }
    for name in sorted(names):
        lower = name.lower()
        if not (archive_suffix(lower) or _SPLIT_RE.match(lower)):
            continue
        path = os.path.join(folder, name)
        volumes = volume_set(path)
        if volumes is not None:
            # One offer per set, through its first part, once every part is
            # here and none is still downloading.
            if os.path.normcase(volumes.first) != os.path.normcase(path):
                continue
            if not volumes.complete or not _volume_set_finished(
                volumes, in_progress, current
            ):
                continue
        try:
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            stat_result = os.stat(path)
        except OSError:
            continue
        if current - stat_result.st_mtime < 15:
            continue
        key = _dismissal_key(path, stat_result.st_size, stat_result.st_mtime)
        if key in dismissals:
            continue
        stem = archive_stem(name)
        guessed_title, guessed_version = guess_title_and_version(stem)
        squashed = _squash_key(guessed_title)
        if not squashed:
            continue
        games = by_key.get(squashed, [])
        if not games:
            continue
        for game in games:
            if guessed_version and normalize_version(
                guessed_version
            ) == normalize_version(game["version"]):
                continue
            offers.append(
                {
                    "game_id": game["id"],
                    "game_title": game["title"],
                    "installed_version": game["version"],
                    "latest_version": game["latest_version"],
                    "archive_path": path,
                    "archive_version": guessed_version,
                    "guessed_title": guessed_title,
                    "key": key,
                }
            )
    return offers


# Old private names, kept for callers and tests that still use them.
_tree_bytes = tree_bytes
_real = real_path
_is_within = is_within_real
_relative_inside = relative_inside
_old_install_root = old_install_root
_absolute_game_path = absolute_game_path
_trash_root_for = trash_root_for
