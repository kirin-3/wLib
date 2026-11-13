# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared archive transfer and validation."""
from __future__ import annotations

import errno
import hashlib
import http.client
import os
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from collections.abc import Mapping
from typing import BinaryIO, Protocol, cast
from collections.abc import Callable

from core.downloads.pacing import SpeedLimiter
from core.host_platform import get_data_subdir


class DownloadError(Exception):
    """An expected, user-visible download failure."""


class DownloadPaused(DownloadError):
    """The transfer stopped because its download was paused.

    A resumable partial file is kept so the download continues from it."""

    def __init__(self) -> None:
        super().__init__("paused")


def disk_error(exc: OSError) -> DownloadError:
    """A write failure in the downloads folder, said as what it is."""
    if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return DownloadError("Not enough disk space for the download")
    return DownloadError(
        f"Could not save the download: {exc.strerror or exc.__class__.__name__}"
    )


class Decryptor(Protocol):
    def update(self, data: bytes) -> bytes: ...
    def finish(self) -> None: ...


@dataclass
class FileInfo:
    name: str
    size: int | None
    stream_factory: Callable[[], BinaryIO]
    decryptor: Decryptor | None = None
    close: Callable[[], None] | None = None
    # Opens the file from a byte offset (an HTTP Range request); returns None
    # when the host ignores the range, so the transfer starts over. Hosts that
    # decrypt while downloading cannot resume and leave it None.
    resume_factory: Callable[[int], BinaryIO | None] | None = None
    # Names the file across runs (host and file ID), so a later download of
    # the same file continues the partial file a failed one left behind.
    resume_key: str = ""


RESUME_PREFIX = "resume-"
# Reconnects after the connection drops mid-file, before the job fails.
RESUME_RETRIES = 3
RESUME_RETRY_DELAY_SECONDS = 2.0
_active_parts: set[Path] = set()
_active_parts_lock = threading.Lock()


def resume_part_name(info: FileInfo) -> str:
    """The partial file a resumable transfer of `info` uses, or "" when it cannot resume."""
    if info.resume_factory is None or not info.resume_key or info.size is None:
        return ""
    digest = hashlib.sha256(f"{info.resume_key}\0{info.size}".encode()).hexdigest()[:24]
    return f"{RESUME_PREFIX}{digest}.part"


def can_resume(info: FileInfo) -> bool:
    return bool(resume_part_name(info))


def _resume_part(directory: Path, info: FileInfo) -> Path | None:
    name = resume_part_name(info)
    return directory / name if name else None


def open_range(
    open_request: Callable[[dict[str, str]], BinaryIO], offset: int
) -> BinaryIO | None:
    """Open a stream from `offset` with a Range header, or None if unsupported.

    `open_request(headers)` sends the host's request with the extra headers.
    A 206 answer whose Content-Range starts at `offset` is the rest of the
    file; anything else (a plain 200) is closed and reported as unsupported.
    """
    stream = open_request({"Range": f"bytes={offset}-"})
    status = cast(object, getattr(stream, "status", None))
    headers = cast(Mapping[str, str] | None, getattr(stream, "headers", None))
    content_range = str(headers.get("Content-Range", "") if headers is not None else "")
    if status == 206 and content_range.startswith(f"bytes {offset}-"):
        return stream
    stream.close()
    return None


class Host(Protocol):
    def matches(self, url: str) -> bool: ...
    def probe(self, url: str) -> FileInfo: ...
    def classify_error(self, status: int, body: str) -> str: ...


