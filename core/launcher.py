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
import glob
import shutil
import threading
import time
from urllib.parse import quote
from contextlib import nullcontext
from collections.abc import Callable, Sequence
from _thread import LockType, start_new_thread
from dataclasses import dataclass
from typing import IO, Protocol, TypedDict, cast, final

from .database import (
    RPGMAKER_LINUX_RUNNER_SETTING,
    get_setting,
    normalize_launch_mode,
    CompanionTool,
    split_companion_arguments,
    web_player_devtools_enabled,
)
from . import display_info, game_sandbox
from . import gamescope as gamescope_mod
from .gamescope import GamescopeOptions
from .host_platform import (
    get_cheat_engine_dir,
    get_default_wine_prefix,
    host_command,
    host_system_layout,
    host_which,
    host_x11_sockets,
    is_flatpak,
    is_windows,
    no_console_window,
    open_windows_system_target,
    proton_compat_data_path as proton_compat_data_path,
)

# Consecutive failed sandbox process-list reads (one per second) before the
# playtime tracker treats the game as ended.
SANDBOX_LIST_FAILURE_LIMIT = 60


def wrap_performance_tools(cmd: list[str], tools: list[str], user_args: list[str]) -> list[str]:
    existing = {os.path.basename(arg) for arg in user_args}
    return [*(tool for tool in tools if os.path.basename(tool) not in existing), *cmd]


def gamescope_skip_reason(
    *, windows: bool, browser_launch: bool, flatpak_player: bool, user_args: list[str]
) -> str:
    """Why a launch with gamescope enabled runs without it ('' when it does not skip).

    "not installed" is decided later, by the host lookup.
    """
    if windows:
        return "Windows"
    if browser_launch:
        return "browser launch"
    # The Flatpak player runs inside the app, where host tools are absent.
    if flatpak_player:
        return "Flatpak web player"
    if gamescope_mod.in_gamescope_session():
        return "already in a gamescope session"
    if gamescope_mod.args_start_gamescope(user_args):
        return "arguments already start gamescope"
    return ""


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


_CREATE_SUSPENDED = 0x00000004
# The real host, unlike is_windows(), which tests replace.
_NATIVE_WINDOWS: bool = sys.platform == "win32"


