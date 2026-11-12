# SPDX-License-Identifier: GPL-3.0-or-later
"""Join single-frame WebP images into one animated WebP (no dependencies)."""

from __future__ import annotations

import struct

MAX_CANVAS = 1 << 24  # 24-bit size fields hold width-1 and height-1
MAX_DURATION = (1 << 24) - 1


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """(fourcc, payload) pairs of a RIFF/WEBP file; raises ValueError if malformed."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("Not a WebP image")
    end = min(len(data), struct.unpack("<I", data[4:8])[0] + 8)
    pos = 12
    out: list[tuple[bytes, bytes]] = []
    while pos < end:
        if pos + 8 > end:
            raise ValueError("Truncated WebP chunk")
        fourcc = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        payload = data[pos + 8 : pos + 8 + size]
        if len(payload) != size:
            raise ValueError("Truncated WebP chunk")
        out.append((fourcc, payload))
        pos += 8 + size + (size & 1)
    return out


def _chunk(fourcc: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) & 1 else b""
    return fourcc + struct.pack("<I", len(payload)) + payload + pad


def _u24(value: int) -> bytes:
    return value.to_bytes(3, "little")


def _frame_parts(data: bytes) -> tuple[list[bytes], bool]:
    """The image-data chunks of one still frame and whether it has alpha."""
    parts: list[bytes] = []
    alpha = False
    has_image = False
    for fourcc, payload in _chunks(data):
        if fourcc == b"ALPH":
            alpha = True
            parts.append(_chunk(fourcc, payload))
        elif fourcc == b"VP8 ":
            has_image = True
            parts.append(_chunk(fourcc, payload))
        elif fourcc == b"VP8L":
            has_image = True
            if len(payload) >= 5 and payload[0] == 0x2F:
                alpha = alpha or bool(int.from_bytes(payload[1:5], "little") >> 28 & 1)
            parts.append(_chunk(fourcc, payload))
        elif fourcc == b"ANMF":
            raise ValueError("Frame is already animated")
    if not has_image:
        raise ValueError("WebP frame has no image data")
    return parts, alpha


def mux_animated_webp(
    frames: list[bytes], delays_ms: list[int], width: int, height: int, loop_count: int = 0
) -> bytes:
    """Build an animated WebP from full-canvas single-frame WebP byte strings.

    loop_count 0 loops forever. Each frame covers the whole canvas."""
    if not frames or len(frames) != len(delays_ms):
        raise ValueError("Frames and delays must match and not be empty")
    if not (0 < width <= MAX_CANVAS and 0 < height <= MAX_CANVAS):
        raise ValueError("Invalid canvas size")
    if not 0 <= loop_count <= 0xFFFF:
        raise ValueError("Invalid loop count")
    body = bytearray()
    any_alpha = False
    for data, delay in zip(frames, delays_ms, strict=True):
        parts, alpha = _frame_parts(data)
        any_alpha = any_alpha or alpha
        header = (
            _u24(0) + _u24(0) + _u24(width - 1) + _u24(height - 1)
            + _u24(max(0, min(int(delay), MAX_DURATION)))
            + bytes([0x02])  # do not blend, do not dispose: frames cover the canvas
        )
        body += _chunk(b"ANMF", header + b"".join(parts))
    flags = 0x02 | (0x10 if any_alpha else 0)
    vp8x = _chunk(b"VP8X", bytes([flags]) + b"\x00\x00\x00" + _u24(width - 1) + _u24(height - 1))
    anim = _chunk(b"ANIM", b"\xff\xff\xff\xff" + struct.pack("<H", loop_count))
    payload = b"WEBP" + vp8x + anim + bytes(body)
    return b"RIFF" + struct.pack("<I", len(payload)) + payload
