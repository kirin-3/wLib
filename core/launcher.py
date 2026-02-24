import subprocess
import os
from .database import get_setting

class Launcher:
    def __init__(self):
        pass

    def launch(self, exe_path: str):
        """
        Launches the given executable using the configured Proton/Wine path
        and the default Wine prefix.
        """
        if not os.path.exists(exe_path):
            print(f"Error: Executable not found at {exe_path}")
            return {"success": False, "error": f"Executable not found at {exe_path}"}

        proton_path = get_setting("proton_path")
        wine_prefix = get_setting("wine_prefix_path")
        
        if not wine_prefix:
            # We must supply a prefix for Proton to function at all. 
            # If the user didn't set one, use a default wLib specific one
            wine_prefix = os.path.expanduser("~/.local/share/wLib/prefix")
            os.makedirs(wine_prefix, exist_ok=True)

        is_proton = proton_path and "proton" in os.path.basename(proton_path).lower()

        command = [proton_path] if proton_path else ["wine"]
        if is_proton:
            command.append("run")
            
        command.append(exe_path)
        
        env = os.environ.copy()
        
        enable_logging = get_setting("enable_logging") == "true"
        
        # Auto-detect engines for specific launcher arguments
        game_dir = os.path.dirname(exe_path)
        
        # RPGMaker MV / MZ (NW.js Chromium)
        if os.path.exists(os.path.join(game_dir, "nw.dll")) and os.path.exists(os.path.join(game_dir, "www")):
            print("Detected RPGMaker MV/MZ. Applying libglesv2 and winegstreamer overrides...")
            # Disable libglesv2/libegl to prevent ANGLE from crashing DXVK/Wine
            # Disable winegstreamer to prevent Proton-GE from hanging on missing 32-bit host dependencies (forces fallback to game's own ffmpeg.dll)
            existing_overrides = env.get("WINEDLLOVERRIDES", "")
            additional_overrides = "libglesv2=d;libegl=d;winegstreamer=d"
            if existing_overrides:
                env["WINEDLLOVERRIDES"] = f"{existing_overrides};{additional_overrides}"
            else:
                env["WINEDLLOVERRIDES"] = additional_overrides
        
        # Proton and Wine prefix handling
        if is_proton:
            # Proton expects STEAM_COMPAT_DATA_PATH which contains a 'pfx' folder
            env["STEAM_COMPAT_DATA_PATH"] = wine_prefix
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = "/tmp/wlib"
        else:
            env["WINEPREFIX"] = wine_prefix

        print(f"Executing: {' '.join(command)} with prefix {wine_prefix}")
        
        try:
            if enable_logging:
                game_dir = os.path.dirname(exe_path)
                env["PROTON_LOG"] = "1"
                env["PROTON_LOG_DIR"] = game_dir
                env["WINEDEBUG"] = "+all"
                log_path = os.path.splitext(exe_path)[0] + "_wlib.log"
                print(f"Debug logging enabled. Outputting to {log_path}")
                log_file = open(log_path, "w")
                subprocess.Popen(
                    command, 
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT
                )
            else:
                subprocess.Popen(
                    command, 
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            return {"success": True}
        except Exception as e:
            print(f"Error launching game: {e}")
            return {"success": False, "error": str(e)}
