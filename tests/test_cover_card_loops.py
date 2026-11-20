# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

from PyQt6.QtGui import QImageReader

from core import cover_cards


def test_card_copy_keeps_one_repeat(tmp_path: Path) -> None:
    source = tmp_path / ("a" * 32 + ".gif")
    _ = source.write_bytes(
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
        b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x01\x00\x00"  # Repeat once.
        b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00"
        b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x4c\x01\x00"
        b"\x3b"
    )
    assert QImageReader(str(source)).loopCount() == 1

    assert cover_cards.make_card_copies(source) == (True, True)
    paths = cover_cards.card_paths(source.name)
    assert paths is not None
    reader = QImageReader(str(paths[1]))
    assert reader.imageCount() == 2
    assert reader.loopCount() == 1
