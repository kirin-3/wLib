# SPDX-License-Identifier: GPL-3.0-or-later
import ntpath
import os
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
import atexit
import signal
import threading
import time
from urllib.parse import quote
from collections.abc import Callable
from _thread import LockType
from dataclasses import dataclass
from typing import IO, Protocol, TypedDict, cast, final

from .database import (
    RPGMAKER_LINUX_RUNNER_SETTING,
    get_setting,
    normalize_launch_mode,
    CompanionTool,
    split_companion_arguments,
)
from . import game_sandbox
from .host_platform import (
    get_cheat_engine_dir,
    get_default_wine_prefix,
    host_command,
    host_which,
    is_windows,
    open_windows_system_target,
)


def _host_path_module():
    return ntpath if is_windows() else posixpath


def is_elf(path: str) -> bool:
    try:
        with open(path, "rb") as file:
            return file.read(4) == b"\x7fELF"
    except OSError:
        return False


def is_script(path: str) -> bool:
    try:
        with open(path, "rb") as file:
            return file.read(2) == b"#!"
    except OSError:
        return False


def ensure_executable(path: str) -> bool:
    """Add the exec bit that Windows-made archives drop; True if path is now executable.

    Ren'Py's .sh execs lib/*linux*/<binary>, which lost its bit in the same unzip.
    """
    targets = [path]
    if path.endswith(".sh") or is_script(path):
        lib_dir = os.path.join(os.path.dirname(path), "lib")
        try:
            for name in os.listdir(lib_dir):
                platform_dir = os.path.join(lib_dir, name)
                if "linux" in name and os.path.isdir(platform_dir):
                    targets += [
                        os.path.join(platform_dir, f) for f in os.listdir(platform_dir)
                    ]
        except OSError:
            pass
    for target in targets:
        if target != path and not is_elf(target):
            continue
        try:
            mode = os.stat(target).st_mode
            os.chmod(target, mode | ((mode & 0o444) >> 2))  # x wherever r is set
        except OSError:
            pass
    # chmod "succeeds" without effect on some NTFS/exFAT mounts, so re-check.
    return os.access(path, os.X_OK)


def proton_compat_data_path(prefix: str) -> str:
    """STEAM_COMPAT_DATA_PATH for a configured prefix (Proton's Wine prefix is <it>/pfx).

    A plain Wine prefix (drive_c without pfx) is never reused by Proton, which
    gets an isolated proton_compat subdirectory instead.
    """
    path_module = _host_path_module()
    if os.path.isdir(path_module.join(prefix, "drive_c")) and not os.path.isdir(
        path_module.join(prefix, "pfx")
    ):
        return path_module.join(prefix, "proton_compat")
    return prefix


