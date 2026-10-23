# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for the per-game mod tools (Ren'Py and RPG Maker MV/MZ).

Everything here takes explicit paths so it can be tested against a temp dir.
`core.api` resolves a game's folder from the stored row and calls in.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

ENGINE_RENPY = "renpy"
ENGINE_RPGM = "rpgm"

# progress(done, total, current file name) and cancelled() -> bool
ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]

PARTIAL_SUFFIX = ".wlib-partial"
CHUNK_SIZE = 1024 * 1024


class GameToolError(Exception):
    """A tool action failed; the message is safe to show the user."""


def game_tool_folder(row: Mapping[str, object]) -> str:
    """The folder of the stored executable, where engine files are looked up."""
    exe_path = str(row.get("exe_path") or "").strip()
    if not exe_path:
        raise ValueError("Game has no executable path")
    return os.path.dirname(os.path.abspath(exe_path))


def is_renpy_folder(folder: str) -> bool:
    return all(os.path.isdir(os.path.join(folder, part)) for part in ("renpy", "game"))


def rpgm_data_root(folder: str) -> str | None:
    """First of `<folder>/www` or `<folder>` that holds `data/System.json`."""
    for candidate in (os.path.join(folder, "www"), folder):
        if os.path.isfile(os.path.join(candidate, "data", "System.json")):
            return candidate
    return None


def detect_engine(folder: str) -> str:
    """"renpy", "rpgm" or "" from the files on disk (the stored label is ignored)."""
    if not folder or not os.path.isdir(folder):
        return ""
    if is_renpy_folder(folder):
        return ENGINE_RENPY
    if rpgm_data_root(folder) is not None:
        return ENGINE_RPGM
    return ""


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
