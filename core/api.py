from core.database import init_db
from core.scraper import Scraper
from core.launcher import Launcher
import sys
import os

APP_VERSION = "0.2.7"


class Api:
    def __init__(self):
        self.window = None
        # Make sure our database is initialized
        init_db()
        self.scraper = Scraper()
        self.launcher = Launcher()
        self._update_running = False
        self._update_total = 0
        self._update_checked = 0
        self._update_current = ""
        self._update_results = []
        self._deps_install_status = {
            "running": False,
            "done": 0,
            "total": 0,
            "current": "",
            "error": "",
        }
        self._rtp_install_status = {
            "running": False,
            "done": 0,
            "total": 0,
            "current": "",
            "error": "",
        }

    def _load_webview_module(self):
        import importlib

        try:
            return importlib.import_module("webview")
        except Exception:
            return None

    def set_window(self, window):
        self.window = window

    def open_extension_folder(self):
        import subprocess, shutil, json

        # Source: bundled extension inside the AppImage / dev source
        if getattr(sys, "frozen", False):
            bundle_root = getattr(
                sys,
                "_MEIPASS",
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            bundled_ext_dir = os.path.join(bundle_root, "extension")
        else:
            bundled_ext_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension"
            )
        # Target: persistent location in user data dir
        persistent_ext_dir = os.path.expanduser("~/.local/share/wLib/extension")

        chrome_dir = os.path.join(persistent_ext_dir, "chrome")
        firefox_dir = os.path.join(persistent_ext_dir, "firefox")

        # Copy or update extension to persistent location
        try:
            needs_copy = False
            if not os.path.isdir(chrome_dir) or not os.path.isfile(
                os.path.join(firefox_dir, "wLib.xpi")
            ):
                needs_copy = True
            else:
                # Compare manifest.json versions to detect updates
                bundled_manifest = os.path.join(bundled_ext_dir, "manifest.json")
                installed_manifest = os.path.join(chrome_dir, "manifest.json")
                if os.path.isfile(bundled_manifest) and os.path.isfile(
                    installed_manifest
                ):
                    with open(bundled_manifest) as f:
                        bundled_ver = json.load(f).get("version", "0")
                    with open(installed_manifest) as f:
                        installed_ver = json.load(f).get("version", "0")
                    if bundled_ver != installed_ver:
                        needs_copy = True
                elif os.path.isfile(bundled_manifest):
                    needs_copy = True

            if needs_copy and os.path.isdir(bundled_ext_dir):
                if os.path.exists(persistent_ext_dir):
                    shutil.rmtree(persistent_ext_dir)
                os.makedirs(persistent_ext_dir, exist_ok=True)

                # Setup Chrome folder
                shutil.copytree(bundled_ext_dir, chrome_dir)

                # Strip out "scripts" from Chrome manifest (v3 compatibility)
                chrome_manifest_path = os.path.join(chrome_dir, "manifest.json")
                with open(chrome_manifest_path, "r") as f:
                    chrome_manifest = json.load(f)

                if (
                    "background" in chrome_manifest
                    and "scripts" in chrome_manifest["background"]
                ):
                    del chrome_manifest["background"]["scripts"]
                    with open(chrome_manifest_path, "w") as f:
                        json.dump(chrome_manifest, f, indent=4)

                # Setup Firefox folder
                os.makedirs(firefox_dir, exist_ok=True)
                xpi_path = os.path.join(firefox_dir, "wLib")
                shutil.make_archive(xpi_path, "zip", bundled_ext_dir)
                os.rename(xpi_path + ".zip", xpi_path + ".xpi")
        except Exception as e:
            print(f"[wLib] Failed to sync extension: {e}")
            return {"success": False, "error": str(e)}

        # Open the persistent extension folder
        for cmd in ["xdg-open", "gio", "kde-open5", "gnome-open"]:
            binary = shutil.which(cmd)
            if binary:
                if cmd == "gio":
                    subprocess.Popen([binary, "open", persistent_ext_dir])
                else:
                    subprocess.Popen([binary, persistent_ext_dir])
                return {"success": True}
        return {"success": False, "error": "No file manager opener found"}

    # ==========================
    # Database / Game API
    # ==========================
    def get_games(self):
        from core.database import get_all_games

        return get_all_games()

    def add_game(
        self,
        title,
        exe_path,
        f95_url="",
        version="",
        cover_image="",
        tags="",
        rating="",
        developer="",
        engine="",
        run_japanese_locale=False,
        run_wayland=False,
        auto_inject_ce=False,
        custom_prefix="",
        proton_version="",
    ):
        from core.database import add_game

        game_id = add_game(
            title,
            exe_path,
            f95_url,
            version=version,
            cover_image=cover_image,
            tags=tags,
            rating=rating,
            developer=developer,
            engine=engine,
            run_japanese_locale=run_japanese_locale,
            run_wayland=run_wayland,
            auto_inject_ce=auto_inject_ce,
            custom_prefix=custom_prefix,
            proton_version=proton_version,
        )
        return {"id": game_id, "title": title}

    def delete_game(self, game_id):
        from core.database import delete_game

        delete_game(game_id)
        return {"success": True}

    def update_game(self, game_id, fields):
        from core.database import update_game

        update_game(game_id, fields)
        return {"success": True}

    # ==========================
    # Scraper API
    # ==========================
    def check_for_updates(self, url):
        """
        Uses Playwright to hit F95Zone and extract the version string from the thread title.
        Stores it in latest_version for comparison with the current version.
        """
        if not isinstance(url, str) or not url.strip():
            return {"success": False, "error": "A valid thread URL is required"}

        url = url.strip()
        print(f"Checking for updates for {url}")
        try:
            version_info = self.scraper.get_thread_version(url, headless=True)
            if not version_info.get("success"):
                return {
                    "success": False,
                    "error": version_info.get("error", "Failed to check for updates"),
                }

            remote_version = version_info.get("version")
            if not remote_version:
                return {
                    "success": False,
                    "error": "Could not extract a version from thread",
                }

            # Store in latest_version field
            from core.database import get_connection

            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE games SET latest_version = ? WHERE f95_url = ?",
                    (remote_version, url),
                )
                # Check if it differs from current version
                cursor.execute("SELECT version FROM games WHERE f95_url = ?", (url,))
                row = cursor.fetchone()
                current_version = row[0] if row and row[0] is not None else ""
                conn.commit()
            finally:
                if conn is not None:
                    conn.close()

            has_update = bool(
                current_version
                and remote_version
                and str(current_version).strip() != str(remote_version).strip()
            )
            return {
                "success": True,
                "version": remote_version,
                "has_update": has_update,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_in_browser(self, url):
        """Opens a URL in the user's default browser and brings it to foreground."""
        import webbrowser

        webbrowser.open(url)
        return {"success": True}

    def check_all_updates(self):
        """Start a background thread to check all games for updates, 15s apart."""
        import threading

        if hasattr(self, "_update_running") and self._update_running:
            return {"success": False, "error": "Update check already in progress"}

        from core.database import get_all_games

        all_games = get_all_games()
        games_with_url = [g for g in all_games if g.get("f95_url")]

        self._update_running = True
        self._update_total = len(games_with_url)
        self._update_checked = 0
        self._update_current = ""
        self._update_results = []

        # Record the check timestamp
        from datetime import datetime
        from core.database import get_connection

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('last_update_check', ?)",
                (datetime.now().isoformat(),),
            )
            conn.commit()
        except Exception as e:
            print(f"[wLib] Failed to update last_update_check setting: {e}")
        finally:
            if conn is not None:
                conn.close()

        def run_checks():
            try:
                urls = [game["f95_url"] for game in games_with_url]

                def check_callback(url, result):
                    if not self._update_running:
                        return False  # Stop checking

                    game = next(
                        (g for g in games_with_url if g["f95_url"] == url), None
                    )
                    if not game:
                        return True

                    self._update_current = game.get("title", "Unknown")

                    has_update = False
                    callback_error = result.get("error")
                    if result.get("success") and result.get("version"):
                        remote_version = result["version"]
                        from core.database import get_connection

                        conn = None
                        try:
                            conn = get_connection()
                            cursor = conn.cursor()
                            cursor.execute(
                                "UPDATE games SET latest_version = ? WHERE f95_url = ?",
                                (remote_version, url),
                            )
                            cursor.execute(
                                "SELECT version FROM games WHERE f95_url = ?", (url,)
                            )
                            row = cursor.fetchone()
                            current_version = (
                                row[0] if row and row[0] is not None else ""
                            )
                            conn.commit()
                            has_update = bool(
                                current_version
                                and remote_version
                                and str(current_version).strip()
                                != str(remote_version).strip()
                            )
                        except Exception as e:
                            callback_error = f"Failed to save update data: {e}"
                        finally:
                            if conn is not None:
                                conn.close()

                    self._update_results.append(
                        {
                            "id": game["id"],
                            "title": game.get("title", ""),
                            "current_version": game.get("version", ""),
                            "latest_version": result.get("version", ""),
                            "has_update": has_update,
                            "error": callback_error,
                        }
                    )
                    self._update_checked += 1
                    return True

                self.scraper.get_multiple_thread_versions(
                    urls, headless=True, delay=15, callback=check_callback
                )
            finally:
                self._update_checked = self._update_total
                self._update_current = ""
                self._update_running = False

        thread = threading.Thread(target=run_checks, daemon=True)
        thread.start()
        return {"success": True, "total": self._update_total}

    def get_update_status(self):
        """Get the current status of the bulk update check."""
        return {
            "running": getattr(self, "_update_running", False),
            "total": getattr(self, "_update_total", 0),
            "checked": getattr(self, "_update_checked", 0),
            "current": getattr(self, "_update_current", ""),
            "results": getattr(self, "_update_results", []),
        }

    def cancel_update_check(self):
        """Cancel an in-progress bulk update check."""
        self._update_running = False
        return {"success": True}

    def get_auto_check_setting(self):
        """Get the auto update check frequency setting."""
        from core.database import get_connection

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'auto_update_check'")
        row = cursor.fetchone()
        freq = row[0] if row else "weekly"
        cursor.execute("SELECT value FROM settings WHERE key = 'last_update_check'")
        row2 = cursor.fetchone()
        last = row2[0] if row2 else ""
        conn.close()
        return {"frequency": freq, "last_check": last}

    def set_auto_check_setting(self, frequency):
        """Set auto update check frequency: 'weekly', 'monthly', or 'off'."""
        from core.database import get_connection

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_update_check', ?)",
            (frequency,),
        )
        conn.commit()
        conn.close()
        return {"success": True}

    def maybe_auto_check(self):
        """Check if auto update should run based on frequency and last check time."""
        from core.database import get_connection
        from datetime import datetime, timedelta

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'auto_update_check'")
        row = cursor.fetchone()
        freq = row[0] if row else "weekly"

        if freq == "off":
            conn.close()
            return {"triggered": False, "reason": "Auto-check is disabled"}

        cursor.execute("SELECT value FROM settings WHERE key = 'last_update_check'")
        row2 = cursor.fetchone()
        last_check_str = row2[0] if row2 else ""
        conn.close()

        now = datetime.now()
        should_check = False

        if not last_check_str:
            should_check = True
        else:
            try:
                last_check = datetime.fromisoformat(last_check_str)
                if freq == "weekly" and now - last_check > timedelta(weeks=1):
                    should_check = True
                elif freq == "monthly" and now - last_check > timedelta(days=30):
                    should_check = True
            except ValueError:
                should_check = True

        if should_check:
            result = self.check_all_updates()
            return {"triggered": True, "result": result}

        return {"triggered": False, "reason": f"Last checked: {last_check_str}"}

    # ==========================
    # Launcher API
    # ==========================
    def get_available_runners(self):
        """Scan ~/.local/share/wLib/proton/ for available proton versions and return them."""
        import os
        import shutil

        runners = []

        # Check system wine
        if shutil.which("wine"):
            runners.append({"name": "System Wine", "path": "wine"})

        proton_dir = os.path.expanduser("~/.local/share/wLib/proton")
        if os.path.exists(proton_dir):
            for entry in os.listdir(proton_dir):
                full_path = os.path.join(proton_dir, entry)
                if os.path.isdir(full_path):
                    proton_exec = os.path.join(full_path, "proton")
                    if os.path.exists(proton_exec):
                        runners.append({"name": entry, "path": proton_exec})

        return {"success": True, "runners": runners}

    def launch_game(
        self,
        game_id,
        exe_path,
        command_line_args="",
        run_japanese_locale=False,
        run_wayland=False,
        auto_inject_ce=False,
        custom_prefix="",
        proton_version="",
    ):
        """
        Uses the Launcher class to execute the game via Proton/Wine.
        """
        print(
            f"Launching {exe_path} (Args: {command_line_args}, JP Locale: {run_japanese_locale}, Wayland: {run_wayland}, Auto Inject CE: {auto_inject_ce}, Custom Prefix: {custom_prefix}, Proton Version: {proton_version})"
        )

        def on_exit(delta, is_final=True):
            try:
                from core.database import update_playtime

                update_playtime(game_id, delta)
                if is_final and self.window:
                    self.window.evaluate_js(
                        "window.dispatchEvent(new CustomEvent('wlib-refresh-library'))"
                    )
            except Exception as e:
                print(f"Failed to update playtime for game {game_id}: {e}")

        result = self.launcher.launch(
            exe_path,
            command_line_args,
            run_japanese_locale,
            run_wayland,
            auto_inject_ce,
            custom_prefix,
            proton_version,
            on_exit_callback=on_exit,
        )
        return result

    def install_rpgmaker_dependencies(self, prefix_path=None, proton_path=None):
        """
        Runs winetricks to install the common dependencies needed for RPGMaker/Unity visual novels.
        """
        from core.database import get_setting
        import subprocess
        import os

        wine_prefix = prefix_path if prefix_path else get_setting("wine_prefix_path")
        proton_path_to_use = proton_path if proton_path else get_setting("proton_path")

        if not wine_prefix:
            wine_prefix = os.path.expanduser("~/.local/share/wLib/prefix")
            os.makedirs(wine_prefix, exist_ok=True)

        is_proton = (
            proton_path_to_use
            and "proton" in os.path.basename(proton_path_to_use).lower()
        )

        env = os.environ.copy()
        if is_proton:
            # Proton creates the actual prefix in a 'pfx' subfolder
            pfx_path = os.path.join(wine_prefix, "pfx")
            env["WINEPREFIX"] = pfx_path if os.path.exists(pfx_path) else wine_prefix
        else:
            env["WINEPREFIX"] = wine_prefix

        verbs = [
            "corefonts",
            "d3dcompiler_42",
            "d3dcompiler_43",
            "d3dcompiler_47",
            "d3dx9",
            "d3dx11_42",
            "d3dx11_43",
            "quartz",
            "directshow",
            "wmp9",
            "dotnetdesktop8",
            "vcrun2013",
            "vcrun2022",
            "dotnet40",
            "dotnet45",
            "dotnet46",
            "dotnet461",
            "dotnet462",
            "dotnet472",
            "allfonts",
            "cjkfonts",
            "consolas",
            "unifont",
            "vcrun6",
            "vcrun2010",
            "dotnetcoredesktop3",
        ]
        command = ["winetricks", "-q"] + verbs
        print(
            f"Running winetricks command in {env.get('WINEPREFIX')}: {' '.join(command)}"
        )

        self._deps_install_status = {
            "running": True,
            "done": 0,
            "total": len(verbs),
            "current": verbs[0],
            "error": "",
        }

        # We run this in a background thread so we don't block the UI returning 'success' immediately
        # (Winetricks downloads huge MS packages that take longer than the IPC timeout)
        def _install_deps():
            try:
                for i, verb in enumerate(verbs):
                    self._deps_install_status["done"] = i
                    self._deps_install_status["current"] = verb
                    print(f"Installing winetricks verb {i + 1}/{len(verbs)}: {verb}")
                    subprocess.run(
                        ["winetricks", "-q", verb],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                self._deps_install_status["done"] = len(verbs)
                self._deps_install_status["current"] = ""
                self._deps_install_status["running"] = False
                from core.database import update_setting

                update_setting("dlls_installed", "true")
                print("Finished installing winetricks dependencies.")
            except Exception as e:
                self._deps_install_status["running"] = False
                self._deps_install_status["error"] = str(e)
                print(f"Winetricks encountered an error: {e}")

        import threading

        threading.Thread(target=_install_deps, daemon=True).start()
        return {"success": True}

    def install_rpgmaker_rtp(self):
        """
        Downloads and installs RPG Maker VX Ace and XP RTPs into the configured wine prefix.
        Currently downloads the official zips and triggers their setup.exe silently.
        """
        from core.database import get_setting
        import subprocess
        import os
        import urllib.request
        import zipfile
        import threading

        wine_prefix = get_setting("wine_prefix_path")
        proton_path = get_setting("proton_path")

        if not wine_prefix:
            wine_prefix = os.path.expanduser("~/.local/share/wLib/prefix")
            os.makedirs(wine_prefix, exist_ok=True)

        is_proton = bool(
            isinstance(proton_path, str)
            and proton_path
            and "proton" in os.path.basename(proton_path).lower()
        )
        env = os.environ.copy()

        if is_proton:
            pfx_path = os.path.join(wine_prefix, "pfx")
            env["WINEPREFIX"] = pfx_path if os.path.exists(pfx_path) else wine_prefix
            env["STEAM_COMPAT_DATA_PATH"] = wine_prefix
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = "/tmp/wlib"
        else:
            env["WINEPREFIX"] = wine_prefix

        # Ensure the wine prefix is ready
        os.makedirs(env["WINEPREFIX"], exist_ok=True)

        rtps = [
            {
                "name": "VX Ace",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/RPGVXAce_RTP.zip",
                "filename": "vxace_rtp.zip",
            },
            {
                "name": "VX",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/vx_rtp102e.zip",
                "filename": "vx_rtp.zip",
            },
            {
                "name": "XP",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/xp_rtp104e.exe",
                "filename": "xp_rtp104e.exe",
            },
            {
                "name": "2003",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/rpg2003_rtp_installer.zip",
                "filename": "rpg2003_rtp.zip",
            },
        ]

        # We run this in a background thread so we don't block the UI returning 'success' immediately
        def _download_and_install():
            import shutil

            rtp_dir = os.path.expanduser("~/.local/share/wLib/rtp")
            os.makedirs(rtp_dir, exist_ok=True)

            import ssl

            ctx = ssl.create_default_context()

            for i, rtp in enumerate(rtps):
                self._rtp_install_status["done"] = i
                self._rtp_install_status["current"] = rtp["name"]
                download_path = os.path.join(rtp_dir, rtp["filename"])
                extract_path = os.path.join(rtp_dir, rtp["name"].replace(" ", "_"))

                print(f"Downloading {rtp['name']} RTP...")
                if not os.path.exists(download_path):
                    try:
                        req = urllib.request.Request(
                            rtp["url"], headers={"User-Agent": "Mozilla/5.0"}
                        )
                        with (
                            urllib.request.urlopen(
                                req, context=ctx, timeout=30
                            ) as response,
                            open(download_path, "wb") as out_file,
                        ):
                            shutil.copyfileobj(response, out_file)
                    except Exception as e:
                        print(f"Failed to download {rtp['name']} RTP: {e}")
                        continue

                setup_exe = None

                if download_path.endswith(".exe"):
                    setup_exe = download_path
                else:
                    print(f"Extracting {rtp['name']} RTP...")
                    if not os.path.exists(extract_path):
                        with zipfile.ZipFile(download_path, "r") as zf:
                            zf.extractall(extract_path)

                    # Find the setup executable inside the extracted folder
                    for root, dirs, files in os.walk(extract_path):
                        for f in files:
                            if f.lower().endswith(".exe"):
                                setup_exe = os.path.join(root, f)
                                break
                        if setup_exe:
                            break

                if setup_exe:
                    print(f"Installing {rtp['name']} RTP silently in prefix...")
                    setup_exe_str = str(setup_exe)
                    command = ["wine", setup_exe_str, "/S", "/v/qn"]
                    if is_proton and isinstance(proton_path, str):
                        command = [proton_path, "run", setup_exe_str, "/S", "/v/qn"]
                    try:
                        subprocess.run(
                            command,
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=120,
                            check=True,
                        )
                        print(f"Finished installing {rtp['name']} RTP.")
                    except Exception as e:
                        print(f"Wine failed to run {rtp['name']} setup: {e}")

            self._rtp_install_status["done"] = len(rtps)
            self._rtp_install_status["current"] = ""
            self._rtp_install_status["running"] = False
            from core.database import update_setting

            update_setting("rtps_installed", "true")
            print("All RTPs installed successfully.")

        self._rtp_install_status = {
            "running": True,
            "done": 0,
            "total": len(rtps),
            "current": rtps[0]["name"],
            "error": "",
        }
        import threading

        threading.Thread(target=_download_and_install, daemon=True).start()
        return {"success": True}

    def get_install_status(self):
        """Returns the current status of background DLL and RTP installs, plus whether they've previously completed."""
        from core.database import get_setting

        return {
            "deps": getattr(
                self,
                "_deps_install_status",
                {"running": False, "done": 0, "total": 0, "current": "", "error": ""},
            ),
            "rtps": getattr(
                self,
                "_rtp_install_status",
                {"running": False, "done": 0, "total": 0, "current": "", "error": ""},
            ),
            "dlls_installed": get_setting("dlls_installed") == "true",
            "rtps_installed": get_setting("rtps_installed") == "true",
        }

    def get_system_deps_command(self):
        """Detects the system package manager and returns the command to install 32-bit GStreamer/multimedia dependencies."""
        import shutil

        commands = {
            "dnf": {
                "name": "Fedora / RHEL",
                "command": "sudo dnf install -y gstreamer1-plugins-base.i686 gstreamer1-plugins-good.i686 gstreamer1-plugins-bad-free.i686 gstreamer1.i686 libvpx.i686 opus.i686 libvorbis.i686 libtheora.i686 libogg.i686 flac-libs.i686 speex.i686 libjpeg-turbo.i686 libsndfile.i686 libwebp.i686",
            },
            "apt": {
                "name": "Debian / Ubuntu",
                "command": "sudo dpkg --add-architecture i386 && sudo apt update && sudo apt install -y libgstreamer1.0-0:i386 gstreamer1.0-plugins-base:i386 gstreamer1.0-plugins-good:i386 gstreamer1.0-plugins-bad:i386 libvpx-dev:i386 libopus0:i386 libvorbis0a:i386 libtheora0:i386 libogg0:i386 libflac8:i386 libspeex1:i386 libjpeg62-turbo:i386 libsndfile1:i386 libwebp-dev:i386",
            },
            "pacman": {
                "name": "Arch Linux",
                "command": "sudo pacman -S --noconfirm lib32-gstreamer lib32-gst-plugins-base lib32-gst-plugins-good lib32-gst-plugins-bad-libs lib32-libvpx lib32-opus lib32-libvorbis lib32-libtheora lib32-libogg lib32-flac lib32-speex lib32-libjpeg-turbo lib32-libsndfile lib32-libwebp",
            },
            "zypper": {
                "name": "openSUSE",
                "command": "sudo zypper install -y gstreamer-plugins-base-32bit gstreamer-plugins-good-32bit gstreamer-plugins-bad-32bit libvpx-32bit libopus0-32bit libvorbis-32bit libtheora-32bit libogg-32bit libFLAC8-32bit speex-32bit libjpeg62-32bit libsndfile1-32bit libwebp-32bit",
            },
            "xbps-install": {
                "name": "Void Linux",
                "command": "sudo xbps-install -y gst-plugins-base1-32bit gst-plugins-good1-32bit gst-plugins-bad1-32bit libvpx-32bit libopus-32bit libvorbis-32bit libtheora-32bit libogg-32bit libflac-32bit speex-32bit libjpeg-turbo-32bit libsndfile-32bit libwebp-32bit",
            },
        }

        for pkg_mgr, info in commands.items():
            if shutil.which(pkg_mgr):
                return {
                    "detected": True,
                    "package_manager": pkg_mgr,
                    "distro": info["name"],
                    "command": info["command"],
                }

        return {
            "detected": False,
            "package_manager": "unknown",
            "distro": "Unknown",
            "command": "# Could not detect your package manager. Please install 32-bit GStreamer plugins manually.",
        }

    # ==========================
    # Settings & Utils API
    # ==========================
    def download_proton_ge(self):
        """
        Downloads the latest Proton-GE release and extracts it to the local share directory.
        """
        import urllib.request
        import json
        import os
        import tarfile

        try:
            print("Fetching latest GE-Proton release info...")
            api_url = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases/latest"
            req = urllib.request.Request(api_url, headers={"User-Agent": "wLib"})

            import ssl

            ctx = ssl.create_default_context()

            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                data = json.loads(response.read().decode())

            download_url = None
            release_name = data.get("tag_name", "proton-ge")
            for asset in data.get("assets", []):
                if asset.get("name", "").endswith(".tar.gz"):
                    download_url = asset.get("browser_download_url")
                    break

            if not download_url:
                return {
                    "success": False,
                    "error": "Could not find a valid release tarball.",
                }

            wlib_share_dir = os.path.expanduser("~/.local/share/wLib/proton")
            os.makedirs(wlib_share_dir, exist_ok=True)

            tar_path = os.path.join("/tmp", f"{release_name}.tar.gz")

            print(f"Downloading {release_name} from {download_url}...")
            # We must use urlopen with our context and shutil here instead of urlretrieve
            # because urlretrieve doesn't easily accept a custom SSL context.
            import shutil

            download_req = urllib.request.Request(
                download_url, headers={"User-Agent": "wLib"}
            )
            with (
                urllib.request.urlopen(
                    download_req, context=ctx, timeout=30
                ) as response,
                open(tar_path, "wb") as out_file,
            ):
                shutil.copyfileobj(response, out_file)

            print(f"Extracting {tar_path}...")
            with tarfile.open(tar_path, "r:gz") as tar:
                members = tar.getmembers()
                # Find the root extracted folder name (usually GE-Proton-X)
                root_dir = members[0].name.split("/")[0]
                tar.extractall(path=wlib_share_dir)

            os.remove(tar_path)

            proton_executable = os.path.join(wlib_share_dir, root_dir, "proton")
            if not os.path.exists(proton_executable):
                return {
                    "success": False,
                    "error": f"Proton executable not found at {proton_executable}",
                }

            from core.database import update_setting

            update_setting("proton_path", proton_executable)

            print(f"Successfully installed GE-Proton to {proton_executable}")
            return {"success": True, "path": proton_executable}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_settings(self):
        from core.database import get_setting

        return {
            "proton_path": get_setting("proton_path"),
            "wine_prefix_path": get_setting("wine_prefix_path"),
            "enable_logging": get_setting("enable_logging") == "true",
        }

    def save_settings(self, settings):
        from core.database import update_setting

        update_setting("proton_path", settings.get("proton_path", ""))
        update_setting("wine_prefix_path", settings.get("wine_prefix_path", ""))
        update_setting(
            "enable_logging", "true" if settings.get("enable_logging") else "false"
        )
        return {"success": True}

    def browse_file(self):
        """Opens a native file dialog to select an executable."""
        if self.window:
            webview = self._load_webview_module()
            if webview is None:
                return ""

            try:
                dt = webview.FileDialog.OPEN
            except AttributeError:
                dt = webview.OPEN_DIALOG
            file_types = ("Executables (*.exe;*.sh;*.AppImage)", "All files (*.*)")
            result = self.window.create_file_dialog(
                dt, allow_multiple=False, file_types=file_types
            )
            if result and len(result) > 0:
                return result[0]
        return ""

    def browse_directory(self):
        """Opens a native directory dialog, e.g. for choosing a wine prefix."""
        if self.window:
            webview = self._load_webview_module()
            if webview is None:
                return ""

            try:
                dt = webview.FileDialog.FOLDER
            except AttributeError:
                dt = webview.FOLDER_DIALOG
            result = self.window.create_file_dialog(dt, allow_multiple=False)
            if result and len(result) > 0:
                return result[0]
        return ""

    def find_save_files(
        self, exe_path, title="", engine="", custom_prefix="", proton_version=""
    ):
        """
        Searches common save file locations for a game.
        Returns a list of {path, type, description} for each found location.
        """
        import os
        import glob
        from core.database import get_setting

        results = []
        game_dir = os.path.dirname(exe_path) if exe_path else ""
        game_name = os.path.splitext(os.path.basename(exe_path))[0] if exe_path else ""
        title_clean = title.strip() if title else game_name

        # ── 1. Check game directory for common save folders/files ──
        if game_dir and os.path.isdir(game_dir):
            save_patterns = ["save", "saves", "Save", "Saves", "SaveData", "savedata"]
            for pattern in save_patterns:
                candidate = os.path.join(game_dir, pattern)
                if os.path.isdir(candidate):
                    results.append(
                        {
                            "path": candidate,
                            "type": "Game Folder",
                            "description": f'Found "{pattern}/" folder next to executable',
                        }
                    )

            # RPGMaker MV/MZ: www/save/
            www_save = os.path.join(game_dir, "www", "save")
            if os.path.isdir(www_save):
                results.append(
                    {
                        "path": www_save,
                        "type": "RPGMaker MV/MZ",
                        "description": "RPGMaker MV/MZ web save folder",
                    }
                )

            # Check for loose save files
            save_extensions = ["*.sav", "*.rpgsave", "*.save", "*.dat"]
            for ext in save_extensions:
                found = glob.glob(os.path.join(game_dir, ext))
                if found:
                    results.append(
                        {
                            "path": game_dir,
                            "type": "Game Folder",
                            "description": f"Found {len(found)} {ext} file(s) in game directory",
                        }
                    )
                    break  # Don't duplicate the game_dir

        # ── 2. Check Ren'Py native save path (~/.renpy/) ──
        renpy_base = os.path.expanduser("~/.renpy")
        if os.path.isdir(renpy_base):
            # Try to match by game name or title
            for entry in os.listdir(renpy_base):
                entry_lower = entry.lower()
                if (
                    title_clean
                    and title_clean.lower().replace(" ", "").replace("'", "")
                    in entry_lower.replace("-", "").replace("_", "")
                ) or (game_name and game_name.lower() in entry_lower):
                    candidate = os.path.join(renpy_base, entry)
                    if os.path.isdir(candidate):
                        results.append(
                            {
                                "path": candidate,
                                "type": "Ren'Py",
                                "description": f"Ren'Py native save folder: ~/.renpy/{entry}",
                            }
                        )

        # ── 3. Check Wine prefix AppData folders ──
        wine_prefix = (
            custom_prefix if custom_prefix else get_setting("wine_prefix_path")
        )
        if not wine_prefix:
            wine_prefix = os.path.expanduser("~/.local/share/wLib/prefix")

        proton_path = proton_version if proton_version else get_setting("proton_path")
        is_proton = proton_path and "proton" in os.path.basename(proton_path).lower()

        if is_proton:
            pfx_path = os.path.join(wine_prefix, "pfx")
            actual_prefix = pfx_path if os.path.exists(pfx_path) else wine_prefix
        else:
            actual_prefix = wine_prefix

        # Search through all user folders in the prefix
        users_dir = os.path.join(actual_prefix, "drive_c", "users")
        if os.path.isdir(users_dir):
            appdata_variants = [
                ("AppData/Roaming", "AppData Roaming"),
                ("AppData/Local", "AppData Local"),
                ("AppData/LocalLow", "AppData LocalLow"),
                ("My Documents", "My Documents"),
                ("Documents", "Documents"),
                ("Saved Games", "Saved Games"),
            ]
            for user_folder in os.listdir(users_dir):
                user_path = os.path.join(users_dir, user_folder)
                if not os.path.isdir(user_path) or user_folder in ("Public",):
                    continue

                for sub_path, label in appdata_variants:
                    appdata_dir = os.path.join(user_path, sub_path)
                    if not os.path.isdir(appdata_dir):
                        continue

                    # Search for folders that fuzzy-match the game title or exe name
                    try:
                        for entry in os.listdir(appdata_dir):
                            entry_lower = entry.lower()
                            title_words = (
                                title_clean.lower().split() if title_clean else []
                            )
                            game_name_lower = game_name.lower() if game_name else ""

                            # Match if any significant title word (3+ chars) appears in folder name
                            matched = False
                            if game_name_lower and game_name_lower in entry_lower:
                                matched = True
                            elif title_words:
                                significant_words = [
                                    w for w in title_words if len(w) >= 3
                                ]
                                if significant_words and any(
                                    w in entry_lower for w in significant_words
                                ):
                                    matched = True

                            if matched:
                                candidate = os.path.join(appdata_dir, entry)
                                if os.path.isdir(candidate):
                                    results.append(
                                        {
                                            "path": candidate,
                                            "type": f"Wine Prefix ({label})",
                                            "description": f"{label}/{entry}",
                                        }
                                    )
                    except PermissionError:
                        continue

        # Deduplicate by path
        seen = set()
        unique_results = []
        for r in results:
            if r["path"] not in seen:
                seen.add(r["path"])
                unique_results.append(r)

        return unique_results

    def open_folder(self, path):
        """Opens a folder in the system file manager."""
        import subprocess, shutil, os

        if not os.path.exists(path):
            return {"success": False, "error": f"Path does not exist: {path}"}
        for cmd in ["xdg-open", "gio", "kde-open5", "gnome-open"]:
            binary = shutil.which(cmd)
            if binary:
                if cmd == "gio":
                    subprocess.Popen([binary, "open", path])
                else:
                    subprocess.Popen([binary, path])
                return {"success": True}
        return {"success": False, "error": "No file manager opener found"}

    def open_dev_tools(self):
        if self.window:
            self.window.toggle_inspect()

    def check_app_updates(self):
        """Fetches the latest release from the kirin-3/wLib GitHub repository."""
        import urllib.request
        import json
        import logging

        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/kirin-3/wLib/releases/latest",
                headers={"User-Agent": "wLib-AppUpdater"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    latest_tag = data.get("tag_name", "")
                    latest_tag = (
                        latest_tag if latest_tag.startswith("v") else f"v{latest_tag}"
                    )
                    return {
                        "success": True,
                        "version": latest_tag,
                        "current_version": f"v{APP_VERSION}",
                        "changelog": data.get("body", ""),
                        "url": data.get("html_url", ""),
                        "assets": [
                            {
                                "name": a.get("name"),
                                "url": a.get("browser_download_url"),
                            }
                            for a in data.get("assets", [])
                        ],
                    }
        except Exception as e:
            logging.error(f"Failed to check app updates: {e}")
            return {"success": False, "error": str(e)}

    def get_app_version(self):
        """Returns the current app version."""
        return {"version": f"v{APP_VERSION}"}

    # ==========================
    # Cheat Engine API
    # ==========================
    def is_cheat_engine_installed(self):
        """Checks if Cheat Engine (Lunar Engine) is installed in the wLib data directory."""
        import os

        ce_dir = os.path.expanduser("~/.local/share/wLib/CheatEngine")
        # We look for the 64-bit Lunar Engine executable inside the extracted folder
        # The zip extracts into a "Lunar Engine" subfolder usually, or directly containing it.
        # Let's search for "lunarengine-x86_64.exe" or "cheatengine-x86_64.exe"
        extracted_folder = os.path.join(ce_dir, "Lunar Engine")
        if os.path.exists(os.path.join(extracted_folder, "lunarengine-x86_64.exe")):
            return {
                "installed": True,
                "path": os.path.join(extracted_folder, "lunarengine-x86_64.exe"),
            }
        if os.path.exists(os.path.join(ce_dir, "lunarengine-x86_64.exe")):
            return {
                "installed": True,
                "path": os.path.join(ce_dir, "lunarengine-x86_64.exe"),
            }
        return {"installed": False, "path": ""}

    def download_cheat_engine(self):
        """
        Downloads a safe, portable build of Cheat Engine (Lunar Engine v7.2).
        Lunar Engine is an undetected CE fork that works perfectly in Wine.
        """
        import os
        import subprocess
        import urllib.request
        import zipfile
        import shutil

        url = "https://github.com/visibou/lunarengine/releases/download/v.7.2/Lunar.Engine.zip"
        ce_dir = os.path.expanduser("~/.local/share/wLib/CheatEngine")
        zip_path = os.path.join(ce_dir, "ce.zip")

        try:
            # Clean up old directory if exists
            if os.path.exists(ce_dir):
                shutil.rmtree(ce_dir)

            os.makedirs(ce_dir, exist_ok=True)

            print(f"Downloading Cheat Engine from {url}...")

            req = urllib.request.Request(url, headers={"User-Agent": "wLib"})
            with (
                urllib.request.urlopen(req, timeout=30) as response,
                open(zip_path, "wb") as out_file,
            ):
                shutil.copyfileobj(response, out_file)

            print(f"Extracting Cheat Engine to {ce_dir}...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(ce_dir)

            os.remove(zip_path)  # cleanup

            # Verify install
            check = self.is_cheat_engine_installed()
            if check["installed"]:
                print("Cheat Engine successfully installed!")
                return {"success": True, "path": check["path"]}
            else:
                return {
                    "success": False,
                    "error": "Extracted successfully but could not find lunarengine-x86_64.exe",
                }

        except Exception as e:
            return {"success": False, "error": str(e)}
