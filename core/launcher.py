# SPDX-License-Identifier: GPL-3.0-or-later
import ntpath
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Protocol, TypedDict, cast, final

from .database import RPGMAKER_LINUX_RUNNER_SETTING, get_setting, normalize_launch_mode
from .host_platform import (
    get_cheat_engine_dir,
    get_default_wine_prefix,
    is_windows,
    open_windows_system_target,
)


def _host_path_module():
    return ntpath if is_windows() else posixpath


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


class Launcher:
    def __init__(self):
        pass

    def _resolve_runner_candidate_path(self, raw_path: str) -> str:
        path_module = _host_path_module()
        expanded_path = path_module.expanduser(raw_path.strip())
        if path_module.sep not in expanded_path and not expanded_path.startswith("."):
            path_match = shutil.which(expanded_path)
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
        path_runner = shutil.which("rpgmaker-linux")
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
    ) -> dict[str, object]:
        """
        Launches the given executable natively if it's a Linux binary, .sh, or .jar.
        Otherwise, launches using the configured Proton/Wine path.
        """
        if not isinstance(exe_path, str) or not exe_path.strip():
            return {
                "success": False,
                "error": "Executable path must be a non-empty string",
            }

        exe_path = exe_path.strip()

        if not os.path.exists(exe_path):
            print(f"Error: Executable not found at {exe_path}")
            return {"success": False, "error": f"Executable not found at {exe_path}"}

        # Keep path semantics tied to the selected host capability, not to the
        # Python process running the tests. This also lets Windows CI exercise
        # the Linux compatibility path without rewriting POSIX paths.
        path_module = _host_path_module()
        exe_path = path_module.abspath(exe_path)
        launch_mode = normalize_launch_mode(launch_mode)

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

        if "%command%" in args:
            while args and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[0]):
                key, value = args.pop(0).split("=", 1)
                env[key] = value

        ext = path_module.splitext(exe_path)[1].lower()
        enable_logging = get_setting("enable_logging") == "true"
        game_dir = path_module.dirname(exe_path)

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
        ) -> dict[str, object]:
            import time
            import threading

            log_file = None
            try:
                if enable_logging:
                    log_path = path_module.splitext(exe_path)[0] + "_wlib.log"
                    print(f"Debug logging enabled. Outputting to {log_path}")
                    log_file = open(log_path, "w")

                try:
                    game_proc = subprocess.Popen(
                        cmd,
                        env=env_vars,
                        stdout=log_file if log_file is not None else subprocess.DEVNULL,
                        stderr=(
                            subprocess.STDOUT
                            if log_file is not None
                            else subprocess.DEVNULL
                        ),
                        cwd=game_dir,
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
                    game_proc = _ElevatedProcess(cmd, game_dir)

                # Do not count time spent waiting for UAC approval as playtime.
                start_time = time.time()
                if log_file is not None:
                    # Ensure the file gets closed when process finishes in the background
                    def track_log_file():
                        _ = game_proc.wait()
                        log_file.close()

                    threading.Thread(target=track_log_file, daemon=True).start()

                if is_wine_executable and run_ce:
                    # Spawn CE in a background thread after a delay
                    def inject_ce():
                        import time

                        print(
                            "Waiting 5 seconds for game to initialize before attaching Cheat Engine..."
                        )
                        time.sleep(5)

                        ce_dir = get_cheat_engine_dir()
                        ce_exe = path_module.join(
                            ce_dir, "Lunar Engine", "lunarengine-x86_64.exe"
                        )
                        if not os.path.exists(ce_exe):
                            ce_exe = path_module.join(ce_dir, "lunarengine-x86_64.exe")

                        if os.path.exists(ce_exe):
                            # Write autorun lua script to auto-attach
                            autorun_dir = path_module.join(
                                path_module.dirname(ce_exe), "autorun"
                            )
                            os.makedirs(autorun_dir, exist_ok=True)
                            lua_script = path_module.join(
                                autorun_dir, "wlib_autoattach.lua"
                            )
                            with open(lua_script, "w") as f:
                                # OpenProcess automatically attaches CE to the process name
                                safe_game_exe = (
                                    (game_exe or "")
                                    .replace("\n", "")
                                    .replace("\r", "")
                                    .replace("\\", "\\\\")
                                    .replace('"', '\\"')
                                )
                                _ = f.write(f'OpenProcess("{safe_game_exe}")\n')

                            print(
                                f"Launching Cheat Engine: {ce_exe} in WINEPREFIX {env_vars.get('WINEPREFIX', 'default')}"
                            )
                            # Launch CE using the SAME wine/proton command prefix
                            ce_cmd = [proton_path] if proton_path else ["wine"]
                            if is_proton:
                                ce_cmd.append("run")
                            ce_cmd.append(ce_exe)

                            _ = subprocess.Popen(
                                ce_cmd,
                                env=env_vars,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        else:
                            print(
                                f"Cheat Engine executable not found for auto-injection at {ce_exe}"
                            )

                    import threading

                    threading.Thread(target=inject_ce, daemon=True).start()

                if on_exit_callback:

                    def track_playtime_thread():
                        last_saved_time = start_time
                        while game_proc.poll() is None:
                            try:
                                _ = game_proc.wait(timeout=60)
                                # Process exited cleanly during wait
                                break
                            except subprocess.TimeoutExpired:
                                pass
                            now = time.time()
                            delta = int(now - last_saved_time)
                            last_saved_time = now
                            if on_exit_callback is not None:
                                _ = on_exit_callback(delta, False)

                        now = time.time()
                        delta = int(now - last_saved_time)
                        if delta > 0:
                            if on_exit_callback is not None:
                                _ = on_exit_callback(delta, True)
                        else:
                            if on_exit_callback is not None:
                                _ = on_exit_callback(0, True)

                    threading.Thread(target=track_playtime_thread, daemon=True).start()

                if log_file is None and not on_exit_callback:
                    threading.Thread(target=game_proc.wait, daemon=True).start()

                return {"success": True}
            except Exception as e:
                if log_file is not None:
                    log_file.close()
                print(f"Error launching game: {e}")
                return {"success": False, "error": str(e)}

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
                runtime_dirs.add(
                    path_module.abspath(path_module.expanduser(meipass))
                )

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
            ):
                _ = clean_env.pop(var, None)

            original_library_path = str(
                clean_env.pop("LD_LIBRARY_PATH_ORIG", "") or ""
            ).strip()
            if original_library_path:
                clean_env["LD_LIBRARY_PATH"] = original_library_path
            else:
                library_path = str(clean_env.get("LD_LIBRARY_PATH") or "")
                library_paths = [
                    entry
                    for entry in library_path.split(path_module.pathsep)
                    if entry
                    and not is_packaged_runtime_library_path(entry)
                ]
                if library_paths:
                    clean_env["LD_LIBRARY_PATH"] = path_module.pathsep.join(
                        library_paths
                    )
                elif appimage_context or library_path:
                    _ = clean_env.pop("LD_LIBRARY_PATH", None)

            if appimage_context and not str(clean_env.get("LD_LIBRARY_PATH") or ""):
                _ = clean_env.pop("LD_LIBRARY_PATH", None)
            return clean_env

        def build_host_tool_env(
            env_vars: dict[str, str], strip_wine_env: bool = True
        ) -> dict[str, str]:
            clean_env = without_packaged_runtime_env(env_vars)
            if strip_wine_env:
                clean_env = without_wine_proton_env(clean_env)
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
            base_cmd = [runner_status["path"], "--gamepath", runner_game_dir]

            command = build_command(base_cmd, args)
            print(f"Executing via RPGMaker Linux runner: {' '.join(command)}")
            return execute_process(command, build_host_tool_env(env))

        def execute_html_game(strip_wine_env: bool = False) -> dict[str, object]:
            # Convert to absolute path and file:// URL for proper browser handling
            abs_path = path_module.abspath(exe_path)
            normalized_url_path = abs_path.replace(chr(92), "/")
            file_url = (
                f"file:///{normalized_url_path.lstrip('/')}"
                if is_windows()
                else f"file://{normalized_url_path}"
            )
            if is_windows():
                opened, error = open_windows_system_target(abs_path)
                if opened:
                    print(f"Opening HTML game in default browser: {file_url}")
                    return {"success": True}
                return {
                    "success": False,
                    "error": f"Failed to open HTML game: {error}",
                }

            command = ["xdg-open", file_url]
            print(f"Opening HTML game in default browser: {file_url}")
            try:
                # Use clean environment without Wine/Proton/AppImage variables that might interfere
                clean_env = build_host_tool_env(
                    os.environ.copy(), strip_wine_env=strip_wine_env
                )

                _ = subprocess.Popen(
                    command,
                    env=clean_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=path_module.dirname(abs_path),
                )
                return {"success": True}
            except Exception as e:
                print(f"Error launching HTML game: {e}")
                return {"success": False, "error": str(e)}

        def execute_host_native(strict_native: bool) -> dict[str, object] | None:
            process_env = build_host_tool_env(env)

            # 1. Native Shell Script (e.g. Ren'Py)
            if ext == ".sh":
                if strict_native and not os.access(exe_path, os.X_OK):
                    return {
                        "success": False,
                        "error": (
                            "Linux Native launch mode requires executable "
                            f"permission for {exe_path}"
                        ),
                    }
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
                return execute_html_game(strip_wine_env=strict_native)

            # 4. Native Linux Binary (Executable without .exe/.bat extension)
            # Some native Linux games like Godot have no extension or .x86_64.
            if os.access(exe_path, os.X_OK) and ext not in [".exe", ".bat"]:
                command = build_command([exe_path], args)
                print(f"Executing Linux binary natively: {' '.join(command)}")
                return execute_process(command, process_env)

            if strict_native:
                if ext in [".exe", ".bat"]:
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
            if launch_mode != "auto":
                return {
                    "success": False,
                    "error": (
                        f"Launch mode '{launch_mode}' is only available on Linux. "
                        "Choose Auto Detect to launch this game directly on Windows."
                    ),
                    "code": "unsupported_platform",
                }

            process_env = without_wine_proton_env(env)
            if ext == ".exe":
                return execute_process(
                    build_command([exe_path], args), process_env
                )
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
                return execute_process(
                    build_command(base_command, args), process_env
                )
            if ext == ".jar":
                return execute_process(
                    build_command(["java", "-jar", exe_path], args), process_env
                )
            if ext in (".html", ".htm"):
                return execute_html_game(strip_wine_env=True)
            return {
                "success": False,
                "error": f"Unsupported Windows game target: {exe_path}",
            }

        if launch_mode in ("auto", "native"):
            native_result = execute_host_native(strict_native=launch_mode == "native")
            if native_result is not None:
                return native_result

        if launch_mode == "rpgmaker_linux":
            return execute_rpgmaker_linux_runner()

        # 5. Fallback to Wine / Proton execution for Windows executables
        env = without_packaged_runtime_env(env)
        proton_path = proton_version if proton_version else get_setting("proton_path")
        wine_prefix = (
            custom_prefix if custom_prefix else get_setting("wine_prefix_path")
        )

        if not wine_prefix:
            # We must supply a prefix for Proton to function at all.
            # If the user didn't set one, use a default wLib specific one
            wine_prefix = get_default_wine_prefix()
            os.makedirs(wine_prefix, exist_ok=True)

        is_proton = proton_path and "proton" in path_module.basename(proton_path).lower()

        base_cmd = [proton_path] if proton_path else ["wine"]
        if is_proton:
            base_cmd.append("run")

        base_cmd.append(exe_path)
        command = build_command(base_cmd, args)

        # Apply global DLL overrides required for various titles
        global_overrides = "mscoree=n,b;msvcrt=b,n;winhttp=n,b"
        existing_overrides = env.get("WINEDLLOVERRIDES", "")
        if existing_overrides:
            env["WINEDLLOVERRIDES"] = f"{existing_overrides};{global_overrides}"
        else:
            env["WINEDLLOVERRIDES"] = global_overrides

        # Auto-detect engines for specific launcher arguments
        # RPGMaker MV / MZ (NW.js Chromium)
        if os.path.exists(path_module.join(game_dir, "nw.dll")) and os.path.exists(
            path_module.join(game_dir, "www")
        ):
            print(
                "Detected RPGMaker MV/MZ. Applying winegstreamer override and NW.js flags..."
            )
            existing_overrides = env.get("WINEDLLOVERRIDES", "")
            additional_overrides = "winegstreamer=d"
            if existing_overrides:
                env["WINEDLLOVERRIDES"] = f"{existing_overrides};{additional_overrides}"
            else:
                env["WINEDLLOVERRIDES"] = additional_overrides
            # Add NW.js/Chromium flags for better Wine compatibility
            command.extend(["--disable-gpu-sandbox", "--no-sandbox"])

        # Proton and Wine prefix handling
        if is_proton:
            # Prevent proton from polluting a standard wine prefix
            if os.path.isdir(
                path_module.join(wine_prefix, "drive_c")
            ) and not os.path.isdir(path_module.join(wine_prefix, "pfx")):
                print(
                    "Warning: Standard Wine prefix detected. Creating isolated proton directory."
                )
                wine_prefix = path_module.join(wine_prefix, "proton_compat")
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
            env["WINEDEBUG"] = "+all"

        print(
            f"Executing via Wine/Proton: {' '.join(command)} with prefix {wine_prefix}"
        )
        game_exe_name = path_module.basename(exe_path)
        return execute_process(
            command,
            env,
            is_wine_executable=True,
            run_ce=auto_inject_ce,
            game_exe=game_exe_name,
        )
