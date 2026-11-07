# SPDX-License-Identifier: GPL-3.0-or-later
import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtGui import QColor, QImage, QImageReader, QImageWriter

from core.webp_anim import mux_animated_webp


def encode_frame(color: int, size=(20, 10), alpha=False, lossless=False) -> bytes:
    fmt = QImage.Format.Format_ARGB32 if alpha else QImage.Format.Format_RGB32
    image = QImage(size[0], size[1], fmt)
    image.fill(QColor(color, 40, 90, 128 if alpha else 255))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buf, b"webp")
    if lossless:
        writer.setQuality(100)
    assert writer.write(image)
    return bytes(data)


def read_back(data: bytes):
    ba = QByteArray(data)
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf, b"webp")
    out = []
    count = reader.imageCount()
    for _ in range(count):
        image = reader.read()
        delay = reader.nextImageDelay()
        out.append((image.width(), image.height(), delay))
    return count, reader.loopCount(), out


def test_mux_roundtrip():
    frames = [encode_frame(c) for c in (10, 120, 240)]
    data = mux_animated_webp(frames, [50, 100, 150], 20, 10)
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8
    count, loops, images = read_back(data)
    assert count == 3
    assert loops == -1
    assert [(w, h) for w, h, _ in images] == [(20, 10)] * 3


def test_mux_delays():
    frames = [encode_frame(c) for c in (10, 120)]
    data = mux_animated_webp(frames, [50, 130], 20, 10)
    ba = QByteArray(data)
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf, b"webp")
    delays = []
    for _ in range(reader.imageCount()):
        assert not reader.read().isNull()
        delays.append(reader.nextImageDelay())
    assert delays == [50, 130]


def test_odd_padding_and_alpha():
    frames = [encode_frame(c, size=(7, 5), alpha=True, lossless=True) for c in (1, 200)]
    data = mux_animated_webp(frames, [40, 40], 7, 5)
    assert data[20] & 0x10  # VP8X alpha flag
    assert len(data) % 2 == 0
    count, _loops, images = read_back(data)
    assert count == 2 and images[0][:2] == (7, 5)


def test_single_frame():
    data = mux_animated_webp([encode_frame(5)], [100], 20, 10, loop_count=3)
    count, _loops, _ = read_back(data)
    assert count == 1


@pytest.mark.parametrize(
    "frames,delays",
    [([], []), ([b"nope"], [10]), ([b"RIFF\x04\x00\x00\x00WEBP"], [10])],
)
def test_malformed_input(frames, delays):
    with pytest.raises(ValueError):
        mux_animated_webp(frames, delays, 20, 10)


def test_mismatched_lengths():
    with pytest.raises(ValueError):
        mux_animated_webp([encode_frame(1)], [10, 20], 20, 10)


def test_rejects_animated_frame():
    animated = mux_animated_webp([encode_frame(1)], [10], 20, 10)
    with pytest.raises(ValueError):
        mux_animated_webp([animated], [10], 20, 10)
