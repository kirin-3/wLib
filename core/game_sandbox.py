# SPDX-License-Identifier: GPL-3.0-or-later
"""Host sandbox detection, command construction and per-game storage."""

from __future__ import annotations

import ntpath
import glob
import os
import shlex
import subprocess
from collections.abc import Mapping
from threading import Lock
from typing import Literal, TypedDict, cast
from http.client import HTTPResponse

from core.database import get_setting
from core.host_platform import (
    get_data_dir,
    get_host_name,
    host_command,
    host_which,
    is_flatpak,
    no_console_window,
)


# No --die-with-parent: a sandboxed game keeps running when wLib quits, like
# every other game. Stopping bwrap still ends the whole PID namespace.
BWRAP_NAMESPACE_OPTIONS = (
    "--unshare-user",
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-uts",
    "--unshare-cgroup-try",
)

# Host variables a sandboxed game may see. Everything else from wLib's own
# environment (tokens, SSH/GPG agents, cloud credentials) stays outside.
SANDBOX_ENV_NAMES = frozenset(
    {
        "PATH", "LANG", "LANGUAGE", "TERM", "TZ", "USER", "LOGNAME", "SHELL",
        "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
        "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP",
        "XDG_DATA_DIRS", "XDG_CONFIG_DIRS", "XMODIFIERS", "GTK_IM_MODULE",
        "QT_IM_MODULE", "SDL_IM_MODULE", "PULSE_SERVER", "PULSE_SINK",
        "PIPEWIRE_RUNTIME_DIR", "LD_LIBRARY_PATH", "LIBGL_ALWAYS_SOFTWARE",
        "GAMEID", "STORE", "SteamAppId", "SteamGameId", "SteamOverlayGameId",
    }
)
SANDBOX_ENV_PREFIXES = (
    "LC_", "WINE", "PROTON", "DXVK_", "VKD3D_", "MESA_", "__GL", "__NV",
    "__VK", "VK_", "NVIDIA_", "DRI_", "LIBGL_", "SDL_", "MANGOHUD", "ENABLE_VK",
    "STEAM_", "UMU_", "PRESSURE_VESSEL_", "WLIB_", "GST_", "ALSA_",
    "RADV_", "AMD_", "INTEL_", "ANV_", "PULSE_",
)


# Qt and Chromium settings the built-in web player needs from its own runtime.
PLAYER_ENV_NAMES = frozenset(
    {
        "QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QT_QUICK_BACKEND", "QT_OPENGL",
        "QSG_RHI_BACKEND", "QTWEBENGINE_CHROMIUM_FLAGS", "QTWEBENGINE_DISABLE_SANDBOX",
        "QTWEBENGINE_RESOURCES_PATH", "QTWEBENGINE_LOCALES_PATH", "QML2_IMPORT_PATH",
        "PYTHONHOME", "PYTHONPATH", "DEV_MODE",
    }
)


def sandbox_environment(
    environment: Mapping[str, str],
    host: Mapping[str, str] | None = None,
    extra_names: frozenset[str] | None = None,
) -> dict[str, str]:
    """The environment a sandboxed game is started with.

    Keeps display, audio, locale and graphics/Wine settings from the host, and
    every variable wLib or the user's launch options set or changed. Drops
    the rest of wLib's own environment."""
    host_env = os.environ if host is None else host
    return {
        key: value
        for key, value in environment.items()
        if key in SANDBOX_ENV_NAMES
        or key in (extra_names or ())
        or key.startswith(SANDBOX_ENV_PREFIXES)
        or host_env.get(key) != value
    }


class SandboxStatus(TypedDict):
    status: Literal["ready", "missing", "unusable"]
    tool: str
    path: str
    version: str
    reason: str


_probe_cache: dict[tuple[str, str], SandboxStatus] = {}
_probe_lock = Lock()


def get_sandbox_status(
    custom_path: str | None = None,
    *,
    platform: str | None = None,
    recheck: bool = False,
) -> SandboxStatus:
    custom = (
        str(get_setting("sandboxie_custom_path") or "")
        if custom_path is None
        else custom_path
    )
    key = (get_host_name(platform), custom.strip())
    with _probe_lock:
        if recheck:
            _probe_cache.clear()
        result = _probe_cache.get(key)
        if result is None:
            result = _probe(*key)
            print(
                f"[wLib] Sandbox check: status={result['status']} tool={result['tool']} "
                + f"path={result['path']!r} version={result['version']!r} reason={result['reason']!r}"
            )
            if result["status"] == "ready":
                if len(_probe_cache) >= 8:
                    del _probe_cache[next(iter(_probe_cache))]
                _probe_cache[key] = result
        return result.copy()


