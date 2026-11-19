# SPDX-License-Identifier: GPL-3.0-or-later
"""App-to-extension bridge: connection registry and request matching.

The extension keeps a WebSocket to ``GET /api/bridge``. This module owns the
newest-first registry of eligible connections, pending ``fetch`` requests
keyed by UUID, and the ``is_available()`` / ``fetch()`` surface the scraper
uses. It knows nothing about F95 parsing.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import socket
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from io import BufferedIOBase
from typing import BinaryIO, cast, final
from collections.abc import Callable

from core import extension_ws
from core.lewdcorner import Site

logger = logging.getLogger("wlib.bridge")


class BridgeUnavailable(Exception):
    """Raised when no eligible extension is connected."""


class BridgeFailed(Exception):
    """Raised when the extension did not return a usable body in time."""


class LcImageLimitReached(BridgeFailed):
    """Raised when background LC image fetches used their rolling hour.

    ``retry_in`` is the number of seconds until the oldest of them is an hour old."""

    def __init__(self, message: str, retry_in: float) -> None:
        super().__init__(message)
        self.retry_in: float = retry_in


# An image answer: the extension sends at most this much image data.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
# Background (cover cache) LC image pacing.
LC_BACKGROUND_GAP_SECONDS = 5.0
LC_BACKGROUND_PER_HOUR = 120
LC_GAP_SECONDS = 2.0


@dataclass
class ImageResponse:
    status: int
    content_type: str
    final_url: str
    body: bytes


@dataclass
class BridgeResponse:
    status: int
    final_url: str
    body: str


@final
class _Pending:
    def __init__(self, connection: BridgeConnection) -> None:
        self.connection = connection
        self.event = threading.Event()
        self.response: BridgeResponse | None = None
        self.error: str = "pending"


@final
class _PendingImage:
    def __init__(self, connection: BridgeConnection) -> None:
        self.connection = connection
        self.event = threading.Event()
        self.chunks: list[bytes] = []
        self.size = 0
        self.status = 0
        self.content_type = ""
        self.final_url = ""
        self.error = "pending"


@final
class BridgeConnection:
    """One bridge socket. Tests use fakes with the same surface."""

    def __init__(self, send_text: Callable[[str], None] | None = None) -> None:
        self._send_text_fn = send_text
        # Which extension pairing opened this socket, and how to cut it.
        self.pairing_id = ""
        self.shutdown: Callable[[], None] | None = None
        self._send_lock = threading.Lock()
        self._hello_ok = False
        self.can_fetch = False
        self.can_fetch_lc = False
        self.can_resolve = False
        self.can_resolve_lc = False
        self.can_fetch_lc_images = False
        self.eligible = False
        self.closed = False
        self.extension_version = ""
        self.opened_at = time.monotonic()

    def send_text(self, message: str) -> None:
        fn = self._send_text_fn
        if fn is None:
            raise BridgeFailed("connection has no transport")
        with self._send_lock:
            fn(message)

    def send_locked(self, send: Callable[[], None]) -> None:
        """Run a raw frame write under the same lock as text messages."""
        with self._send_lock:
            send()

    def note_hello(
        self, protocol: object, can_fetch: object, extension_version: object = "",
        can_resolve: object = False, *, can_fetch_lc: object = False, can_resolve_lc: object = False,
        can_fetch_lc_images: object = False,
    ) -> None:
        self._hello_ok = protocol in (1, 2)
        self.can_fetch = bool(can_fetch) if isinstance(can_fetch, bool) else False
        self.can_fetch_lc = self._hello_ok and can_fetch_lc is True
        self.can_resolve = can_resolve is True
        self.can_resolve_lc = self._hello_ok and can_resolve_lc is True
        self.can_fetch_lc_images = self._hello_ok and can_fetch_lc_images is True
        self.eligible = self._hello_ok and self.can_fetch
        if isinstance(extension_version, str):
            self.extension_version = extension_version.strip()[:32]

    def note_status(
        self, can_fetch: object, can_fetch_lc: object = None, can_resolve_lc: object = None,
        can_fetch_lc_images: object = None,
    ) -> None:
        if isinstance(can_fetch, bool):
            self.can_fetch = can_fetch
            self.eligible = self._hello_ok and self.can_fetch
        if isinstance(can_fetch_lc, bool):
            self.can_fetch_lc = self._hello_ok and can_fetch_lc
        if isinstance(can_resolve_lc, bool):
            self.can_resolve_lc = self._hello_ok and can_resolve_lc
        if isinstance(can_fetch_lc_images, bool):
            self.can_fetch_lc_images = self._hello_ok and can_fetch_lc_images


@final
class _LcGate:
    """One LC request at a time; waiting foreground requests go first.

    ``with gate:`` holds it as a foreground request. Background holders also
    wait out their own gap here, without holding the gate, so a foreground
    request that arrives meanwhile does not wait behind that gap."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._busy = False
        self._waiting_foreground = 0
        # Tests replace it to move a fake clock instead of sleeping.
        self.timed_wait: Callable[[threading.Condition, float], object] = (
            lambda cond, seconds: cond.wait(seconds)
        )

    def locked(self) -> bool:
        return self._busy

    def acquire_foreground(self) -> None:
        with self._cond:
            self._waiting_foreground += 1
            try:
                while self._busy:
                    _ = self._cond.wait()
            finally:
                self._waiting_foreground -= 1
            self._busy = True

    def acquire_background(self, ready_at: Callable[[], float]) -> None:
        """Wait until the gate is free, no foreground waits and ``ready_at()`` passed."""
        with self._cond:
            while True:
                if self._busy or self._waiting_foreground:
                    _ = self._cond.wait()
                    continue
                remaining = ready_at() - time.monotonic()
                if remaining > 0:
                    _ = self.timed_wait(self._cond, remaining)
                    continue
                break
            self._busy = True

    def release(self) -> None:
        with self._cond:
            self._busy = False
            self._cond.notify_all()

    def __enter__(self) -> _LcGate:
        self.acquire_foreground()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


