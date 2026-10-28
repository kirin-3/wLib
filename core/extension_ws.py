# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal RFC 6455 server-side WebSocket helpers for the extension bridge.

Only what ``/api/bridge`` needs: handshake accept-key computation, frame
encoding for server-to-extension messages, and strict frame reading for
extension-to-app messages (masked client frames, 16- and 64-bit lengths,
continuation reassembly, ping/pong, close, 8 MiB message limit).

Server frames are sent unfragmented and unmasked. Client frames must be
masked; unmasked client frames are rejected.
"""

from __future__ import annotations

import base64
import hashlib
import struct
from io import BufferedIOBase
from typing import BinaryIO, final

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE_BYTES = 8 * 1024 * 1024

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocketError(Exception):
    """Raised for malformed frames or protocol violations."""


class MessageTooLarge(WebSocketError):
    """Raised when a frame or reassembled message exceeds the 8 MiB limit."""


def websocket_accept_key(client_key: str) -> str:
    """Compute ``Sec-WebSocket-Accept`` for the RFC 6455 sample vector."""
    cleaned = client_key.strip()
    digest = hashlib.sha1((cleaned + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_server_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    """Encode one unfragmented, unmasked server frame."""
    if len(payload) > MAX_MESSAGE_BYTES:
        raise MessageTooLarge("server message exceeds 8 MiB")
    first = (0x80 if fin else 0x00) | (opcode & 0x0F)
    header = bytearray([first])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < (1 << 16):
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)
    return bytes(header) + payload


def encode_server_text(message: str) -> bytes:
    """Encode a text message as one unmasked server frame."""
    return encode_server_frame(OP_TEXT, message.encode("utf-8"))


def encode_server_pong(payload: bytes = b"") -> bytes:
    """Encode a pong reply (used to answer extension pings)."""
    return encode_server_frame(OP_PONG, payload)


def encode_server_close(code: int = 1000, reason: bytes = b"") -> bytes:
    """Encode a close frame with an optional status code."""
    payload = struct.pack("!H", code) + reason if reason or code else b""
    return encode_server_frame(OP_CLOSE, payload)


def encode_client_frame(
    opcode: int, payload: bytes, *, fin: bool = True, mask: bytes | None = None
) -> bytes:
    """Encode a masked client frame (test helper; the bridge only reads these)."""
    if mask is None:
        mask = b"\x01\x02\x03\x04"
    if len(mask) != 4:
        raise ValueError("mask must be 4 bytes")
    first = (0x80 if fin else 0x00) | (opcode & 0x0F)
    header = bytearray([first])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < (1 << 16):
        header.append(0x80 | 126)
        header += struct.pack("!H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", length)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


def _read_exactly(rfile: BinaryIO | BufferedIOBase, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = rfile.read(remaining)
        if not chunk:
            raise WebSocketError("connection closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(rfile: BinaryIO | BufferedIOBase) -> tuple[bool, int, bytes]:
    """Read one client frame, returning ``(fin, opcode, payload)``.

    Requires the mask bit (clients must mask). Rejects control frames over
    125 bytes, fragments of control frames, and payloads over 8 MiB.
    Answers nothing here; the caller decides how to reply to ping/close.
    """
    header = _read_exactly(rfile, 2)
    first, second = header[0], header[1]
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if opcode >= 0x8 and not fin:
        raise WebSocketError("fragmented control frame")
    if not masked:
        raise WebSocketError("client frames must be masked")
    if length == 126:
        length = struct.unpack("!H", _read_exactly(rfile, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exactly(rfile, 8))[0]
        if length >> 63:
            raise WebSocketError("invalid 64-bit length")
    if opcode >= 0x8 and length > 125:
        raise WebSocketError("control frame too large")
    if length > MAX_MESSAGE_BYTES:
        raise MessageTooLarge("frame exceeds 8 MiB")
    mask = _read_exactly(rfile, 4)
    raw = _read_exactly(rfile, length) if length else b""
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
    return fin, opcode, payload


def recv_message(
    rfile: BinaryIO | BufferedIOBase,
    state: MessageState | None = None,
) -> tuple[str, bytes]:
    """Read frames until a complete data message or control action arrives.

    Returns ``("text", payload)`` for a reassembled text message (continuation
    frames joined), ``("ping", payload)``, ``("pong", payload)``, or
    ``("close", payload)``. Binary frames are rejected. Reassembled text over
    8 MiB raises :class:`MessageTooLarge`.

    Pass a per-connection :class:`MessageState` to keep a partly reassembled
    message across a control frame (RFC 6455 allows ping/pong between
    fragments). Without one, each call starts with an empty message.
    """
    message = state if state is not None else MessageState()
    while True:
        fin, opcode, payload = read_frame(rfile)
        if opcode == OP_TEXT:
            if message.active:
                raise WebSocketError("new data frame inside fragments")
            if fin:
                if len(payload) > MAX_MESSAGE_BYTES:
                    raise MessageTooLarge("message exceeds 8 MiB")
                return ("text", payload)
            message.fragments.append(payload)
            message.active = True
        elif opcode == OP_CONT:
            if not message.active:
                raise WebSocketError("orphan continuation frame")
            message.fragments.append(payload)
            total = sum(len(part) for part in message.fragments)
            if total > MAX_MESSAGE_BYTES:
                message.fragments.clear()
                message.active = False
                raise MessageTooLarge("message exceeds 8 MiB")
            if fin:
                data = b"".join(message.fragments)
                message.fragments.clear()
                message.active = False
                return ("text", data)
        elif opcode == OP_PING:
            return ("ping", payload)
        elif opcode == OP_PONG:
            return ("pong", payload)
        elif opcode == OP_CLOSE:
            return ("close", payload)
        else:
            raise WebSocketError(f"unsupported opcode {opcode:#x}")


@final
class MessageState:
    """Partial text message carried across ``recv_message`` calls."""

    def __init__(self) -> None:
        self.fragments: list[bytes] = []
        self.active: bool = False
