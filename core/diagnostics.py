# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from copy import deepcopy
import getpass
import importlib
import os
import platform
import re
import sys
import threading
from collections.abc import Callable
from typing import cast

from core import database
from core.host_platform import (
    get_data_dir,
    host_which,
    is_flatpak,
    is_linux,
    is_windows,
)

_stages: dict[str, dict[str, object]] = {}
_stage_lock = threading.Lock()


def record_renderer_stage(stage: str, details: dict[str, object]) -> None:
    with _stage_lock:
        _stages[stage] = deepcopy(details)


def get_renderer_stages() -> dict[str, dict[str, object]]:
    with _stage_lock:
        return deepcopy(_stages)


def packaging_label() -> str:
    if is_flatpak():
        return "Flatpak"
    if os.environ.get("APPIMAGE"):
        return "AppImage"
    if not getattr(sys, "frozen", False):
        return "Source"
    if is_windows():
        return "Windows build"
    root = str(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    if root == "/opt/wlib" or root.startswith("/opt/wlib/"):
        return "Native package"
    return "tar.gz"


def collect_basic(app_version: str, extension_version: str) -> dict[str, object]:
    os_name = platform.platform()
    if is_linux():
        try:
            release = platform.freedesktop_os_release()
            os_name = release.get("PRETTY_NAME", os_name)
        except OSError:
            pass
    elif is_windows():
        os_name = "Windows " + " ".join(part for part in platform.win32_ver() if part)
    qt_version: str | None = None
    webengine_version: str | None = None
    try:
        qt_core = importlib.import_module("qtpy.QtCore")
        qt_version = cast(Callable[[], str], qt_core.qVersion)()
        webengine = importlib.import_module("qtpy.QtWebEngineCore")
        version = getattr(webengine, "qWebEngineVersion", None)
        if callable(version):
            webengine_version = str(version())
    except (ImportError, RuntimeError, AttributeError):
        pass
    stages = get_renderer_stages()
    count = database.get_game_count()
    size = 0
    for path in (database.DB_PATH, database.DB_PATH + "-wal"):
        try:
            size += os.path.getsize(path)
        except OSError:
            pass
    return {
        "app_version": app_version,
        "extension_version": extension_version,
        "packaging": packaging_label(),
        "os": os_name,
        "platform": sys.platform,
        "python": platform.python_version(),
        "qt": qt_version,
        "webengine": webengine_version,
        "renderer_stages": stages,
        "data_path": get_data_dir(),
        "database_path": database.DB_PATH,
        "log_path": os.path.join(get_data_dir(), "logs"),
        "database_bytes": size,
        "game_count": count,
    }


def collect_tools(
    runner_lookup: Callable[[], dict[str, object]],
    extension_lookup: Callable[[], object],
) -> dict[str, object]:
    tools: dict[str, object] = {
        name: host_which(name) for name in ("7zz", "7z", "7za", "bsdtar", "unrar")
    }
    if is_linux():
        tools["wine"] = host_which("wine")
        tools["runners"] = runner_lookup().get("runners", [])
    tools["extension"] = extension_lookup()
    return tools


def redact_paths(text: str) -> str:
    home = os.path.expanduser("~").rstrip("/\\")
    flags = re.IGNORECASE if is_windows() else 0
    if home:
        pattern = re.escape(home)
        if is_windows():
            pattern = r"[/\\]".join(
                re.escape(part) for part in re.split(r"[/\\]", home)
            )
        text = re.sub(pattern + r"(?=$|[/\\\s\"'])", "~", text, flags=flags)
    username = getpass.getuser()
    if len(username) >= 3:
        text = re.sub(
            r"(?<=[/\\])" + re.escape(username) + r"(?=$|[/\\\s\"'])",
            "<user>",
            text,
            flags=flags,
        )
    return text


def build_report(
    basic: dict[str, object], tools: dict[str, object] | None = None
) -> str:
    # Only collector fields enter reports; callers cannot accidentally include game records.
    allowed = (
        "app_version",
        "extension_version",
        "packaging",
        "os",
        "platform",
        "python",
        "qt",
        "webengine",
        "renderer_stages",
        "data_path",
        "database_path",
        "log_path",
        "database_bytes",
        "game_count",
    )
    lines = ["wLib diagnostics"]

    def append(
        label: str, value: object, depth: int = 0, missing: str = "Not available"
    ) -> None:
        prefix = "  " * depth
        if isinstance(value, (dict, list)) and not value:
            lines.append(f"{prefix}{label}: {missing}")
        elif isinstance(value, dict):
            lines.append(f"{prefix}{label}:")
            for key, item in cast(dict[str, object], value).items():
                append(key, item, depth + 1, missing)
        elif isinstance(value, list):
            lines.append(f"{prefix}{label}:")
            for item in cast(list[object], value):
                append("runner", item, depth + 1, missing)
        else:
            lines.append(
                f"{prefix}{label}: {value if value is not None and value != '' else missing}"
            )

    for key in allowed:
        append(key, basic.get(key))
    if tools is not None:
        # A tool lookup that ran and found nothing is "Not found", as on the page.
        append("tools", tools, missing="Not found")
    return redact_paths("\n".join(lines))