_lock = threading.Lock()
_connections: list[BridgeConnection] = []
_pending: dict[str, _Pending] = {}
_pending_images: dict[str, _PendingImage] = {}
_lc_request_lock = _LcGate()
_last_lc_request: float | None = None
# Send times of the latest background LC image fetches (rolling hour).
_lc_background_fetches: deque[float] = deque(maxlen=LC_BACKGROUND_PER_HOUR)
# Called (outside any lock) when a connection starts to report LC image support.
_on_lc_images_available: list[Callable[[], None]] = []


def add_lc_images_listener(callback: Callable[[], None]) -> None:
    """Run ``callback`` each time a connection's ``can_fetch_lc_images`` turns true."""
    with _lock:
        if callback not in _on_lc_images_available:
            _on_lc_images_available.append(callback)


def _notify_lc_images_available() -> None:
    with _lock:
        callbacks = list(_on_lc_images_available)
    for callback in callbacks:
        try:
            callback()
        except Exception:
            logger.exception("LC image listener failed")


@final
class _PendingResolve:
    def __init__(self, connection: BridgeConnection) -> None:
        self.connection = connection
        self.event = threading.Event()
        self.result: dict[str, object] | None = None
        self.error = "pending"
        self.needs_user_at: float | None = None
        self.on_progress: Callable[[str], None] | None = None


_resolve_lock = threading.Lock()
_pending_resolves: dict[str, _PendingResolve] = {}
# Connection history for diagnostics. A browser that suspends the extension's
# background page shows up as many short connections.
_disconnects = 0
_last_disconnect_at: float | None = None
_last_connection_seconds: float | None = None
_last_fetch: dict[str, object] | None = None


def reset_for_tests() -> None:
    """Clear registry and pending (tests only)."""
    global _disconnects, _last_disconnect_at, _last_connection_seconds, _last_fetch, _last_lc_request
    global _lc_request_lock
    _lc_request_lock = _LcGate()
    with _lock:
        _connections.clear()
        _pending.clear()
        _pending_images.clear()
        _lc_background_fetches.clear()
        _on_lc_images_available.clear()
        _pending_resolves.clear()
        _disconnects = 0
        _last_disconnect_at = None
        _last_connection_seconds = None
        _last_fetch = None
        _last_lc_request = None