def staging_dir() -> Path:
    path = Path(get_data_subdir("downloads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    # Backslashes are separators on Windows, even when running on Linux.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(" .")
    return name[:200] or "download"


def transfer(
    info: FileInfo,
    job_id: str,
    cancel: Event,
    progress: Callable[[int, int | None], None],
    verifying: Callable[[], None] | None = None,
    *,
    directory: Path | None = None,
    pause: Event | None = None,
    limiter: SpeedLimiter | None = None,
    interrupt: Event | None = None,
) -> Path:
    """Transfer to a temporary file, then validate and atomically publish it.

    `pause` stops the transfer with `DownloadPaused` and keeps a resumable
    partial file. `limiter` paces the reads; its waits end at once when
    `interrupt` (by default `cancel`) is set, which the caller sets together
    with `cancel` or `pause`.
    """
    interrupt = interrupt or cancel

    def check_stop() -> None:
        if pause is not None and pause.is_set():
            raise DownloadPaused()
        if cancel.is_set():
            raise DownloadError("cancelled")

    directory = directory or staging_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", job_id):
        if info.close:
            info.close()
        raise DownloadError("Invalid download job")
    name = _safe_name(info.name)
    suffix = Path(name).suffix.lower()
    if suffix not in (".zip", ".rar", ".7z"):
        if info.close:
            info.close()
        raise DownloadError("file invalid")
    resume_part = _resume_part(directory, info)
    if resume_part is not None:
        with _active_parts_lock:
            if resume_part in _active_parts:
                resume_part = None  # Another job is fetching the same file.
            else:
                _active_parts.add(resume_part)
    part = resume_part or directory / f"{job_id}.part"
    keep_part = False
    target = directory / name
    if target.exists():
        target = directory / f"{Path(name).stem}-{job_id}{suffix}"
    done = 0
    try:
        offset = 0
        if resume_part is not None and part.is_file():
            offset = part.stat().st_size
            if info.size is not None and offset > info.size:
                part.unlink()
                offset = 0
        def check_space() -> None:
            if info.size is not None and shutil.disk_usage(directory).free < info.size - offset:
                raise DownloadError(f"Not enough disk space: {info.size - offset} bytes needed")

        check_space()
        stream: BinaryIO | None = None
        if offset and info.resume_factory is not None:
            try:
                stream = info.resume_factory(offset)
            except (OSError, http.client.HTTPException):
                stream = None
            if stream is None:
                part.unlink(missing_ok=True)
                offset = 0
                check_space()
        if stream is None:
            stream = info.stream_factory()
        done = offset
        retries = 0
        # A resumable file keeps what arrived when the connection fails, so the
        # next attempt (this job's retry or a later download) continues it.
        keep_part = resume_part is not None
        with part.open("ab" if offset else "wb") as output:
            try:
                while True:
                    check_stop()
                    size = limiter.chunk_size() if limiter is not None else 1024 * 1024
                    if limiter is not None and not limiter.acquire(size, interrupt):
                        check_stop()
                    try:
                        data = stream.read(size)
                    except (OSError, http.client.HTTPException) as exc:
                        if (
                            info.resume_factory is None
                            or resume_part is None
                            or retries >= RESUME_RETRIES
                        ):
                            raise DownloadError("network error") from exc
                        retries += 1
                        stream.close()
                        output.flush()
                        if interrupt.wait(RESUME_RETRY_DELAY_SECONDS):
                            check_stop()
                        try:
                            resumed = info.resume_factory(done)
                        except (OSError, http.client.HTTPException) as retry_exc:
                            raise DownloadError("network error") from retry_exc
                        if resumed is None:
                            raise DownloadError("network error") from exc
                        stream = resumed
                        continue
                    if not data:
                        break
                    if info.size is not None and done + len(data) > info.size:
                        raise DownloadError("file invalid")
                    done += len(data)
                    if info.decryptor:
                        data = info.decryptor.update(data)
                    try:
                        _ = output.write(data)
                    except OSError as exc:
                        raise disk_error(exc) from exc
                    progress(done, info.size)
            finally:
                stream.close()
        check_stop()
        if verifying:
            verifying()
        if info.size is not None and done != info.size:
            raise DownloadError("network error: incomplete download")
        if info.decryptor:
            info.decryptor.finish()
        with part.open("rb") as stream:
            header = stream.read(8)
        signatures = {".zip": (b"PK\x03\x04",), ".7z": (b"7z\xbc\xaf\x27\x1c",), ".rar": (b"Rar!\x1a\x07",)}
        if not any(header.startswith(signature) for signature in signatures[suffix]):
            raise DownloadError("file invalid")
        if target.exists():
            target = directory / f"{Path(name).stem}-{job_id}{suffix}"
        keep_part = False
        try:
            os.link(part, target)
        except FileExistsError:
            target = directory / f"{Path(name).stem}-{job_id}{suffix}"
            os.link(part, target)
        except OSError:
            # Filesystems without hard links (FAT, some FUSE mounts).
            os.replace(part, target)
        return target
    except DownloadError as exc:
        if str(exc) in ("cancelled", "file invalid") or str(exc).startswith("Not enough"):
            keep_part = False
        raise
    except BaseException:
        keep_part = False
        raise
    finally:
        if resume_part is not None:
            with _active_parts_lock:
                _active_parts.discard(resume_part)
        if keep_part and part.is_file():
            # Touch it: its download's last attempt.
            try:
                os.utime(part)
            except OSError:
                pass
        else:
            part.unlink(missing_ok=True)
        if info.close:
            info.close()
