# SPDX-License-Identifier: GPL-3.0-or-later
"""The size of the screen that holds the wLib window, in physical pixels.

`main.py` sets it on the GUI thread; the launcher reads it from any thread to
size gamescope when both gamescope resolutions are Auto.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_screen_size: tuple[int, int] | None = None


def set_screen_size(width: object, height: object) -> None:
    """Record the screen size; a size that is not two positive numbers clears it."""
    global _screen_size
    try:
        size = (int(str(width)), int(str(height)))
    except (TypeError, ValueError):
        size = None
    if size is not None and (size[0] <= 0 or size[1] <= 0):
        size = None
    with _lock:
        _screen_size = size


def screen_size() -> tuple[int, int] | None:
    with _lock:
        return _screen_size