def close_paired_connections(pairing_id: str) -> None:
    """Cut every bridge socket of a pairing that was just removed."""
    with _lock:
        matching = [conn for conn in _connections if conn.pairing_id == pairing_id]
    for conn in matching:
        if conn.shutdown is not None:
            try:
                conn.shutdown()
            except OSError:
                pass


def _register_connection(conn: BridgeConnection) -> None:
    with _lock:
        if conn not in _connections:
            _connections.insert(0, conn)


def _remove_connection(conn: BridgeConnection) -> None:
    global _disconnects, _last_disconnect_at, _last_connection_seconds
    duration = 0.0
    with _lock:
        was_open = not conn.closed
        conn.closed = True
        if conn in _connections:
            _connections.remove(conn)
        if was_open:
            now = time.monotonic()
            duration = now - conn.opened_at
            _disconnects += 1
            _last_disconnect_at = now
            _last_connection_seconds = duration
        doomed = [
            key for key, item in _pending.items() if item.connection is conn
        ]
        for key in doomed:
            item = _pending.pop(key)
            item.error = "disconnected"
            item.event.set()
        for key in [key for key, image in _pending_images.items() if image.connection is conn]:
            image = _pending_images.pop(key)
            image.error = "disconnected"
            image.event.set()
        for key in [key for key, item in _pending_resolves.items() if item.connection is conn]:
            pending_resolve = _pending_resolves.pop(key)
            pending_resolve.error = "disconnected"
            pending_resolve.event.set()
    if was_open:
        logger.info("Browser extension disconnected after %.0f s", duration)


def _newest_eligible(site: Site = "f95") -> BridgeConnection | None:
    with _lock:
        for conn in _connections:
            if not conn.closed and (conn.can_fetch_lc if site == "lc" else conn.eligible):
                return conn
    return None


def is_available(*, site: Site = "f95") -> bool:
    """True when at least one eligible extension is connected."""
    return _newest_eligible(site) is not None


def _newest_lc_image_fetcher() -> BridgeConnection | None:
    with _lock:
        return next((conn for conn in _connections if not conn.closed and conn.can_fetch_lc_images), None)


def can_fetch_lc_images() -> bool:
    """True when a connected extension reports that it can fetch LC images."""
    return _newest_lc_image_fetcher() is not None


def record_fetch_outcome(
    status: int | None, signed_in: bool | None, error: str = ""
) -> None:
    """Remember the latest extension fetch for diagnostics.

    ``signed_in`` is the caller's reading of the page (``None`` when the
    response does not say). A change in signed-in state is logged once, so a
    browser that sends no site cookies leaves a trace without flooding the log.
    """
    global _last_fetch
    with _lock:
        previous = _last_fetch.get("signed_in") if _last_fetch else None
        _last_fetch = {
            "at": time.monotonic(),
            "status": status,
            "signed_in": signed_in,
            "error": error,
        }
    if error:
        logger.warning("Browser extension fetch failed: %s", error)
    elif signed_in is False and previous is not False:
        logger.warning(
            "Browser extension fetched F95Zone without a login (no F95Zone cookies sent)"
        )
    elif signed_in is True and previous is not True:
        logger.info("Browser extension fetched F95Zone while logged in")


