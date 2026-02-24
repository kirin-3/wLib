from core.database import init_db
from core.scraper import Scraper
from core.launcher import Launcher
import sys
import os

class Api:
    def __init__(self):
        self.window = None
        # Make sure our database is initialized
        init_db()
        self.scraper = Scraper()
        self.launcher = Launcher()

    def set_window(self, window):
        self.window = window

    def open_extension_folder(self):
        import subprocess, shutil
        ext_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'extension')
        # Try multiple openers for cross-distro compatibility
        for cmd in ['xdg-open', 'gio', 'kde-open5', 'gnome-open']:
            binary = shutil.which(cmd)
            if binary:
                if cmd == 'gio':
                    subprocess.Popen([binary, 'open', ext_dir])
                else:
                    subprocess.Popen([binary, ext_dir])
                return {"success": True}
        return {"success": False, "error": "No file manager opener found"}

    # ==========================
    # Database / Game API
    # ==========================
    def get_games(self):
        from core.database import get_all_games
        return get_all_games()

    def add_game(self, title, exe_path, f95_url='', cover_image='', tags='', rating='', developer='', engine=''):
        from core.database import add_game
        game_id = add_game(title, exe_path, f95_url, cover_image=cover_image, tags=tags, rating=rating, developer=developer, engine=engine)
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
        print(f"Checking for updates for {url}")
        try:
            version_info = self.scraper.get_thread_version(url, headless=True)
            if version_info.get("success") and version_info.get("version"):
                remote_version = version_info["version"]
                # Store in latest_version field
                from core.database import get_connection
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE games SET latest_version = ? WHERE f95_url = ?", (remote_version, url))
                # Check if it differs from current version
                cursor.execute("SELECT version FROM games WHERE f95_url = ?", (url,))
                row = cursor.fetchone()
                current_version = row[0] if row else ''
                conn.commit()
                conn.close()
                has_update = bool(current_version and remote_version and current_version.strip() != remote_version.strip())
                return {"success": True, "version": remote_version, "has_update": has_update}
            return version_info
        except Exception as e:
            return {"error": str(e)}

    def open_in_browser(self, url):
        """Opens a URL in the user's default browser and brings it to foreground."""
        import webbrowser
        webbrowser.open(url)
        return {"success": True}

    def check_all_updates(self):
        """Start a background thread to check all games for updates, 15s apart."""
        import threading
        if hasattr(self, '_update_running') and self._update_running:
            return {"success": False, "error": "Update check already in progress"}
        
        from core.database import get_all_games
        all_games = get_all_games()
        games_with_url = [g for g in all_games if g.get('f95_url')]
        
        self._update_running = True
        self._update_total = len(games_with_url)
        self._update_checked = 0
        self._update_current = ''
        self._update_results = []
        
        # Record the check timestamp
        from datetime import datetime
        from core.database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('last_update_check', ?)", (datetime.now().isoformat(),))
        conn.commit()
        conn.close()
        
        def run_checks():
            import time as _time
            for i, game in enumerate(games_with_url):
                if not self._update_running:
                    break  # cancelled
                self._update_current = game.get('title', 'Unknown')
                self._update_checked = i
                try:
                    result = self.check_for_updates(game['f95_url'])
                    has_update = result.get('has_update', False)
                    self._update_results.append({
                        'id': game['id'],
                        'title': game.get('title', ''),
                        'current_version': game.get('version', ''),
                        'latest_version': result.get('version', ''),
                        'has_update': has_update,
                        'error': result.get('error', None)
                    })
                except Exception as e:
                    self._update_results.append({
                        'id': game['id'],
                        'title': game.get('title', ''),
                        'current_version': game.get('version', ''),
                        'latest_version': '',
                        'has_update': False,
                        'error': str(e)
                    })
                # Rate limit: 15s between checks (skip delay after last)
                if i < len(games_with_url) - 1:
                    _time.sleep(15)
            self._update_checked = self._update_total
            self._update_current = ''
            self._update_running = False
        
        thread = threading.Thread(target=run_checks, daemon=True)
        thread.start()
        return {"success": True, "total": self._update_total}
    
    def get_update_status(self):
        """Get the current status of the bulk update check."""
        return {
            "running": getattr(self, '_update_running', False),
            "total": getattr(self, '_update_total', 0),
            "checked": getattr(self, '_update_checked', 0),
            "current": getattr(self, '_update_current', ''),
            "results": getattr(self, '_update_results', [])
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
        freq = row[0] if row else 'weekly'
        cursor.execute("SELECT value FROM settings WHERE key = 'last_update_check'")
        row2 = cursor.fetchone()
        last = row2[0] if row2 else ''
        conn.close()
        return {"frequency": freq, "last_check": last}

    def set_auto_check_setting(self, frequency):
        """Set auto update check frequency: 'weekly', 'monthly', or 'off'."""
        from core.database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_update_check', ?)", (frequency,))
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
        freq = row[0] if row else 'weekly'
        
        if freq == 'off':
            conn.close()
            return {"triggered": False, "reason": "Auto-check is disabled"}
        
        cursor.execute("SELECT value FROM settings WHERE key = 'last_update_check'")
        row2 = cursor.fetchone()
        last_check_str = row2[0] if row2 else ''
        conn.close()
        
        now = datetime.now()
        should_check = False
        
        if not last_check_str:
            should_check = True
        else:
            try:
                last_check = datetime.fromisoformat(last_check_str)
                if freq == 'weekly' and now - last_check > timedelta(weeks=1):
                    should_check = True
                elif freq == 'monthly' and now - last_check > timedelta(days=30):
                    should_check = True
            except ValueError:
                should_check = True
        
        if should_check:
            result = self.check_all_updates()
            return {"triggered": True, "result": result}
        
        return {"triggered": False, "reason": f"Last checked: {last_check_str}"}
    # Launcher API
    # ==========================
    def launch_game(self, exe_path):
        """
        Uses the Launcher class to execute the game via Proton/Wine.
        """
        print(f"Launching {exe_path}")
        result = self.launcher.launch(exe_path)
        return result

    def install_rpgmaker_dependencies(self):
        """
        Runs winetricks to install the common dependencies needed for RPGMaker/Unity visual novels.
        """
        from core.database import get_setting
        import subprocess
        import os
        
        wine_prefix = get_setting("wine_prefix_path")
        proton_path = get_setting("proton_path")
        
        if not wine_prefix:
            wine_prefix = os.path.expanduser("~/.local/share/wLib/prefix")
            os.makedirs(wine_prefix, exist_ok=True)
            
        is_proton = proton_path and "proton" in os.path.basename(proton_path).lower()
        
        env = os.environ.copy()
        if is_proton:
            # Proton creates the actual prefix in a 'pfx' subfolder
            pfx_path = os.path.join(wine_prefix, "pfx")
            env["WINEPREFIX"] = pfx_path if os.path.exists(pfx_path) else wine_prefix
        else:
            env["WINEPREFIX"] = wine_prefix
            
        verbs = ["corefonts", "d3dcompiler_43", "d3dcompiler_47", "d3dx9", "quartz", "directshow", "wmp9"]
        command = ["winetricks", "-q"] + verbs
        print(f"Running winetricks command in {env.get('WINEPREFIX')}: {' '.join(command)}")
        
        try:
            subprocess.Popen(
                command, 
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

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
            
        is_proton = proton_path and "proton" in os.path.basename(proton_path).lower()
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
            {"name": "VX Ace", "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/RPGVXAce_RTP.zip", "filename": "vxace_rtp.zip"},
            {"name": "VX", "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/vx_rtp102e.zip", "filename": "vx_rtp.zip"},
            {"name": "XP", "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/xp_rtp104e.exe", "filename": "xp_rtp104e.exe"},
            {"name": "2003", "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/rpg2003_rtp_installer.zip", "filename": "rpg2003_rtp.zip"}
        ]
        
        # We run this in a background thread so we don't block the UI returning 'success' immediately
        def _download_and_install():
            import shutil
            rtp_dir = os.path.expanduser("~/.local/share/wLib/rtp")
            os.makedirs(rtp_dir, exist_ok=True)
            
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            for rtp in rtps:
                download_path = os.path.join(rtp_dir, rtp["filename"])
                extract_path = os.path.join(rtp_dir, rtp["name"].replace(" ", "_"))
                
                print(f"Downloading {rtp['name']} RTP...")
                if not os.path.exists(download_path):
                    try:
                        req = urllib.request.Request(rtp["url"], headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(req, context=ctx, timeout=30) as response, open(download_path, 'wb') as out_file:
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
                        with zipfile.ZipFile(download_path, 'r') as zf:
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
                    command = [proton_path, "run", setup_exe, "/S", "/v/qn"] if is_proton else ["wine", setup_exe, "/S", "/v/qn"]
                    try:
                        subprocess.run(
                            command,
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=120
                        )
                        print(f"Finished installing {rtp['name']} RTP.")
                    except Exception as e:
                        print(f"Wine failed to run {rtp['name']} setup: {e}")
                        
        threading.Thread(target=_download_and_install, daemon=True).start()
        return {"success": True}

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
            req = urllib.request.Request(api_url, headers={'User-Agent': 'wLib'})
            
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                data = json.loads(response.read().decode())
                
            download_url = None
            release_name = data.get("tag_name", "proton-ge")
            for asset in data.get("assets", []):
                if asset.get("name", "").endswith(".tar.gz"):
                    download_url = asset.get("browser_download_url")
                    break
                    
            if not download_url:
                return {"success": False, "error": "Could not find a valid release tarball."}
                
            wlib_share_dir = os.path.expanduser("~/.local/share/wLib/proton")
            os.makedirs(wlib_share_dir, exist_ok=True)
            
            tar_path = os.path.join("/tmp", f"{release_name}.tar.gz")
            
            print(f"Downloading {release_name} from {download_url}...")
            # We must use urlopen with our context and shutil here instead of urlretrieve 
            # because urlretrieve doesn't easily accept a custom SSL context.
            import shutil
            download_req = urllib.request.Request(download_url, headers={'User-Agent': 'wLib'})
            with urllib.request.urlopen(download_req, context=ctx, timeout=30) as response, open(tar_path, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
            
            print(f"Extracting {tar_path}...")
            with tarfile.open(tar_path, "r:gz") as tar:
                members = tar.getmembers()
                # Find the root extracted folder name (usually GE-Proton-X)
                root_dir = members[0].name.split('/')[0]
                tar.extractall(path=wlib_share_dir)
                
            os.remove(tar_path)
            
            proton_executable = os.path.join(wlib_share_dir, root_dir, "proton")
            if not os.path.exists(proton_executable):
                return {"success": False, "error": f"Proton executable not found at {proton_executable}"}
                
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
            "enable_logging": get_setting("enable_logging") == "true"
        }

    def save_settings(self, settings):
        from core.database import update_setting
        update_setting("proton_path", settings.get("proton_path", ""))
        update_setting("wine_prefix_path", settings.get("wine_prefix_path", ""))
        update_setting("enable_logging", "true" if settings.get("enable_logging") else "false")
        return {"success": True}

    def browse_file(self):
        """Opens a native file dialog to select an executable."""
        if self.window:
            import webview
            result = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
            if result and len(result) > 0:
                return result[0]
        return ""

    def browse_directory(self):
        """Opens a native directory dialog, e.g. for choosing a wine prefix."""
        if self.window:
            import webview
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)
            if result and len(result) > 0:
                return result[0]
        return ""

    def open_dev_tools(self):
        if self.window:
            self.window.toggle_inspect()