def _probe(host: str, custom: str) -> SandboxStatus:
    result: SandboxStatus = {
        "status": "missing",
        "tool": "bwrap" if host == "linux" else "sandboxie",
        "path": "",
        "version": "",
        "reason": "",
    }
    if host == "windows":
        if custom and ntpath.basename(custom).casefold() != "start.exe":
            result.update(
                status="unusable", reason="Choose the Sandboxie Start.exe executable."
            )
            return result
        candidates = [custom] if custom else []
        for base in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ):
            candidates.extend(
                ntpath.join(base, name, "Start.exe")
                for name in ("Sandboxie-Plus", "Sandboxie Plus", "Sandboxie")
            )
        for candidate in candidates:
            if os.path.isfile(candidate):
                result.update(status="ready", path=candidate)
                return result
        result["reason"] = (
            "Sandboxie-Plus is not installed, or Start.exe could not be found."
        )
        return result
    if host != "linux":
        result.update(
            status="unusable",
            tool="",
            reason="Game sandboxing is supported on Linux and Windows.",
        )
        return result
    path = host_which("bwrap")
    if not path:
        result["reason"] = "Bubblewrap is not installed."
        return result
    result["path"] = path
    try:
        version = subprocess.run(
            host_command([path, "--version"]),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if version.returncode:
            result.update(
                status="unusable",
                reason="Bubblewrap could not run: " + version.stderr.strip(),
            )
            return result
        result["version"] = version.stdout.strip()
        probe = subprocess.run(
            host_command(
                [
                    path,
                    *BWRAP_NAMESPACE_OPTIONS,
                    "--ro-bind",
                    "/",
                    "/",
                    "--proc",
                    "/proc",
                    "--dev",
                    "/dev",
                    "true",
                ]
            ),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if probe.returncode:
            error = probe.stderr.strip()
            lowered = error.lower()
            namespace_denied = any(
                text in lowered
                for text in (
                    "operation not permitted",
                    "no permissions to create new namespace",
                    "setting up uid map",
                )
            ) or (
                "permission denied" in lowered
                and any(text in lowered for text in ("namespace", "uid map", "gid map"))
            )
            result.update(
                status="unusable",
                reason=(
                    "The system blocks unprivileged user namespaces. Installing bubblewrap does not fix this. "
                    + error
                    if namespace_denied
                    else "Bubblewrap could not start a sandbox: " + error
                ),
            )
            return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.update(status="unusable", reason=str(exc))
        return result
    result["status"] = "ready"
    return result


# D-Bus and accessibility bus helpers that a game or a library can start in
# the sandbox. They never exit on their own, so they must not keep the game
# counted as running. /proc comm holds 15 characters, so long names are cut.
_BUS_HELPER_PATTERN = "|".join(
    (
        "dbus-daemon",
        "dbus-launch",
        "dbus-broker",
        "dbus-broker-lau",
        "at-spi-bus-laun",
        "at-spi2-registr",
    )
)

# bwrap's namespace init (PID 1) and this shell remain after the launched
# command exits, and bus helpers are ignored. Count before starting sleep, so
# the waiting helper itself cannot keep us alive. bwrap's init waits for every
# child, so a leftover helper would keep the namespace alive: before exiting,
# kill every other process in this PID namespace (`kill -1` cannot reach
# outside it, and it skips this shell and PID 1).
SUPERVISOR = (
    '"$@"; result=$?; while :; do n=0; '
    "for p in /proc/[0-9]*; do "
    'pid=${p#/proc/}; case "$pid" in 1|$$) continue;; esac; '
    '{ read -r stat < "$p/stat"; } 2>/dev/null || continue; '
    'comm=${stat#*[(]}; comm=${comm%) *}; '
    f'case "$comm" in {_BUS_HELPER_PATTERN}) continue;; esac; '
    "n=$((n+1)); done; "
    '[ "$n" -eq 0 ] && { kill -KILL -1 2>/dev/null; exit "$result"; }; '
    "sleep 1; done"
)


# Wine's own background processes. They stay while Cheat Engine keeps the
# wineserver busy, so they must not count as the game still running.
_WINE_SERVICE_PATTERN = "|".join(
    (
        "wineserver",
        "wine-preloader",
        # /proc comm holds 15 characters, so "wine64-preloader" reads as this.
        "wine64-preloade",
        "services.exe",
        "winedevice.exe",
        "plugplay.exe",
        "explorer.exe",
        "rpcss.exe",
        "svchost.exe",
        "conhost.exe",
        "wineboot.exe",
        "start.exe",
        "tabtip.exe",
        "rundll32.exe",
    )
)


def cheat_engine_wrapper(game_command: list[str], ce_command: list[str]) -> list[str]:
    """Run the game with Cheat Engine attached, inside the bwrap supervisor.

    Cheat Engine starts in its own process group. Once the game and anything
    it spawned are gone (only bwrap's init, the supervisor, this shell and
    Wine's background services are left besides that group), the group is stopped so a Cheat Engine window
    left open does not keep the game counted as running.
    """
    ce = shlex.join(ce_command)
    game = shlex.join(game_command)
    script = (
        "ce=; "
        "if command -v setsid >/dev/null 2>&1; then "
        f"setsid sh -c 'sleep 5; exec \"$@\"' sh {ce} & ce=$!; "
        f"else (sleep 5; {ce}) & fi; "
        f"{game}; result=$?; "
        '[ -n "$ce" ] && while :; do n=0; '
        "for p in /proc/[0-9]*; do "
        'pid=${p#/proc/}; case "$pid" in 1|$$|$PPID) continue;; esac; '
        '{ read -r stat < "$p/stat"; } 2>/dev/null || continue; '
        'comm=${stat#*[(]}; comm=${comm%) *}; '
        f'case "$comm" in {_WINE_SERVICE_PATTERN}) continue;; esac; '
        'set -- ${stat##*) }; [ "$3" = "$ce" ] || n=$((n+1)); done; '
        '[ "$n" -eq 0 ] && { kill -TERM -- "-$ce" 2>/dev/null; break; }; '
        "sleep 1; done; "
        'exit "$result"'
    )
    return ["sh", "-c", script]


# Runs inside gamescope, just before bwrap: gamescope picks its display number
# and Xauthority file only when it starts, so their binds are added here.
# Arguments: <index> <full|x11> <bwrap argv...>. The binds go in front of the
# argv element at <index>. "full" also binds the Xauthority file and
# gamescope's Wayland socket; "x11" (the network-only wrapper, which already
# sees the host filesystem) binds only the X socket.
_GAMESCOPE_BINDS = (
    '[ -n "$d" ] && set -- "$@" --ro-bind-try "/tmp/.X11-unix/X$d" "/tmp/.X11-unix/X$d"; '
    'if [ "$mode" = full ]; then '
    '[ -n "${XAUTHORITY:-}" ] && set -- "$@" --ro-bind-try "$XAUTHORITY" "$XAUTHORITY"; '
    '[ -n "$w" ] && set -- "$@" --ro-bind-try "$w" "$w"; '
    "fi; "
)
GAMESCOPE_BIND_SHIM = (
    'i=$1; mode=$2; shift 2; c=$#; n=0; '
    'd=${DISPLAY:-}; d=${d##*:}; d=${d%%.*}; case "$d" in ""|*[!0-9]*) d=;; esac; '
    'w=${GAMESCOPE_WAYLAND_DISPLAY:-}; '
    'case "$w" in ""|/*) ;; *) if [ -n "${XDG_RUNTIME_DIR:-}" ]; then w=$XDG_RUNTIME_DIR/$w; else w=; fi;; esac; '
    # Rotate the arguments once, adding the binds before element <index>.
    'while [ "$n" -lt "$c" ]; do '
    f'if [ "$n" -eq "$i" ]; then {_GAMESCOPE_BINDS}fi; '
    'set -- "$@" "$1"; shift; n=$((n+1)); done; '
    f'if [ "$i" -ge "$c" ]; then {_GAMESCOPE_BINDS}fi; '
    'exec "$@"'
)


def gamescope_bind_wrapper(bwrap_argv: list[str], index: int, mode: str) -> list[str]:
    return ["sh", "-c", GAMESCOPE_BIND_SHIM, "sh", str(index), mode, *bwrap_argv]


def _x11_socket_binds(x11_sockets: tuple[str, ...]) -> list[str]:
    argv: list[str] = []
    for name in x11_sockets:
        path = f"/tmp/.X11-unix/{name}"
        argv += ["--ro-bind-try", path, path]
    return argv


# (source, destination) pairs bound read-only when MangoHud is in effect.
MangoHudMounts = tuple[tuple[str, str], ...]

# MangoHud aborts without /sys/class/drm and reads hwmon and power_supply for
# temperature and battery data. Their entries link into /sys/devices.
MANGOHUD_SYS_CLASSES = ("/sys/class/drm", "/sys/class/hwmon", "/sys/class/power_supply")


def mangohud_mounts(environment: Mapping[str, str], home: str) -> MangoHudMounts:
    """Host paths MangoHud needs in the sandbox, as read-only bind pairs.

    The user's configuration folder goes to the sandbox's `~/.config/MangoHud`,
    where MangoHud looks under the sandbox's XDG_CONFIG_HOME. Files named by
    MANGOHUD_CONFIGFILE and MANGOHUD_PRESETSFILE keep their own paths. Real
    paths are the bind sources and must not expose HOME or wLib's data.
    """
    if is_flatpak():
        # Inside Flatpak XDG_CONFIG_HOME is the app's own folder.
        config_home = environment.get("HOST_XDG_CONFIG_HOME", "")
    else:
        config_home = environment.get("XDG_CONFIG_HOME", "")
    if not os.path.isabs(config_home):
        config_home = os.path.join(home, ".config")
    pairs: list[tuple[str, str]] = [(path, path) for path in MANGOHUD_SYS_CLASSES]
    pairs.append(
        (
            os.path.realpath(os.path.join(config_home, "MangoHud")),
            os.path.join(home, ".config", "MangoHud"),
        )
    )
    for name in ("MANGOHUD_CONFIGFILE", "MANGOHUD_PRESETSFILE"):
        value = environment.get(name, "")
        if os.path.isabs(value):
            pairs.append((os.path.realpath(value), value))
    check_readonly_mounts(tuple(source for source, _ in pairs), home)
    return tuple(pairs)


def build_bwrap_argv(
    command: list[str],
    *,
    game_dir: str,
    home: str,
    sandbox_home: str,
    cwd: str,
    prefix: str = "",
    readonly_paths: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
    nixos: bool = False,
    system_links: Mapping[str, str] | None = None,
    bwrap: str = "bwrap",
    umu_data: str = "",
    umu_runtime: str = "",
    umu_var: str = "",
    network: bool = False,
    writable_paths: tuple[str, ...] = (),
    x11_sockets: tuple[str, ...] = (),
    gamescope: bool = False,
    mangohud: MangoHudMounts | None = None,
) -> list[str]:
    """Pure argv builder; optional host paths are handled by bubblewrap's try flags.

    `mangohud` holds the read-only (source, destination) pairs from
    `mangohud_mounts()`; pass it only when MangoHud is in effect for the launch.

    With `gamescope=True` the result runs inside gamescope: the host X sockets,
    host Wayland socket and host Xauthority are left out, and the
    `GAMESCOPE_BIND_SHIM` adds gamescope's own display when bwrap starts.
    """
    env = environment or {}
    check_readonly_mounts(readonly_paths, home)
    check_writable_mounts(writable_paths, home)
    argv = [bwrap, *BWRAP_NAMESPACE_OPTIONS]
    if network:
        argv.append("--unshare-net")
    system = ["/usr", "/bin", "/sbin", "/opt"]
    system += ["/nix"] if nixos else ["/lib", "/lib64", "/lib32"]
    system += [
        "/etc/ld.so.cache",
        "/etc/ld.so.conf",
        "/etc/ld.so.conf.d",
        "/etc/fonts",
        "/etc/vulkan",
        "/etc/glvnd",
        "/etc/alternatives",
        "/etc/nsswitch.conf",
        "/etc/passwd",
        "/etc/group",
        "/etc/hosts",
        "/etc/resolv.conf",
        "/etc/localtime",
        "/etc/hostname",
        "/etc/machine-id",
        "/etc/ssl",
        # Arch's /etc/ssl/certs entries are symlinks into /etc/ca-certificates.
        "/etc/ca-certificates",
        "/etc/pki",
        "/etc/drirc",
        "/etc/alsa",
        "/etc/asound.conf",
        "/etc/pulse",
        "/sys/dev/char",
        "/sys/devices",
        "/sys/bus/pci",
    ]
    for path in system:
        # Merged-/usr hosts link /lib to usr/lib; a bind would break the
        # relative ../lib32 target of 32-bit loaders.
        if nixos or path not in ("/bin", "/sbin", "/lib", "/lib64", "/lib32"):
            argv += ["--ro-bind-try", path, path]
            continue
        # system_links is the host's view when running inside Flatpak.
        if system_links is not None:
            target = system_links.get(path)
        else:
            target = os.readlink(path) if os.path.islink(path) else None
        if target:
            argv += ["--symlink", target, path]
        else:
            argv += ["--ro-bind-try", path, path]
    # The sandbox user owns this /tmp/.X11-unix, so a nested X server
    # (gamescope, Xvfb) can create its display here. Only the host's display
    # sockets are bound in; the host folder belongs to root, which shows as
    # nobody in the user namespace.
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/.X11-unix"]
    argv += ["--tmpfs", "/run"]
    if nixos:
        argv += ["--ro-bind-try", "/run/current-system", "/run/current-system"]
    for path in (
        "/dev/ntsync",
        "/dev/dri",
        "/dev/snd",
        "/dev/shm",
        "/dev/nvidia0",
        "/dev/nvidia1",
        "/dev/nvidiactl",
        "/dev/nvidia-modeset",
        "/dev/nvidia-uvm",
        "/dev/nvidia-uvm-tools",
    ):
        argv += ["--dev-bind-try", path, path]
    argv += ["--bind", sandbox_home, home, "--setenv", "HOME", home]
    for key, suffix in (
        ("XDG_CONFIG_HOME", "/.config"),
        ("XDG_CACHE_HOME", "/.cache"),
        ("XDG_DATA_HOME", "/.local/share"),
        ("XDG_STATE_HOME", "/.local/state"),
    ):
        argv += ["--setenv", key, home + suffix]
    for path in readonly_paths:
        argv += ["--ro-bind", path, path]
    # After the sandbox-home bind, so the configuration folder lands in the
    # game's own ~/.config. Every pair is optional on the host.
    for source, destination in mangohud or ():
        argv += ["--ro-bind-try", source, destination]
    if umu_data and umu_runtime and umu_var:
        check_readonly_mounts((umu_data,), home)
        runtime_root = os.path.realpath(umu_data)
        for writable in (game_dir, prefix):
            if writable:
                candidate = os.path.realpath(writable)
                if os.path.commonpath((candidate, runtime_root)) in (
                    candidate,
                    runtime_root,
                ):
                    raise ValueError(
                        "The game folder or prefix overlaps the shared umu runtime."
                    )
        argv += [
            "--ro-bind",
            umu_data,
            umu_data,
            "--setenv",
            "XDG_DATA_HOME",
            os.path.dirname(umu_data),
        ]
        argv += ["--bind", umu_var, os.path.join(umu_runtime, "var")]
        for ref in glob.glob(
            os.path.join(umu_runtime, "*_platform_*", "files", ".ref")
        ):
            argv += ["--bind", ref, ref]
    # Writable mounts follow runtime mounts, so game files remain writable even
    # if the executable and a runtime happen to share a parent directory.
    argv += ["--bind", game_dir, game_dir]
    if prefix:
        argv += ["--bind", prefix, prefix]
    for writable in writable_paths:
        if writable:
            argv += ["--bind", writable, writable]
    # After every mount above (/tmp, /run), so these binds stay visible.
    display_index = len(argv)
    if not gamescope:
        argv += _x11_socket_binds(x11_sockets)
    runtime = env.get("XDG_RUNTIME_DIR", "")
    if runtime:
        for name in (
            *(() if gamescope else ((env.get("WAYLAND_DISPLAY") or "wayland-0"),)),
            "pipewire-0",
            "pulse",
        ):
            path = name if name.startswith("/") else os.path.join(runtime, name)
            argv += ["--ro-bind-try", path, path]
    if not gamescope:
        authority = env.get("XAUTHORITY") or os.path.join(home, ".Xauthority")
        argv += ["--ro-bind-try", authority, authority]
    argv += [
        # libdbus starts its own bus when no address is set, and that bus never
        # exits. An address that cannot connect gives an error instead. The
        # sandbox's /run is a new tmpfs, so the path never exists.
        "--setenv",
        "DBUS_SESSION_BUS_ADDRESS",
        "unix:path=/run/wlib-no-session-bus",
        "--setenv",
        "NO_AT_BRIDGE",
        "1",
        "--unsetenv",
        "DBUS_SYSTEM_BUS_ADDRESS",
        "--chdir",
        cwd,
        "--",
        "sh",
        "-c",
        SUPERVISOR,
        "sh",
        *command,
    ]
    if gamescope:
        return gamescope_bind_wrapper(argv, display_index, "full")
    return argv


def sandbox_folder(game_id: int) -> str:
    from core.host_platform import get_data_dir

    if isinstance(game_id, bool) or game_id <= 0:
        raise ValueError("A sandbox needs a valid library game ID.")
    return os.path.join(get_data_dir(), "sandbox", str(game_id))


REMOVED_SANDBOX_KEEP_SECONDS = 30 * 24 * 60 * 60


def flatpak_player_argv(
    player_command: list[str],
    *,
    game_dir: str,
    profile: str,
    network_blocked: bool,
    environment: Mapping[str, str] | None = None,
    sandbox: bool = True,
) -> list[str]:
    """Run the built-in player in a new Flatpak instance through the portal.

    With `sandbox`, the sub-sandbox sees only the game folder and the player's
    storage folder (both read-write), the display, sound and GPU, and no other
    user files. Without it, the instance keeps the app's permissions and is
    used only to remove network access.
    """
    argv = ["flatpak-spawn", *(["--sandbox"] if sandbox else []), "--watch-bus"]
    if sandbox:
        argv += [
            f"--sandbox-expose-path={game_dir}",
            f"--sandbox-expose-path={profile}",
            "--sandbox-flag=share-display",
            "--sandbox-flag=share-sound",
            "--sandbox-flag=share-gpu",
        ]
    if network_blocked:
        argv.append("--no-network")
    for key in sorted(PLAYER_ENV_NAMES):
        value = (environment or {}).get(key)
        if value:
            argv.append(f"--env={key}={value}")
    return [*argv, "--", *player_command]


def web_player_folder(game_id: int) -> str:
    from core.host_platform import get_data_dir

    if isinstance(game_id, bool) or game_id <= 0:
        raise ValueError("The web player needs a valid library game ID.")
    return os.path.join(get_data_dir(), "web-player", str(game_id))


def _retire_folder(folder: str, game_id: int) -> str | None:
    from datetime import datetime

    if not os.path.isdir(folder) or os.path.islink(folder):
        return None
    removed_root = os.path.join(os.path.dirname(folder), "removed")
    os.makedirs(removed_root, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target = os.path.join(removed_root, f"{game_id}_{stamp}")
    os.replace(folder, target)
    # The rename keeps the old mtime; the 30 days count from the removal.
    os.utime(target)
    return target


def retire_web_player_folder(game_id: int) -> str | None:
    """Move a removed game's built-in player storage to web-player/removed/."""
    return _retire_folder(web_player_folder(game_id), game_id)


def retire_sandbox_folder(game_id: int) -> str | None:
    """Move a removed game's sandbox aside; return the new path, or None.

    A later game that gets the same ID must not inherit its home or Wine
    prefix. The folder can hold saves, so it stays in `sandbox/removed/` for
    30 days before `prune_removed_sandboxes` deletes it.
    """
    return _retire_folder(sandbox_folder(game_id), game_id)


def prune_removed_sandboxes(now: float | None = None) -> int:
    """Delete sandboxes and player storage of removed games older than 30 days; return the count."""
    import shutil
    import time

    from core.host_platform import get_data_dir

    cutoff = (time.time() if now is None else now) - REMOVED_SANDBOX_KEEP_SECONDS
    removed = 0
    for parent in ("sandbox", "web-player"):
        root = os.path.join(get_data_dir(), parent, "removed")
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_dir() or entry.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 0 if os.path.exists(entry.path) else 1
    return removed


def resolve_game_folder(exe_path: str, launch_mode: str = "auto") -> str:
    folder = os.path.dirname(os.path.abspath(exe_path))
    if launch_mode == "rpgmaker_linux" and os.path.basename(folder).lower() == "www":
        folder = os.path.dirname(folder)
    return folder


def sandbox_wine_prefix(game_id: int) -> str:
    """Wine/Proton prefix of a sandboxed game that has no custom prefix.

    The sandbox mounts the prefix read-write, so a shared one would let a
    sandboxed game plant DLLs or registry entries that later run unsandboxed
    in other games. Each sandboxed game gets its own prefix instead.
    """
    return os.path.join(sandbox_folder(game_id), "prefix")


def sandboxed_prefix_override(
    custom_prefix: str, sandboxed: bool, game_id: int | None
) -> str:
    """The prefix a game uses instead of the shared one, or "" for none.

    A custom prefix always wins. Otherwise a sandboxed Linux game uses its
    own prefix; Windows sandboxing (Sandboxie) does not run Wine.
    """
    if custom_prefix.strip():
        return custom_prefix
    if not sandboxed or game_id is None or os.name == "nt":
        return ""
    return sandbox_wine_prefix(game_id)


def prepare_sandbox_home(game_id: int) -> str:
    home = os.path.join(sandbox_folder(game_id), "home")
    os.makedirs(home, exist_ok=True)
    return home


def normalize_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    path = os.path.realpath(os.path.abspath(os.path.expanduser(prefix)))
    return os.path.dirname(path) if os.path.basename(path).lower() == "pfx" else path


def _sandboxie_call(tool: str, args: list[str]) -> str:
    result = subprocess.run(
        [tool, *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=15,
        check=False,
        creationflags=no_console_window(),
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"Sandboxie command failed ({result.returncode}): {args[0]}"
        )
    return result.stdout.strip()


def build_network_bwrap_argv(
    command: list[str],
    *,
    cwd: str,
    bwrap: str = "bwrap",
    x11_sockets: tuple[str, ...] = (),
    gamescope: bool = False,
) -> list[str]:
    """Net-only wrapper: the host filesystem and processes stay as they are.

    Only /tmp/.X11-unix is private: a tmpfs that the namespace user owns,
    with the host's display sockets bound in, so a nested X server can start.
    With `gamescope=True` it holds only gamescope's socket, bound when bwrap
    starts inside gamescope.
    """
    head = [
        bwrap,
        "--unshare-user",
        "--unshare-net",
        "--dev-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp/.X11-unix",
    ]
    tail = ["--chdir", cwd, "--", *command]
    if gamescope:
        return gamescope_bind_wrapper([*head, *tail], len(head), "x11")
    return [*head, *_x11_socket_binds(x11_sockets), *tail]


def configure_sandboxie_box(
    start: str,
    game_id: int,
    game_dir: str,
    block_network: bool = False,
    extra_write_paths: tuple[str, ...] = (),
) -> str:
    box = f"wLib_{game_id}"
    root = sandbox_folder(game_id)
    os.makedirs(root, exist_ok=True)
    ini = ntpath.join(ntpath.dirname(start), "SbieIni.exe")
    for key, value in [
        ("Enabled", "y"),
        ("FileRootPath", root),
        ("AutoDelete", "n"),
        # The box is reused, so an empty value clears an earlier block.
        (
            "ClosedFilePath",
            "!<InternetAccess>,InternetAccessDevices" if block_network else "",
        ),
        # Otherwise Sandboxie opens a message window (SBIE1307) each time
        # the game tries to go online.
        ("NotifyInternetAccessDenied", "n" if block_network else ""),
        # Sandboxie appends '*': the separator prevents matching sibling
        # folders whose names merely start with the game's directory name.
        ("OpenFilePath", ntpath.normpath(game_dir).rstrip("\\/") + "\\"),
    ]:
        _ = _sandboxie_call(ini, ["set", box, key, value])
    # Pass-through write paths (the built-in player's storage folder). "set"
    # above reset the list, so a path removed since the last launch is dropped.
    for extra in extra_write_paths:
        if extra:
            _ = _sandboxie_call(
                ini, ["append", box, "OpenFilePath", ntpath.normpath(extra).rstrip("\\/") + "\\"]
            )
    _ = _sandboxie_call(start, ["/reload"])
    return box


def list_box_pids(start: str, box: str) -> list[int]:
    output = _sandboxie_call(start, [f"/box:{box}", "/listpids"])
    try:
        numbers = [int(line.strip()) for line in output.splitlines() if line.strip()]
    except ValueError as exc:
        raise RuntimeError("Sandboxie returned an invalid process list.") from exc
    if not numbers or numbers[0] < 0 or len(numbers) != numbers[0] + 1:
        raise RuntimeError("Sandboxie returned an incomplete process list.")
    return numbers[1:]


def terminate_box(start: str, box: str) -> None:
    _ = _sandboxie_call(start, [f"/box:{box}", "/terminate"])


PACKAGE_INSTALLS = {
    "pacman": ["-S", "--noconfirm", "bubblewrap"],
    "apt": ["install", "-y", "bubblewrap"],
    "dnf": ["install", "-y", "bubblewrap"],
    "zypper": ["--non-interactive", "install", "bubblewrap"],
    "xbps-install": ["-y", "bubblewrap"],
}


def get_sandbox_install_info() -> dict[str, object]:
    host = get_host_name()
    info: dict[str, object] = {
        "platform": host,
        "tool": "",
        "command": "",
        "can_install": False,
        "pkexec_available": False,
        "download_url": "",
        "package_manager": "",
    }
    if host == "windows":
        info.update(
            tool="sandboxie",
            can_install=True,
            download_url="https://sandboxie-plus.com/downloads/",
        )
    elif host == "linux":
        info.update(
            tool="bwrap", download_url="https://github.com/containers/bubblewrap"
        )
        pkexec = bool(host_which("pkexec"))
        info["pkexec_available"] = pkexec
        for manager in PACKAGE_INSTALLS:
            if host_which(manager):
                # Present a familiar interactive command; the background action
                # uses the same package with the manager's unattended flag.
                args = (
                    "-S"
                    if manager == "pacman"
                    else "install"
                    if manager in ("apt", "dnf", "zypper")
                    else "-S"
                )
                info.update(
                    command=f"sudo {manager} {args} bubblewrap",
                    package_manager=manager,
                    can_install=pkexec,
                )
                if manager == "xbps-install":
                    info["command"] = "sudo xbps-install bubblewrap"
                break
    return info


def _install_command(command: list[str]) -> None:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=900,
        check=False,
        creationflags=no_console_window(),
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or "Installation failed or authentication was cancelled."
        )


def install_sandbox_tool(tool: str) -> None:
    host = get_host_name()
    if (tool, host) not in (("bwrap", "linux"), ("sandboxie", "windows")):
        raise ValueError("unsupported_platform")
    if tool == "bwrap":
        info = get_sandbox_install_info()
        manager = str(info["package_manager"])
        pkexec = host_which("pkexec")
        executable = host_which(manager) if manager else None
        if not pkexec or not executable:
            raise RuntimeError(
                "Install bubblewrap with your system package manager, then choose Recheck."
            )
        _install_command(host_command([pkexec, executable, *PACKAGE_INSTALLS[manager]]))
        return
    winget = host_which("winget")
    if winget:
        _install_command(
            [
                winget,
                "install",
                "--id",
                "Sandboxie.Plus",
                "-e",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
        )
        return
    _download_sandboxie_installer()


def _download_sandboxie_installer() -> None:
    import platform
    import tempfile
    import urllib.request
    from urllib.parse import urlparse

    request = urllib.request.Request(
        "https://api.github.com/repos/sandboxie-plus/Sandboxie/releases/latest",
        headers={"User-Agent": "wLib"},
    )
    with cast(HTTPResponse, urllib.request.urlopen(request, timeout=30)) as response:
        release = _json_object(response.read())
    architecture = (
        "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    )
    raw_assets = release.get("assets", [])
    if not isinstance(raw_assets, list):
        raise RuntimeError("The release returned an invalid asset list.")
    assets = [
        cast(dict[str, object], item)
        for item in cast(list[object], raw_assets)
        if isinstance(item, dict)
    ]
    asset = next(
        (
            item
            for item in assets
            if str(item.get("name", ""))
            .lower()
            .startswith(f"sandboxie-plus-{architecture}-")
            and str(item.get("name", "")).lower().endswith(".exe")
        ),
        None,
    )
    if not asset:
        raise RuntimeError(
            "The official release has no installer for this architecture."
        )
    url = str(asset.get("browser_download_url", ""))
    if (
        not url.startswith(
            "https://github.com/sandboxie-plus/Sandboxie/releases/download/"
        )
        or urlparse(url).scheme != "https"
    ):
        raise RuntimeError("The release returned an unexpected installer URL.")
    # The temporary directory is removed on every exit, including a bad signature.
    with tempfile.TemporaryDirectory(prefix="wlib-sandboxie-") as directory:
        installer = os.path.join(directory, "Sandboxie-Plus.exe")
        with (
            cast(HTTPResponse, urllib.request.urlopen(url, timeout=60)) as response,
            open(installer, "wb") as output,
        ):
            import shutil

            shutil.copyfileobj(response, output)
        escaped = installer.replace("'", "''")
        # Verify the OS trust chain and a publisher identity observed on the
        # official release. A different publisher fails closed for manual review.
        verification = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"$s=Get-AuthenticodeSignature -LiteralPath '{escaped}'; @{{Status=$s.Status.ToString();Publisher=$s.SignerCertificate.Subject}} | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False,
            creationflags=no_console_window(),
        )
        try:
            signature = _json_object(verification.stdout)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Could not verify the installer signature.") from exc
        publisher = str(signature.get("Publisher", ""))
        if (
            verification.returncode
            or signature.get("Status") != "Valid"
            or not publisher.startswith("CN=NOVASYNC LABS PTE. LTD.,")
        ):
            raise RuntimeError(
                "The installer signature is invalid or has an unexpected publisher. The download was deleted."
            )
        _install_command(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"try {{ $p=Start-Process -FilePath '{escaped}' -ArgumentList '/VERYSILENT' -Verb RunAs -PassThru -Wait; exit $p.ExitCode }} catch {{ Write-Error 'Installation cancelled or elevation failed'; exit 1 }}",
            ]
        )


def _json_object(payload: str | bytes) -> dict[str, object]:
    import json

    value = cast(object, json.loads(payload))
    if not isinstance(value, dict):
        raise RuntimeError("Expected a JSON object.")
    return cast(dict[str, object], value)


def _exposes_path(path: str, root: str) -> bool:
    """True when mounting `path` would reveal `root` or one of its ancestors."""
    target = os.path.realpath(os.path.abspath(path))
    real_root = os.path.realpath(os.path.abspath(root))
    return target == real_root or real_root.startswith(target.rstrip(os.sep) + os.sep)


def exposes_home(path: str, home: str) -> bool:
    """True when mounting `path` would reveal HOME itself or one of its ancestors."""
    return _exposes_path(path, home)


def _protected_roots(home: str) -> tuple[str, ...]:
    """Folders a game mount must never contain.

    The library database and settings (including sync credentials) live in the
    data dir, and every other game's sandbox home lives below `sandbox/`, so an
    ancestor of either is as unsafe as an ancestor of HOME.
    """
    data_dir = os.path.realpath(os.path.abspath(get_data_dir()))
    return (
        os.path.realpath(os.path.abspath(home)),
        data_dir,
        os.path.join(data_dir, "sandbox"),
    )


def _exposes_protected_root(path: str, home: str) -> bool:
    return any(_exposes_path(path, root) for root in _protected_roots(home))


def check_writable_mounts(paths: tuple[str, ...], home: str) -> None:
    for path in paths:
        if path and _exposes_protected_root(path, home):
            raise ValueError(
                f"Cannot sandbox a game whose folder or Wine prefix is {path}: "
                + "mounting it would expose your whole home folder or wLib's data folder. "
                + "Move the game into its own folder, or turn sandboxing off for it."
            )


def check_readonly_mounts(paths: tuple[str, ...], home: str) -> None:
    """Refuse read-only mounts that would still reveal HOME or wLib's data.

    A runtime folder can resolve to HOME when the configured Wine/Proton
    executable or RPGMaker runner sits directly in the home folder. Mounting it
    read-only is not a safe substitute: the game could read every file there.
    The wLib data dir is protected for the same reason: it holds the library
    database, sync settings and every game's sandbox storage.
    """
    for path in paths:
        if path and _exposes_protected_root(path, home):
            raise ValueError(
                f"Cannot sandbox a launch whose runtime folder or MangoHud configuration is {path}: "
                + "mounting it would expose your whole home folder or wLib's data folder. "
                + "Move the runtime or MangoHud configuration into its own folder, "
                + "or turn sandboxing off for this game."
            )


def runtime_folder(executable: str, home: str | None = None) -> str:
    """Include Wine's sibling libraries and the RPGMaker runner's package files."""
    folder = os.path.dirname(os.path.realpath(executable))
    if os.path.basename(folder).lower() not in ("bin", "packagefiles"):
        return folder
    parent = os.path.dirname(folder)
    # ~/.local/bin/wine must not expose ~/.local (wLib's data and every other
    # game's sandbox) or shadow the sandbox's own ~/.local/share.
    user_home = os.path.realpath(home or os.path.expanduser("~"))
    if exposes_home(parent, user_home) or parent == os.path.join(user_home, ".local"):
        return folder
    return parent
