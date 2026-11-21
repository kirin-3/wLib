# SPDX-License-Identifier: GPL-3.0-or-later
# pyright: reportImportCycles=false
"""Shared helpers for the per-game mod tools (Ren'Py, RPG Maker, Unity).

Everything here takes explicit paths so it can be tested against a temp dir.
`core.api` resolves a game's folder from the stored row and calls in.
"""

from __future__ import annotations

import errno
import os
import re
import secrets
import shutil
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

ENGINE_RENPY = "renpy"
ENGINE_RPGM = "rpgm"
ENGINE_RGSS = "rgss"
ENGINE_UNITY = "unity"

# progress(done, total, current file name) and cancelled() -> bool
ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]

PARTIAL_SUFFIX = ".wlib-partial"
CHUNK_SIZE = 1024 * 1024


_TEMP_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_BINARY", 0)
)


def create_temp_file(path: str, suffix: str = PARTIAL_SUFFIX) -> tuple[int, str]:
    """Create a new, empty file next to `path` and return (fd, its path).

    The name is random and the file is created exclusively, so an existing
    file or symlink at a fixed `<name><suffix>` path is never opened,
    truncated or removed. The caller owns the file and removes it.
    """
    folder = os.path.dirname(path)
    for _ in range(100):
        temporary = os.path.join(folder, f".wlib-{secrets.token_hex(4)}{suffix}")
        try:
            return os.open(temporary, _TEMP_FLAGS, 0o666), temporary
        except FileExistsError:
            continue
    raise FileExistsError(errno.EEXIST, "No free temporary file name", path)


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


_RGSS_ARCHIVE_EXTENSIONS = (".rgssad", ".rgss2a", ".rgss3a")
_RGSS_BACKUP_SUFFIX = ".wlib-backup"
_RGSS_SCRIPT_NAMES = frozenset(
    ("scripts.rxdata", "scripts.rvdata", "scripts.rvdata2")
)


def is_rgss_folder(folder: str) -> bool:
    """True when `folder` holds an RGSS archive (or backup) or `Data/Scripts.*`."""
    try:
        entries = os.listdir(folder)
    except OSError:
        return False
    for name in entries:
        lowered = name.lower()
        base = (
            lowered[: -len(_RGSS_BACKUP_SUFFIX)]
            if lowered.endswith(_RGSS_BACKUP_SUFFIX)
            else lowered
        )
        path = os.path.join(folder, name)
        if base.endswith(_RGSS_ARCHIVE_EXTENSIONS) and not os.path.islink(path) and os.path.isfile(path):
            return True
        if lowered == "data" and os.path.isdir(path):
            try:
                data_entries = os.listdir(path)
            except OSError:
                continue
            for sub in data_entries:
                if sub.lower() in _RGSS_SCRIPT_NAMES:
                    sub_path = os.path.join(path, sub)
                    if not os.path.islink(sub_path) and os.path.isfile(sub_path):
                        return True
    return False


def detect_engine(folder: str, exe_path: str = "") -> str:
    """"renpy", "rpgm", "rgss", "unity" or "" from the files on disk (the stored label is ignored)."""
    if not folder or not os.path.isdir(folder):
        return ""
    if is_renpy_folder(folder):
        return ENGINE_RENPY
    if rpgm_data_root(folder) is not None:
        return ENGINE_RPGM
    if is_rgss_folder(folder):
        return ENGINE_RGSS
    from core.unity_tools import is_unity_folder

    if is_unity_folder(folder, exe_path):
        return ENGINE_UNITY
    return ""


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


# ── Shared extraction helpers ──


_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


class _DiskUsage(Protocol):
    @property
    def free(self) -> int: ...


def is_inside(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        return False


def validate_output_folder(game_folder: str, output: str) -> str:
    """The real path of an existing absolute folder outside the game folder."""
    if not output or not os.path.isabs(output):
        raise GameToolError("Choose an output folder")
    if not os.path.isdir(output):
        raise GameToolError("The output folder does not exist")
    real_output = os.path.realpath(output)
    if is_inside(os.path.realpath(game_folder), real_output):
        raise GameToolError(
            "Choose a folder outside the game folder, or extract into the game folder"
        )
    return real_output


def safe_relative_parts(name: str) -> list[str] | None:
    """Path parts of an archive entry name, or None when it is unsafe."""
    normalized = name.replace("\\", "/")
    if not normalized or "\0" in normalized:
        return None
    if normalized.startswith("/") or _DRIVE_PREFIX.match(normalized):
        return None
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return parts


def resolve_destination(root: str, root_real: str, name: str) -> str | None:
    parts = safe_relative_parts(name)
    if parts is None:
        return None
    destination = os.path.join(root, *parts)
    # Resolves a symlink planted in the target as well as `..` tricks.
    if not is_inside(root_real, os.path.realpath(os.path.dirname(destination))):
        return None
    return destination


def export_root(
    output_real: str, game_real: str, stem: str, used_targets: set[str]
) -> str:
    """The folder under `output_real` that one archive is exported into.

    A name already taken in this run, a symlink, or a folder that resolves
    outside the output folder or overlaps the game folder is passed over for
    the next `<stem>_<n>` name, so the export never writes into the game.
    """
    target = stem
    counter = 2
    while True:
        if target not in used_targets:
            root = os.path.join(output_real, target)
            root_real = os.path.realpath(root)
            if (
                not os.path.islink(root)
                and is_inside(output_real, root_real)
                and not is_inside(game_real, root_real)
                and not is_inside(root_real, game_real)
            ):
                used_targets.add(target)
                return root
        target = f"{stem}_{counter}"
        counter += 1


def nearest_existing(path: str) -> str:
    current = os.path.abspath(path)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def check_free_space(
    items: Iterable[tuple[str, int]],
    disk_usage: Callable[[str], _DiskUsage] | None = None,
) -> None:
    usage_fn = shutil.disk_usage if disk_usage is None else disk_usage
    needed: dict[int, tuple[int, str]] = {}
    for destination, size in items:
        anchor = nearest_existing(os.path.dirname(destination))
        try:
            device = os.stat(anchor).st_dev
        except OSError:
            continue
        total, _path = needed.get(device, (0, anchor))
        needed[device] = (total + size, anchor)
    for total, anchor in needed.values():
        try:
            free = usage_fn(anchor).free
        except OSError:
            continue
        if free < total:
            raise GameToolError(
                f"Not enough free space: need {format_bytes(total)}, "
                + f"have {format_bytes(free)}."
            )
