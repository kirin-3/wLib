# SPDX-License-Identifier: GPL-3.0-or-later
"""Device-only, bounded image storage; names never contain source game data."""

from __future__ import annotations

import hashlib
import io
import logging
import threading
import time
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path
from collections import OrderedDict
from typing import BinaryIO
from collections.abc import Callable, Iterable
from uuid import uuid4

from core import database, lewdcorner

MAX_COVER_SIZE = 20 * 1024 * 1024
MAX_TYPE_BOX_SIZE = 64 * 1024
UNSUPPORTED_IMAGE = "The file is not a supported image (JPEG, PNG, WebP, GIF or AVIF)."
COVER_NAME_RE = re.compile(r"[0-9a-f]{32}\.(jpg|png|webp|gif|avif)\Z")
# Device-local copies of LC screenshots in the cover cache folder.
LC_COPY_RE = re.compile(r"lc-([0-9a-f]{32})\.(jpg|png|webp|gif|avif)\Z")
LC_KEY_RE = re.compile(r"[0-9a-f]{32}\Z")
COVER_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "avif": "image/avif",
}


def covers_dir() -> Path:
    return Path(database.DATA_DIR, "covers")


def _child_entry(folder: Path, name: str) -> tuple[Path, os.stat_result | None] | None:
    """``folder / name`` and its stat (links followed; None when missing).

    None when the path leaves ``folder``. ``folder`` must be resolved, and
    ``name`` must already match a strict name pattern (no separators, no
    ``..``). Then only the last part can be a link, so one ``lstat`` decides;
    the full ``resolve()`` check runs only for links. A missing file gives its
    path, as a non-strict ``resolve()`` would."""
    path = folder / name
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return path, None
    except OSError:
        pass
    else:
        if not stat.S_ISLNK(info.st_mode):
            return path, info
    if path.resolve().parent != folder:
        return None
    try:
        return path, os.stat(path)
    except OSError:
        return path, None


def child_in_folder(folder: Path, name: str) -> Path | None:
    """``folder / name`` when it stays in ``folder``, else None (see ``_child_entry``)."""
    entry = _child_entry(folder, name)
    return None if entry is None else entry[0]


def child_file(folder: Path, name: str) -> tuple[Path, os.stat_result] | None:
    """``folder / name`` and its stat when it is a regular file that stays in
    ``folder``; same rules as ``child_in_folder``."""
    entry = _child_entry(folder, name)
    if entry is None or entry[1] is None or not stat.S_ISREG(entry[1].st_mode):
        return None
    return entry[0], entry[1]


def cover_path(name: object, *, folder: Path | None = None) -> Path | None:
    """A local cover file path; ``folder`` is the resolved covers folder, if known."""
    if not isinstance(name, str) or not COVER_NAME_RE.fullmatch(name):
        return None
    return child_in_folder(folder or covers_dir().resolve(), name)


def cover_file(name: object, *, folder: Path | None = None) -> tuple[Path, os.stat_result] | None:
    """A local cover that exists as a regular file, with its stat."""
    if not isinstance(name, str) or not COVER_NAME_RE.fullmatch(name):
        return None
    return child_file(folder or covers_dir().resolve(), name)


def image_looks_complete(data: bytes) -> bool:
    """False for an image whose end is missing (a download cut off midway).

    Checks the end marker of the formats that have one; others pass."""
    try:
        kind = _image_type(data[:64])
    except ValueError:
        return True
    if kind == "jpg":
        # Some encoders append padding or metadata after the end marker.
        return b"\xff\xd9" in data[-1024:]
    if kind == "png":
        return b"IEND" in data[-16:]
    if kind == "gif":
        return data.rstrip(b"\x00").endswith(b"\x3b")
    if kind == "webp":
        return len(data) >= int.from_bytes(data[4:8], "little") + 8
    return True


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


def validate_image_bytes(data: bytes) -> str:
    """The extension of a supported image in ``data``; ValueError otherwise."""
    with io.BytesIO(data) as stream:
        extension, _ = _inspect_image(stream)
    return extension


