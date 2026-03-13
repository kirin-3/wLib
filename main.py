from __future__ import annotations

import importlib
import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
import shutil
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, Protocol, cast, override

from core.api import Api
from core.f95zone import normalize_thread_url

DEFAULT_PLAYWRIGHT_BROWSERS_PATH = os.path.expanduser("~/.cache/ms-playwright")
playwright_browsers_path = DEFAULT_PLAYWRIGHT_BROWSERS_PATH
APP_DATA_DIR = os.path.expanduser("~/.local/share/wLib")
PYWEBVIEW_STORAGE_DIR_NAME = "webview"
PYWEBVIEW_HTTP_PORT = 42001

DEV_MODE = os.environ.get("DEV_MODE", "0") == "1"
VITE_DEV_SERVER = "http://localhost:5173"

if TYPE_CHECKING:
    from webview import Window


class WebviewModule(Protocol):
    def create_window(
        self,
        title: str,
        url: str,
        *,
        js_api: Api,
        width: int,
        height: int,
        background_color: str,
    ) -> Window: ...

    def start(
        self,
        *,
        gui: str,
        debug: bool,
        http_server: bool,
        icon: str | None = None,
        http_port: int | None = None,
        private_mode: bool = True,
        storage_path: str | None = None,
    ) -> None: ...


class ExtensionWindow(Protocol):
    on_top: bool

    def evaluate_js(self, script: str) -> object: ...


webview: WebviewModule | None = None

window_ref: Window | None = None


def configure_playwright_browsers_path() -> str:
    global playwright_browsers_path

    configured_path = (os.environ.get("WLIB_PLAYWRIGHT_BROWSERS_PATH") or "").strip()

    if not configured_path:
        try:
            from core.database import get_setting, init_db

            init_db()
            configured_path = (get_setting("playwright_browsers_path") or "").strip()
        except Exception as e:
            print(f"[wLib] Failed to read Playwright path from settings: {e}")

    playwright_browsers_path = configured_path or DEFAULT_PLAYWRIGHT_BROWSERS_PATH
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = playwright_browsers_path
    return playwright_browsers_path


def configure_ssl_certificates() -> str | None:
    runtime_dir = os.path.dirname(os.path.abspath(sys.executable))
    bundled_certifi_path = os.path.join(
        runtime_dir, "_internal", "certifi", "cacert.pem"
    )
    existing_cert_file = (os.environ.get("SSL_CERT_FILE") or "").strip()
    existing_requests_bundle = (os.environ.get("REQUESTS_CA_BUNDLE") or "").strip()
    selected_cert_file: str | None = None

    if existing_cert_file and os.path.isfile(existing_cert_file):
        selected_cert_file = existing_cert_file
    else:
        if existing_cert_file:
            print(f"[wLib] Ignoring missing SSL_CERT_FILE: {existing_cert_file}")

        certifi_path = ""
        try:
            certifi_module = importlib.import_module("certifi")
            where_fn = getattr(certifi_module, "where", None)
            if callable(where_fn):
                certifi_candidate = where_fn()
                if isinstance(certifi_candidate, str):
                    certifi_path = certifi_candidate
        except Exception as e:
            print(f"[wLib] Failed to resolve certifi CA bundle: {e}")

        candidate_paths = [
            existing_requests_bundle,
            bundled_certifi_path,
            certifi_path,
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/ssl/cert.pem",
        ]
        for candidate in candidate_paths:
            if candidate and os.path.isfile(candidate):
                selected_cert_file = candidate
                break

    if selected_cert_file:
        os.environ["SSL_CERT_FILE"] = selected_cert_file
        os.environ["REQUESTS_CA_BUNDLE"] = selected_cert_file
        os.environ["CURL_CA_BUNDLE"] = selected_cert_file

    default_verify_paths = ssl.get_default_verify_paths()
    ssl_summary = (
        "[wLib] SSL certificates: "
        + f"ssl_cert_file={os.environ.get('SSL_CERT_FILE') or '<unset>'}, "
        + f"requests_ca_bundle={os.environ.get('REQUESTS_CA_BUNDLE') or '<unset>'}, "
        + f"default_cafile={default_verify_paths.cafile or '<unset>'}, "
        + f"default_capath={default_verify_paths.capath or '<unset>'}"
    )
    print(ssl_summary)

    return selected_cert_file


