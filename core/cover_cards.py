# SPDX-License-Identifier: GPL-3.0-or-later
"""Small still and animated copies of animated covers, for dense views.

Copies live in ``covers/cards/`` as ``<source-stem>.still.webp`` and
``<source-stem>.anim.webp``. They are device-local and derived: existence on
disk is the only state, and the still copy is written last so its presence
means the copies are ready."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO
from collections.abc import Callable

from core import database
from core.game_covers import cache_covers_dir, covers_dir
from core.webp_anim import mux_animated_webp

if TYPE_CHECKING:
    from PyQt6.QtGui import QImage, QImageReader

CARD_MAX_WIDTH = 640
MAX_FRAMES = 300
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
WEBP_QUALITY = 80
DEFAULT_DELAY_MS = 100
NOTIFY_INTERVAL_SECONDS = 5.0
STALE_TEMP_SECONDS = 3600
CARD_NAME_RE = re.compile(r"[0-9a-f]{32}\.(still|anim)\.webp\Z")
_SOURCE_RE = re.compile(r"[0-9a-f]{32}\.(jpg|png|webp|gif|avif)\Z")
_CHECK_EXTENSIONS = {".gif", ".webp"}


def cards_dir() -> Path:
    return covers_dir() / "cards"


def card_path(name: object) -> Path | None:
    if not isinstance(name, str) or not CARD_NAME_RE.fullmatch(name):
        return None
    folder = cards_dir().resolve()
    path = folder / name
    return path if path.resolve().parent == folder else None


def card_paths(source_name: object) -> tuple[Path, Path] | None:
    """(still, animated) copy paths for a cover file name, or None if invalid."""
    if not isinstance(source_name, str) or not _SOURCE_RE.fullmatch(source_name):
        return None
    stem = source_name.split(".", 1)[0]
    still = card_path(f"{stem}.still.webp")
    anim = card_path(f"{stem}.anim.webp")
    if still is None or anim is None:
        return None
    return still, anim


_animated_cache: dict[tuple[str, int, int], bool] = {}
_animated_lock = threading.Lock()


def _gif_animated(handle: BinaryIO) -> bool:
    """Whether a GIF has a second frame; walks the block structure only."""
    head = handle.read(13)
    if len(head) < 13 or head[:3] != b"GIF":
        return False
    if head[10] & 0x80:
        _ = handle.seek(3 << ((head[10] & 0x07) + 1), os.SEEK_CUR)
    frames = 0

    def skip_sub_blocks() -> bool:
        while True:
            size = handle.read(1)
            if not size:
                return False
            if size[0] == 0:
                return True
            _ = handle.seek(size[0], os.SEEK_CUR)

    while True:
        kind = handle.read(1)
        if kind == b"\x2c":  # image descriptor
            frames += 1
            if frames > 1:
                return True
            desc = handle.read(9)
            if len(desc) < 9:
                return False
            if desc[8] & 0x80:
                _ = handle.seek(3 << ((desc[8] & 0x07) + 1), os.SEEK_CUR)
            _ = handle.read(1)  # LZW minimum code size
            if not skip_sub_blocks():
                return False
        elif kind == b"\x21":  # extension
            _ = handle.read(1)
            if not skip_sub_blocks():
                return False
        else:
            return False


def _is_animated_uncached(path: Path) -> bool:
    with path.open("rb") as handle:
        suffix = path.suffix.lower()
        if suffix == ".gif":
            return _gif_animated(handle)
        head = handle.read(64)
    if suffix == ".webp":
        # The extended header (VP8X) carries an animation flag.
        return head[:4] == b"RIFF" and head[8:16] == b"WEBPVP8X" and len(head) > 20 and bool(head[20] & 0x02)
    if suffix == ".avif":
        # An image sequence lists the `avis` brand in its `ftyp` box.
        if head[4:8] != b"ftyp":
            return False
        size = int.from_bytes(head[:4], "big")
        return b"avis" in head[8:min(size, len(head))]
    return False


def is_animated(path: Path) -> bool:
    """Whether a cover file is animated (GIF, WebP or AVIF); False when unreadable."""
    try:
        info = path.stat()
    except OSError:
        return False
    key = (str(path), info.st_mtime_ns, info.st_size)
    with _animated_lock:
        known = _animated_cache.get(key)
    if known is not None:
        return known
    try:
        result = _is_animated_uncached(path)
    except OSError:
        return False
    with _animated_lock:
        if len(_animated_cache) > 4096:
            _animated_cache.clear()
        _animated_cache[key] = result
    return result


def webp_writer_available() -> bool:
    try:
        from PyQt6.QtGui import QImageWriter

        return b"webp" in {fmt.data().lower() for fmt in QImageWriter.supportedImageFormats()}
    except Exception:
        return False


def _encode(image: QImage) -> bytes:
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QImageWriter

    data = QByteArray()
    buf = QBuffer(data)
    _ = buf.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buf, b"webp")
    writer.setQuality(WEBP_QUALITY)
    if not writer.write(image):
        raise ValueError(writer.errorString() or "WebP encode failed")
    buf.close()
    return data.data()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".card-", delete=False) as out:
            temp = out.name
            _ = out.write(data)
        os.replace(temp, path)
        temp = None
    finally:
        if temp is not None:
            Path(temp).unlink(missing_ok=True)


def _frame_delay(reader: QImageReader) -> int:
    delay = reader.nextImageDelay()
    return DEFAULT_DELAY_MS if delay <= 10 else delay


def make_card_copies(source_path: str | Path) -> tuple[bool, bool]:
    """Make the card copies of one cover; returns (still_made, animated_made).

    Static, unreadable and non-animated sources make nothing and never raise."""
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QImageReader

    source = Path(source_path)
    paths = card_paths(source.name)
    if paths is None or source.suffix.lower() not in _CHECK_EXTENSIONS:
        return False, False
    still_path, anim_path = paths
    try:
        reader = QImageReader(str(source))
        reader.setAutoTransform(False)
        if not reader.canRead():
            return False, False
        count = reader.imageCount()
        if count <= 1:
            return False, False
        first = reader.read()
        if first.isNull():
            return False, False
        width, height = first.width(), first.height()
        target_w = min(CARD_MAX_WIDTH, width)
        target_h = max(1, round(height * target_w / width))
        size = QSize(target_w, target_h)

        def shrink(image: QImage) -> QImage:
            if image.size() == size:
                return image
            return image.scaled(
                size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )

        still = _encode(shrink(first))
        animated: bytes | None = None
        if count <= MAX_FRAMES:
            delays = [_frame_delay(reader)]
            frames = [still]
            total = len(still)
            try:
                for _ in range(count - 1):
                    image = reader.read()
                    if image.isNull():
                        raise ValueError("frame could not be read")
                    delays.append(_frame_delay(reader))
                    frames.append(_encode(shrink(image)))
                    total += len(frames[-1])
                    if total > MAX_OUTPUT_BYTES:
                        raise ValueError("animation too large")
                loops = reader.loopCount()
                loop_count = 0 if loops < 0 else max(1, loops)
                animated = mux_animated_webp(frames, delays, target_w, target_h, loop_count)
                if len(animated) > MAX_OUTPUT_BYTES:
                    animated = None
            except Exception as exc:
                logging.getLogger(__name__).info("No animated card copy for %s: %s", source.name, exc)
                animated = None
        if animated is not None:
            _write_atomic(anim_path, animated)
        else:
            anim_path.unlink(missing_ok=True)
        _write_atomic(still_path, still)
        if not source.exists():  # replaced or deleted while we worked
            still_path.unlink(missing_ok=True)
            anim_path.unlink(missing_ok=True)
            return False, False
        return True, animated is not None
    except Exception as exc:
        logging.getLogger(__name__).info("No card copies for %s: %s", source.name, exc)
        return False, False


def _existing_stems() -> set[str]:
    stems: set[str] = set()
    for folder in (covers_dir(), cache_covers_dir()):
        try:
            for entry in folder.iterdir():
                if _SOURCE_RE.fullmatch(entry.name) and entry.is_file():
                    stems.add(entry.name.split(".", 1)[0])
        except OSError:
            pass
    return stems


def _is_stale(path: Path) -> bool:
    """Temp files of a running write are fresh; only old ones are leftovers."""
    try:
        return time.time() - path.stat().st_mtime > STALE_TEMP_SECONDS
    except OSError:
        return False


def sweep_orphans() -> int:
    """Delete card copies whose source cover file is gone; returns files deleted."""
    folder = cards_dir()
    if not folder.is_dir():
        return 0
    stems = _existing_stems()
    removed = 0
    for entry in folder.iterdir():
        orphan = bool(
            CARD_NAME_RE.fullmatch(entry.name) and entry.name.split(".", 1)[0] not in stems
        ) or (entry.name.startswith(".card-") and _is_stale(entry))
        if orphan and (entry.is_file() or entry.is_symlink()):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def pending_sources() -> list[Path]:
    """Referenced local and cached covers that have no still copy yet."""
    out: list[Path] = []
    candidates = [
        (covers_dir(), database.referenced_cover_local_names()),
        (cache_covers_dir(), database.referenced_cover_cache_names()),
    ]
    for folder, names in candidates:
        for name in sorted(names):
            paths = card_paths(name)
            source = folder / name
            if paths is None or paths[0].exists() or source.suffix.lower() not in _CHECK_EXTENSIONS:
                continue
            if source.is_file():
                out.append(source)
    return out


class CoverCardService:
    def __init__(
        self,
        on_copies_ready: Callable[[], None] | None = None,
        pending: Callable[[], list[Path]] = pending_sources,
        make: Callable[[Path], tuple[bool, bool]] = make_card_copies,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.on_copies_ready: Callable[[], None] | None = on_copies_ready
        self.disabled: bool = False
        self._pending: Callable[[], list[Path]] = pending
        self._make: Callable[[Path], tuple[bool, bool]] = make
        self._clock: Callable[[], float] = clock
        self._done: set[str] = set()
        self._stop: threading.Event = threading.Event()
        self._wake: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        self._dirty: bool = False
        self._last_notify: float | None = None

    def start(self) -> None:
        if self._thread is not None or self._stop.is_set():
            return
        if not webp_writer_available():
            self.disabled = True
            logging.getLogger(__name__).info("Card copies disabled: Qt has no WebP writer")
            return
        self._thread = threading.Thread(target=self._run, name="cover-cards", daemon=True)
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

    def sweep(self) -> None:
        try:
            _ = sweep_orphans()
        except Exception:
            logging.getLogger(__name__).exception("Card copy sweep failed")

    def _notify(self, force: bool = False) -> None:
        if not self._dirty:
            return
        now = self._clock()
        if not force and self._last_notify is not None and now - self._last_notify < NOTIFY_INTERVAL_SECONDS:
            return
        self._dirty = False
        self._last_notify = now
        if self.on_copies_ready is not None:
            try:
                self.on_copies_ready()
            except Exception:
                logging.getLogger(__name__).exception("Card copy notice failed")

    def process_pending(self) -> None:
        """Make copies for every pending source, one at a time."""
        for source in self._pending():
            if self._stop.is_set() or self.disabled:
                break
            if source.name in self._done:
                continue
            self._done.add(source.name)
            still, _anim = self._make(source)
            if still:
                self._dirty = True
                self._notify()
        self._notify(force=True)

    def _run(self) -> None:
        log = logging.getLogger(__name__)
        self.sweep()
        while not self._stop.is_set():
            _ = self._wake.wait()
            self._wake.clear()
            if self._stop.is_set() or self.disabled:
                continue
            try:
                self.sweep()
                self.process_pending()
            except Exception:
                log.exception("Card copy pass failed")
