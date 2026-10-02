# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Literal, TypedDict


HostName = Literal["linux", "windows", "unsupported"]


class PlatformCapabilities(TypedDict):
    platform: HostName
    native_windows_launch: bool
    wine_proton: bool
    runtime_installers: bool
    wayland: bool
    rpgmaker_linux: bool
    cheat_engine_injection: bool
    launch_modes: list[str]
    data_dir: str
    cache_dir: str
    playwright_browsers_path: str
    extension_dir: str


def get_host_name(platform: str | None = None) -> HostName:
    value = (platform or sys.platform).lower()
    if value.startswith("win"):
        return "windows"
    if value.startswith("linux"):
        return "linux"
    return "unsupported"


def is_windows(platform: str | None = None) -> bool:
    return get_host_name(platform) == "windows"


def is_linux(platform: str | None = None) -> bool:
    return get_host_name(platform) == "linux"


def _environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _home(home: str | None) -> str:
    return os.path.abspath(os.path.expanduser(home or "~"))


def get_data_dir(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | None = None,
) -> str:
    env = _environment(environ)
    override = str(env.get("WLIB_DATA_DIR") or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))

    host = get_host_name(platform)
    if host == "windows":
        base = str(env.get("LOCALAPPDATA") or "").strip()
        if not base:
            base = os.path.join(_home(home), "AppData", "Local")
    else:
        base = str(env.get("XDG_DATA_HOME") or "").strip()
        if not base:
            base = os.path.join(_home(home), ".local", "share")
    return os.path.abspath(os.path.join(os.path.expanduser(base), "wLib"))


def get_cache_root(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | None = None,
) -> str:
    env = _environment(environ)
    override = str(env.get("WLIB_CACHE_DIR") or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))

    host = get_host_name(platform)
    if host == "windows":
        return os.path.join(
            get_data_dir(platform=platform, environ=env, home=home), "cache"
        )

    base = str(env.get("XDG_CACHE_HOME") or "").strip()
    if not base:
        base = os.path.join(_home(home), ".cache")
    return os.path.abspath(os.path.expanduser(base))


def get_playwright_browsers_path(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | None = None,
) -> str:
    env = _environment(environ)
    override = str(env.get("WLIB_PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if get_host_name(platform) == "windows":
        return os.path.join(
            get_data_dir(platform=platform, environ=env, home=home), "playwright"
        )
    return os.path.join(
        get_cache_root(platform=platform, environ=env, home=home), "ms-playwright"
    )


def get_data_subdir(name: str, *, platform: str | None = None) -> str:
    return os.path.join(get_data_dir(platform=platform), name)


def get_browser_session_dir() -> str:
    return get_data_subdir("browser_session")


def get_extension_dir(*, platform: str | None = None) -> str:
    return get_data_subdir("extension", platform=platform)


def get_default_wine_prefix() -> str:
    return get_data_subdir("prefix")


def get_proton_dir() -> str:
    return get_data_subdir("proton")


def get_rtp_dir() -> str:
    return get_data_subdir("rtp")


def get_cheat_engine_dir() -> str:
    return get_data_subdir("CheatEngine")


def get_platform_capabilities(platform: str | None = None) -> PlatformCapabilities:
    host = get_host_name(platform)
    linux = host == "linux"
    windows = host == "windows"
    return {
        "platform": host,
        "native_windows_launch": windows,
        "wine_proton": linux,
        "runtime_installers": linux,
        "wayland": linux,
        "rpgmaker_linux": linux,
        "cheat_engine_injection": linux,
        "launch_modes": ["auto", "native", "wine_proton", "rpgmaker_linux"]
        if linux
        else ["auto"],
        "data_dir": get_data_dir(platform=platform),
        "cache_dir": get_cache_root(platform=platform),
        "playwright_browsers_path": get_playwright_browsers_path(platform=platform),
        "extension_dir": get_extension_dir(platform=platform),
    }


def unsupported_on_host(feature: str) -> dict[str, object]:
    return {
        "success": False,
        "error": f"{feature} is only available on Linux.",
        "code": "unsupported_platform",
        "error_code": "unsupported_platform",
    }


def open_windows_system_target(target: str) -> tuple[bool, str]:
    if not is_windows():
        return False, "Windows system associations are unavailable on this host."

    startfile = getattr(os, "startfile", None)
    if not callable(startfile):
        return False, "Windows system association API is unavailable."

    try:
        _ = startfile(target)
    except OSError as exc:
        return False, str(exc)
    return True, ""
