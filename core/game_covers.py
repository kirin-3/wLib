# SPDX-License-Identifier: GPL-3.0-or-later
"""Device-only, bounded image storage; names never contain source game data."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from core import database

MAX_COVER_SIZE = 20 * 1024 * 1024
COVER_NAME_RE = re.compile(r"[0-9a-f]{32}\.(jpg|png|webp|gif)\Z")
COVER_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def covers_dir() -> Path:
    return Path(database.DATA_DIR, "covers")


def cover_path(name: object) -> Path | None:
    if not isinstance(name, str) or not COVER_NAME_RE.fullmatch(name):
        return None
    folder = covers_dir().resolve()
    path = folder / name
    if path.resolve().parent != folder:
        return None
    return path


def _image_type(header: bytes) -> str:
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    raise ValueError("The file is not a supported image (JPEG, PNG, WebP or GIF).")


def _open_image_source(path: str | Path) -> BinaryIO:
    """Open a regular file for reading; reject FIFOs/devices before touching them.

    Opening a FIFO for reading blocks until a writer appears, which would hang
    the bridge thread (a picked file, or a cover path in an imported backup).
    O_NONBLOCK makes the open itself safe, then the fstat check rejects
    anything that is not a regular file.
    """
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
    )
    fd = os.open(os.fspath(path), flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(
                "The file is not a supported image (JPEG, PNG, WebP or GIF)."
            )
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


def validate_cover_file(path: str | Path) -> str:
    with _open_image_source(path) as stream:
        if os.fstat(stream.fileno()).st_size > MAX_COVER_SIZE:
            raise ValueError("The image is larger than 20 MiB.")
        return _image_type(stream.read(12))


def copy_cover_file(path: str | Path) -> str:
    folder = covers_dir()
    folder.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with _open_image_source(path) as source:
            if os.fstat(source.fileno()).st_size > MAX_COVER_SIZE:
                raise ValueError("The image is larger than 20 MiB.")
            header = source.read(12)
            extension = _image_type(header)
            name = f"{uuid4().hex}.{extension}"
            with tempfile.NamedTemporaryFile(
                dir=folder, prefix=".cover-", delete=False
            ) as dest:
                temp_name = dest.name
                _ = dest.write(header)
                total = len(header)
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_COVER_SIZE:
                        raise ValueError("The image is larger than 20 MiB.")
                    _ = dest.write(chunk)
            os.replace(temp_name, folder / name)
            temp_name = None
        return name
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def delete_cover_file(name: object) -> None:
    path = cover_path(name)
    if path is not None:
        path.unlink(missing_ok=True)


def set_cover(game_id: int, path: str) -> str:
    name = copy_cover_file(path)
    try:
        old = database.set_game_local_cover(game_id, name)
    except Exception:
        delete_cover_file(name)
        raise
    delete_cover_file(old)
    return name


def clear_cover(game_id: int) -> None:
    delete_cover_file(database.clear_game_local_cover(game_id))
