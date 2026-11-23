# SPDX-License-Identifier: GPL-3.0-or-later
"""The native screen size of a game, read from its files (RPG Maker MV and MZ only).

Read-only and best effort: any read error or unexpected shape means "no size",
so a launch never fails because of it. No JavaScript is run.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from typing import NamedTuple, cast

MAX_SIDE = 16384
MAX_PLUGINS_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MV_DEFAULT = (816, 624)
# O_NONBLOCK and O_NOCTTY do not exist on Windows; O_BINARY exists only there
# and keeps the C runtime from changing line ends.
_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOCTTY", 0)
    | getattr(os, "O_BINARY", 0)
)

# Plugin name -> (width parameter, height parameter). Plugins load in list
# order and each sets the screen size, so the last enabled one wins.
KNOWN_PLUGINS: dict[str, tuple[str, str]] = {
    "YEP_CoreEngine": ("Screen Width", "Screen Height"),
    "Community_Basic": ("screenWidth", "screenHeight"),
}

_DIGITS_RE = re.compile(r"^\d+$", re.ASCII)
_JS_COMMENT_OR_STRING_RE = re.compile(
    r"""
    (?P<comment>
        //[^\r\n\u2028\u2029]*
        | /\*(?:[^*]++|\*(?!/))*+(?:\*/|\Z)
    )
    | '(?:[^'\\\r\n]++|\\.)*+'
    | "(?:[^"\\\r\n]++|\\.)*+"
    | `(?:[^`\\]++|\\.)*+`
    """,
    re.VERBOSE | re.DOTALL,
)
_MANAGER_RE = {
    "width": re.compile(r"SceneManager\._screenWidth\s*=\s*(\d+)\s*;", re.ASCII),
    "height": re.compile(r"SceneManager\._screenHeight\s*=\s*(\d+)\s*;", re.ASCII),
}


class NativeSize(NamedTuple):
    width: int
    height: int
    source: str  # Short text for the debug log, e.g. "www/data/System.json".


def detect_native_size(folder: str) -> NativeSize | None:
    """The game's native size, or None when it is not an MV/MZ game or cannot be read."""
    if not folder or not os.path.isdir(folder):
        return None
    root = _data_root(folder)
    if root is None:
        return None
    prefix = "www/" if os.path.normpath(root) != os.path.normpath(folder) else ""
    system = _read_json(os.path.join(root, "data", "System.json"), MAX_FILE_BYTES)
    if not isinstance(system, dict):
        return None
    advanced = cast(Mapping[str, object], system).get("advanced")
    if isinstance(advanced, dict):
        fields = cast(Mapping[str, object], advanced)
        size = _size(fields.get("screenWidth"), fields.get("screenHeight"))
        return NativeSize(*size, f"{prefix}data/System.json") if size else None
    if not os.path.isfile(os.path.join(root, "js", "rpg_core.js")):
        return None
    return _mv_size(root, prefix)


def _data_root(folder: str) -> str | None:
    """Same rule as `core.game_tools.rpgm_data_root()` (a test keeps them equal).

    Not imported: the launcher imports this module, and game_tools reaches the
    launcher again through unity_tools and archive_install (an import cycle).
    """
    for candidate in (os.path.join(folder, "www"), folder):
        if os.path.isfile(os.path.join(candidate, "data", "System.json")):
            return candidate
    return None


def _mv_size(root: str, prefix: str) -> NativeSize:
    result = NativeSize(*MV_DEFAULT, "MV default")
    managers = _read_text(os.path.join(root, "js", "rpg_managers.js"), MAX_FILE_BYTES)
    if managers is not None:
        # Comments are whitespace, while strings still occupy a token.
        managers = _JS_COMMENT_OR_STRING_RE.sub(
            lambda match: " " if match.group("comment") else '""', managers
        )
        width = _MANAGER_RE["width"].search(managers)
        height = _MANAGER_RE["height"].search(managers)
        size = (
            _size(int(width.group(1)), int(height.group(1)))
            if width and height
            else None
        )
        if size:
            result = NativeSize(*size, f"{prefix}js/rpg_managers.js")
    for entry in _plugin_list(os.path.join(root, "js", "plugins.js")):
        if not isinstance(entry, dict):
            continue
        plugin = cast(Mapping[str, object], entry)
        if plugin.get("status") is not True:
            continue
        name = plugin.get("name")
        if not isinstance(name, str) or name not in KNOWN_PLUGINS:
            continue
        raw_params = plugin.get("parameters")
        if not isinstance(raw_params, dict):
            continue
        params = cast(Mapping[str, object], raw_params)
        width_key, height_key = KNOWN_PLUGINS[name]
        size = _size(
            _param_int(params.get(width_key)), _param_int(params.get(height_key))
        )
        if size:
            result = NativeSize(*size, f"{prefix}js/plugins.js ({name})")
    return result


def _plugin_list(path: str) -> list[object]:
    """The `$plugins` array of plugins.js, parsed as JSON; [] when not readable."""
    text = _read_text(path, MAX_PLUGINS_BYTES)
    if text is None:
        return []
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        value = cast(object, json.loads(text[start : end + 1]))
    except ValueError:
        return []
    return cast(list[object], value) if isinstance(value, list) else []


def _param_int(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return int(text) if _DIGITS_RE.match(text) else None


def _size(width: object, height: object) -> tuple[int, int] | None:
    if not (_valid_side(width) and _valid_side(height)):
        return None
    assert isinstance(width, int) and isinstance(height, int)
    return width, height


def _valid_side(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= MAX_SIDE
    )


def _read_text(path: str, limit: int) -> str | None:
    """The text of a regular file of at most `limit` bytes; None otherwise.

    A named pipe or a device would make open() or read() wait for a writer
    and stall the launch: the open does not wait (O_NONBLOCK where it
    exists), and only a regular file is read.
    """
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        with open(fd, encoding="utf-8-sig", errors="replace", closefd=False) as handle:
            return handle.read(limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)


def _read_json(path: str, limit: int) -> object:
    text = _read_text(path, limit)
    if text is None:
        return None
    try:
        return cast(object, json.loads(text))
    except ValueError:
        return None
