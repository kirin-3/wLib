# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict


HostName = Literal["linux", "windows", "unsupported"]


class PlatformCapabilities(TypedDict):
    platform: HostName
    sandbox: str | None
    native_windows_launch: bool
    wine_proton: bool
    runtime_installers: bool
    wayland: bool
    rpgmaker_linux: bool
    companion_tools: bool
    cheat_engine_injection: bool
    launch_modes: list[str]
    data_dir: str
    cache_dir: str
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
        legacy_dir = os.path.join(_home(home), ".local", "share", "wLib")
        xdg_home = str(env.get("XDG_DATA_HOME") or "").strip()
        if not xdg_home:
            return legacy_dir
        xdg_dir = os.path.abspath(os.path.join(os.path.expanduser(xdg_home), "wLib"))
        # Releases before XDG support always used ~/.local/share/wLib; keep
        # using an existing library there instead of starting empty.
        if not os.path.isdir(xdg_dir) and os.path.isdir(legacy_dir):
            return legacy_dir
        return xdg_dir
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


def get_data_subdir(name: str, *, platform: str | None = None) -> str:
    return os.path.join(get_data_dir(platform=platform), name)


def get_browser_session_dir() -> str:
    return get_data_subdir("browser_session")


def get_legacy_browser_dirs() -> list[str]:
    return [get_browser_session_dir(), get_data_subdir("playwright")]


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
        "sandbox": "bwrap" if linux else "sandboxie" if windows else None,
        "native_windows_launch": windows,
        "wine_proton": linux,
        "runtime_installers": linux,
        "wayland": linux,
        "rpgmaker_linux": linux,
        "companion_tools": linux,
        "cheat_engine_injection": linux,
        "launch_modes": ["auto", "native", "wine_proton", "rpgmaker_linux", "custom"]
        if linux
        else ["auto"],
        "data_dir": get_data_dir(platform=platform),
        "cache_dir": get_cache_root(platform=platform),
        "extension_dir": get_extension_dir(platform=platform),
    }


def unsupported_on_host(feature: str) -> dict[str, object]:
    return {
        "success": False,
        "error": f"{feature} is only available on Linux.",
        "code": "unsupported_platform",
        "error_code": "unsupported_platform",
    }


FLATPAK_INFO_PATH = "/.flatpak-info"

# PyInstaller points LD_LIBRARY_PATH at the bundled libraries. flatpak-spawn is
# the runtime's binary, and the bundle's older libmount makes its GIO fail to
# load, so the variable must not reach it. The host command never sees it.
_FLATPAK_SPAWN = ("env", "-u", "LD_LIBRARY_PATH", "flatpak-spawn", "--host")

# `kill -0 $g` covers the window before the child has called setsid (no group
# yet). The trap escalates to SIGKILL on the host because flatpak-spawn cannot
# forward the sandbox-side SIGKILL from Launcher.stop().
_HOST_SUPERVISOR_SCRIPT = (
    'setsid "$@" & g=$!; '
    'trap "kill -TERM -$g 2>/dev/null; (sleep 4; kill -KILL -$g 2>/dev/null) &" TERM INT HUP; '
    "while kill -0 $g 2>/dev/null || kill -0 -$g 2>/dev/null; do sleep 1; done"
)


def is_flatpak() -> bool:
    """True when running inside a Flatpak sandbox (design D4).

    Uses `os.path.isfile` (the marker is a file) so blanket
    `patch("os.path.exists", return_value=True)` fixtures in unrelated tests
    do not flip sandbox mode on.
    """
    try:
        return os.path.isfile(FLATPAK_INFO_PATH)
    except OSError:
        return False


def _is_sandbox_only_env_var(name: str) -> bool:
    if name == "LD_LIBRARY_PATH":
        return True
    return name.startswith(("QT_", "PYTHON", "FLATPAK_", "QTWEBENGINE_"))


def host_which(name: str) -> str | None:
    """Find `name` on the host PATH (design D4).

    Outside Flatpak this is `shutil.which`. Inside, the sandbox PATH is
    invisible, so resolve via a host login shell plus common per-user folders.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    if not is_flatpak():
        return shutil.which(cleaned)
    try:
        completed = subprocess.run(
            [
                *_FLATPAK_SPAWN,
                "sh",
                "-lc",
                'PATH="$PATH:$HOME/.local/bin:$HOME/.nix-profile/bin:'
                + '/nix/var/nix/profiles/default/bin:/home/linuxbrew/.linuxbrew/bin:'
                + '/run/current-system/sw/bin"; command -v "$1"',
                "sh",
                cleaned,
            ],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    # A login profile may print text first; `command -v` writes the last line.
    lines = [line.strip() for line in (completed.stdout or "").splitlines()]
    path = next((line for line in reversed(lines) if line), "")
    if not path.startswith("/"):
        return None
    return path


def host_command(
    cmd: Sequence[str],
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    supervise: bool = False,
) -> list[str]:
    """Prefix `cmd` so it runs on the host when inside Flatpak (design D4/D5).

    Outside Flatpak the command is returned unchanged. Inside, the result is
    `env -u LD_LIBRARY_PATH flatpak-spawn --host --watch-bus [--directory=cwd]
    [--env=K=V ...] -- <cmd>`, where only env vars that differ from `os.environ` are passed and
    sandbox-only vars are never forwarded. With `supervise=True` the host-side
    command is wrapped in a small `sh`/`setsid` supervisor so process-group
    tracking keeps working across the sandbox boundary.
    """
    base = [str(part) for part in cmd]
    if not is_flatpak():
        return base
    inner: list[str] = base
    if supervise:
        inner = ["sh", "-c", _HOST_SUPERVISOR_SCRIPT, "sh", *base]
    argv: list[str] = [*_FLATPAK_SPAWN, "--watch-bus"]
    if cwd is not None:
        argv.append(f"--directory={cwd}")
    if env:
        for key, value in env.items():
            if _is_sandbox_only_env_var(key):
                continue
            if os.environ.get(key) == value:
                continue
            argv.append(f"--env={key}={value}")
    argv.append("--")
    argv.extend(inner)
    return argv


def open_windows_system_target(
    target: str, *, platform: str | None = None
) -> tuple[bool, str]:
    if not is_windows(platform):
        return False, "Windows system associations are unavailable on this host."

    startfile = getattr(os, "startfile", None)
    if not callable(startfile):
        return False, "Windows system association API is unavailable."

    try:
        _ = startfile(target)
    except OSError as exc:
        return False, str(exc)
    return True, ""
