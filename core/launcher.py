import subprocess
import os
from .database import get_setting


class Launcher:
    def __init__(self):
        pass

    def launch(
        self,
        exe_path: str,
        command_line_args: str = "",
        run_japanese_locale: bool = False,
        run_wayland: bool = False,
        auto_inject_ce: bool = False,
        custom_prefix: str = "",
        proton_version: str = "",
        on_exit_callback=None,
    ):
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

        env = os.environ.copy()

        # Apply Japanese locale to the environment if requested
        if run_japanese_locale:
            print("Applying Japanese locale (ja_JP.UTF-8)")
            env["LC_ALL"] = "ja_JP.UTF-8"
            env["LANG"] = "ja_JP.UTF-8"

        # Apply Wayland compatibility environment variables if requested
        if run_wayland:
            print("Applying Wayland compatibility mode")
            env["MESA_VK_WSI_PRESENT_MODE"] = "immediate"
            env["vk_xwayland_wait_ready"] = "false"
            env["SDL_VIDEODRIVER"] = ""

        import shlex

        if command_line_args is None:
            command_line_args = ""
        elif not isinstance(command_line_args, str):
            command_line_args = str(command_line_args)

        try:
            args = shlex.split(command_line_args)
        except ValueError as exc:
            return {"success": False, "error": f"Invalid command line arguments: {exc}"}

        ext = os.path.splitext(exe_path)[1].lower()
        enable_logging = get_setting("enable_logging") == "true"
        game_dir = os.path.dirname(exe_path)

        # Helper to apply Steam-style %command% substitution
        def build_command(base_cmd, user_args):
            if "%command%" in user_args:
                idx = user_args.index("%command%")
                return user_args[:idx] + base_cmd + user_args[idx + 1 :]
            return base_cmd + user_args

        # Helper for common subprocess execution
        def execute_process(
            cmd, env_vars, is_wine_executable=False, run_ce=False, game_exe=""
        ):
            import time
            import threading

            start_time = time.time()
            try:
                if enable_logging:
                    log_path = os.path.splitext(exe_path)[0] + "_wlib.log"
                    print(f"Debug logging enabled. Outputting to {log_path}")
                    log_file = open(log_path, "w")
                    game_proc = subprocess.Popen(
                        cmd, env=env_vars, stdout=log_file, stderr=subprocess.STDOUT
                    )

                    # Ensure the file gets closed when process finishes in the background
                    def track_log_file():
                        game_proc.wait()
                        log_file.close()

                    import threading

                    threading.Thread(target=track_log_file, daemon=True).start()
                else:
                    game_proc = subprocess.Popen(
                        cmd,
                        env=env_vars,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

                if is_wine_executable and run_ce:
                    # Spawn CE in a background thread after a delay
                    def inject_ce():
                        import time

                        print(
                            "Waiting 5 seconds for game to initialize before attaching Cheat Engine..."
                        )
                        time.sleep(5)

                        ce_dir = os.path.expanduser("~/.local/share/wLib/CheatEngine")
                        ce_exe = os.path.join(
                            ce_dir, "Lunar Engine", "lunarengine-x86_64.exe"
                        )
                        if not os.path.exists(ce_exe):
                            ce_exe = os.path.join(ce_dir, "lunarengine-x86_64.exe")

                        if os.path.exists(ce_exe):
                            # Write autorun lua script to auto-attach
                            autorun_dir = os.path.join(
                                os.path.dirname(ce_exe), "autorun"
                            )
                            os.makedirs(autorun_dir, exist_ok=True)
                            lua_script = os.path.join(
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
                                f.write(f'OpenProcess("{safe_game_exe}")\n')

                            print(
                                f"Launching Cheat Engine: {ce_exe} in WINEPREFIX {env_vars.get('WINEPREFIX', 'default')}"
                            )
                            # Launch CE using the SAME wine/proton command prefix
                            ce_cmd = [proton_path] if proton_path else ["wine"]
                            if is_proton:
                                ce_cmd.append("run")
                            ce_cmd.append(ce_exe)

                            subprocess.Popen(
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
                                game_proc.wait(timeout=60)
                                # Process exited cleanly during wait
                                break
                            except subprocess.TimeoutExpired:
                                pass
                            now = time.time()
                            delta = int(now - last_saved_time)
                            last_saved_time = now
                            if on_exit_callback is not None:
                                on_exit_callback(delta, is_final=False)

                        now = time.time()
                        delta = int(now - last_saved_time)
                        if delta > 0:
                            if on_exit_callback is not None:
                                on_exit_callback(delta, is_final=True)
                        else:
                            if on_exit_callback is not None:
                                on_exit_callback(0, is_final=True)

                    threading.Thread(target=track_playtime_thread, daemon=True).start()

                if not enable_logging and not on_exit_callback:
                    threading.Thread(target=game_proc.wait, daemon=True).start()

                return {"success": True}
            except Exception as e:
                print(f"Error launching game: {e}")
                return {"success": False, "error": str(e)}

        # 1. Native Shell Script (e.g. Ren'Py)
        if ext == ".sh":
            command = build_command([exe_path], args)
            print(f"Executing shell script natively: {' '.join(command)}")
            return execute_process(command, env)

        # 2. Java Archives (.jar)
        if ext == ".jar":
            command = build_command(["java", "-jar", exe_path], args)
            print(f"Executing Java archive: {' '.join(command)}")
            return execute_process(command, env)

        # 3. Native Linux Binary (Executable without .exe/.bat extension or standard Linux build)
        # Some native Linux games like Godot have no extension or .x86_64
        if os.access(exe_path, os.X_OK) and ext not in [".exe", ".bat"]:
            command = build_command([exe_path], args)
            print(f"Executing Linux binary natively: {' '.join(command)}")
            return execute_process(command, env)

        # 4. Fallback to Wine / Proton execution for Windows executables
        proton_path = proton_version if proton_version else get_setting("proton_path")
        wine_prefix = (
            custom_prefix if custom_prefix else get_setting("wine_prefix_path")
        )

        if not wine_prefix:
            # We must supply a prefix for Proton to function at all.
            # If the user didn't set one, use a default wLib specific one
            wine_prefix = os.path.expanduser("~/.local/share/wLib/prefix")
            os.makedirs(wine_prefix, exist_ok=True)

        is_proton = proton_path and "proton" in os.path.basename(proton_path).lower()

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
        if os.path.exists(os.path.join(game_dir, "nw.dll")) and os.path.exists(
            os.path.join(game_dir, "www")
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
                os.path.join(wine_prefix, "drive_c")
            ) and not os.path.isdir(os.path.join(wine_prefix, "pfx")):
                print(
                    "Warning: Standard Wine prefix detected. Creating isolated proton directory."
                )
                wine_prefix = os.path.join(wine_prefix, "proton_compat")
                os.makedirs(wine_prefix, exist_ok=True)

            env["STEAM_COMPAT_DATA_PATH"] = wine_prefix
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = "/tmp/wlib"
        else:
            # If the selected prefix is actually a Proton prefix (contains pfx subfolder),
            # standard Wine must point directly to the pfx subfolder
            if os.path.isdir(os.path.join(wine_prefix, "pfx")):
                wine_prefix = os.path.join(wine_prefix, "pfx")

            env["WINEPREFIX"] = wine_prefix

        if enable_logging:
            env["PROTON_LOG"] = "1"
            env["PROTON_LOG_DIR"] = game_dir
            env["WINEDEBUG"] = "+all"

        print(
            f"Executing via Wine/Proton: {' '.join(command)} with prefix {wine_prefix}"
        )
        game_exe_name = os.path.basename(exe_path)
        return execute_process(
            command,
            env,
            is_wine_executable=True,
            run_ce=auto_inject_ce,
            game_exe=game_exe_name,
        )