def status_snapshot() -> dict[str, object]:
    """JSON-serializable bridge state for diagnostics."""
    now = time.monotonic()
    with _lock:
        open_connections = [conn for conn in _connections if not conn.closed]
        newest = open_connections[0] if open_connections else None
        last_fetch = dict(_last_fetch) if _last_fetch else None
        snapshot: dict[str, object] = {
            "connected": newest is not None,
            "connections": len(open_connections),
            "eligible": any(conn.eligible for conn in open_connections),
            "extension_version": newest.extension_version if newest else "",
            "can_fetch": newest.can_fetch if newest else None,
            "can_fetch_lc": any(conn.can_fetch_lc for conn in open_connections),
            "can_resolve_lc": any(conn.can_fetch_lc and conn.can_resolve and conn.can_resolve_lc for conn in open_connections),
            "can_fetch_lc_images": any(conn.can_fetch_lc_images for conn in open_connections),
            "connected_seconds": (
                round(now - newest.opened_at) if newest else None
            ),
            "disconnects": _disconnects,
            "last_connection_seconds": (
                round(_last_connection_seconds)
                if _last_connection_seconds is not None
                else None
            ),
            "last_disconnect_seconds_ago": (
                round(now - _last_disconnect_at)
                if _last_disconnect_at is not None
                else None
            ),
        }
    if last_fetch is not None:
        fetched_at = cast(float, last_fetch.pop("at"))
        last_fetch["seconds_ago"] = round(now - fetched_at)
    snapshot["last_fetch"] = last_fetch
    return snapshot


def _handle_message(conn: BridgeConnection, message: object) -> None:
    """Dispatch one decoded JSON message from the extension."""
    if not isinstance(message, dict):
        return
    message = cast(dict[str, object], message)
    raw_type = message.get("type")
    if not isinstance(raw_type, str):
        return
    if raw_type == "hello":
        conn.note_hello(
            message.get("protocol"),
            message.get("can_fetch"),
            message.get("extension_version"),
            message.get("can_resolve"),
            can_fetch_lc=message.get("can_fetch_lc"),
            can_resolve_lc=message.get("can_resolve_lc"),
            can_fetch_lc_images=message.get("can_fetch_lc_images"),
        )
        logger.info(
            "Browser extension connected (version %s, protocol %s, can fetch F95Zone: %s, LewdCorner: %s, can_resolve_lc: %s, can_fetch_lc_images: %s)",
            conn.extension_version or "unknown",
            message.get("protocol"),
            conn.can_fetch,
            conn.can_fetch_lc,
            conn.can_resolve_lc,
            conn.can_fetch_lc_images,
        )
        if conn.can_fetch_lc_images:
            _notify_lc_images_available()
    elif raw_type == "status":
        could_fetch = conn.can_fetch
        could_resolve_lc = conn.can_resolve_lc
        could_fetch_lc_images = conn.can_fetch_lc_images
        conn.note_status(
            message.get("can_fetch"), message.get("can_fetch_lc"), message.get("can_resolve_lc"),
            message.get("can_fetch_lc_images"),
        )
        if conn.can_resolve_lc != could_resolve_lc:
            logger.info("Browser extension can_resolve_lc: %s", conn.can_resolve_lc)
        if conn.can_fetch != could_fetch:
            logger.info("Browser extension can fetch: %s", conn.can_fetch)
        if conn.can_fetch_lc_images != could_fetch_lc_images:
            logger.info("Browser extension can_fetch_lc_images: %s", conn.can_fetch_lc_images)
            if conn.can_fetch_lc_images:
                _notify_lc_images_available()
    elif raw_type == "ping":
        try:
            conn.send_text(json.dumps({"type": "pong"}))
        except BridgeFailed:
            pass
    elif raw_type in ("resolve_progress", "resolve_result"):
        request_id = message.get("id")
        if not isinstance(request_id, str):
            return
        with _lock:
            pending_resolve = _pending_resolves.get(request_id)
            if pending_resolve is None or pending_resolve.connection is not conn:
                return
            if raw_type == "resolve_result":
                _ = _pending_resolves.pop(request_id)
                pending_resolve.result = message
                pending_resolve.event.set()
                return
            stage = message.get("stage")
            if stage == "needs_user":
                pending_resolve.needs_user_at = time.monotonic()
            callback = pending_resolve.on_progress
        if isinstance(stage, str) and callback:
            callback(stage)
    elif raw_type == "fetch_image_chunk":
        _handle_image_chunk(conn, message)
    elif raw_type == "fetch_result":
        request_id = message.get("id")
        if not isinstance(request_id, str) or not request_id:
            return
        with _lock:
            item = _pending.get(request_id)
            if item is None or item.connection is not conn:
                return
            _ = _pending.pop(request_id)
        if message.get("ok") is True:
            try:
                status = int(str(message.get("status", 200)))
            except (TypeError, ValueError):
                status = 200
            final_url = (
                str(message.get("final_url", ""))
                if isinstance(message.get("final_url"), str)
                else ""
            )
            body = (
                str(message.get("body", ""))
                if isinstance(message.get("body"), str)
                else ""
            )
            item.response = BridgeResponse(
                status=status, final_url=final_url, body=body
            )
            item.error = ""
        else:
            raw_error = message.get("error")
            item.error = (
                str(raw_error).strip()
                if isinstance(raw_error, str) and raw_error.strip()
                else "fetch failed"
            )
        item.event.set()
    else:
        # Unknown message types are ignored for forward compatibility.
        return