def _split_windows_command_line(command_line: str) -> list[str]:
    """Split arguments using the quoting rules used by Windows applications."""
    arguments: list[str] = []
    index = 0
    length = len(command_line)

    while True:
        while index < length and command_line[index] in " \t":
            index += 1
        if index >= length:
            return arguments

        argument: list[str] = []
        in_quotes = False
        while index < length:
            character = command_line[index]
            if character in " \t" and not in_quotes:
                break

            if character == "\\":
                slash_start = index
                while index < length and command_line[index] == "\\":
                    index += 1
                slash_count = index - slash_start
                if index >= length or command_line[index] != '"':
                    argument.extend("\\" * slash_count)
                    continue

                argument.extend("\\" * (slash_count // 2))
                if slash_count % 2:
                    argument.append('"')
                    index += 1
                    continue

                if in_quotes and index + 1 < length and command_line[index + 1] == '"':
                    argument.append('"')
                    index += 2
                else:
                    in_quotes = not in_quotes
                    index += 1
                continue

            if character == '"':
                if in_quotes and index + 1 < length and command_line[index + 1] == '"':
                    argument.append('"')
                    index += 2
                else:
                    in_quotes = not in_quotes
                    index += 1
                continue

            argument.append(character)
            index += 1

        if in_quotes:
            raise ValueError("No closing quotation")
        arguments.append("".join(argument))


class RpgmakerLinuxRunnerStatus(TypedDict):
    available: bool
    path: str
    source: str
    configured_path: str
    error: str


class ExitCallback(Protocol):
    def __call__(self, delta: int, is_final: bool = True) -> object: ...


class _ElevatedProcess:
    """Keep the ShellExecuteEx process handle usable by the playtime tracker."""

    def __init__(self, command: list[str], cwd: str):
        import _winapi
        import ctypes
        import weakref
        from ctypes import wintypes

        @final
        class ShellExecuteInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", wintypes.ULONG),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIcon", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        info = ShellExecuteInfo(
            cbSize=ctypes.sizeof(ShellExecuteInfo),
            fMask=0x40 | 0x100 | 0x400,  # NOCLOSEPROCESS | NOASYNC | FLAG_NO_UI
            lpVerb="runas",
            lpFile=command[0],
            lpParameters=subprocess.list2cmdline(command[1:]),
            lpDirectory=cwd,
            nShow=1,  # SW_SHOWNORMAL
        )
        # Windows-only stdlib symbols are absent from Linux type-checking stubs.
        load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
        win_error = cast(Callable[[], OSError], getattr(ctypes, "WinError"))
        shell_execute = load_dll("shell32", use_last_error=True).ShellExecuteExW
        shell_execute.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
        shell_execute.restype = wintypes.BOOL
        if not cast(int, shell_execute(ctypes.byref(info))):
            raise win_error()
        handle = cast(int | None, info.hProcess)
        if not handle:
            raise OSError("Windows did not return a process handle for the game.")

        self._handle: int = handle
        get_process_id = load_dll("kernel32", use_last_error=True).GetProcessId
        get_process_id.argtypes = [wintypes.HANDLE]
        get_process_id.restype = wintypes.DWORD
        self.pid: int = cast(int, get_process_id(handle))
        self.args: list[str] = command
        self.returncode: int | None = None
        # Keep the handle open while any tracking/logging thread is using it.
        close_handle = cast(Callable[[int], None], getattr(_winapi, "CloseHandle"))
        _ = weakref.finalize(self, close_handle, self._handle)

    def wait(self, timeout: float | None = None) -> int:
        import _winapi

        if self.returncode is None:
            wait = cast(
                Callable[[int, int], int], getattr(_winapi, "WaitForSingleObject")
            )
            exit_code = cast(
                Callable[[int], int], getattr(_winapi, "GetExitCodeProcess")
            )
            milliseconds = (
                0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
            )
            result = wait(self._handle, milliseconds)
            if result == 0x102:  # WAIT_TIMEOUT
                raise subprocess.TimeoutExpired(self.args, timeout or 0)
            self.returncode = exit_code(self._handle)
        return self.returncode

    def poll(self) -> int | None:
        try:
            return self.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return None


def without_wine_proton_env(env_vars: dict[str, str]) -> dict[str, str]:
    clean_env = env_vars.copy()
    for var in (
        "WINEPREFIX",
        "WINEDLLOVERRIDES",
        "WINEDEBUG",
        "PROTON_LOG",
        "PROTON_LOG_DIR",
        "STEAM_COMPAT_DATA_PATH",
        "STEAM_COMPAT_CLIENT_INSTALL_PATH",
        "STEAM_COMPAT_INSTALL_PATH",
        "UMU_ID",
    ):
        _ = clean_env.pop(var, None)
    return clean_env


def is_packaged_runtime_library_path(raw_path: str) -> bool:
    path_module = _host_path_module()
    path = raw_path.strip()
    if not path:
        return True

    expanded_path = path_module.expanduser(path)
    if "/.mount_" in path or "/.mount_" in expanded_path:
        return True

    normalized_path = path_module.abspath(expanded_path)
    runtime_dirs: set[str] = set()
    meipass = str(getattr(sys, "_MEIPASS", "") or "").strip()
    if meipass:
        runtime_dirs.add(path_module.abspath(path_module.expanduser(meipass)))

    executable_dir = path_module.dirname(path_module.abspath(sys.executable))
    runtime_dirs.add(path_module.join(executable_dir, "_internal"))
    if normalized_path in runtime_dirs:
        return True

    if path_module.basename(normalized_path) != "_internal":
        return False

    try:
        runtime_files = os.listdir(normalized_path)
    except OSError:
        return False

    return "base_library.zip" in runtime_files or any(
        entry.startswith("libpython") for entry in runtime_files
    )


def without_packaged_runtime_env(env_vars: dict[str, str]) -> dict[str, str]:
    path_module = _host_path_module()
    clean_env = env_vars.copy()
    appimage_context = any(
        var in clean_env
        for var in (
            "APPIMAGE",
            "APPDIR",
            "ARGV0",
            "APPIMAGE_SILENT_INSTALL",
            "OWD",
            "APPIMAGE_EXTRACT_AND_RUN",
            "LD_LIBRARY_PATH_ORIG",
        )
    ) or "/.mount_" in clean_env.get("LD_LIBRARY_PATH", "")

    for var in (
        "APPIMAGE",
        "APPDIR",
        "ARGV0",
        "APPIMAGE_SILENT_INSTALL",
        "OWD",
        "APPIMAGE_EXTRACT_AND_RUN",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONEXECUTABLE",
        "QT_QPA_PLATFORM",
        "QT_QUICK_BACKEND",
        "QSG_RHI_BACKEND",
        "QT_OPENGL",
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "QT_PLUGIN_PATH",
        "QML2_IMPORT_PATH",
        "WLIB_QPA_PLATFORM",
        "PYWEBVIEW_GUI",
    ):
        _ = clean_env.pop(var, None)

    original_library_path = str(clean_env.pop("LD_LIBRARY_PATH_ORIG", "") or "").strip()
    if original_library_path:
        clean_env["LD_LIBRARY_PATH"] = original_library_path
    else:
        library_path = str(clean_env.get("LD_LIBRARY_PATH") or "")
        library_paths = [
            entry
            for entry in library_path.split(path_module.pathsep)
            if entry and not is_packaged_runtime_library_path(entry)
        ]
        if library_paths:
            clean_env["LD_LIBRARY_PATH"] = path_module.pathsep.join(library_paths)
        elif appimage_context or library_path:
            _ = clean_env.pop("LD_LIBRARY_PATH", None)

    if appimage_context and not str(clean_env.get("LD_LIBRARY_PATH") or ""):
        _ = clean_env.pop("LD_LIBRARY_PATH", None)
    return clean_env


@dataclass
class WineContext:
    command: list[str]
    env: dict[str, str]


@dataclass
class ToolProcess:
    proc: subprocess.Popen[bytes]
    kind: str


@dataclass
class RunningGame:
    # None while the process is still starting (e.g. waiting on a UAC prompt).
    proc: subprocess.Popen[bytes] | _ElevatedProcess | None
    kill_info: tuple[str, dict[str, str]] | None
    on_exit_callback: ExitCallback | None = None
    last_saved_time: float = 0.0
    log_file: IO[str] | None = None
    prefix: str = ""
    sandboxed: bool = False
    sandbox_box: str = ""
    sandbox_tool: str = ""
    title: str = ""
    wine_context: WineContext | None = None
    game_exe: str = ""
    host_native: bool = True


class Launcher:
    def __init__(self):
        self._running: dict[int, RunningGame] = {}
        self._tools: dict[int, dict[int, ToolProcess]] = {}
        self._running_lock: LockType = threading.Lock()
        # Set while shutting down so exit callbacks skip the (closed) UI window.
        self.shutting_down: bool = False
        _ = atexit.register(self.shutdown)

    def shutdown(self) -> None:
        """Flush accumulated playtime and close logs for all running games upon exit."""
        self.shutting_down = True
        now = time.monotonic()
        with self._running_lock:
            running_entries = list(self._running.items())
            self._running.clear()
            # Games remain running when wLib closes; their tools do too.
            self._tools.clear()
        for game_id, entry in running_entries:
            if entry.log_file is not None:
                try:
                    entry.log_file.close()
                except OSError:
                    pass
                entry.log_file = None
            if entry.on_exit_callback is not None and entry.last_saved_time > 0:
                delta = max(0, int(now - entry.last_saved_time))
                try:
                    _ = entry.on_exit_callback(delta, True)
                except Exception as exc:
                    print(
                        f"Failed to record final playtime on shutdown for game {game_id}: {exc}"
                    )

    def get_running_games(self) -> list[int]:
        with self._running_lock:
            return list(self._running)

    def list_launchable_tools(
        self, game_id: int, tools: list[CompanionTool]
    ) -> list[CompanionTool]:
        with self._running_lock:
            run = self._running.get(game_id)
            if run is None or run.proc is None:
                return []
            return [
                tool
                for tool in tools
                if tool["kind"] != "wine"
                or (run.wine_context is not None and not run.sandboxed)
            ]

    @staticmethod
    def _tool_process_group(proc: subprocess.Popen[bytes]) -> int | None:
        pid = proc.pid
        # killpg(1, sig) maps to kill(-1, sig) on Linux, broadcasting to
        # processes owned by the user. Never coerce arbitrary PID-like values
        # (including mocks), or signal our own process group.
        if type(pid) is not int or pid <= 1 or pid == os.getpgrp():
            return None
        return pid

    @staticmethod
    def _tool_group_alive(proc: subprocess.Popen[bytes]) -> bool:
        pgid = Launcher._tool_process_group(proc)
        if pgid is None:
            return False
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @staticmethod
    def _close_tool_processes(tools: list[ToolProcess]) -> None:
        for tool in tools:
            if tool.kind != "wine":
                continue
            pgid = Launcher._tool_process_group(tool.proc)
            if pgid is None:
                continue
            try:
                os.killpg(pgid, signal.SIGTERM)
            except OSError as exc:
                print(f"Could not close Wine tool process group {pgid}: {exc}")
                continue

            def escalate(group: int = pgid) -> None:
                time.sleep(2)
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    print(f"Could not close Wine tool process group {group}: {exc}")

            threading.Thread(target=escalate, daemon=True).start()

    def launch_companion_tool(
        self, game_id: int, tool: CompanionTool
    ) -> dict[str, object]:
        name = tool["name"]
        try:
            with self._running_lock:
                run = self._running.get(game_id)
                if run is None or run.proc is None:
                    return {"success": False, "error": f"{name}: game is not running"}
                if tool["kind"] == "wine" and (
                    run.wine_context is None or run.sandboxed
                ):
                    reason = (
                        "sandboxed games do not support Wine tools"
                        if run.sandboxed
                        else "game is not running through Wine/Proton"
                    )
                    return {"success": False, "error": f"{name}: {reason}"}
                entries = self._tools.setdefault(game_id, {})
                previous = entries.get(tool["id"])
                if previous is not None and (
                    previous.proc.poll() is None
                    or (
                        previous.kind == "wine"
                        and self._tool_group_alive(previous.proc)
                    )
                ):
                    return {
                        "success": False,
                        "code": "tool_running",
                        "error": f"{name} is already open for this game",
                    }
                args, extra_env = split_companion_arguments(
                    tool["arguments"], run.game_exe
                )
                if tool["kind"] == "wine":
                    assert run.wine_context is not None
                    env = run.wine_context.env.copy()
                    command = [*run.wine_context.command, tool["path"], *args]
                else:
                    env = without_wine_proton_env(
                        without_packaged_runtime_env(dict(os.environ))
                    )
                    command = (
                        args if tool["kind"] == "command" else [tool["path"], *args]
                    )
                env.update(extra_env)
                if not command:
                    raise ValueError("Command line must contain a command")
                if tool["kind"] != "command" and not os.path.isfile(tool["path"]):
                    raise ValueError("Executable was not found: " + tool["path"])
                cwd = (
                    os.path.dirname(run.game_exe)
                    if tool["kind"] == "command"
                    else os.path.dirname(tool["path"])
                )
                proc = subprocess.Popen(
                    host_command(command, env=env, cwd=cwd, supervise=True),
                    env=env,
                    cwd=cwd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                entry = ToolProcess(proc, tool["kind"])
                entries[tool["id"]] = entry

            def wait_tool() -> None:
                _ = proc.wait()
                while entry.kind == "wine" and self._tool_group_alive(proc):
                    with self._running_lock:
                        if self._tools.get(game_id, {}).get(tool["id"]) is not entry:
                            return
                    time.sleep(0.2)
                with self._running_lock:
                    current = self._tools.get(game_id, {})
                    if current.get(tool["id"]) is entry:
                        del current[tool["id"]]

            threading.Thread(target=wait_tool, daemon=True).start()
            return {"success": True}
        except (OSError, ValueError) as exc:
            return {"success": False, "error": f"{name}: {exc}"}

    def stop(self, game_id: int) -> dict[str, object]:
        with self._running_lock:
            running = self._running.get(game_id)
            proc = running.proc if running is not None else None
            # wineserver -k ends every process in the prefix, so spare games that
            # share it. Compare normalized prefixes and include games that have no
            # resolved wineserver (for example a Proton runner in a directory
            # without files/bin or dist/bin).
            def shareable_prefix(entry: RunningGame) -> str:
                if entry.prefix:
                    return entry.prefix
                if entry.kill_info is not None:
                    return game_sandbox.normalize_prefix(
                        str(entry.kill_info[1].get("WINEPREFIX") or "")
                    )
                return ""

            running_prefix = shareable_prefix(running) if running is not None else ""
            prefix_shared = bool(running_prefix) and any(
                other_id != game_id and shareable_prefix(other) == running_prefix
                for other_id, other in self._running.items()
            )
        if running is None:
            return {
                "success": False,
                "code": "not_running",
                "error": "Game is not running",
            }
        if proc is None:
            return {
                "success": False,
                "code": "starting",
                "error": "Game is still starting",
            }
        with self._running_lock:
            tools = list(self._tools.pop(game_id, {}).values())
        self._close_tool_processes(tools)
        pid = proc.pid
        try:
            if running.sandbox_box:
                game_sandbox.terminate_box(running.sandbox_tool, running.sandbox_box)
            elif is_windows():
                result = subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode:
                    return {
                        "success": False,
                        "error": "Could not stop the game. It may require administrator rights; close the game itself.",
                    }
            else:
                if running.kill_info and not prefix_shared:
                    wineserver, env = running.kill_info
                    try:
                        _ = subprocess.run(
                            host_command([wineserver, "-k"], env=env),
                            env=env,
                            capture_output=True,
                            timeout=2,
                            check=False,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                killpg = cast(Callable[[int, int], None], getattr(os, "killpg"))
                try:
                    killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    # The tracking thread removes the entry and flushes final playtime.
                    return {"success": True}

                def force_stop() -> None:
                    time.sleep(5)
                    try:
                        # Check the group even if its original parent already exited.
                        killpg(pid, 0)
                        killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError as exc:
                        print(f"Could not force-stop game {game_id}: {exc}")

                threading.Thread(target=force_stop, daemon=True).start()
            return {"success": True}
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return {"success": False, "error": str(exc)}

    def _resolve_runner_candidate_path(self, raw_path: str) -> str:
        path_module = _host_path_module()
        expanded_path = path_module.expanduser(raw_path.strip())
        if path_module.sep not in expanded_path and not expanded_path.startswith("."):
            path_match = host_which(expanded_path)
            if path_match:
                return path_module.abspath(path_match)
        return path_module.abspath(expanded_path)

    def _validate_runner_candidate(self, raw_path: str) -> tuple[bool, str, str]:
        candidate_path = self._resolve_runner_candidate_path(raw_path)
        if not os.path.isfile(candidate_path):
            return (
                False,
                candidate_path,
                f"RPGMaker Linux runner was not found at {candidate_path}",
            )
        if not os.access(candidate_path, os.X_OK):
            return (
                False,
                candidate_path,
                f"RPGMaker Linux runner is not executable: {candidate_path}",
            )
        return True, candidate_path, ""

    def _read_upstream_custom_runner_path(self) -> str:
        path_module = _host_path_module()
        config_path = path_module.expanduser("~/.config/defrpgmakerlinuxpath.txt")
        try:
            with open(config_path, encoding="utf-8") as config_file:
                configured_base = config_file.readline().strip()
        except OSError:
            return ""

        if not configured_base:
            return ""

        base_path = path_module.abspath(
            path_module.expanduser(configured_base.rstrip(path_module.sep))
        )
        return path_module.join(
            base_path,
            "nwjs",
            "nwjs",
            "packagefiles",
            "nwjsstart-cicpoffs.sh",
        )

    def get_rpgmaker_linux_runner_status(
        self, configured_path: object | None = None
    ) -> RpgmakerLinuxRunnerStatus:
        if is_windows():
            return {
                "available": False,
                "path": "",
                "source": "",
                "configured_path": str(configured_path or "").strip(),
                "error": "RPGMaker Linux runner is only available on Linux.",
            }

        if configured_path is None:
            configured_path = get_setting(RPGMAKER_LINUX_RUNNER_SETTING)

        configured_runner_path = str(configured_path or "").strip()
        if configured_runner_path:
            valid, resolved_path, error = self._validate_runner_candidate(
                configured_runner_path
            )
            return {
                "available": valid,
                "path": resolved_path if valid else "",
                "source": "configured",
                "configured_path": configured_runner_path,
                "error": error,
            }

        candidates: list[tuple[str, str]] = []
        path_runner = host_which("rpgmaker-linux")
        if path_runner:
            candidates.append(("path", path_runner))
        candidates.extend(
            [
                ("local-bin", "~/.local/bin/rpgmaker-linux"),
                (
                    "default-install",
                    "~/desktopapps/nwjs/nwjs/packagefiles/nwjsstart-cicpoffs.sh",
                ),
            ]
        )
        custom_runner = self._read_upstream_custom_runner_path()
        if custom_runner:
            candidates.append(("custom-install", custom_runner))

        seen_paths: set[str] = set()
        for source, raw_path in candidates:
            valid, resolved_path, _error = self._validate_runner_candidate(raw_path)
            if resolved_path in seen_paths:
                continue
            seen_paths.add(resolved_path)
            if valid:
                return {
                    "available": True,
                    "path": resolved_path,
                    "source": source,
                    "configured_path": "",
                    "error": "",
                }

        return {
            "available": False,
            "path": "",
            "source": "",
            "configured_path": "",
            "error": "RPGMaker Linux runner is not installed or configured.",
        }

    def launch(
        self,
        exe_path: object,
        command_line_args: object | None = "",
        run_japanese_locale: bool = False,
        run_wayland: bool = False,
        auto_inject_ce: bool = False,
        custom_prefix: str = "",
        proton_version: str = "",
        launch_mode: str = "auto",
        on_exit_callback: ExitCallback | None = None,
        game_id: int | None = None,
        sandbox_enabled: bool = False,
        game_title: str = "",
    ) -> dict[str, object]:
        """
        Launches the given executable natively if it's a Linux binary, .sh, or .jar.
        Otherwise, launches using the configured Proton/Wine path.
        """
        with self._running_lock:
            if game_id in self._running:
                return {
                    "success": False,
                    "code": "already_running",
                    "error": "Game is already running",
                }
        if not isinstance(exe_path, str) or not exe_path.strip():
            return {
                "success": False,
                "error": "Executable path must be a non-empty string",
            }

        # A leading ~ is accepted in the path fields; expand it before every
        # existence check so the launcher uses the same path as path checks.
        exe_path = os.path.expanduser(exe_path.strip())
        launch_mode = normalize_launch_mode(launch_mode)

        # Reject imported Linux-only modes before path checks or sandbox probing
        # can return a more confusing error on Windows.
        if is_windows() and launch_mode != "auto":
            return {
                "success": False,
                "error": (
                    f"Launch mode '{launch_mode}' is only available on Linux. "
                    "Choose Auto Detect to launch this game directly on Windows."
                ),
                "code": "unsupported_platform",
            }

        if not os.path.exists(exe_path):
            print(f"Error: Executable not found at {exe_path}")
            return {"success": False, "error": f"Executable not found at {exe_path}"}

        # Keep path semantics tied to the selected host capability, not to the
        # Python process running the tests. This also lets Windows CI exercise
        # the Linux compatibility path without rewriting POSIX paths.
        path_module = _host_path_module()
        exe_path = path_module.abspath(exe_path)

        env = os.environ.copy()

        # Apply Japanese locale to the environment if requested
        if run_japanese_locale and not is_windows():
            print("Applying Japanese locale (ja_JP.UTF-8)")
            env["LC_ALL"] = "ja_JP.UTF-8"
            env["LANG"] = "ja_JP.UTF-8"

        # Apply Wayland compatibility environment variables if requested
        if run_wayland and not is_windows():
            print("Applying Wayland compatibility mode")
            env["MESA_VK_WSI_PRESENT_MODE"] = "immediate"
            env["vk_xwayland_wait_ready"] = "false"
            env["SDL_VIDEODRIVER"] = ""

        if command_line_args is None:
            command_line_args = ""
        elif not isinstance(command_line_args, str):
            command_line_args = str(command_line_args)

        try:
            args = (
                _split_windows_command_line(command_line_args)
                if is_windows()
                else shlex.split(command_line_args, posix=True)
            )
        except ValueError as exc:
            return {"success": False, "error": f"Invalid command line arguments: {exc}"}

        # Explicit VAR=value prefixes; host-env cleanup must never strip these.
        user_env: dict[str, str] = {}
        if "%command%" in args or launch_mode == "custom":
            while args and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[0]):
                key, value = args.pop(0).split("=", 1)
                user_env[key] = value
        env.update(user_env)

        ext = path_module.splitext(exe_path)[1].lower()
        enable_logging = get_setting("enable_logging") == "true"
        game_dir = path_module.dirname(exe_path)
        # HTML is exempt only when the launch opens the system browser. Custom
        # Command, the RPGMaker runner and a forced Wine/Proton launch run a
        # real process even when the selected target happens to be an .html
        # file, so those still get the sandbox.
        browser_launch = ext in (".html", ".htm") and (
            is_windows() or launch_mode in ("auto", "native")
        )
        sandboxed = bool(sandbox_enabled and not browser_launch)
        sandbox_status: game_sandbox.SandboxStatus | None = None
        if sandboxed:
            if game_id is None or game_id <= 0:
                return {
                    "success": False,
                    "error": "Sandboxed games need a library game ID.",
                }
            sandbox_status = game_sandbox.get_sandbox_status()
            if sandbox_status["status"] != "ready":
                code = (
                    "sandbox_missing"
                    if sandbox_status["status"] == "missing"
                    else "sandbox_unusable"
                )
                return {
                    "success": False,
                    "code": code,
                    "error": sandbox_status["reason"],
                }

        # Helper to apply Steam-style %command% substitution
        def build_command(base_cmd: list[str], user_args: list[str]) -> list[str]:
            if "%command%" in user_args:
                result: list[str] = []
                for arg in user_args:
                    if arg == "%command%":
                        result.extend(base_cmd)
                    else:
                        result.append(arg)
                return result
            return base_cmd + user_args

        # Helper for common subprocess execution
        def execute_process(
            cmd: list[str],
            env_vars: dict[str, str],
            is_wine_executable: bool = False,
            run_ce: bool = False,
            game_exe: str = "",
            kill_info: tuple[str, dict[str, str]] | None = None,
            cwd_override: str | None = None,
            runtime_paths: tuple[str, ...] = (),
            wine_context: WineContext | None = None,
        ) -> dict[str, object]:
            def prepare_ce_command() -> list[str]:
                ce_dir = get_cheat_engine_dir()
                ce_exe = path_module.join(
                    ce_dir, "Lunar Engine", "lunarengine-x86_64.exe"
                )
                if not os.path.exists(ce_exe):
                    ce_exe = path_module.join(ce_dir, "lunarengine-x86_64.exe")
                if not os.path.exists(ce_exe):
                    print(
                        f"Cheat Engine executable not found for auto-injection at {ce_exe}"
                    )
                    return []
                autorun_dir = path_module.join(path_module.dirname(ce_exe), "autorun")
                os.makedirs(autorun_dir, exist_ok=True)
                safe_name = (
                    (game_exe or "")
                    .replace("\n", "")
                    .replace("\r", "")
                    .replace("\\", "\\\\")
                    .replace('"', '\\"')
                )
                with open(
                    path_module.join(autorun_dir, "wlib_autoattach.lua"), "w"
                ) as script:
                    _ = script.write(f'OpenProcess("{safe_name}")\n')
                command = [proton_path] if proton_path else ["wine"]
                if is_proton:
                    command.append("run")
                return [*command, ce_exe]

            log_file = None
            effective_cwd = cwd_override or game_dir
            prefix = ""
            mount_prefix = ""
            if is_wine_executable or user_env.get("WINEPREFIX") or user_env.get("STEAM_COMPAT_DATA_PATH"):
                raw_prefix = env_vars.get("WINEPREFIX") or env_vars.get(
                    "STEAM_COMPAT_DATA_PATH", ""
                )
                prefix = game_sandbox.normalize_prefix(raw_prefix)
                mount_prefix = os.path.abspath(os.path.expanduser(raw_prefix))
                if os.path.basename(mount_prefix).lower() == "pfx":
                    mount_prefix = os.path.dirname(mount_prefix)
            # Reserve the slot under the lock, but start the process outside it so a
            # UAC prompt doesn't block stop/get_running_games or other games' exits.
            entry = RunningGame(
                None,
                kill_info,
                on_exit_callback=on_exit_callback,
                last_saved_time=0.0,
                log_file=None,
                prefix=prefix,
                sandboxed=sandboxed,
                title=game_title or (f"Game {game_id}"),
                wine_context=wine_context if not sandboxed else None,
                game_exe=path_module.abspath(exe_path),
                host_native=not is_wine_executable,
            )
            with self._running_lock:
                if game_id in self._running:
                    return {
                        "success": False,
                        "code": "already_running",
                        "error": "Game is already running",
                    }
                for other in self._running.values():
                    if (
                        prefix
                        and prefix == other.prefix
                        and (sandboxed or other.sandboxed)
                    ):
                        return {
                            "success": False,
                            "code": "prefix_in_use",
                            "error": f"{other.title} is already running on this Wine prefix. Use a separate custom prefix to run both games.",
                        }
                if game_id is not None:
                    self._running[game_id] = entry
            try:
                if sandboxed and sandbox_status is not None and is_windows():
                    assert game_id is not None
                    # The game folder is a write-through path, so it must not be the profile.
                    game_sandbox.check_writable_mounts(
                        (game_dir,), os.path.expanduser("~")
                    )
                    entry.sandbox_box = game_sandbox.configure_sandboxie_box(
                        sandbox_status["path"], game_id, game_dir
                    )
                    entry.sandbox_tool = sandbox_status["path"]
                    cmd = [entry.sandbox_tool, f"/box:{entry.sandbox_box}", *cmd]
                if sandboxed and sandbox_status is not None and not is_windows():
                    assert game_id is not None
                    if mount_prefix:
                        os.makedirs(mount_prefix, exist_ok=True)
                    readonly = runtime_paths
                    if is_wine_executable and run_ce:
                        ce_command = prepare_ce_command()
                        if ce_command:
                            readonly += (get_cheat_engine_dir(),)
                            cmd = [
                                "sh",
                                "-c",
                                f"(sleep 5; {shlex.join(ce_command)}) & {shlex.join(cmd)}",
                            ]
                        run_ce = False
                    sandbox_game_dir = game_sandbox.resolve_game_folder(
                        exe_path, launch_mode
                    )
                    user_home = os.path.expanduser("~")
                    game_sandbox.check_writable_mounts(
                        (sandbox_game_dir, mount_prefix), user_home
                    )
                    cmd = game_sandbox.build_bwrap_argv(
                        cmd,
                        game_dir=sandbox_game_dir,
                        home=user_home,
                        sandbox_home=game_sandbox.prepare_sandbox_home(game_id),
                        cwd=effective_cwd,
                        prefix=mount_prefix,
                        readonly_paths=readonly,
                        environment=env_vars,
                        nixos=os.path.isdir("/nix/store"),
                        bwrap=sandbox_status["path"],
                    )
                    entry.kill_info = None
                if enable_logging:
                    log_path = path_module.splitext(exe_path)[0] + "_wlib.log"
                    try:
                        log_file = open(log_path, "w")
                    except OSError:
                        # Read-only game folder: log to the temp dir instead of failing the launch.
                        log_path = path_module.join(
                            tempfile.gettempdir(), path_module.basename(log_path)
                        )
                        log_file = open(log_path, "w")
                    print(f"Debug logging enabled. Outputting to {log_path}")

                try:
                    game_proc = subprocess.Popen(
                        host_command(
                            cmd, env=env_vars, cwd=effective_cwd, supervise=True
                        ),
                        env=env_vars,
                        stdout=log_file if log_file is not None else subprocess.DEVNULL,
                        stderr=(
                            subprocess.STDOUT
                            if log_file is not None
                            else subprocess.DEVNULL
                        ),
                        cwd=effective_cwd,
                        start_new_session=not is_windows(),
                    )
                except OSError as exc:
                    if not is_windows() or getattr(exc, "winerror", None) != 740:
                        raise
                    # ShellExecuteEx cannot redirect stdout/stderr across elevation.
                    if log_file is not None:
                        _ = log_file.write(
                            "Game requires administrator rights; elevated output cannot be captured.\n"
                        )
                        log_file.close()
                        log_file = None
                    game_proc = _ElevatedProcess(cmd, effective_cwd)
                # Do not count time spent waiting for UAC approval as playtime.
                start_time = time.monotonic()
                with self._running_lock:
                    entry.proc = game_proc
                    entry.last_saved_time = start_time
                    entry.log_file = log_file
                if is_wine_executable and run_ce:
                    # Spawn CE in a background thread after a delay
                    def game_still_running() -> bool:
                        if self.shutting_down:
                            return False
                        if game_id is not None:
                            with self._running_lock:
                                return self._running.get(game_id) is entry
                        return game_proc.poll() is None

                    def inject_ce():
                        import time

                        print(
                            "Waiting 5 seconds for game to initialize before attaching Cheat Engine..."
                        )
                        time.sleep(5)
                        if not game_still_running():
                            return

                        ce_cmd = prepare_ce_command()
                        if ce_cmd and game_still_running():
                            ce_proc = subprocess.Popen(
                                host_command(ce_cmd, env=env_vars),
                                env=env_vars,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            threading.Thread(target=ce_proc.wait, daemon=True).start()

                    threading.Thread(target=inject_ce, daemon=True).start()

                def track_playtime_thread():
                    last_saved_time = start_time
                    box_started = False
                    killpg = getattr(os, "killpg", None)
                    pid = getattr(game_proc, "pid", None)

                    def group_alive() -> bool:
                        # Launcher stubs exit while their children keep the game running.
                        if is_windows() or killpg is None or pid is None:
                            return False
                        try:
                            killpg(pid, 0)
                        except PermissionError:
                            return True  # exists, owned by another user
                        except OSError:
                            return False
                        return True

                    try:
                        while True:
                            if entry.sandbox_box:
                                try:
                                    pids = game_sandbox.list_box_pids(
                                        entry.sandbox_tool, entry.sandbox_box
                                    )
                                except (
                                    OSError,
                                    RuntimeError,
                                    subprocess.TimeoutExpired,
                                ) as exc:
                                    print(f"Could not read sandbox process list: {exc}")
                                    time.sleep(1)
                                    continue
                                if pids:
                                    box_started = True
                                elif box_started or time.monotonic() - start_time >= 5:
                                    break
                                time.sleep(1)
                            elif game_proc.poll() is None:
                                try:
                                    _ = game_proc.wait(timeout=60)
                                except subprocess.TimeoutExpired:
                                    pass
                                else:
                                    if not group_alive():
                                        break
                            elif group_alive():
                                time.sleep(1)
                            else:
                                break

                            now = time.monotonic()
                            if now - last_saved_time < 60:
                                continue
                            delta = int(now - last_saved_time)
                            with self._running_lock:
                                # shutdown() may have taken the entry and flushed it.
                                owned = (
                                    game_id is None
                                    or self._running.get(game_id) is entry
                                )
                                if owned:
                                    entry.last_saved_time = now
                            if not owned:
                                break
                            last_saved_time = now
                            if on_exit_callback is not None:
                                try:
                                    _ = on_exit_callback(delta, False)
                                except Exception as exc:
                                    print(f"Failed to record playtime: {exc}")
                    finally:
                        tools: list[ToolProcess] = []
                        with self._running_lock:
                            if game_id is not None:
                                current = self._running.get(game_id)
                                if current is not None and current.proc is game_proc:
                                    del self._running[game_id]
                                    tools = list(self._tools.pop(game_id, {}).values())
                                    should_report_exit = True
                                else:
                                    should_report_exit = False
                            else:
                                should_report_exit = True
                        self._close_tool_processes(tools)
                        if log_file is not None:
                            try:
                                log_file.close()
                            except OSError:
                                pass
                        now = time.monotonic()
                        if should_report_exit and on_exit_callback is not None:
                            _ = on_exit_callback(
                                max(0, int(now - last_saved_time)), True
                            )

                threading.Thread(target=track_playtime_thread, daemon=True).start()

                return {"success": True}
            except Exception as e:
                with self._running_lock:
                    if game_id is not None and self._running.get(game_id) is entry:
                        del self._running[game_id]
                if log_file is not None:
                    log_file.close()
                print(f"Error launching game: {e}")
                return {"success": False, "error": str(e)}

        def build_host_tool_env(
            env_vars: dict[str, str], strip_wine_env: bool = True
        ) -> dict[str, str]:
            clean_env = without_packaged_runtime_env(env_vars)
            if strip_wine_env:
                clean_env = without_wine_proton_env(clean_env)
            clean_env.update(user_env)
            return clean_env

        def execute_rpgmaker_linux_runner() -> dict[str, object]:
            runner_status = self.get_rpgmaker_linux_runner_status()
            if not runner_status["available"]:
                return {
                    "success": False,
                    "error": runner_status["error"]
                    or "RPGMaker Linux runner is not available.",
                }

            runner_game_dir = path_module.dirname(path_module.abspath(exe_path))
            if path_module.basename(runner_game_dir).lower() == "www":
                parent_dir = path_module.dirname(runner_game_dir)
                if os.path.isfile(path_module.join(parent_dir, "package.json")):
                    runner_game_dir = parent_dir
            base_cmd = [runner_status["path"], "--gamepath", runner_game_dir]

            command = build_command(base_cmd, args)
            print(f"Executing via RPGMaker Linux runner: {' '.join(command)}")
            return execute_process(
                command,
                build_host_tool_env(env),
                cwd_override=runner_game_dir,
                runtime_paths=(game_sandbox.runtime_folder(runner_status["path"]),),
            )

        def execute_html_game() -> dict[str, object]:
            # Convert to absolute path and file:// URL for proper browser handling
            abs_path = path_module.abspath(exe_path)
            if is_windows():
                opened, error = open_windows_system_target(abs_path)
                if opened:
                    print(f"Opening HTML game in default browser: {abs_path}")
                    return {"success": True, "tracks_playtime": False}
                return {
                    "success": False,
                    "error": f"Failed to open HTML game: {error}",
                }

            file_url = "file://" + quote(abs_path, safe="/")
            command = ["xdg-open", file_url]
            print(f"Opening HTML game in default browser: {file_url}")
            try:
                # Use clean environment without Wine/Proton/AppImage variables
                # that might interfere. Reuse the launch env so Japanese
                # locale / Wayland / user VAR=value prefixes still apply.
                clean_env = build_host_tool_env(env)

                proc = subprocess.Popen(
                    command,
                    env=clean_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=path_module.dirname(abs_path),
                )

                threading.Thread(target=proc.wait, daemon=True).start()
                return {"success": True, "tracks_playtime": False}
            except Exception as e:
                print(f"Error launching HTML game: {e}")
                return {"success": False, "error": str(e)}

        def execute_host_native(strict_native: bool) -> dict[str, object] | None:
            process_env = build_host_tool_env(env)

            # Unzipping often drops the exec bit; Wine can't run these either.
            if (
                (ext == ".sh" or is_elf(exe_path) or is_script(exe_path))
                and not os.access(exe_path, os.X_OK)
                and not ensure_executable(exe_path)
            ):
                return {
                    "success": False,
                    "error": (
                        f"{exe_path} is not executable and wLib could not fix it "
                        "(read-only or noexec mount?). Run chmod +x on it, "
                        "then launch again."
                    ),
                }

            # 1. Native Shell Script (e.g. Ren'Py)
            if ext == ".sh":
                command = build_command([exe_path], args)
                print(f"Executing shell script natively: {' '.join(command)}")
                return execute_process(command, process_env)

            # 2. Java Archives (.jar)
            if ext == ".jar":
                command = build_command(["java", "-jar", exe_path], args)
                print(f"Executing Java archive: {' '.join(command)}")
                return execute_process(command, process_env)

            # 3. HTML Games (browser-based)
            # Playtime tracking is not supported because browser processes cannot
            # be reliably monitored when other tabs or windows are open.
            if ext in [".html", ".htm"]:
                return execute_html_game()

            # 4. Native Linux Binary (Executable without .exe/.bat/.cmd extension)
            # Some native Linux games like Godot have no extension or .x86_64.
            # An ELF binary misnamed with a Windows extension still runs
            # natively; forcing it through Wine would only fail.
            if os.access(exe_path, os.X_OK) and (
                ext not in [".exe", ".bat", ".cmd"]
                or is_elf(exe_path)
                or is_script(exe_path)
            ):
                command = build_command([exe_path], args)
                print(f"Executing Linux binary natively: {' '.join(command)}")
                return execute_process(command, process_env)

            if strict_native:
                if ext in [".exe", ".bat", ".cmd"]:
                    return {
                        "success": False,
                        "error": (
                            "Linux Native launch mode cannot run Windows "
                            "executables directly. Choose Auto Detect or "
                            "Wine/Proton for this game."
                        ),
                    }
                return {
                    "success": False,
                    "error": (
                        "Linux Native launch mode requires an executable "
                        f"host-native file: {exe_path}"
                    ),
                }

            return None

        if is_windows():
            process_env = build_host_tool_env(env)
            if ext == ".exe":
                return execute_process(build_command([exe_path], args), process_env)
            if ext in (".bat", ".cmd"):
                command_processor = os.environ.get("COMSPEC") or "cmd.exe"
                base_command = [
                    command_processor,
                    "/d",
                    "/s",
                    "/c",
                    "call",
                    exe_path,
                ]
                return execute_process(build_command(base_command, args), process_env)
            if ext == ".jar":
                return execute_process(
                    build_command(["java", "-jar", exe_path], args), process_env
                )
            if ext in (".html", ".htm"):
                return execute_html_game()
            return {
                "success": False,
                "error": f"Unsupported Windows game target: {exe_path}",
            }

        if launch_mode == "custom":
            if not args:
                return {
                    "success": False,
                    "error": "Custom Command mode needs a command",
                }
            command = build_command([exe_path], args) if "%command%" in args else args
            return execute_process(command, build_host_tool_env(env))

        if launch_mode in ("auto", "native"):
            native_result = execute_host_native(strict_native=launch_mode == "native")
            if native_result is not None:
                return native_result

        if launch_mode == "rpgmaker_linux":
            return execute_rpgmaker_linux_runner()

        # 5. Fallback to Wine / Proton execution for Windows executables
        env = without_packaged_runtime_env(env)
        env = without_wine_proton_env(env)
        env.update(user_env)
        # Expand "~" like the dependency/RTP installers so both use the same prefix.
        proton_path = path_module.expanduser(
            (proton_version or get_setting("proton_path") or "").strip()
        )
        if proton_path and path_module.sep in proton_path:
            proton_path = path_module.abspath(proton_path)

        wine_prefix = path_module.expanduser(
            (custom_prefix or get_setting("wine_prefix_path") or "").strip()
        )
        if wine_prefix:
            wine_prefix = path_module.abspath(wine_prefix)

        if not wine_prefix:
            # We must supply a prefix for Proton to function at all.
            # If the user didn't set one, use a default wLib specific one
            wine_prefix = get_default_wine_prefix()
            os.makedirs(wine_prefix, exist_ok=True)

        is_proton = (
            proton_path and "proton" in path_module.basename(proton_path).lower()
        )

        base_cmd = [proton_path] if proton_path else ["wine"]
        if is_proton:
            base_cmd.append("run")

        base_cmd.append(exe_path)
        command = build_command(base_cmd, args)

        # Global DLL overrides required for various titles. Defaults go first:
        # Wine keeps the last entry per DLL, so the user's own overrides win.
        dll_overrides = ["mscoree=n,b;msvcrt=b,n;winhttp=n,b"]

        # Auto-detect engines for specific launcher arguments
        # RPGMaker MV / MZ (NW.js Chromium)
        if os.path.exists(path_module.join(game_dir, "nw.dll")) and os.path.exists(
            path_module.join(game_dir, "www")
        ):
            print(
                "Detected RPGMaker MV/MZ. Applying winegstreamer override and NW.js flags..."
            )
            dll_overrides.append("winegstreamer=d")
            # Add NW.js/Chromium flags for better Wine compatibility
            command.extend(["--disable-gpu-sandbox", "--no-sandbox"])

        if env.get("WINEDLLOVERRIDES"):
            dll_overrides.append(env["WINEDLLOVERRIDES"])
        env["WINEDLLOVERRIDES"] = ";".join(dll_overrides)

        # Proton and Wine prefix handling
        if is_proton:
            compat_path = proton_compat_data_path(wine_prefix)
            if compat_path != wine_prefix:
                print(
                    "Warning: Standard Wine prefix detected. Creating isolated proton directory."
                )
                wine_prefix = compat_path
                os.makedirs(wine_prefix, exist_ok=True)

            env["STEAM_COMPAT_DATA_PATH"] = wine_prefix
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = "/tmp/wlib"
            env["STEAM_COMPAT_INSTALL_PATH"] = game_dir
            _ = env.setdefault("UMU_ID", "wlib")
        else:
            # If the selected prefix is actually a Proton prefix (contains pfx subfolder),
            # standard Wine must point directly to the pfx subfolder
            if os.path.isdir(path_module.join(wine_prefix, "pfx")):
                wine_prefix = path_module.join(wine_prefix, "pfx")

            env["WINEPREFIX"] = wine_prefix

        if enable_logging:
            env["PROTON_LOG"] = "1"
            env["PROTON_LOG_DIR"] = game_dir
            env["WINEDEBUG"] = "-all,err+all,warn+all"

        print(
            f"Executing via Wine/Proton: {' '.join(command)} with prefix {wine_prefix}"
        )
        game_exe_name = path_module.basename(exe_path)
        wineserver = ""
        if is_proton and proton_path:
            for layout in ("files", "dist"):
                candidate = path_module.join(
                    path_module.dirname(proton_path), layout, "bin", "wineserver"
                )
                if os.path.isfile(candidate):
                    wineserver = candidate
                    break
        else:
            # Wine builds ship wineserver beside wine; otherwise use the one on PATH.
            sibling = (
                path_module.join(path_module.dirname(proton_path), "wineserver")
                if proton_path and path_module.dirname(proton_path)
                else ""
            )
            wineserver = (
                sibling
                if sibling and os.path.isfile(sibling)
                else host_which("wineserver") or ""
            )
        kill_env = env.copy()
        kill_env["WINEPREFIX"] = (
            path_module.join(wine_prefix, "pfx") if is_proton else wine_prefix
        )
        return execute_process(
            command,
            env,
            is_wine_executable=True,
            wine_context=WineContext(base_cmd[:-1], env.copy()),
            run_ce=auto_inject_ce,
            game_exe=game_exe_name,
            kill_info=(wineserver, kill_env) if wineserver else None,
            runtime_paths=(game_sandbox.runtime_folder(proton_path),)
            if proton_path and path_module.isabs(proton_path)
            else (),
        )
