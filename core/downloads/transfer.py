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
from urllib.error import HTTPError
from collections.abc import Callable

from core.archive_formats import (
    SIGNATURE_BYTES,
    archive_suffix,
    looks_like_archive,
    split_archive_name,
)
from core.downloads.network import cloudflare_refusal
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


def delete_part(name: str, directory: Path | None = None) -> bool:
    """Delete a removed download's partial file, unless a transfer writes it.

    Jobs for the same host file share one resumable partial file. The check
    and the delete hold the lock that `transfer` takes the file with, so a
    transfer never writes to a deleted file. True when the file is gone.
    """
    if not name or "/" in name or "\\" in name or not name.endswith(".part"):
        return False
    path = (directory or staging_dir()) / name
    with _active_parts_lock:
        if path in _active_parts:
            return False
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False
    return True


def open_range(
    open_request: Callable[[dict[str, str]], BinaryIO], offset: int
) -> BinaryIO | None:
    """Open a stream from `offset` with a Range header, or None if unsupported.

    `open_request(headers)` sends the host's request with the extra headers.
    A 206 answer whose Content-Range starts at `offset` is the rest of the
    file; anything else (a plain 200) is closed and reported as unsupported.
    HTTP 429 fails with the same rate-limit message as a full request;
    Cloudflare 1010 fails with the challenge message.
    """
    try:
        stream = open_request({"Range": f"bytes={offset}-"})
    except HTTPError as exc:
        if cloudflare_refusal(exc):
            raise DownloadError("blocked by a challenge") from exc
        if exc.code == 429:
            exc.close()
            raise DownloadError("transfer quota or rate limit reached") from exc
        raise
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


_NAME_BYTES = 200


def _safe_name(name: str) -> str:
    # Backslashes are separators on Windows, even when running on Linux.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(" .")
    # Keep the archive suffix and leave room in the 255-byte filename limit
    # for the `-<job id>` that a name clash adds.
    stem, suffix = split_archive_name(name)
    limit = _NAME_BYTES - len(suffix.encode())
    stem = stem.encode()[:limit].decode(errors="ignore")
    return stem + suffix or "download"


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
    suffix = archive_suffix(name)
    if not suffix:
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
    stem, name_suffix = split_archive_name(name)
    # `Game.tar.bz2` becomes `Game-<job>.tar.bz2`, not `Game.tar-<job>.bz2`.
    unique = directory / f"{stem}-{job_id}{name_suffix}"
    if target.exists():
        target = unique
    done = 0
    try:
        offset = 0
        if resume_part is not None and part.is_file():
            offset = part.stat().st_size
            if info.size is not None and offset > info.size:
                part.unlink()
                offset = 0

        def check_space() -> None:
            if (
                info.size is not None
                and shutil.disk_usage(directory).free < info.size - offset
            ):
                raise DownloadError(
                    f"Not enough disk space: {info.size - offset} bytes needed"
                )

        check_space()
        stream: BinaryIO | None = None
        if offset and info.resume_factory is not None:
            try:
                stream = info.resume_factory(offset)
            except DownloadError:
                keep_part = True
                raise
            except (OSError, http.client.HTTPException) as exc:
                # Another 4xx (a 416 for a complete partial file, a 403)
                # means the range will not work: start over, as before.
                if isinstance(exc, HTTPError) and exc.code != 429 and exc.code < 500:
                    stream = None
                else:
                    # Keep the partial file for a retry after a raw request failure.
                    keep_part = True
                    raise DownloadError("network error") from exc
            if stream is None:
                # Open the full file before the partial is dropped: a host
                # that refuses both (a quota or captcha limit) keeps the
                # bytes for a later retry.
                try:
                    stream = info.stream_factory()
                except DownloadError:
                    keep_part = True
                    raise
                part.unlink(missing_ok=True)
                offset = 0
                try:
                    check_space()
                except BaseException:
                    stream.close()
                    raise
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
            header = stream.read(SIGNATURE_BYTES)
        if not looks_like_archive(header, suffix):
            raise DownloadError("file invalid")
        if target.exists():
            target = unique
        keep_part = False
        try:
            os.link(part, target)
        except FileExistsError:
            target = unique
            os.link(part, target)
        except OSError:
            # Filesystems without hard links (FAT, some FUSE mounts).
            os.replace(part, target)
        return target
    except DownloadError as exc:
        if str(exc) in ("cancelled", "file invalid") or str(exc).startswith(
            "Not enough"
        ):
            keep_part = False
        raise
    except BaseException:
        keep_part = False
        raise
    finally:
        # Delete the partial file before it is released, so a job that takes
        # the same file next never has it deleted under it.
        with _active_parts_lock:
            try:
                if keep_part and part.is_file():
                    # Touch it: its download's last attempt.
                    try:
                        os.utime(part)
                    except OSError:
                        pass
                else:
                    part.unlink(missing_ok=True)
            finally:
                if resume_part is not None:
                    _active_parts.discard(resume_part)
        if info.close:
            info.close()