def _handle_image_chunk(conn: BridgeConnection, message: dict[str, object]) -> None:
    request_id = message.get("id")
    if not isinstance(request_id, str) or not request_id:
        return
    index = message.get("index")
    data = message.get("data", "")
    with _lock:
        item = _pending_images.get(request_id)
        if item is None or item.connection is not conn:
            return

        def fail(error: str) -> None:
            _ = _pending_images.pop(request_id, None)
            item.error = error
            item.event.set()

        if not isinstance(index, int) or isinstance(index, bool) or index != len(item.chunks):
            fail("image chunk missing")
            return
        if index == 0:
            if message.get("ok") is not True:
                raw_error = message.get("error")
                fail(raw_error.strip()[:80] if isinstance(raw_error, str) and raw_error.strip() else "image fetch failed")
                return
            status = message.get("status")
            item.status = status if isinstance(status, int) and not isinstance(status, bool) else 0
            content_type = message.get("content_type")
            item.content_type = content_type.strip()[:200] if isinstance(content_type, str) else ""
            final_url = message.get("final_url")
            item.final_url = final_url if isinstance(final_url, str) else ""
        if not isinstance(data, str):
            fail("image chunk is not text")
            return
        try:
            chunk = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            fail("image chunk is not base64")
            return
        item.size += len(chunk)
        if item.size > MAX_IMAGE_BYTES:
            fail("too_large")
            return
        item.chunks.append(chunk)
        if message.get("last") is True:
            _ = _pending_images.pop(request_id, None)
            item.error = ""
            item.event.set()


def _wait_lc_gap() -> None:
    # Callers hold _lc_request_lock until the request starts.
    if _last_lc_request is not None:
        delay = LC_GAP_SECONDS - (time.monotonic() - _last_lc_request)
        if delay > 0:
            time.sleep(delay)


def fetch(url: str, timeout: float = 30, *, site: Site = "f95") -> BridgeResponse:
    """Send one fetch to the newest eligible extension and wait for its body."""
    if site == "lc":
        if not is_available(site="lc"):
            raise BridgeUnavailable("no eligible extension connected")
        with _lc_request_lock:
            _wait_lc_gap()
            return _fetch(url, timeout, site)
    return _fetch(url, timeout, site)


def _fetch(url: str, timeout: float, site: Site) -> BridgeResponse:
    global _last_lc_request
    conn = _newest_eligible(site)
    if conn is None:
        raise BridgeUnavailable("no eligible extension connected")
    request_id = uuid.uuid4().hex
    item = _Pending(conn)
    with _lock:
        # A disconnect between picking the connection and registering here
        # has already failed its pending items; this one would wait the full
        # timeout while the caller holds the F95 request lock.
        if conn.closed:
            raise BridgeUnavailable("extension disconnected")
        _pending[request_id] = item
    request = {"type": "fetch", "id": request_id, "url": url}
    if site == "lc":
        request["site"] = site
        _last_lc_request = time.monotonic()
    payload = json.dumps(request)
    try:
        conn.send_text(payload)
    except (BridgeFailed, OSError, ValueError) as exc:
        with _lock:
            _ = _pending.pop(request_id, None)
        raise BridgeFailed(str(exc) or "failed to send fetch") from exc
    if not item.event.wait(timeout):
        with _lock:
            _ = _pending.pop(request_id, None)
        raise BridgeFailed("extension did not answer in time")
    if item.response is not None:
        return item.response
    raise BridgeFailed(item.error or "extension fetch failed")


