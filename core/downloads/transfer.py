# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared archive transfer and validation."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import BinaryIO, Callable, Protocol

from core.host_platform import get_data_subdir


class DownloadError(Exception):
    """An expected, user-visible download failure."""


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
) -> Path:
    """Transfer to a temporary file, then validate and atomically publish it."""
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
    if info.size is not None and shutil.disk_usage(directory).free < info.size:
        if info.close:
            info.close()
        raise DownloadError(f"Not enough disk space: {info.size} bytes needed")
    part = directory / f"{job_id}.part"
    target = directory / name
    if target.exists():
        target = directory / f"{Path(name).stem}-{job_id}{suffix}"
    done = 0
    try:
        with info.stream_factory() as stream, part.open("xb") as output:
            while True:
                if cancel.is_set():
                    raise DownloadError("cancelled")
                data = stream.read(1024 * 1024)
                if not data:
                    break
                done += len(data)
                if info.decryptor:
                    data = info.decryptor.update(data)
                _ = output.write(data)
                progress(done, info.size)
        if cancel.is_set():
            raise DownloadError("cancelled")
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
        try:
            os.link(part, target)
        except FileExistsError:
            target = directory / f"{Path(name).stem}-{job_id}{suffix}"
            os.link(part, target)
        except OSError:
            # Filesystems without hard links (FAT, some FUSE mounts).
            os.replace(part, target)
        return target
    finally:
        part.unlink(missing_ok=True)
        if info.close:
            info.close()
