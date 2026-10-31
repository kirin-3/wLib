# SPDX-License-Identifier: GPL-3.0-or-later
"""Device-only, bounded image storage; names never contain source game data."""

from __future__ import annotations

import io
import logging
import threading
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from core import database

MAX_COVER_SIZE = 20 * 1024 * 1024
MAX_TYPE_BOX_SIZE = 64 * 1024
UNSUPPORTED_IMAGE = "The file is not a supported image (JPEG, PNG, WebP, GIF or AVIF)."
COVER_NAME_RE = re.compile(r"[0-9a-f]{32}\.(jpg|png|webp|gif|avif)\Z")
COVER_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "avif": "image/avif",
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
    if header[4:8] == b"ftyp":
        box_size = int.from_bytes(header[:4], "big")
        payload = 8
        if box_size == 1:
            box_size = int.from_bytes(header[8:16], "big")
            payload = 16
        if (
            box_size == len(header)
            and payload + 8 <= box_size <= MAX_TYPE_BOX_SIZE
            and (box_size - payload) % 4 == 0
        ):
            # The minor version between major and compatible brands is not a brand.
            brands = [header[payload : payload + 4]] + [
                header[i : i + 4] for i in range(payload + 8, box_size, 4)
            ]
            if any(brand in (b"avif", b"avis") for brand in brands):
                return "avif"
    raise ValueError(UNSUPPORTED_IMAGE)


def _inspect_image(stream: BinaryIO) -> tuple[str, bytes]:
    """Read a bounded prefix once, shared by validation and byte-preserving copy."""
    source_size = (len(stream.getbuffer()) if isinstance(stream, io.BytesIO)
                   else os.fstat(stream.fileno()).st_size)
    if source_size > MAX_COVER_SIZE:
        raise ValueError("The image is larger than 20 MiB.")
    header = stream.read(12)
    if header[4:8] == b"ftyp":
        size = int.from_bytes(header[:4], "big")
        if size == 1:
            header += stream.read(4)
            if len(header) != 16:
                raise ValueError(UNSUPPORTED_IMAGE)
            size = int.from_bytes(header[8:16], "big")
        if not len(header) <= size <= min(source_size, MAX_TYPE_BOX_SIZE):
            raise ValueError(UNSUPPORTED_IMAGE)
        header += stream.read(size - len(header))
    return _image_type(header), header


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
            raise ValueError(UNSUPPORTED_IMAGE)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


def validate_cover_file(path: str | Path) -> str:
    with _open_image_source(path) as stream:
        extension, _ = _inspect_image(stream)
        return extension


def _store_cover(source: BinaryIO, folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        extension, header = _inspect_image(source)
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


def copy_cover_file(path: str | Path) -> str:
    with _open_image_source(path) as source:
        return _store_cover(source, covers_dir())


def cache_covers_dir() -> Path:
    return covers_dir() / "cache"


def cached_cover_path(name: object) -> Path | None:
    if not isinstance(name, str) or not COVER_NAME_RE.fullmatch(name):
        return None
    folder = cache_covers_dir().resolve()
    path = folder / name
    return path if path.resolve().parent == folder else None


def store_cached_cover(data: bytes) -> str:
    with io.BytesIO(data) as source:
        return _store_cover(source, cache_covers_dir())


def delete_cached_cover(name: object) -> None:
    path = cached_cover_path(name)
    if path is not None:
        path.unlink(missing_ok=True)


class CoverCacheService:
    def __init__(self) -> None:
        self.disabled: bool = False
        self._stop: threading.Event = threading.Event()
        self._wake: threading.Event = threading.Event()
        self._store_lock: threading.Lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._failed: set[str] = set()

    def start(self) -> None:
        if self._thread is None and not self._stop.is_set():
            self._thread = threading.Thread(target=self._run, name="cover-cache", daemon=True)
            self._thread.start()
            self.wake()

    def wake(self) -> None:
        if not self.disabled:
            self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def status(self) -> dict[str, object]:
        with self._store_lock:
            sizes: list[int] = []
            for name in database.referenced_cover_cache_names():
                path = cached_cover_path(name)
                try:
                    if path is not None and path.is_file():
                        sizes.append(path.stat().st_size)
                except OSError:
                    pass
            return {"success": True, "count": len(sizes), "bytes": sum(sizes),
                    "running": bool(self._thread and self._thread.is_alive() and not self.disabled)}

    def purge(self) -> None:
        with self._store_lock:
            self.disabled = True
            database.clear_game_cover_cache()
            if cache_covers_dir().exists():
                for path in cache_covers_dir().iterdir():
                    if path.is_file() or path.is_symlink():
                        path.unlink(missing_ok=True)

    def delete_game(self, game_id: int) -> None:
        with self._store_lock:
            game = database.get_game(game_id)
            database.delete_game(game_id)
            if game:
                delete_cached_cover(game.get("cover_cache"))

    def _sweep(self) -> None:
        with self._store_lock:
            names = database.referenced_cover_cache_names()
            if cache_covers_dir().exists():
                for path in cache_covers_dir().iterdir():
                    if path.name not in names and (path.is_file() or path.is_symlink()):
                        path.unlink(missing_ok=True)

    def _fill(self) -> None:
        from core.scraper import f95_get_bytes

        for game in database.get_cover_cache_work():
            if self.disabled or self._stop.is_set():
                return
            game_id = int(str(game["id"]))
            url = str(game.get("cover_image_path") or "")
            with self._store_lock:
                if self.disabled or self._stop.is_set():
                    return
                path = cached_cover_path(game.get("cover_cache"))
                if url and game.get("cover_cache_url") == url and path is not None and path.is_file():
                    continue
                delete_cached_cover(game.get("cover_cache"))
                database.clear_game_cover_cache(game_id)
            if not url or url in self._failed:
                continue
            try:
                data = f95_get_bytes(url, MAX_COVER_SIZE)
                with self._store_lock:
                    if self.disabled or self._stop.is_set():
                        return
                    name = store_cached_cover(data)
                    try:
                        if not database.set_game_cover_cache(game_id, url, name):
                            delete_cached_cover(name)
                    except Exception:
                        delete_cached_cover(name)
                        raise
            except Exception as exc:
                self._failed.add(url)
                logging.getLogger(__name__).info("Cover cache skipped %s: %s", game_id, exc)

    def _run(self) -> None:
        try:
            self._sweep()
            while not self._stop.is_set():
                _ = self._wake.wait()
                self._wake.clear()
                if not self.disabled and not self._stop.is_set():
                    self._fill()
        except Exception:
            logging.getLogger(__name__).exception("Cover cache worker stopped")