def _background_ready_at() -> float:
    return (_last_lc_request or 0.0) + LC_BACKGROUND_GAP_SECONDS


def _check_background_limit() -> None:
    now = time.monotonic()
    with _lock:
        if len(_lc_background_fetches) >= LC_BACKGROUND_PER_HOUR and now - _lc_background_fetches[0] < 3600:
            raise LcImageLimitReached(
                "LewdCorner image limit for this hour reached", 3600 - (now - _lc_background_fetches[0])
            )


def fetch_image(url: str, *, background: bool, timeout: float = 60) -> ImageResponse:
    """Fetch one LC attachment image through the newest extension that can.

    ``background`` (the cover cache) waits 5 s after the last LC request and
    counts toward 120 fetches per rolling hour; foreground (open details)
    uses the normal LC gap and goes before waiting background fetches."""
    global _last_lc_request
    if _newest_lc_image_fetcher() is None:
        raise BridgeUnavailable("no extension with LewdCorner image support connected")
    if background:
        _check_background_limit()
        _lc_request_lock.acquire_background(_background_ready_at)
    else:
        _lc_request_lock.acquire_foreground()
    try:
        if background:
            # Another background fetch may have used the hour while this one waited.
            _check_background_limit()
        else:
            _wait_lc_gap()
        conn = _newest_lc_image_fetcher()
        if conn is None:
            raise BridgeUnavailable("no extension with LewdCorner image support connected")
        request_id = uuid.uuid4().hex
        item = _PendingImage(conn)
        with _lock:
            if conn.closed:
                raise BridgeUnavailable("extension disconnected")
            _pending_images[request_id] = item
        try:
            _last_lc_request = time.monotonic()
            if background:
                with _lock:
                    _lc_background_fetches.append(_last_lc_request)
            conn.send_text(json.dumps({"type": "fetch_image", "id": request_id, "url": url}))
        except (BridgeFailed, OSError, ValueError) as exc:
            with _lock:
                _ = _pending_images.pop(request_id, None)
            raise BridgeFailed(str(exc) or "failed to send image fetch") from exc
        if not item.event.wait(timeout):
            with _lock:
                _ = _pending_images.pop(request_id, None)
            raise BridgeFailed("extension did not answer in time")
    finally:
        _lc_request_lock.release()
    if item.error:
        raise BridgeFailed(item.error)
    return ImageResponse(
        status=item.status, content_type=item.content_type,
        final_url=item.final_url, body=b"".join(item.chunks),
    )


def _can_resolve(conn: BridgeConnection, site: Site) -> bool:
    return not conn.closed and conn.can_resolve and (
        conn.can_fetch_lc and conn.can_resolve_lc if site == "lc" else conn.eligible
    )


def _newest_resolver(site: Site) -> BridgeConnection | None:
    with _lock:
        return next((conn for conn in _connections if _can_resolve(conn, site)), None)


