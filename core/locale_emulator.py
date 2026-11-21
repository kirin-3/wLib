# SPDX-License-Identifier: GPL-3.0-or-later
"""Locale Emulator detection and command wrap for Windows launches.

Locale Emulator (``LEProc.exe``) starts a Windows game with a Japanese code
page and locale. The user downloads it; wLib only finds it and starts games
through it.
"""

from __future__ import annotations

import ntpath
import os
import subprocess
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from threading import Lock
from types import ModuleType
from typing import Literal, TypedDict, cast

from core.database import get_setting
from core.host_platform import get_host_name

LEPROC_NAME = "LEProc.exe"
RELEASES_URL = "https://github.com/xupefei/Locale-Emulator/releases"
WRONG_FILE_ERROR = "Choose the Locale Emulator LEProc.exe executable."

# LEInstaller.exe registers its context-menu handler as a .NET COM class.
# "CodeBase" is a file:/// URL of LEContextMenuHandler.dll, which sits in the
# Locale Emulator folder next to LEProc.exe. Per-user installs write HKCU,
# all-user installs write HKLM.
_HANDLER_KEY = (
    r"Software\Classes\CLSID\{C52B9871-E5E9-41FD-B84D-C5ACADBEC7AE}\InprocServer32"
)


class LocaleEmulatorStatus(TypedDict):
    status: Literal["ready", "missing", "unusable"]
    path: str
    reason: str


_cache: dict[str, LocaleEmulatorStatus] = {}
_lock = Lock()


def is_leproc_path(path: str) -> bool:
    return ntpath.basename(path.strip()).casefold() == LEPROC_NAME.casefold()


def get_status(
    custom_path: str | None = None,
    *,
    platform: str | None = None,
    recheck: bool = False,
) -> LocaleEmulatorStatus:
    """Find LEProc.exe: the saved path, then the registry, then Program Files.

    Only a ``ready`` result is cached, so a missing tool is looked for again
    on the next call. ``recheck`` clears the cache.
    """
    custom = (
        str(get_setting("locale_emulator_path") or "")
        if custom_path is None
        else custom_path
    ).strip()
    host = get_host_name(platform)
    key = f"{host}\0{custom}"
    with _lock:
        if recheck:
            _cache.clear()
        cached = _cache.get(key)
        if cached is not None:
            return cached.copy()
        result = _probe(host, custom)
        print(
            f"[wLib] Locale Emulator check: status={result['status']} "
            + f"path={result['path']!r} reason={result['reason']!r}"
        )
        if result["status"] == "ready":
            _cache.clear()
            _cache[key] = result
        return result.copy()


def _probe(host: str, custom: str) -> LocaleEmulatorStatus:
    if host != "windows":
        return {
            "status": "unusable",
            "path": "",
            "reason": "Locale Emulator is only used on Windows.",
        }
    if custom:
        if not is_leproc_path(custom):
            return {"status": "unusable", "path": custom, "reason": WRONG_FILE_ERROR}
        if _is_file(custom):
            return {"status": "ready", "path": custom, "reason": ""}
        return {
            "status": "missing",
            "path": custom,
            "reason": f"Locale Emulator was not found at {custom}.",
        }
    for candidate in [*_registry_candidates(), *_program_files_candidates()]:
        if _is_file(candidate):
            return {"status": "ready", "path": candidate, "reason": ""}
    return {
        "status": "missing",
        "path": "",
        "reason": "Locale Emulator is not installed, or LEProc.exe could not be found.",
    }


def _is_file(path: str) -> bool:
    return os.path.isfile(path)


def _load_winreg() -> ModuleType | None:
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def _registry_candidates() -> list[str]:
    winreg = _load_winreg()
    if winreg is None:
        return []
    # Windows-only; absent from the Linux type stubs.
    open_key = cast(
        Callable[[object, str, int, int], AbstractContextManager[object]],
        getattr(winreg, "OpenKey"),
    )
    query_value = cast(
        Callable[[object, str], tuple[object, int]], getattr(winreg, "QueryValueEx")
    )
    read_access = int(cast(int, getattr(winreg, "KEY_READ"))) | int(
        cast(int, getattr(winreg, "KEY_WOW64_64KEY"))
    )
    candidates: list[str] = []
    for root_name in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"):
        root = cast(object, getattr(winreg, root_name))
        try:
            with open_key(root, _HANDLER_KEY, 0, read_access) as key:
                value, _kind = query_value(key, "CodeBase")
        except OSError:
            continue
        folder = _codebase_folder(str(value))
        if folder:
            candidates.append(ntpath.join(folder, LEPROC_NAME))
    return candidates


def _codebase_folder(codebase: str) -> str:
    """The folder of a .NET CodeBase URL such as file:///C:/LE/x.dll."""
    value = codebase.strip()
    for prefix in ("file:///", "file://"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
            break
    else:
        return ""
    value = value.replace("/", "\\")
    if not ntpath.isabs(value):
        return ""
    return ntpath.dirname(value)


def _program_files_candidates() -> list[str]:
    return [
        ntpath.join(base, "Locale Emulator", LEPROC_NAME)
        for base in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        )
    ]


def wrap_command(leproc: str, command: Sequence[str]) -> list[str]:
    """``[LEProc.exe, game.exe, *args]`` with the argument boundaries kept.

    LEProc.exe joins the arguments after the game path with plain spaces, so
    each one that needs quotes is quoted once more here. The .NET parser in
    LEProc.exe removes that level, and the game gets the original argument.
    """
    if not command:
        return [leproc]
    game, *args = command
    return [leproc, game, *(_quote_for_leproc(arg) for arg in args)]


def _quote_for_leproc(arg: str) -> str:
    if arg and not any(char in arg for char in ' \t"'):
        return arg
    return subprocess.list2cmdline([arg])
