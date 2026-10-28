# SPDX-License-Identifier: GPL-3.0-or-later
"""Host sandbox detection, command construction and per-game storage."""

from __future__ import annotations

import ntpath
import os
import subprocess
from threading import Lock
from typing import Literal, TypedDict, cast
from http.client import HTTPResponse

from core.database import get_setting
from core.host_platform import get_host_name, host_command, host_which


BWRAP_NAMESPACE_OPTIONS = (
    "--unshare-user",
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-uts",
    "--unshare-cgroup-try",
    "--die-with-parent",
)


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


# The shell and bwrap's namespace init remain after the launched command exits.
# Count before starting sleep, so the waiting helper itself cannot keep us alive.
SUPERVISOR = (
    '"$@"; result=$?; while :; do n=0; '
    "for p in /proc/[0-9]*; do n=$((n+1)); done; "
    '[ "$n" -le 2 ] && exit "$result"; sleep 1; done'
)


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
    bwrap: str = "bwrap",
) -> list[str]:
    """Pure argv builder; optional host paths are handled by bubblewrap's try flags."""
    env = environment or {}
    check_readonly_mounts(readonly_paths, home)
    argv = [bwrap, *BWRAP_NAMESPACE_OPTIONS]
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
        argv += ["--ro-bind-try", path, path]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run"]
    if nixos:
        argv += ["--ro-bind-try", "/run/current-system", "/run/current-system"]
    for path in (
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
    # Writable mounts follow runtime mounts, so game files remain writable even
    # if the executable and a runtime happen to share a parent directory.
    argv += ["--bind", game_dir, game_dir]
    if prefix:
        argv += ["--bind", prefix, prefix]
    argv += ["--ro-bind-try", "/tmp/.X11-unix", "/tmp/.X11-unix"]
    runtime = env.get("XDG_RUNTIME_DIR", "")
    if runtime:
        for name in (
            (env.get("WAYLAND_DISPLAY") or "wayland-0"),
            "pipewire-0",
            "pulse",
        ):
            path = name if name.startswith("/") else os.path.join(runtime, name)
            argv += ["--ro-bind-try", path, path]
    authority = env.get("XAUTHORITY") or os.path.join(home, ".Xauthority")
    argv += [
        "--ro-bind-try",
        authority,
        authority,
        "--unsetenv",
        "DBUS_SESSION_BUS_ADDRESS",
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
    return argv


def sandbox_folder(game_id: int) -> str:
    from core.host_platform import get_data_dir

    if isinstance(game_id, bool) or game_id <= 0:
        raise ValueError("A sandbox needs a valid library game ID.")
    return os.path.join(get_data_dir(), "sandbox", str(game_id))


def resolve_game_folder(exe_path: str, launch_mode: str = "auto") -> str:
    folder = os.path.dirname(os.path.abspath(exe_path))
    if launch_mode == "rpgmaker_linux" and os.path.basename(folder).lower() == "www":
        folder = os.path.dirname(folder)
    return folder


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
        [tool, *args], capture_output=True, text=True, timeout=15, check=False
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"Sandboxie command failed ({result.returncode}): {args[0]}"
        )
    return result.stdout.strip()


def configure_sandboxie_box(start: str, game_id: int, game_dir: str) -> str:
    box = f"wLib_{game_id}"
    root = sandbox_folder(game_id)
    os.makedirs(root, exist_ok=True)
    ini = ntpath.join(ntpath.dirname(start), "SbieIni.exe")
    for key, value in [
        ("Enabled", "y"),
        ("FileRootPath", root),
        ("AutoDelete", "n"),
        # Sandboxie appends '*': the separator prevents matching sibling
        # folders whose names merely start with the game's directory name.
        ("OpenFilePath", ntpath.normpath(game_dir).rstrip("\\/") + "\\"),
    ]:
        _ = _sandboxie_call(ini, ["set", box, key, value])
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
        stdin=subprocess.DEVNULL,
        timeout=900,
        check=False,
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
            timeout=60,
            check=False,
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


def exposes_home(path: str, home: str) -> bool:
    """True when mounting `path` would reveal HOME itself or one of its ancestors."""
    target = os.path.realpath(os.path.abspath(path))
    real_home = os.path.realpath(os.path.abspath(home))
    return target == real_home or real_home.startswith(target.rstrip(os.sep) + os.sep)


def check_writable_mounts(paths: tuple[str, ...], home: str) -> None:
    for path in paths:
        if path and exposes_home(path, home):
            raise ValueError(
                f"Cannot sandbox a game whose folder or Wine prefix is {path}: "
                + "mounting it would expose your whole home folder. "
                + "Move the game into its own folder, or turn sandboxing off for it."
            )


def check_readonly_mounts(paths: tuple[str, ...], home: str) -> None:
    """Refuse read-only mounts that would still reveal HOME or an ancestor.

    A runtime folder can resolve to HOME when the configured Wine/Proton
    executable or RPGMaker runner sits directly in the home folder. Mounting it
    read-only is not a safe substitute: the game could read every file there.
    """
    for path in paths:
        if path and exposes_home(path, home):
            raise ValueError(
                f"Cannot sandbox a launch whose runtime folder is {path}: "
                + "mounting it would expose your whole home folder. "
                + "Move the runtime into its own folder, or turn sandboxing off for this game."
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
