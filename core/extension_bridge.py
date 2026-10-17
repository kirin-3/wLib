# SPDX-License-Identifier: GPL-3.0-or-later
"""App-to-extension bridge: connection registry and request matching.

The extension keeps a WebSocket to ``GET /api/bridge``. This module owns the
newest-first registry of eligible connections, pending ``fetch`` requests
keyed by UUID, and the ``is_available()`` / ``fetch()`` surface the scraper
uses. It knows nothing about F95 parsing.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from io import BufferedIOBase
from typing import BinaryIO, Callable, cast, final

from core import extension_ws


class BridgeUnavailable(Exception):
    """Raised when no eligible extension is connected."""


class BridgeFailed(Exception):
    """Raised when the extension did not return a usable body in time."""


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
class BridgeConnection:
    """One bridge socket. Tests use fakes with the same surface."""

    def __init__(self, send_text: Callable[[str], None] | None = None) -> None:
        self._send_text_fn = send_text
        self._send_lock = threading.Lock()
        self._hello_ok = False
        self.can_fetch = False
        self.eligible = False
        self.closed = False

    def send_text(self, message: str) -> None:
        fn = self._send_text_fn
        if fn is None:
            raise BridgeFailed("connection has no transport")
        with self._send_lock:
            fn(message)

    def note_hello(self, protocol: object, can_fetch: object) -> None:
        self._hello_ok = protocol == 1
        self.can_fetch = bool(can_fetch) if isinstance(can_fetch, bool) else False
        self.eligible = self._hello_ok and self.can_fetch

    def note_status(self, can_fetch: object) -> None:
        if isinstance(can_fetch, bool):
            self.can_fetch = can_fetch
            self.eligible = self._hello_ok and self.can_fetch


_lock = threading.Lock()
_connections: list[BridgeConnection] = []
_pending: dict[str, _Pending] = {}


def reset_for_tests() -> None:
    """Clear registry and pending (tests only)."""
    with _lock:
        _connections.clear()
        _pending.clear()


def _register_connection(conn: BridgeConnection) -> None:
    with _lock:
        if conn not in _connections:
            _connections.insert(0, conn)


def _remove_connection(conn: BridgeConnection) -> None:
    with _lock:
        conn.closed = True
        if conn in _connections:
            _connections.remove(conn)
        doomed = [
            key for key, item in _pending.items() if item.connection is conn
        ]
        for key in doomed:
            item = _pending.pop(key)
            item.error = "disconnected"
            item.event.set()


def _newest_eligible() -> BridgeConnection | None:
    with _lock:
        for conn in _connections:
            if conn.eligible and not conn.closed:
                return conn
    return None


def is_available() -> bool:
    """True when at least one eligible extension is connected."""
    return _newest_eligible() is not None


def _handle_message(conn: BridgeConnection, message: object) -> None:
    """Dispatch one decoded JSON message from the extension."""
    if not isinstance(message, dict):
        return
    message = cast(dict[str, object], message)
    raw_type = message.get("type")
    if not isinstance(raw_type, str):
        return
    if raw_type == "hello":
        conn.note_hello(message.get("protocol"), message.get("can_fetch"))
    elif raw_type == "status":
        conn.note_status(message.get("can_fetch"))
    elif raw_type == "ping":
        try:
            conn.send_text(json.dumps({"type": "pong"}))
        except BridgeFailed:
            pass
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


def fetch(url: str, timeout: float = 30) -> BridgeResponse:
    """Send one fetch to the newest eligible extension and wait for its body."""
    conn = _newest_eligible()
    if conn is None:
        raise BridgeUnavailable("no eligible extension connected")
    request_id = uuid.uuid4().hex
    item = _Pending(conn)
    with _lock:
        _pending[request_id] = item
    payload = json.dumps({"type": "fetch", "id": request_id, "url": url})
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


def _send_raw(wfile: BinaryIO | BufferedIOBase, data: bytes) -> None:
    _ = wfile.write(data)
    _ = wfile.flush()


def serve_bridge_connection(
    sock: object, rfile: BinaryIO | BufferedIOBase, wfile: BinaryIO | BufferedIOBase
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
    _register_connection(conn)
    try:
        while True:
            try:
                kind, payload = extension_ws.recv_message(rfile)
            except extension_ws.MessageTooLarge:
                try:
                    _send_raw(wfile, extension_ws.encode_server_close(1009))
                except (OSError, ValueError):
                    pass
                break
            except (extension_ws.WebSocketError, OSError, ValueError):
                break
            if kind == "close":
                try:
                    _send_raw(
                        wfile, extension_ws.encode_server_close(1000)
                    )
                except (OSError, ValueError):
                    pass
                break
            if kind == "ping":
                try:
                    _send_raw(wfile, extension_ws.encode_server_pong(payload))
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