def resolve(
    url: str, host: str, timeout: float = 35,
    on_progress: Callable[[str], None] | None = None,
    start: Callable[[Callable[[], None]], None] | None = None,
    cancel: threading.Event | None = None, *, site: Site = "f95",
) -> dict[str, object]:
    """Resolve one link, releasing the site's pacing slot after sending it."""
    global _last_lc_request
    if site == "lc" and _newest_resolver(site) is None:
        raise BridgeUnavailable("no extension with LewdCorner download resolution connected")
    with _resolve_lock:
        conn = _newest_resolver(site)
        if conn is None:
            raise BridgeUnavailable("no extension with download resolution connected")
        request_id = uuid.uuid4().hex
        item = _PendingResolve(conn)
        item.on_progress = on_progress
        with _lock:
            if conn.closed:
                raise BridgeUnavailable("extension disconnected")
            _pending_resolves[request_id] = item
        try:
            payload = json.dumps({"type": "resolve", "id": request_id, "url": url, "host": host})
            send = lambda: conn.send_text(payload)  # noqa: E731
            if site == "lc":
                with _lc_request_lock:
                    _wait_lc_gap()
                    if cancel is not None and cancel.is_set():
                        raise BridgeFailed("cancelled")
                    if not _can_resolve(conn, site):
                        raise BridgeUnavailable("extension no longer supports LewdCorner downloads")
                    _last_lc_request = time.monotonic()
                    send()
            elif start is None:
                send()
            else:
                start(send)
            deadline = time.monotonic() + timeout
            def abandon() -> None:
                # The extension closes the window it opened for this request.
                try:
                    conn.send_text(json.dumps({"type": "resolve_cancel", "id": request_id}))
                except (BridgeFailed, OSError, ValueError):
                    pass

            while not item.event.is_set():
                if cancel is not None and cancel.is_set():
                    abandon()
                    raise BridgeFailed("cancelled")
                if item.needs_user_at is not None:
                    # A little past the extension's own 5-minute limit, so its answer wins.
                    deadline = item.needs_user_at + 305
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    abandon()
                    raise BridgeFailed("extension did not resolve in time")
                _ = item.event.wait(min(remaining, 0.1))
            if item.result is None:
                raise BridgeFailed(item.error)
            return item.result
        except (OSError, ValueError) as exc:
            raise BridgeFailed("failed to send resolve") from exc
        finally:
            with _lock:
                _ = _pending_resolves.pop(request_id, None)

def _send_raw(wfile: BinaryIO | BufferedIOBase, data: bytes) -> None:
    _ = wfile.write(data)
    _ = wfile.flush()


def serve_bridge_connection(
    sock: object,
    rfile: BinaryIO | BufferedIOBase,
    wfile: BinaryIO | BufferedIOBase,
    pairing_id: str = "",
) -> None:
    """Run the bridge frame loop for one upgraded socket (blocking).

    Called from ``main.py`` after the 101 handshake. Holds the handler thread
    for the connection lifetime; daemon server threads make this cheap (one
    per browser). Removes the connection and fails its pending requests on
    close.
    """
    _ = sock

    def _send_text(message: str) -> None:
        _send_raw(wfile, extension_ws.encode_server_text(message))

    conn = BridgeConnection(send_text=_send_text)
    conn.pairing_id = pairing_id
    if isinstance(sock, socket.socket):
        conn.shutdown = lambda: sock.shutdown(socket.SHUT_RDWR)
    _register_connection(conn)
    state = extension_ws.MessageState()
    try:
        while True:
            try:
                kind, payload = extension_ws.recv_message(rfile, state)
            except extension_ws.MessageTooLarge:
                try:
                    conn.send_locked(lambda: _send_raw(wfile, extension_ws.encode_server_close(1009)))
                except (OSError, ValueError):
                    pass
                break
            except (extension_ws.WebSocketError, OSError, ValueError):
                break
            if kind == "close":
                try:
                    conn.send_locked(
                        lambda: _send_raw(
                            wfile, extension_ws.encode_server_close(1000)
                        )
                    )
                except (OSError, ValueError):
                    pass
                break
            if kind == "ping":
                try:
                    conn.send_locked(lambda: _send_raw(wfile, extension_ws.encode_server_pong(payload)))  # noqa: B023 - send_locked calls it at once
                except (OSError, ValueError):
                    break
                continue
            if kind == "pong":
                continue
            # kind == "text": JSON protocol message.
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                continue
            try:
                message = cast(object, json.loads(text))
            except ValueError:
                continue
            _handle_message(conn, message)
    finally:
        _remove_connection(conn)
        try:
            closer = getattr(sock, "close", None)
            if callable(closer):
                _ = closer()
        except OSError:
            pass
        # Give pending writers a moment to notice disconnect (tests join).
        time.sleep(0)
