# SPDX-License-Identifier: GPL-3.0-or-later
"""Decide whether a launch target opens in the built-in web player or the system browser."""
from __future__ import annotations

import os

from core.database import normalize_html_player, normalize_launch_mode
from core.host_platform import is_windows

WEB_PLAYER_HTML_EXTENSIONS = (".html", ".htm")
WEB_PLAYER_FLASH_EXTENSIONS = (".swf",)


def resolve_web_player(
    target: object,
    launch_mode: object = "auto",
    html_player: object = "",
    setting: bool | None = True,
    *,
    windows: bool | None = None,
) -> str | None:
    """Return "builtin", "browser" or None (not a web target) for a launch target.

    Custom Command, Wine/Proton and the RPGMaker runner start real processes on
    Linux, so an HTML target launched that way is not a web launch. A .swf file
    always uses the built-in player; an HTML file follows the per-game choice,
    then the setting (absent reads as on).
    """
    ext = os.path.splitext(str(target or ""))[1].lower()
    if ext not in WEB_PLAYER_HTML_EXTENSIONS + WEB_PLAYER_FLASH_EXTENSIONS:
        return None
    on_windows = is_windows() if windows is None else windows
    if not on_windows and normalize_launch_mode(launch_mode) not in ("auto", "native"):
        return None
    if ext in WEB_PLAYER_FLASH_EXTENSIONS:
        return "builtin"
    choice = normalize_html_player(html_player)
    if choice:
        return choice
    return "builtin" if setting is None or setting else "browser"