def validate_cover_file(path: str | Path) -> str:
    with _open_image_source(path) as stream:
        extension, _ = _inspect_image(stream)
        return extension


def _store_cover(source: BinaryIO, folder: Path, stem: str = "") -> str:
    folder.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        extension, header = _inspect_image(source)
        name = f"{stem or uuid4().hex}.{extension}"
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


def cached_cover_path(name: object, *, folder: Path | None = None) -> Path | None:
    """A cached cover file path; ``folder`` is the resolved cache folder, if known."""
    if not isinstance(name, str) or not COVER_NAME_RE.fullmatch(name):
        return None
    return child_in_folder(folder or cache_covers_dir().resolve(), name)


def cached_cover_file(name: object, *, folder: Path | None = None) -> tuple[Path, os.stat_result] | None:
    """A cached cover that exists as a regular file, with its stat."""
    if not isinstance(name, str) or not COVER_NAME_RE.fullmatch(name):
        return None
    return child_file(folder or cache_covers_dir().resolve(), name)


def store_cached_cover(data: bytes) -> str:
    with io.BytesIO(data) as source:
        return _store_cover(source, cache_covers_dir())


def delete_cached_cover(name: object) -> None:
    path = cached_cover_path(name)
    if path is not None:
        path.unlink(missing_ok=True)


