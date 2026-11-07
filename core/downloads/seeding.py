# SPDX-License-Identifier: GPL-3.0-or-later
"""Seeding policy: pure functions, no I/O."""
from __future__ import annotations

ASK = "ask"
SEED = "seed"
STOP = "stop"

MODE_ASK = "ask"
MODE_ALWAYS = "always"
MODE_NEVER = "never"
SEED_MODES = (MODE_ASK, MODE_ALWAYS, MODE_NEVER)

DEFAULT_RATIO = 1.0


def decide_after_finish(mode: str) -> str:
    """What to do when a torrent finishes: ask, seed or stop."""
    if mode == MODE_ALWAYS:
        return SEED
    if mode == MODE_NEVER:
        return STOP
    return ASK


def limit_reached(
    uploaded: int, size: int, seconds: float, ratio: float | None, hours: float | None
) -> bool:
    """True when uploaded bytes reach ratio x size, or the seeding time reaches the hour limit.

    `seconds` is time spent seeding only. A missing or zero limit never triggers.
    """
    if ratio is not None and ratio > 0 and size > 0 and uploaded >= ratio * size:
        return True
    return bool(hours is not None and hours > 0 and seconds >= hours * 3600)