def configure_qt_runtime_environment() -> dict[str, str]:
    session_type = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    wayland_display = (os.environ.get("WAYLAND_DISPLAY") or "").strip()
    display = (os.environ.get("DISPLAY") or "").strip()

    override_platform = (os.environ.get("WLIB_QPA_PLATFORM") or "").strip()
    existing_platform = (os.environ.get("QT_QPA_PLATFORM") or "").strip()

    if override_platform:
        os.environ["QT_QPA_PLATFORM"] = override_platform
        selected_platform = override_platform
        source = "override"
    elif existing_platform:
        selected_platform = existing_platform
        source = "existing"
    elif session_type == "wayland" or wayland_display:
        selected_platform = ""
        source = "auto-wayland"
    elif session_type == "x11" or display:
        selected_platform = "xcb"
        os.environ["QT_QPA_PLATFORM"] = selected_platform
        source = "auto-x11"
    else:
        selected_platform = ""
        source = "auto-default"

    platform_label = selected_platform or "<unset>"
    print(
        f"[wLib] Qt runtime environment: session={session_type or '<unknown>'}, wayland_display={wayland_display or '<unset>'}, display={display or '<unset>'}, qt_qpa_platform={platform_label}, source={source}"
    )

    return {
        "session_type": session_type,
        "wayland_display": wayland_display,
        "display": display,
        "qt_qpa_platform": selected_platform,
        "source": source,
    }


def load_webview_module() -> WebviewModule | None:
    global webview

    if webview is not None:
        return webview

    try:
        webview = cast(WebviewModule, cast(object, importlib.import_module("webview")))
    except Exception as e:
        print(f"[wLib] Failed to import pywebview: {e}")
        webview = None

    return webview


def get_webview_storage_path() -> str:
    storage_path = os.path.join(APP_DATA_DIR, PYWEBVIEW_STORAGE_DIR_NAME)
    os.makedirs(storage_path, exist_ok=True)
    return storage_path


def start_webview(
    webview_module: WebviewModule, *, dev_mode: bool, icon_path: str | None
) -> None:
    storage_path = get_webview_storage_path()

    if dev_mode:
        webview_module.start(
            gui="qt",
            debug=False,
            http_server=True,
            private_mode=False,
            storage_path=storage_path,
            icon=icon_path,
        )
        return

    webview_module.start(
        gui="qt",
        debug=False,
        http_server=True,
        http_port=PYWEBVIEW_HTTP_PORT,
        private_mode=False,
        storage_path=storage_path,
        icon=icon_path,
    )