def lc_image_key(url: str) -> str:
    """The local key of an LC image URL: 32 hex characters of its SHA-256."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def lc_copy_file(key: str) -> Path | None:
    """The cached copy of an LC screenshot, when one exists as a regular file."""
    if not LC_KEY_RE.fullmatch(key):
        return None
    folder = cache_covers_dir().resolve()
    for extension in COVER_TYPES:
        entry = child_file(folder, f"lc-{key}.{extension}")
        if entry is not None:
            return entry[0]
    return None


def store_lc_copy(key: str, data: bytes) -> Path:
    if not LC_KEY_RE.fullmatch(key):
        raise ValueError("Invalid LewdCorner image key")
    with io.BytesIO(data) as source:
        return cache_covers_dir() / _store_cover(source, cache_covers_dir(), f"lc-{key}")


def fetch_lc_image(url: str, *, background: bool) -> bytes:
    """An LC image through the browser extension; wLib never requests LC itself.

    Raises ``BridgeUnavailable`` when no extension can fetch LC images, and
    ``ValueError`` or ``BridgeFailed`` for an answer that is not an image."""
    from core import extension_bridge

    answer = extension_bridge.fetch_image(url, background=background)
    content_type = answer.content_type.split(";", 1)[0].strip().lower()
    if answer.status != 200:
        raise ValueError(f"LewdCorner answered HTTP {answer.status}")
    if content_type not in COVER_TYPES.values():
        raise ValueError(f"LewdCorner answered {content_type or 'no content type'}, not an image")
    if not answer.body or len(answer.body) > MAX_COVER_SIZE:
        raise ValueError("The LewdCorner image is empty or larger than 20 MiB")
    _ = validate_image_bytes(answer.body)
    if not image_looks_complete(answer.body):
        raise ValueError("The LewdCorner image was cut off")
    return answer.body


class LcImageKeys:
    """Bounded in-memory ``key -> LC URL`` map for local screenshot addresses.

    Only ``get_game_screenshots`` fills it, so another local program cannot
    make wLib fetch an LC URL of its choice. The keys are random for each
    app session (one per URL), not ``lc_image_key``: that hash names the disk
    copy, and a program that knows the URL could work it out and read the
    image without a pairing token."""

    LIMIT: int = 500

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._urls: OrderedDict[str, str] = OrderedDict()
        self._keys: dict[str, str] = {}

    def add(self, url: str) -> str:
        with self._lock:
            key = self._keys.get(url)
            if key is None:
                key = secrets.token_hex(16)
                self._keys[url] = key
            self._urls[key] = url
            self._urls.move_to_end(key)
            while len(self._urls) > self.LIMIT:
                _, old_url = self._urls.popitem(last=False)
                _ = self._keys.pop(old_url, None)
        return key

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._urls.get(key)


LC_IMAGE_ADDRESS = "http://127.0.0.1:8183/lc-images/"

RETRY_BASE_SECONDS = 15 * 60
RETRY_MAX_SECONDS = 24 * 60 * 60
ERROR_RETRY_SECONDS = 60


class CoverCacheService:
    def __init__(
        self, on_change: Callable[[], None] | None = None,
        lc_screenshot_urls: Callable[[], Iterable[str]] | None = None,
        on_lc_cover: Callable[[], None] | None = None,
    ) -> None:
        self.on_change: Callable[[], None] | None = on_change
        # Called after an LC cover copy is saved: until the window reloads the
        # library, it shows the placeholder (it never loads the LC URL).
        self.on_lc_cover: Callable[[], None] | None = on_lc_cover
        # Every LC screenshot URL a library game shows; copies of others are deleted.
        self.lc_screenshot_urls: Callable[[], Iterable[str]] | None = lc_screenshot_urls
        self.disabled: bool = False
        self._stop: threading.Event = threading.Event()
        self._wake: threading.Event = threading.Event()
        self._store_lock: threading.Lock = threading.Lock()
        self._thread: threading.Thread | None = None
        # url -> (failures, monotonic time it may be tried again)
        self._failed: dict[str, tuple[int, float]] = {}
        # Monotonic time background LC fetches may start again after the hourly limit.
        self._lc_resume_at: float = 0.0

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
            for path in self._lc_copies():
                try:
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
                # The row is gone; a stale cache file must not fail the removal.
                try:
                    delete_cached_cover(game.get("cover_cache"))
                except OSError as exc:
                    logging.getLogger(__name__).warning(
                        "Could not delete cached cover of game %s: %s", game_id, exc
                    )

    @staticmethod
    def _lc_copies() -> list[Path]:
        folder = cache_covers_dir()
        if not folder.exists():
            return []
        return [path for path in folder.iterdir() if LC_COPY_RE.fullmatch(path.name) and path.is_file()]

    def _sweep(self) -> None:
        with self._store_lock:
            names = database.referenced_cover_cache_names()
            if cache_covers_dir().exists():
                for path in cache_covers_dir().iterdir():
                    if LC_COPY_RE.fullmatch(path.name):
                        continue
                    if path.name not in names and (path.is_file() or path.is_symlink()):
                        path.unlink(missing_ok=True)
        self._sweep_lc_copies()

    def _sweep_lc_copies(self) -> None:
        """Delete LC screenshot copies that no library game shows any more."""
        if self.lc_screenshot_urls is None:
            return
        keys = {lc_image_key(url) for url in self.lc_screenshot_urls() if lewdcorner.fetchable_image_url(url)}
        with self._store_lock:
            for path in self._lc_copies():
                match = LC_COPY_RE.fullmatch(path.name)
                if match is not None and match.group(1) not in keys:
                    path.unlink(missing_ok=True)

    def store_lc_screenshot(self, key: str, data: bytes) -> Path | None:
        """Keep a copy of a fetched LC screenshot; None after a purge turned the cache off."""
        with self._store_lock:
            if self.disabled:
                return None
            return store_lc_copy(key, data)

    def _fill(self) -> None:
        from core import extension_bridge
        from core.scraper import f95_get_bytes

        # URLs still waiting on a retry; a failure for any other URL (the game
        # was removed or its cover changed) is dropped after the pass, or its
        # past retry time would wake the loop every second.
        waiting: set[str] = set()
        # LC covers go after the F95 ones, so LC pacing never delays them.
        lc_work: list[tuple[int, str]] = []
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
            if not url:
                continue
            if lewdcorner.hosts_url(url):
                # Other LC images (thumbnails, proxy images) are never fetched.
                if lewdcorner.fetchable_image_url(url):
                    lc_work.append((game_id, url))
            elif not self._fetch_and_store(game_id, url, lambda url: f95_get_bytes(url, MAX_COVER_SIZE), waiting):
                return
        for index, (game_id, url) in enumerate(lc_work):
            # A missing extension is not a failure: the URL waits, without a
            # timer, until an extension with LC image support connects. The
            # hourly limit is not a failure either: the rest waits for it.
            # Waiting URLs keep their failure counts.
            if time.monotonic() < self._lc_resume_at or not extension_bridge.can_fetch_lc_images():
                waiting.update(url for _game_id, url in lc_work[index:])
                break
            try:
                if not self._fetch_and_store(game_id, url, lambda url: fetch_lc_image(url, background=True), waiting):
                    return
                if self.on_lc_cover is not None and url not in self._failed:
                    self.on_lc_cover()
            except extension_bridge.LcImageLimitReached as exc:
                self._lc_resume_at = time.monotonic() + max(1.0, exc.retry_in)
                waiting.update(url for _game_id, url in lc_work[index:])
                break
            except extension_bridge.BridgeUnavailable:
                waiting.update(url for _game_id, url in lc_work[index:])
                break
        for url in [url for url in self._failed if url not in waiting]:
            del self._failed[url]

    def _fetch_and_store(
        self, game_id: int, url: str, download: Callable[[str], bytes], waiting: set[str],
    ) -> bool:
        """Cache one remote cover; False when the pass must stop (purge or stop).

        Re-raises ``BridgeUnavailable`` and ``LcImageLimitReached``, which
        are not failures of the URL."""
        from core import extension_bridge

        failure = self._failed.get(url)
        if failure is not None and time.monotonic() < failure[1]:
            waiting.add(url)
            return True
        try:
            data = download(url)
            if not image_looks_complete(data):
                # Cached for good otherwise: the URL never changes.
                raise ValueError("The cover download was cut off")
            with self._store_lock:
                if self.disabled or self._stop.is_set():
                    return False
                name = store_cached_cover(data)
                try:
                    if not database.set_game_cover_cache(game_id, url, name):
                        delete_cached_cover(name)
                    elif self.on_change is not None:
                        self.on_change()
                except Exception:
                    delete_cached_cover(name)
                    raise
            _ = self._failed.pop(url, None)
        except (extension_bridge.BridgeUnavailable, extension_bridge.LcImageLimitReached):
            raise
        except Exception as exc:
            count = (failure[0] if failure else 0) + 1
            delay = min(RETRY_BASE_SECONDS * (1 << (count - 1)), RETRY_MAX_SECONDS)
            self._failed[url] = (count, time.monotonic() + delay)
            waiting.add(url)
            logging.getLogger(__name__).info("Cover cache skipped %s: %s", game_id, exc)
        return True

    def _next_retry_in(self) -> float | None:
        from core import extension_bridge

        # LC URLs wait without a timer while no extension can fetch them (its
        # connection wakes the cache), and at least until the hourly limit ends.
        lc_ready = extension_bridge.can_fetch_lc_images()
        dues: list[float] = []
        for url, (_count, due) in self._failed.items():
            if not lewdcorner.hosts_url(url):
                dues.append(due)
            elif lc_ready:
                dues.append(max(due, self._lc_resume_at))
        if lc_ready and self._lc_resume_at > time.monotonic():
            dues.append(self._lc_resume_at)
        if not dues:
            return None
        return max(1.0, min(dues) - time.monotonic())

    def _run(self) -> None:
        log = logging.getLogger(__name__)
        try:
            self._sweep()
        except Exception:
            log.exception("Cover cache sweep failed")
        timeout: float | None = None
        while not self._stop.is_set():
            _ = self._wake.wait(timeout)
            self._wake.clear()
            if self.disabled or self._stop.is_set():
                timeout = None
                continue
            try:
                self._fill()
                timeout = self._next_retry_in()
            except Exception:
                # A database error must not end caching for the session.
                log.exception("Cover cache pass failed")
                timeout = ERROR_RETRY_SECONDS