class _WindowsJob:
    """A Job Object that holds a game and every process it starts.

    Many games start through a launcher that runs the real game and exits.
    Linux follows the process group; Windows has none, so the job is what
    tells whether the game still runs and what Stop ends. The job is not
    killed when its handle closes: a game outlives wLib, as on Linux.
    """

    def __init__(self) -> None:
        import ctypes
        import weakref
        from ctypes import wintypes

        load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
        kernel32 = load_dll("kernel32", use_last_error=True)
        create = kernel32.CreateJobObjectW
        create.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        create.restype = wintypes.HANDLE
        handle = cast(int | None, create(None, None))
        if not handle:
            raise OSError("Windows could not create a job object for the game.")
        self._kernel32: ctypes.CDLL = kernel32
        self._handle: int = handle
        close = kernel32.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        _ = weakref.finalize(self, close, handle)

    def add(self, process_handle: int) -> bool:
        from ctypes import wintypes

        assign = self._kernel32.AssignProcessToJobObject
        assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign.restype = wintypes.BOOL
        return bool(cast(int, assign(self._handle, process_handle)))

    def active_processes(self) -> int | None:
        """Processes still running in the job; None when Windows cannot say."""
        import ctypes
        from ctypes import wintypes

        @final
        class BasicAccounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        query = self._kernel32.QueryInformationJobObject
        query.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        query.restype = wintypes.BOOL
        info = BasicAccounting()
        # 1 = JobObjectBasicAccountingInformation
        if not query(self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            return None
        return int(cast(int, info.ActiveProcesses))

    def terminate(self) -> bool:
        from ctypes import wintypes

        terminate = self._kernel32.TerminateJobObject
        terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
        terminate.restype = wintypes.BOOL
        return bool(cast(int, terminate(self._handle, 1)))

    def process_ids(self) -> list[int] | None:
        """IDs of the processes in the job; None when Windows cannot say."""
        import ctypes
        from ctypes import wintypes

        query = self._kernel32.QueryInformationJobObject
        query.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        query.restype = wintypes.BOOL
        for capacity in (64, 4096):

            @final
            class ProcessIdList(ctypes.Structure):
                _fields_ = [
                    ("assigned", wintypes.DWORD),
                    ("listed", wintypes.DWORD),
                    ("ids", ctypes.c_size_t * capacity),
                ]

            info = ProcessIdList()
            # 3 = JobObjectBasicProcessIdList
            if query(self._handle, 3, ctypes.byref(info), ctypes.sizeof(info), None):
                ids = cast(Sequence[int], info.ids)
                return [int(ids[index]) for index in range(int(cast(int, info.listed)))]
            if _last_error() != 234:  # ERROR_MORE_DATA
                return None
        return None

    def game_process_ids(self) -> list[int] | None:
        """Job processes that are the game, not a shared program it opened."""
        pids = self.process_ids()
        if pids is None:
            return None
        return [
            pid
            for pid in pids
            if ntpath.basename(_process_image(pid)).lower()
            not in _WINDOWS_SHARED_PROGRAMS
        ]

    def stop_game(self) -> bool:
        """End the game's processes and leave shared programs running."""
        pids = self.game_process_ids()
        if pids is None:
            return self.terminate()
        return all(_terminate_process(pid) for pid in pids)


# Programs a game starts that are not the game: the browser a link opens when
# none was running, the Steam client a DRM game starts, the console host and
# crash reporter. They can outlive the game by hours, so they neither keep it
# "running" nor get closed by Stop.
_WINDOWS_SHARED_PROGRAMS = frozenset(
    {
        "brave.exe",
        "chrome.exe",
        "conhost.exe",
        "discord.exe",
        "explorer.exe",
        "firefox.exe",
        "iexplore.exe",
        "msedge.exe",
        "opera.exe",
        "steam.exe",
        "steamservice.exe",
        "steamwebhelper.exe",
        "vivaldi.exe",
        "werfault.exe",
    }
)


def _last_error() -> int:
    import ctypes

    # Windows-only; absent from the Linux type stubs.
    get_last_error = cast(Callable[[], int], getattr(ctypes, "get_last_error"))
    return get_last_error()


def _process_image(pid: int) -> str:
    """A process's executable path, or "" when it cannot be read."""
    import ctypes
    from ctypes import wintypes

    load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
    kernel32 = load_dll("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    query = kernel32.QueryFullProcessImageNameW
    query.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    # PROCESS_QUERY_LIMITED_INFORMATION
    handle = cast(int | None, open_process(0x1000, False, pid))
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not query(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return str(cast(object, buffer.value))
    finally:
        _ = cast(object, close(handle))


def _terminate_process(pid: int) -> bool:
    """End one process; True when it is gone."""
    import ctypes
    from ctypes import wintypes

    load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
    kernel32 = load_dll("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    terminate = kernel32.TerminateProcess
    terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    handle = cast(int | None, open_process(0x0001, False, pid))  # PROCESS_TERMINATE
    if not handle:
        # ERROR_INVALID_PARAMETER: the process already exited.
        return _last_error() == 87
    try:
        return bool(cast(int, terminate(handle, 1)))
    finally:
        _ = cast(object, close(handle))


def _resume_process(process_handle: int) -> bool:
    import ctypes
    from ctypes import wintypes

    load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
    resume = load_dll("ntdll").NtResumeProcess
    resume.argtypes = [wintypes.HANDLE]
    resume.restype = ctypes.c_long
    return cast(int, resume(process_handle)) == 0


def _start_in_windows_job(
    start: Callable[[int], "subprocess.Popen[bytes]"],
) -> "tuple[subprocess.Popen[bytes], _WindowsJob | None]":
    """Start a game suspended, put it in a job, then let it run.

    Suspended first, so a launcher cannot start the real game before it is
    in the job. Without a job the game still starts, tracked as before.
    """
    if not _NATIVE_WINDOWS:
        return start(0), None
    try:
        job = _WindowsJob()
    except (OSError, AttributeError) as exc:
        print(f"Could not track the game's child processes: {exc}")
        return start(0), None
    proc = start(_CREATE_SUSPENDED)
    raw_handle = cast(object, getattr(proc, "_handle", None))
    if not isinstance(raw_handle, int):
        # Only a stand-in process (tests) lacks a Windows process handle.
        return proc, None
    handle = int(raw_handle)
    added = job.add(handle)
    if not _resume_process(handle):
        proc.kill()
        raise OSError("Windows could not start the game process.")
    if not added:
        print("Could not track the game's child processes: the job was refused.")
    return proc, job if added else None


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


def self_command(flag: str) -> list[str]:
    """argv prefix that starts a wLib child process (`flag`) from a frozen or source run."""
    if getattr(sys, "frozen", False):
        return [sys.executable, flag]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # A venv's python is a symlink chain that a sandbox does not contain, so run
    # the real interpreter and hand it the venv's packages (see self_env).
    interpreter = os.path.realpath(sys.executable) if sys.prefix != sys.base_prefix else sys.executable
    return [interpreter, os.path.join(repo_root, "main.py"), flag]


def self_env(env: dict[str, str]) -> dict[str, str]:
    """Source runs inside a venv: point the real interpreter at the venv's packages."""
    if getattr(sys, "frozen", False) or sys.prefix == sys.base_prefix:
        return env
    import site

    paths = [path for path in site.getsitepackages() if os.path.isdir(path)]
    existing = env.get("PYTHONPATH", "")
    env = env.copy()
    env["PYTHONPATH"] = os.pathsep.join([*paths, *([existing] if existing else [])])
    return env


def web_player_command() -> list[str]:
    """argv prefix that starts the built-in web player."""
    return self_command("--play-web")


def web_player_runtime_paths() -> tuple[str, ...]:
    """Read-only folders the player needs inside a sandbox: wLib's own files and Python."""
    if getattr(sys, "frozen", False):
        candidates = [
            os.environ.get("APPDIR", ""),
            str(getattr(sys, "_MEIPASS", "") or ""),
            os.path.dirname(os.path.realpath(sys.executable)),
        ]
    else:
        candidates = [
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            sys.prefix,
            sys.base_prefix,
            os.path.dirname(os.path.dirname(os.path.realpath(sys.executable))),
        ]
    folders: list[str] = []
    for candidate in candidates:
        if not candidate or not os.path.isdir(candidate):
            continue
        real = os.path.realpath(candidate)
        if any(os.path.commonpath((real, kept)) == kept for kept in folders):
            continue
        folders = [kept for kept in folders if os.path.commonpath((real, kept)) != real]
        folders.append(real)
    return tuple(folders)


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


# Processes the first Wine program in a prefix starts for the whole prefix.
# They join that program's process group and outlive it, and later games on
# the same prefix use them too.
_WINE_BACKGROUND_NAMES = frozenset(
    {
        "wineserver",
        "services.exe",
        "winedevice.exe",
        "plugplay.exe",
        "explorer.exe",
        "svchost.exe",
        "rpcss.exe",
        "tabtip.exe",
        "conhost.exe",
        "winemenubuilder",
        "winemenubuilder.exe",
        # gamescope and its X server lead the group when the setting wraps
        # the launch; they close after the game, so they are not the game.
        "gamescope",
        "gamescope-wl",
        "xwayland",
    }
)

# Keeps gamescope open until every Wine process in the prefix has exited,
# since a launcher stub can exit while the real game still runs.
# Arguments: <wineserver> <game command...>.
GAMESCOPE_WINE_WAIT = 'ws=$1; shift; "$@"; s=$?; "$ws" -w; exit $s'



def _wine_group_pids(pgid: int) -> tuple[list[int], list[int]] | None:
    """(game pids, Wine background pids) in a process group, from /proc.

    None when the group is not visible here (no /proc, another PID namespace),
    so callers fall back to treating the group as a whole.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    game: list[int] = []
    background: list[int] = []
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="utf-8", errors="replace") as stat_file:
                stat = str(stat_file.read())
        except (OSError, ValueError):
            continue
        # comm can hold spaces and parentheses; the fields follow the last ")".
        open_paren = stat.find("(")
        close_paren = stat.rfind(")")
        if open_paren < 0 or close_paren < open_paren:
            continue
        fields = stat[close_paren + 2 :].split()
        if len(fields) < 3 or fields[2] != str(pgid) or fields[0] in ("Z", "X"):
            continue
        comm = stat[open_paren + 1 : close_paren].lower()
        (background if comm in _WINE_BACKGROUND_NAMES else game).append(int(name))
    if not game and not background:
        return None
    return game, background


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
    umu: bool = False
    network_blocked: bool = False
    # Windows: the job that holds the game and the processes it starts.
    job: _WindowsJob | None = None


def _spawn_sandbox_process(
    start: Callable[[], subprocess.Popen[bytes]],
) -> subprocess.Popen[bytes]:
    """Keep bwrap's parent thread alive while its parent-death guard is active."""
    ready = threading.Event()
    processes: list[subprocess.Popen[bytes]] = []
    errors: list[BaseException] = []

    def parent() -> None:
        try:
            process = start()
            processes.append(process)
        except BaseException as exc:
            errors.append(exc)
            ready.set()
            return
        ready.set()
        try:
            _ = process.wait()
        except (OSError, ChildProcessError):
            pass

    _ = start_new_thread(parent, ())
    _ = ready.wait()
    if errors:
        raise errors[0]
    return processes[0]


def _open_debug_log(path: str) -> IO[str]:
    """Open a game's debug log for writing without following a symlink.

    The log sits in the game folder, which a sandboxed game can write. A
    plain open() would follow a symlink it left there and truncate the file
    it points at outside the sandbox.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o644)
    try:
        return os.fdopen(descriptor, "w")
    except BaseException:
        os.close(descriptor)
        raise


class Launcher:
    def __init__(self):
        self._running: dict[int, RunningGame] = {}
        self._tools: dict[int, dict[int, ToolProcess]] = {}
        self._running_lock: LockType = threading.Lock()
        self.prefix_guard_lock: LockType | None = None
        # Called as prefix_guard(prefix, game_id) under prefix_guard_lock;
        # returns why the launch must be refused, or None.
        self.prefix_guard: Callable[[str, int | None], str | None] | None = None
        # Called as external_guard(entry, game_id) after prefix_guard; lets a
        # separate launcher process (the Steam runner) ask the main app.
        self.external_guard: Callable[[RunningGame, int | None], str | None] | None = None
        # Games another wLib process launched, keyed by game ID, so prefix
        # checks here also see them. external_alive(game_id) drops the entry
        # of a runner that died without reporting its end.
        self._external: dict[int, RunningGame] = {}
        self.external_alive: Callable[[int], bool] | None = None
        # Set while shutting down so exit callbacks skip the (closed) UI window.
        self.shutting_down: bool = False
        _ = atexit.register(self.shutdown)

    def game_using_prefix(self, prefix: str) -> str | None:
        normalized = game_sandbox.normalize_prefix(prefix)
        if not normalized:
            return None
        with self._running_lock:
            self._prune_external_locked()
            return next((entry.title for entry in (*self._running.values(), *self._external.values()) if game_sandbox.normalize_prefix(entry.prefix) == normalized), None)

    def _prefix_conflict_locked(
        self, entry: "RunningGame", game_id: int | None
    ) -> str | None:
        """Why entry cannot share its prefix with a running game. Caller holds _running_lock."""
        if not entry.prefix:
            return None
        self._prune_external_locked()
        for other_id, other in (*self._running.items(), *self._external.items()):
            if other_id == game_id or entry.prefix != other.prefix:
                continue
            # Processes started on a shared wineserver (explorer, services,
            # COM servers) outlive one game's sandbox or network block, so
            # a sandboxed, umu or network-blocked game cannot share a prefix
            # with a game that runs differently.
            if (
                entry.sandboxed
                or other.sandboxed
                or entry.umu
                or other.umu
                or entry.network_blocked != other.network_blocked
            ):
                return f"{other.title} is already running on this Wine prefix. Use a separate custom prefix to run both games."
        return None

    def _prune_external_locked(self) -> None:
        if self.external_alive is None:
            return
        for game_id in list(self._external):
            try:
                alive = self.external_alive(game_id)
            except Exception:
                alive = True
            if not alive:
                del self._external[game_id]

    def reserve_external(self, game_id: int, entry: "RunningGame") -> str | None:
        """Record a game another wLib process is starting, or say why it can't start."""
        with self._running_lock:
            if game_id in self._running:
                return f"{self._running[game_id].title} is already running."
            conflict = self._prefix_conflict_locked(entry, game_id)
            if conflict:
                return conflict
            self._external[game_id] = entry
            return None

    def release_external(self, game_id: int) -> None:
        with self._running_lock:
            _ = self._external.pop(game_id, None)

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
                if tool["kind"] == "wine" and run.umu and run.wine_context is not None:
                    for key in (
                        "WINEPREFIX",
                        "PROTONPATH",
                        "GAMEID",
                        "UMU_CONTAINER_NSENTER",
                        "WLIB_UMU_RUN",
                    ):
                        if key in run.wine_context.env:
                            env[key] = run.wine_context.env[key]
                        else:
                            _ = env.pop(key, None)
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
            elif is_windows() and running.job is not None and running.job.stop_game():
                # Also ends what a launcher that already exited started.
                pass
            elif is_windows():
                result = subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                    check=False,
                    creationflags=no_console_window(),
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
                # Another game on this prefix relies on the wineserver and
                # services in this group, so signal only this game's processes.
                spare_wine = prefix_shared and not running.host_native and not running.sandboxed

                def signal_game(sig: int) -> None:
                    members = _wine_group_pids(pid) if spare_wine else None
                    if members is None:
                        killpg(pid, sig)
                        return
                    if not members[0]:
                        raise ProcessLookupError(pid)
                    for member in members[0]:
                        try:
                            os.kill(member, sig)
                        except ProcessLookupError:
                            pass

                try:
                    signal_game(signal.SIGTERM)
                except ProcessLookupError:
                    # The tracking thread removes the entry and flushes final playtime.
                    return {"success": True}

                def force_stop() -> None:
                    time.sleep(5)
                    try:
                        # Check the group even if its original parent already exited.
                        killpg(pid, 0)
                        signal_game(signal.SIGKILL)
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
        configured_error = ""
        if configured_runner_path:
            valid, resolved_path, configured_error = self._validate_runner_candidate(
                configured_runner_path
            )
            if valid:
                return {
                    "available": True,
                    "path": resolved_path,
                    "source": "configured",
                    "configured_path": configured_runner_path,
                    "error": "",
                }

        # A stale configured path must not hide a runner on PATH or in a known
        # location; keep reporting the bad configured value alongside it.
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
                    "configured_path": configured_runner_path,
                    "error": (
                        f"{configured_error}. Using the detected runner instead."
                        if configured_error
                        else ""
                    ),
                }

        if configured_error:
            return {
                "available": False,
                "path": "",
                "source": "configured",
                "configured_path": configured_runner_path,
                "error": configured_error,
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
        block_network: bool = False,
        gamemode: bool = False,
        mangohud: bool = False,
        html_player: str = "",
        gamescope: GamescopeOptions | None = None,
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
        from .web_launch import resolve_web_player

        web_kind = resolve_web_player(
            exe_path,
            launch_mode,
            html_player,
            # Only HTML follows the setting; skip the lookup for other targets.
            ext not in (".html", ".htm") or get_setting("html_builtin_player") != "false",
            windows=is_windows(),
        )
        browser_launch = web_kind == "browser"
        # Flatpak runs the player inside the app itself, not on the host, and
        # uses Flatpak's own sub-sandbox instead of bubblewrap.
        flatpak_player = web_kind == "builtin" and is_flatpak()
        sandboxed = bool(sandbox_enabled and not browser_launch)
        performance_tools: list[str] = []
        performance_notes: list[str] = []
        for enabled, binary, name in ((gamemode, "gamemoderun", "GameMode"), (mangohud, "mangohud", "MangoHud")):
            if not enabled:
                continue
            reason = (
                "Windows" if is_windows()
                else "browser launch" if browser_launch
                # The Flatpak player runs inside the app, where host tools are absent.
                else "Flatpak web player" if flatpak_player
                else "sandboxed" if binary == "gamemoderun" and sandboxed
                # MangoHud's preload shim makes NW.js spin at startup with no window.
                else "NW.js hangs with it" if binary == "mangohud" and launch_mode == "rpgmaker_linux"
                else ""
            )
            if not reason:
                tool_path = host_which(binary)
                if tool_path:
                    performance_tools.append(tool_path)
                else:
                    reason = "not installed"
            if reason:
                note = f"{name} skipped: {reason}"
                performance_notes.append(note)
                print(note)
        # MangoHud is in effect when wLib wraps the launch, when the user's own
        # wrappers start it, or when the environment turns it on directly.
        user_wrappers = args[: args.index("%command%")] if "%command%" in args else args
        mangohud_active = (
            any(os.path.basename(tool) == "mangohud" for tool in performance_tools)
            or any(os.path.basename(arg) == "mangohud" for arg in user_wrappers)
            or env.get("MANGOHUD") == "1"
        )
        gamescope_cmd: list[str] = []
        if gamescope is not None:
            reason = gamescope_skip_reason(
                windows=is_windows(),
                browser_launch=browser_launch,
                flatpak_player=flatpak_player,
                user_args=args,
            )
            if not reason:
                gamescope_path = host_which("gamescope")
                if gamescope_path:
                    gamescope_cmd = gamescope_mod.gamescope_argv(
                        gamescope, display_info.screen_size(), gamescope_path
                    )
                else:
                    reason = "not installed"
            note = f"Gamescope skipped: {reason}" if reason else f"Gamescope: {shlex.join(gamescope_cmd)} --"
            performance_notes.append(note)
            print(note)
        block = bool(block_network and not browser_launch)
        if block and is_windows() and not sandboxed:
            return {
                "success": False,
                "code": "network_block_needs_sandbox",
                "error": "No internet needs Run in sandbox on Windows.",
            }
        network_status: game_sandbox.SandboxStatus | None = None
        if block and not is_windows() and not flatpak_player:
            # Results that were not Ready are never cached, so this re-probes them.
            network_status = game_sandbox.get_sandbox_status()
            if network_status["status"] != "ready":
                return {
                    "success": False,
                    "code": "network_block_unavailable",
                    "error": f"Cannot block network: {network_status['reason']}",
                }
        sandbox_status: game_sandbox.SandboxStatus | None = None
        if sandboxed and flatpak_player and (game_id is None or game_id <= 0):
            return {
                "success": False,
                "error": "Sandboxed games need a library game ID.",
            }
        if sandboxed and not flatpak_player:
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
            umu_run: bool = False,
            writable_paths: tuple[str, ...] = (),
            early_exit_seconds: float = 0.0,
        ) -> dict[str, object]:
            cmd = wrap_performance_tools(cmd, performance_tools, args)

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
                command = (
                    wine_context.command
                    if umu_run and wine_context
                    else ([proton_path] if proton_path else ["wine"])
                )
                return [*command, ce_exe]

            log_file = None
            effective_cwd = cwd_override or game_dir
            prefix = ""
            mount_prefix = ""
            if (
                is_wine_executable
                or user_env.get("WINEPREFIX")
                or user_env.get("STEAM_COMPAT_DATA_PATH")
            ):
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
                umu=umu_run,
                network_blocked=block,
            )
            # Serialize the prefix-tool check and running-game reservation.
            # Prefix tools take these locks in the same order.
            with self.prefix_guard_lock or nullcontext():
                if self.prefix_guard:
                    error = self.prefix_guard(prefix, game_id)
                    if error:
                        return {"success": False, "code": "prefix_in_use" if prefix else "game_busy", "error": error}
                with self._running_lock:
                    if game_id in self._running:
                        return {
                            "success": False,
                            "code": "already_running",
                            "error": "Game is already running",
                        }
                    conflict = self._prefix_conflict_locked(entry, game_id)
                    if conflict:
                        return {
                            "success": False,
                            "code": "prefix_in_use",
                            "error": conflict,
                        }
                    if game_id is not None:
                        self._running[game_id] = entry
                if self.external_guard:
                    error = self.external_guard(entry, game_id)
                    if error:
                        with self._running_lock:
                            if game_id is not None and self._running.get(game_id) is entry:
                                del self._running[game_id]
                        return {"success": False, "code": "prefix_in_use" if prefix else "game_busy", "error": error}
            try:
                if sandboxed and sandbox_status is not None and is_windows():
                    assert game_id is not None
                    # The game folder is a write-through path, so it must not be the profile.
                    game_sandbox.check_writable_mounts(
                        (game_dir, *writable_paths), os.path.expanduser("~")
                    )
                    entry.sandbox_box = game_sandbox.configure_sandboxie_box(
                        sandbox_status["path"],
                        game_id,
                        game_dir,
                        block_network=block,
                        extra_write_paths=writable_paths,
                    )
                    entry.sandbox_tool = sandbox_status["path"]
                    cmd = [entry.sandbox_tool, f"/box:{entry.sandbox_box}", *cmd]
                if sandboxed and sandbox_status is not None and not is_windows():
                    assert game_id is not None
                    if mount_prefix:
                        os.makedirs(mount_prefix, exist_ok=True)
                    readonly = runtime_paths
                    umu_data = umu_runtime = umu_var = ""
                    if umu_run:
                        from core.umu import umu_data_dir, runtime_status

                        umu_data = umu_data_dir()
                        umu_runtime = str(runtime_status(proton_path)["path"])
                        umu_var = os.path.join(
                            game_sandbox.sandbox_folder(game_id), "umu-var"
                        )
                        os.makedirs(umu_var, exist_ok=True)
                        readonly += (
                            os.path.dirname(env_vars["WLIB_UMU_RUN"]),
                            os.path.dirname(wine_context.command[1])
                            if wine_context
                            else "",
                        )
                        concrete_runner = env_vars.get("PROTONPATH", "")
                        if concrete_runner and os.path.isabs(concrete_runner):
                            readonly += (concrete_runner,)
                    if is_wine_executable and run_ce and not umu_run:
                        ce_command = prepare_ce_command()
                        if ce_command:
                            readonly += (get_cheat_engine_dir(),)
                            cmd = game_sandbox.cheat_engine_wrapper(cmd, ce_command)
                        run_ce = False
                    sandbox_game_dir = game_sandbox.resolve_game_folder(
                        exe_path, launch_mode
                    )
                    user_home = os.path.expanduser("~")
                    game_sandbox.check_writable_mounts(
                        (sandbox_game_dir, mount_prefix), user_home
                    )
                    host_nixos, host_links = host_system_layout()
                    mangohud_binds = (
                        game_sandbox.mangohud_mounts(env_vars, user_home)
                        if mangohud_active
                        else None
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
                        nixos=host_nixos,
                        system_links=host_links,
                        bwrap=sandbox_status["path"],
                        umu_data=umu_data,
                        umu_runtime=umu_runtime,
                        umu_var=umu_var,
                        network=block,
                        writable_paths=writable_paths,
                        x11_sockets=() if gamescope_cmd else host_x11_sockets(),
                        gamescope=bool(gamescope_cmd),
                        mangohud=mangohud_binds,
                    )
                    entry.kill_info = None
                elif block and not is_windows() and not flatpak_player:
                    assert network_status is not None
                    cmd = game_sandbox.build_network_bwrap_argv(
                        cmd,
                        cwd=effective_cwd,
                        bwrap=network_status["path"],
                        x11_sockets=() if gamescope_cmd else host_x11_sockets(),
                        gamescope=bool(gamescope_cmd),
                    )
                if gamescope_cmd:
                    if is_wine_executable and not sandboxed and not umu_run and kill_info:
                        cmd = ["sh", "-c", GAMESCOPE_WINE_WAIT, "sh", kill_info[0], *cmd]
                    # gamescope runs on the host, around bwrap when bwrap is used.
                    cmd = [*gamescope_cmd, "--", *cmd]
                if enable_logging:
                    log_path = path_module.splitext(exe_path)[0] + "_wlib.log"
                    try:
                        log_file = _open_debug_log(log_path)
                    except OSError:
                        # Read-only game folder, or the log name is a symlink
                        # (a sandboxed game can plant one): log to the temp
                        # dir instead of failing the launch.
                        log_path = path_module.join(
                            tempfile.gettempdir(), path_module.basename(log_path)
                        )
                        log_file = _open_debug_log(log_path)
                    print(f"Debug logging enabled. Outputting to {log_path}")
                    for note in performance_notes:
                        _ = log_file.write(note + "\n")
                    log_file.flush()

                # bwrap passes its environment on to the game.
                popen_env = (
                    game_sandbox.sandbox_environment(
                        env_vars,
                        extra_names=game_sandbox.PLAYER_ENV_NAMES
                        if web_kind == "builtin"
                        else frozenset(),
                    )
                    # flatpak-spawn passes only its --env values to the sub-sandbox,
                    # and needs the session bus to reach the Flatpak portal.
                    if sandboxed and not is_windows() and not flatpak_player
                    else env_vars
                )
                try:

                    def start_game(creationflags: int = 0) -> "subprocess.Popen[bytes]":
                        return subprocess.Popen(
                            cmd
                            if flatpak_player
                            else host_command(
                                cmd, env=popen_env, cwd=effective_cwd, supervise=True
                            ),
                            env=popen_env,
                            stdout=log_file
                            if log_file is not None
                            else subprocess.DEVNULL,
                            stderr=(
                                subprocess.STDOUT
                                if log_file is not None
                                else subprocess.DEVNULL
                            ),
                            cwd=effective_cwd,
                            start_new_session=not is_windows(),
                            creationflags=creationflags,
                        )

                    game_job: _WindowsJob | None = None
                    if sandboxed and not is_windows():
                        game_proc = _spawn_sandbox_process(start_game)
                    elif is_windows() and not sandboxed:
                        game_proc, game_job = _start_in_windows_job(start_game)
                    else:
                        game_proc = start_game()
                    entry.job = game_job
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
                if is_wine_executable and run_ce and not (sandboxed and umu_run):
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
                    list_failures = 0
                    killpg = getattr(os, "killpg", None)
                    pid = getattr(game_proc, "pid", None)

                    def group_alive() -> bool:
                        # Launcher stubs exit while their children keep the game running.
                        if is_windows():
                            job = entry.job
                            if job is None:
                                return False
                            game_pids = job.game_process_ids()
                            if game_pids is None:
                                return bool(job.active_processes())
                            return bool(game_pids)
                        if killpg is None or pid is None:
                            return False
                        try:
                            killpg(pid, 0)
                        except PermissionError:
                            return True  # exists, owned by another user
                        except OSError:
                            return False
                        if is_wine_executable and not sandboxed:
                            # wineserver and Wine's services stay in the group
                            # after the game exits; they are not the game.
                            members = _wine_group_pids(cast(int, pid))
                            if members is not None:
                                return bool(members[0])
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
                                    list_failures += 1
                                    print(f"Could not read sandbox process list: {exc}")
                                    # A process list that never comes back would
                                    # otherwise track this game forever.
                                    if list_failures >= SANDBOX_LIST_FAILURE_LIMIT:
                                        break
                                    pids = None
                                else:
                                    list_failures = 0
                                if pids:
                                    box_started = True
                                elif pids is not None and (
                                    box_started or time.monotonic() - start_time >= 5
                                ):
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
                            # Advance by whole seconds so the fraction carries
                            # into the next save instead of being dropped.
                            saved_until = last_saved_time + delta
                            with self._running_lock:
                                # shutdown() may have taken the entry and flushed it.
                                owned = (
                                    game_id is None
                                    or self._running.get(game_id) is entry
                                )
                                if owned:
                                    entry.last_saved_time = saved_until
                            if not owned:
                                break
                            last_saved_time = saved_until
                            if on_exit_callback is not None:
                                try:
                                    _ = on_exit_callback(delta, False)
                                except Exception as exc:
                                    print(f"Failed to record playtime: {exc}")
                    finally:
                        exit_code = game_proc.returncode
                        if isinstance(exit_code, int) and exit_code != 0:
                            print(f"Game process exited with status {exit_code}.")
                        if sandboxed and umu_run and game_id is not None:
                            for path in glob.glob(
                                os.path.join(
                                    game_sandbox.sandbox_folder(game_id),
                                    "umu-var",
                                    "tmp-*",
                                )
                            ):
                                if os.path.isdir(path) and not os.path.islink(path):
                                    shutil.rmtree(path, ignore_errors=True)
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

                if early_exit_seconds > 0:
                    # A sub-sandbox that cannot start exits at once; report that
                    # as a failed launch instead of a game that ended.
                    try:
                        _ = game_proc.wait(timeout=early_exit_seconds)
                    except subprocess.TimeoutExpired:
                        pass
                    else:
                        if game_proc.returncode != 0:
                            with self._running_lock:
                                if game_id is not None and self._running.get(game_id) is entry:
                                    del self._running[game_id]
                            if log_file is not None:
                                log_file.close()
                            return {
                                "success": False,
                                "error": (
                                    "The web player exited before its window opened "
                                    + f"(status {game_proc.returncode})."
                                ),
                            }

                threading.Thread(target=track_playtime_thread, daemon=True).start()

                if sandboxed and umu_run and run_ce:
                    return {
                        "success": True,
                        "notice": "Cheat Engine injection is unavailable for sandboxed Proton games.",
                    }
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
            runner_path = runner_status["path"]
            if sandboxed:
                # The sandbox binds only the runner's real folder (runtime_folder
                # resolves symlinks), so a symlink such as ~/.local/bin/<runner>
                # does not exist there. Run the target instead.
                runner_path = os.path.realpath(runner_path)
            base_cmd = [runner_path, "--gamepath", runner_game_dir]

            command = build_command(base_cmd, args)
            print(f"Executing via RPGMaker Linux runner: {' '.join(command)}")
            return execute_process(
                command,
                build_host_tool_env(env),
                cwd_override=runner_game_dir,
                runtime_paths=(game_sandbox.runtime_folder(runner_path),),
            )

        def execute_web_player() -> dict[str, object]:
            """Run the built-in player as a tracked process (sandbox, No internet, Stop)."""
            if game_id is not None and game_id > 0:
                profile = game_sandbox.web_player_folder(game_id)
            else:
                profile = tempfile.mkdtemp(prefix="wlib-web-player-")
            os.makedirs(profile, exist_ok=True)
            entry_name = path_module.basename(exe_path)
            player = [
                *web_player_command(),
                "--root",
                game_dir,
                "--entry",
                entry_name,
                "--profile",
                profile,
                "--title",
                game_title or entry_name,
            ]
            if ext == ".swf":
                player.append("--flash")
            if block:
                player.append("--offline")
            # Links cannot leave a sandboxed or offline player.
            player += ["--links", "ignore" if (sandboxed or block) else "open"]
            if web_player_devtools_enabled():
                player.append("--devtools")
            player_env = self_env(without_wine_proton_env(env.copy()))
            player_env.update(user_env)
            if sandboxed:
                # The sandbox is the security boundary; Chromium's own cannot nest.
                player_env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
            runtime_paths: tuple[str, ...] = ()
            command = player
            # In Flatpak, "Run in sandbox" and "No internet" both need a new
            # instance from the Flatpak portal; bubblewrap is not used.
            flatpak_spawned = flatpak_player and (sandboxed or block)
            if flatpak_player:
                if flatpak_spawned:
                    command = game_sandbox.flatpak_player_argv(
                        player,
                        game_dir=game_dir,
                        profile=profile,
                        network_blocked=block,
                        environment=player_env,
                        sandbox=sandboxed,
                    )
            elif sandboxed:
                runtime_paths = web_player_runtime_paths()
            print(f"Opening web game in the built-in player: {' '.join(player)}")
            result = execute_process(
                command,
                player_env,
                runtime_paths=runtime_paths,
                writable_paths=(profile,) if sandboxed and not flatpak_player else (),
                early_exit_seconds=2.0 if flatpak_spawned else 0.0,
            )
            if (
                result.get("success")
                and ext != ".swf"
                and not html_player
                and get_setting("html_player_notice_shown") != "true"
            ):
                from .database import update_setting

                update_setting("html_player_notice_shown", "true")
                result["notice"] = "html_player_first_use"
            return result

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
            if web_kind == "builtin":
                return execute_web_player()
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
            if web_kind == "builtin":
                return execute_web_player()
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

        from core.umu import (
            build_umu_invocation,
            locate_umu,
            resolve_runner,
            resolve_codename,
            runtime_status,
        )

        proton_path = resolve_runner(proton_path)

        # A WINEPREFIX= in the launch arguments is the most specific choice.
        # A sandboxed game never shares the global prefix; see
        # game_sandbox.sandbox_wine_prefix.
        wine_prefix = path_module.expanduser(
            (
                user_env.get("WINEPREFIX")
                or (
                    custom_prefix
                    if is_windows()
                    else game_sandbox.sandboxed_prefix_override(
                        custom_prefix, sandboxed, game_id
                    )
                )
                or get_setting("wine_prefix_path")
                or ""
            ).strip()
        )
        if wine_prefix:
            wine_prefix = path_module.abspath(wine_prefix)
            if (
                not is_windows()
                and sandboxed
                and game_id is not None
                and wine_prefix == game_sandbox.sandbox_wine_prefix(game_id)
            ):
                # wLib owns this prefix, so it creates it like the default one.
                os.makedirs(wine_prefix, exist_ok=True)

        if not wine_prefix:
            # We must supply a prefix for Proton to function at all.
            # If the user didn't set one, use a default wLib specific one
            wine_prefix = get_default_wine_prefix()
            os.makedirs(wine_prefix, exist_ok=True)

        is_proton = (
            proton_path and "proton" in path_module.basename(proton_path).lower()
        )

        base_cmd = [proton_path, exe_path]
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
            if block and proton_path in ("GE-Proton", "UMU-Proton"):
                try:
                    proton_path = resolve_codename(proton_path)
                except ValueError:
                    proton_path = ""
                if not os.path.isabs(proton_path):
                    return {
                        "success": False,
                        "code": "proton_missing_offline",
                        "error": "Install or select a local Proton build before launching with No internet.",
                    }
            if not locate_umu()[0]:
                return {
                    "success": False,
                    "code": "umu_missing",
                    "error": "umu-launcher is missing. Install it in Settings.",
                }
            if not runtime_status(proton_path)["ready"]:
                return {
                    "success": False,
                    "code": "umu_runtime_missing",
                    "error": "Prepare the Steam Linux Runtime in Settings before launching.",
                }
            try:
                base_cmd, env = build_umu_invocation(
                    exe_path,
                    prefix=wine_prefix,
                    runner=proton_path,
                    game_id=game_id,
                    game_dir=game_dir,
                    environment=env,
                    sandboxed=sandboxed,
                    log_dir=game_dir if enable_logging else "",
                )
            except (ValueError, OSError) as exc:
                return {"success": False, "error": str(exc)}
            wine_prefix = env["WINEPREFIX"]
            command = build_command(base_cmd, args)
            if os.path.exists(path_module.join(game_dir, "nw.dll")) and os.path.exists(
                path_module.join(game_dir, "www")
            ):
                command.extend(["--disable-gpu-sandbox", "--no-sandbox"])
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
        # Follow a symlinked runner to the build that owns it, so its sibling
        # wineserver is found instead of a different install on PATH.
        runner_path = proton_path
        if proton_path and path_module.sep not in proton_path:
            runner_path = host_which(proton_path) or proton_path
        # Only resolve with the host's own path rules; tests emulate a Linux
        # host on Windows, where os.path.realpath would rewrite POSIX paths.
        if runner_path and path_module is os.path:
            runner_path = os.path.realpath(runner_path)
        if is_proton and runner_path:
            for layout in ("files", "dist"):
                candidate = path_module.join(
                    path_module.dirname(runner_path), layout, "bin", "wineserver"
                )
                if os.path.isfile(candidate):
                    wineserver = candidate
                    break
        else:
            # Wine builds ship wineserver beside wine; otherwise use the one on PATH.
            sibling = (
                path_module.join(path_module.dirname(runner_path), "wineserver")
                if runner_path and path_module.dirname(runner_path)
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
            umu_run=bool(is_proton),
            run_ce=auto_inject_ce,
            game_exe=game_exe_name,
            kill_info=(wineserver, kill_env) if wineserver else None,
            runtime_paths=(game_sandbox.runtime_folder(proton_path),)
            if proton_path and path_module.isabs(proton_path)
            else (),
        )