class ExtensionRequestHandler(BaseHTTPRequestHandler):
    def _find_matching_game(self, url: object) -> dict[str, object] | None:
        if not isinstance(url, str) or not url.strip():
            return None

        from core.database import find_game_by_f95_url

        return find_game_by_f95_url(url)

    def _build_check_payload(self, url: object) -> dict[str, object]:
        matching_game = self._find_matching_game(url)
        payload: dict[str, object] = {"exists": matching_game is not None}

        if matching_game is not None:
            play_status = matching_game.get("play_status")
            if isinstance(play_status, str) and play_status.strip():
                payload["playStatus"] = play_status.strip()

        return payload

    def _get_allowed_origin(self) -> str | None:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return ""

        if origin.startswith(("chrome-extension://", "moz-extension://")):
            return origin

        if DEV_MODE and origin == VITE_DEV_SERVER:
            return origin

        return None

    def _send_cors_headers(self, allowed_origin: str) -> None:
        self.send_header("Vary", "Origin")
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _reject_origin(self) -> None:
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        _ = self.wfile.write(b'{"success": false, "error": "Origin not allowed"}')

    @override
    def log_message(self, format: str, *args: object) -> None:
        # Suppress default HTTP server logs to keep the console clean
        pass

    def do_OPTIONS(self) -> None:
        allowed_origin = self._get_allowed_origin()
        if allowed_origin is None:
            self._reject_origin()
            return

        self.send_response(200, "ok")
        self._send_cors_headers(allowed_origin)
        self.end_headers()

    def do_GET(self) -> None:
        allowed_origin = self._get_allowed_origin()
        if allowed_origin is None:
            self._reject_origin()
            return

        if self.path.startswith("/api/check"):
            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)
            check_url = query.get("url", [""])[0]

            check_payload: dict[str, object] = {"exists": False}
            if check_url:
                try:
                    check_payload = self._build_check_payload(check_url)
                except Exception as e:
                    print(f"[wLib] Database error during extension check: {e}")
                    check_payload = {"exists": False}

            self.send_response(200)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(check_payload).encode())
        elif self.path.startswith("/api/open"):
            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)
            game_url = query.get("url", [""])[0]
            resolved_url = normalize_thread_url(game_url) or game_url

            success = True
            error_message = ""
            if window_ref:
                try:
                    active_window = cast(ExtensionWindow, window_ref)
                    if game_url:
                        matching_game = self._find_matching_game(game_url)
                        if matching_game:
                            resolved_f95_url = matching_game.get("f95_url")
                            if isinstance(resolved_f95_url, str) and resolved_f95_url:
                                resolved_url = resolved_f95_url

                    active_window.on_top = True
                    active_window.on_top = False
                    # Tell the frontend to open the edit modal for this game
                    if resolved_url:
                        import base64

                        resolved_url_text = str(resolved_url)
                        b64_url = base64.b64encode(
                            resolved_url_text.encode("utf-8")
                        ).decode("utf-8")
                        js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-open', {{ detail: {{ url: decodeURIComponent(escape(atob('{b64_url}'))) }} }}));"
                        _ = active_window.evaluate_js(js_code)
                except Exception as e:
                    print(f"[wLib] Failed to process extension open event: {e}")
                    success = False
                    error_message = str(e)
            else:
                success = False
                error_message = "Application window is not ready"

            self.send_response(200)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload: dict[str, object] = {"success": success}
            if error_message:
                payload["error"] = error_message
            _ = self.wfile.write(json.dumps(payload).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        allowed_origin = self._get_allowed_origin()
        if allowed_origin is None:
            self._reject_origin()
            return

        if self.path == "/api/add":
            content_length = int(self.headers.get("Content-Length", "0"))
            post_data = self.rfile.read(content_length)

            try:
                payload = cast(object, json.loads(post_data.decode("utf-8")))

                # We have the payload. Send it to the Vue frontend via PyWebView javascript evaluation
                if window_ref:
                    import base64

                    active_window = cast(ExtensionWindow, window_ref)

                    b64_json = base64.b64encode(
                        json.dumps(payload).encode("utf-8")
                    ).decode("utf-8")
                    js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-add', {{ detail: JSON.parse(decodeURIComponent(escape(atob('{b64_json}')))) }}));"
                    _ = active_window.evaluate_js(js_code)

                self.send_response(200)
                self._send_cors_headers(allowed_origin)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                _ = self.wfile.write(b'{"success": true}')
            except Exception as e:
                self.send_response(400)
                self._send_cors_headers(allowed_origin)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                _ = self.wfile.write(
                    json.dumps({"success": False, "error": str(e)}).encode()
                )
        else:
            self.send_response(404)
            self.end_headers()


def start_extension_server() -> None:
    try:
        server = HTTPServer(("localhost", 8183), ExtensionRequestHandler)
        print("Starting extension HTTP receiver on port 8183...")
        server.serve_forever()
    except Exception as e:
        print(f"Failed to start extension HTTP server: {e}")


def start_vite_dev_server() -> subprocess.Popen[bytes] | None:
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


def _is_same_executable(path_a: str | None, path_b: str | None) -> bool:
    if not path_a or not path_b:
        return False
    try:
        return os.path.samefile(path_a, path_b)
    except Exception:
        return os.path.realpath(path_a) == os.path.realpath(path_b)


def _get_playwright_install_command() -> list[str] | None:
    # Prefer Playwright's packaged driver entrypoint so frozen builds don't
    # accidentally relaunch this app binary via sys.executable.
    try:
        from playwright._impl import _driver as playwright_driver

        driver_executable, driver_cli = playwright_driver.compute_driver_executable()
        if driver_executable and driver_cli:
            return [driver_executable, driver_cli, "install", "chromium"]
    except Exception as e:
        print(f"[wLib] Failed to resolve Playwright driver executable: {e}")

    playwright_cli = shutil.which("playwright")
    if playwright_cli:
        return [playwright_cli, "install", "chromium"]

    if getattr(sys, "frozen", False):
        return None

    return [sys.executable, "-m", "playwright", "install", "chromium"]


def ensure_playwright_browsers() -> bool:
    """Ensure Playwright chromium browser is installed."""
    chromium_path = os.path.join(playwright_browsers_path, "chromium-*")
    import glob

    try:
        _ = importlib.import_module("playwright")
    except ModuleNotFoundError:
        print(
            "[wLib] Playwright Python package is missing; cannot install browser binaries."
        )
        return False

    if glob.glob(chromium_path):
        return True

    install_cmd = _get_playwright_install_command()
    if not install_cmd:
        print("[wLib] Could not locate a Playwright installer command in this build.")
        return False

    if getattr(sys, "frozen", False) and _is_same_executable(
        install_cmd[0], sys.executable
    ):
        print(
            "[wLib] Refusing to run Playwright installer via app executable to avoid recursive relaunch."
        )
        return False

    print("Installing Playwright chromium browser...")
    try:
        _ = subprocess.run(
            install_cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Playwright chromium installed successfully.")
        return True
    except Exception as e:
        print(f"Failed to install Playwright chromium: {e}")
        return False


def ensure_playwright_browsers_async() -> None:
    """Run Playwright browser availability checks outside startup critical path."""

    def _ensure() -> None:
        ok = ensure_playwright_browsers()
        if not ok:
            print(
                "[wLib] Playwright browser check failed; update checks may fail until installed."
            )

    thread = threading.Thread(target=_ensure, daemon=True)
    thread.start()


def main() -> None:
    _ = configure_ssl_certificates()
    _ = configure_playwright_browsers_path()

    if "--install-playwright-if-needed" in sys.argv:
        _ = ensure_playwright_browsers()
        sys.exit(0)

    global window_ref, DEV_MODE

    _ = configure_qt_runtime_environment()
    webview_module = load_webview_module()

    if webview_module is None:
        raise RuntimeError("pywebview is required to run wLib")

    api = Api()
    sync_result = api.sync_extension_files()
    api.set_startup_extension_sync_status(sync_result)
    if sync_result.get("success") is False:
        print(
            f"[wLib] Failed to sync browser extension files on startup: {sync_result.get('error', 'Unknown error')}"
        )
    elif sync_result.get("updated"):
        print(
            f"[wLib] Synced browser extension files on startup: {sync_result.get('installed_version') or '?'} (reason={sync_result.get('reason', 'unknown')})"
        )

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

    window = webview_module.create_window(
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
    start_webview(
        webview_module,
        dev_mode=DEV_MODE,
        icon_path=icon_path if os.path.exists(icon_path) else None,
    )

    # Cleanup Vite server when window closes
    if vite_process:
        vite_process.terminate()


if __name__ == "__main__":
    main()
