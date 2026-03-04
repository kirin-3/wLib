import os
import sys
import threading
import subprocess
import time
import importlib

PLAYWRIGHT_BROWSERS_PATH = os.path.expanduser("~/.cache/ms-playwright")
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = PLAYWRIGHT_BROWSERS_PATH

DEV_MODE = os.environ.get("DEV_MODE", "0") == "1"
VITE_DEV_SERVER = "http://localhost:5173"

from core.api import Api
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    webview = importlib.import_module("webview")
except Exception:
    webview = None

window_ref = None


class ExtensionRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default HTTP server logs to keep the console clean
        pass

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/check"):
            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)
            check_url = query.get("url", [""])[0]

            exists = False
            if check_url:
                conn = None
                try:
                    from core.database import get_connection

                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT id FROM games WHERE f95_url = ?", (check_url,)
                    )
                    exists = cursor.fetchone() is not None
                except Exception as e:
                    print(f"[wLib] Database error during extension check: {e}")
                    exists = False
                finally:
                    if conn is not None:
                        conn.close()

            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"exists": exists}).encode())
        elif self.path.startswith("/api/open"):
            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)
            game_url = query.get("url", [""])[0]

            if window_ref:
                try:
                    window_ref.on_top = True
                    window_ref.on_top = False
                    # Tell the frontend to open the edit modal for this game
                    if game_url:
                        import base64

                        b64_url = base64.b64encode(game_url.encode("utf-8")).decode(
                            "utf-8"
                        )
                        js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-open', {{ detail: {{ url: decodeURIComponent(escape(atob('{b64_url}'))) }} }}));"
                        window_ref.evaluate_js(js_code)
                except Exception as e:
                    print(f"[wLib] Failed to process extension open event: {e}")
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"success": true}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/add":
            content_length = int(self.headers.get("Content-Length", "0"))
            post_data = self.rfile.read(content_length)

            try:
                payload = json.loads(post_data.decode("utf-8"))

                # We have the payload. Send it to the Vue frontend via PyWebView javascript evaluation
                if window_ref:
                    import base64

                    b64_json = base64.b64encode(
                        json.dumps(payload).encode("utf-8")
                    ).decode("utf-8")
                    js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-add', {{ detail: JSON.parse(decodeURIComponent(escape(atob('{b64_json}')))) }}));"
                    window_ref.evaluate_js(js_code)

                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"success": true}')
            except Exception as e:
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"success": False, "error": str(e)}).encode()
                )
        else:
            self.send_response(404)
            self.end_headers()


def start_extension_server():
    try:
        server = HTTPServer(("localhost", 8183), ExtensionRequestHandler)
        print("Starting extension HTTP receiver on port 8183...")
        server.serve_forever()
    except Exception as e:
        print(f"Failed to start extension HTTP server: {e}")


def start_vite_dev_server():
    print("Starting Vite dev server...")
    # Change dir to the 'ui' folder relative to main.py
    ui_dir = os.path.join(os.path.dirname(__file__), "ui")
    if os.path.exists(ui_dir):
        # We start process and let it run
        return subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=ui_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return None


def ensure_playwright_browsers():
    """Ensure Playwright chromium browser is installed."""
    chromium_path = os.path.join(PLAYWRIGHT_BROWSERS_PATH, "chromium-*")
    import glob

    try:
        importlib.import_module("playwright")
    except ModuleNotFoundError:
        print(
            "[wLib] Playwright Python package is missing; cannot install browser binaries."
        )
        return False

    if glob.glob(chromium_path):
        return True

    print("Installing Playwright chromium browser...")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Playwright chromium installed successfully.")
        return True
    except Exception as e:
        print(f"Failed to install Playwright chromium: {e}")
        return False


def ensure_playwright_browsers_async():
    """Run Playwright browser availability checks outside startup critical path."""

    def _ensure():
        ok = ensure_playwright_browsers()
        if not ok:
            print(
                "[wLib] Playwright browser check failed; update checks may fail until installed."
            )

    thread = threading.Thread(target=_ensure, daemon=True)
    thread.start()


def main():
    if "--install-playwright-if-needed" in sys.argv:
        ensure_playwright_browsers()
        sys.exit(0)

    global window_ref, DEV_MODE

    if webview is None:
        raise RuntimeError("pywebview is required to run wLib")

    api = Api()

    # Resolve base path for bundled assets (PyInstaller vs dev source)
    if getattr(sys, "frozen", False):
        script_dir = getattr(
            sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))
        )
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))

    url = os.path.join(script_dir, "ui", "dist", "index.html")
    vite_process = None

    if DEV_MODE:
        url = VITE_DEV_SERVER
        vite_process = start_vite_dev_server()
        # Give Vite a second to start
        time.sleep(2)

    window = webview.create_window(
        "wLib - Game Manager",
        url,
        js_api=api,
        width=1200,
        height=800,
        background_color="#101014",
    )
    window_ref = window
    api.set_window(window)

    # Keep startup responsive by verifying Playwright browsers in the background.
    ensure_playwright_browsers_async()

    # Start the extension background server in a daemon thread
    threading.Thread(target=start_extension_server, daemon=True).start()

    # Start the PyWebView UI loop
    # We set debug=False so it doesn't open the Web Inspector automatically
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wlib.png")
    webview.start(
        gui="qt",
        debug=False,
        http_server=True,
        icon=icon_path if os.path.exists(icon_path) else None,
    )

    # Cleanup Vite server when window closes
    if vite_process:
        vite_process.terminate()


if __name__ == "__main__":
    main()
