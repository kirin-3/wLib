# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import IO, TYPE_CHECKING, Callable, Protocol, cast, override
from collections.abc import Sequence

# --smoke-test must never touch the user's real library. core.database resolves
# the data directory at import time, so pick an isolated temporary one before
# importing it when the caller did not provide WLIB_DATA_DIR.
_smoke_data_dir = ""
if "--smoke-test" in sys.argv and not (os.environ.get("WLIB_DATA_DIR") or "").strip():
    _smoke_data_dir = tempfile.mkdtemp(prefix="wlib-smoke-")
    os.environ["WLIB_DATA_DIR"] = _smoke_data_dir

# Runner dispatch precedes importing the API, WebView, Qt, and server setup.
if __name__ == "__main__" and "--launch" in sys.argv:
    from core.steam_runner import run
    from core.app_log import setup_app_log
    from core.host_platform import get_data_dir

    # The running app owns rotation of the shared log.
    _ = setup_app_log(get_data_dir(), rotate=False)
    try:
        launch_id = int(sys.argv[sys.argv.index("--launch") + 1])
        if launch_id <= 0 or sys.platform != "linux":
            raise ValueError("Invalid game ID or unsupported platform")
    except (ValueError, IndexError):
        print("Usage: wlib --launch <game id>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(run(launch_id))

# The built-in web game player is its own process: it never loads the API,
# the database or the extension server.
if __name__ == "__main__" and "--play-web" in sys.argv:
    from core import web_player

    try:
        _player_args = web_player.parse_args(
            sys.argv[sys.argv.index("--play-web") + 1 :]
        )
    except web_player.UsageError as exc:
        print(f"{exc}\n{web_player.USAGE}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(web_player.run(_player_args))

from core.api import APP_VERSION, Api  # noqa: E402
from core.app_log import setup_app_log  # noqa: E402
from core.diagnostics import record_renderer_stage  # noqa: E402
from core.f95zone import extract_thread_id, normalize_thread_url  # noqa: E402
from core.host_platform import (  # noqa: E402
    get_cache_root,
    get_data_dir,
    get_legacy_browser_dirs,
    is_linux,
    is_windows,
)

APP_DATA_DIR = get_data_dir()
PYWEBVIEW_STORAGE_DIR_NAME = "webview"
PYWEBVIEW_CACHE_VERSION_MARKER = ".webview-cache-version"
PACKAGED_WEBVIEW_CACHE_DIR_NAME = "wlib-bin"
PACKAGED_EXECUTABLE_NAMES = {"wlib-bin", "wlib"}
QTWEBENGINE_CACHE_DIR_NAME = "QtWebEngine"
PYWEBVIEW_QT_PROFILE_NAME = "pywebview"
PYWEBVIEW_HTTP_PORT = 42001
RENDERER_DIAGNOSTICS_LOG = os.path.join(APP_DATA_DIR, "renderer-diagnostics.log")

DEV_MODE = os.environ.get("DEV_MODE", "0") == "1"
VITE_DEV_SERVER = "http://localhost:5173"
EXTENSION_REQUEST_HEADER = "X-wLib-Extension"
# Sync sign-in redirects: path -> (Api method, provider name for the result page).
OAUTH_CALLBACKS = {
    "/dropbox/callback": ("complete_dropbox_callback", "Dropbox"),
    "/gdrive/callback": ("complete_gdrive_callback", "Google Drive"),
}
MAX_EXTENSION_BODY_BYTES = 1024 * 1024
# An oversized body is read and dropped up to this size so the client gets the
# 413 reply; anything larger is cut off with a connection reset.
MAX_DISCARDED_BODY_BYTES = 64 * 1024 * 1024
# Stays under the extension's 10 s request timeout.
DATABASE_READY_TIMEOUT = 8.0
RENDERER_DIAGNOSTICS_MAX_BYTES = 512 * 1024
renderer_log_lock = threading.Lock()

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
        func: Callable[..., None] | None = None,
        args: tuple[object, ...] | None = None,
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


class WaitEvent(Protocol):
    def wait(self, timeout: float | None = None) -> bool: ...


class RendererEvents(Protocol):
    shown: WaitEvent
    loaded: WaitEvent


class RuntimeQtProfile(Protocol):
    def persistentStoragePath(self) -> str: ...

    def cachePath(self) -> str: ...

    def httpUserAgent(self) -> str: ...


class RuntimeQtPage(Protocol):
    def profile(self) -> RuntimeQtProfile: ...


class RuntimeQtWebView(Protocol):
    def page(self) -> RuntimeQtPage: ...


class RuntimeQtWindow(Protocol):
    webview: RuntimeQtWebView


class RendererDiagnosticsWindow(Protocol):
    events: RendererEvents
    native: RuntimeQtWindow | None

    def evaluate_js(self, script: str) -> object: ...


webview: WebviewModule | None = None

window_ref: Window | None = None
api_ref: Api | None = None


def get_packaged_webview_cache_path() -> str:
    return os.path.join(get_cache_root(), PACKAGED_WEBVIEW_CACHE_DIR_NAME)


def get_qtwebengine_cache_root() -> str:
    return os.path.join(get_cache_root(), QTWEBENGINE_CACHE_DIR_NAME)


def get_webview_storage_path(*, create: bool = True) -> str:
    storage_path = os.path.join(APP_DATA_DIR, PYWEBVIEW_STORAGE_DIR_NAME)
    if create:
        os.makedirs(storage_path, exist_ok=True)
    return storage_path


def get_packaged_webview_profile_cache_paths() -> list[str]:
    if is_linux():
        # Qt puts the cache under $XDG_CACHE_HOME/<application name> on Linux.
        # PyWebView leaves Qt's application name as the executable basename
        # (wlib-bin in packaged builds), so the profile cache is
        # ~/.cache/wlib-bin/QtWebEngine/pywebview, not ~/.cache/QtWebEngine.
        application_name = os.path.splitext(os.path.basename(sys.executable))[0]
        return [
            os.path.join(
                get_cache_root(),
                application_name,
                QTWEBENGINE_CACHE_DIR_NAME,
                PYWEBVIEW_QT_PROFILE_NAME,
            ),
        ]
    return [
        os.path.join(get_qtwebengine_cache_root(), PYWEBVIEW_QT_PROFILE_NAME),
    ]


def get_webview_cache_version_marker_path() -> str:
    return os.path.join(APP_DATA_DIR, PYWEBVIEW_CACHE_VERSION_MARKER)


def is_packaged_wlib_runtime() -> bool:
    return bool(getattr(sys, "frozen", False)) and (
        os.path.splitext(os.path.basename(sys.executable))[0].lower()
        in PACKAGED_EXECUTABLE_NAMES
    )


def _clear_webview_cache_path(cache_path: str) -> None:
    if not os.path.lexists(cache_path):
        return
    # Remove a symlinked cache path itself; rmtree() refuses links, which would
    # leave a stale cache behind, and must never touch the link target.
    if os.path.islink(cache_path):
        os.remove(cache_path)
        return
    if os.path.isdir(cache_path):
        shutil.rmtree(cache_path)
        return
    os.remove(cache_path)


def get_webview_cache_key() -> str:
    # Rebuilds that keep APP_VERSION still ship a new UI, so key on its index.html too.
    try:
        index_html = os.path.join(get_bundle_root(), "ui", "dist", "index.html")
        with open(index_html, "rb") as f:
            return APP_VERSION + "+" + hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return APP_VERSION


def ensure_packaged_webview_cache_fresh() -> None:
    if not is_packaged_wlib_runtime():
        print("[wLib] Skipping WebView cache cleanup: not packaged wlib-bin runtime")
        return

    profile_cache_paths = get_packaged_webview_profile_cache_paths()
    legacy_cache_path = get_packaged_webview_cache_path()
    marker_path = get_webview_cache_version_marker_path()
    cache_key = get_webview_cache_key()
    recorded_version = ""

    try:
        with open(marker_path, encoding="utf-8") as marker_file:
            recorded_version = marker_file.read().strip()
    except FileNotFoundError:
        recorded_version = ""
    except Exception as e:
        print(f"[wLib] Failed to read WebView cache version marker: {e}")

    if recorded_version == cache_key:
        print(f"[wLib] WebView cache already fresh for version {cache_key}")
        return

    if not profile_cache_paths:
        print("[wLib] Failed to identify packaged WebView profile cache paths")
        return

    print(
        "[wLib] Clearing packaged WebView cache "
        + f"(previous_version={recorded_version or '<none>'}, "
        + f"current_version={cache_key}, "
        + f"profile_paths={profile_cache_paths}, legacy_path={legacy_cache_path})"
    )

    active_cache_path = ""
    try:
        for cache_path in profile_cache_paths:
            active_cache_path = cache_path
            _clear_webview_cache_path(cache_path)
    except Exception as e:
        print(
            "[wLib] Failed to clear packaged WebView cache at "
            + f"{active_cache_path or '<unknown>'}: {e}"
        )
        return

    try:
        if legacy_cache_path not in profile_cache_paths:
            _clear_webview_cache_path(legacy_cache_path)
    except Exception as e:
        print(
            "[wLib] Failed to clear legacy packaged WebView cache at "
            + f"{legacy_cache_path}: {e}"
        )

    try:
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as marker_file:
            _ = marker_file.write(cache_key + "\n")
    except Exception as e:
        print(f"[wLib] Failed to write WebView cache version marker: {e}")
        return

    print(f"[wLib] Packaged WebView cache is fresh for version {cache_key}")


def configure_ssl_certificates() -> str | None:
    runtime_dir = os.path.dirname(os.path.abspath(sys.executable))
    bundled_certifi_path = os.path.join(
        runtime_dir, "_internal", "certifi", "cacert.pem"
    )
    existing_cert_file = (os.environ.get("SSL_CERT_FILE") or "").strip()
    existing_requests_bundle = (os.environ.get("REQUESTS_CA_BUNDLE") or "").strip()
    existing_curl_bundle = (os.environ.get("CURL_CA_BUNDLE") or "").strip()
    selected_cert_file: str | None = None

    # A stale path in one of these variables overrides OpenSSL's built-in CA
    # locations (a missing SSL_CERT_FILE leaves zero trusted roots), so drop
    # values that do not point at a readable file instead of only logging them.
    for env_name, value in (
        ("SSL_CERT_FILE", existing_cert_file),
        ("REQUESTS_CA_BUNDLE", existing_requests_bundle),
        ("CURL_CA_BUNDLE", existing_curl_bundle),
    ):
        if value and not os.path.isfile(value):
            print(f"[wLib] Ignoring missing {env_name}: {value}")
            _ = os.environ.pop(env_name, None)
    existing_cert_file = (os.environ.get("SSL_CERT_FILE") or "").strip()
    existing_requests_bundle = (os.environ.get("REQUESTS_CA_BUNDLE") or "").strip()
    existing_curl_bundle = (os.environ.get("CURL_CA_BUNDLE") or "").strip()

    if existing_cert_file:
        selected_cert_file = existing_cert_file
    else:
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
            existing_curl_bundle,
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
    linux_host = is_linux()

    if override_platform:
        os.environ["QT_QPA_PLATFORM"] = override_platform
        selected_platform = override_platform
        source = "override"
    elif existing_platform:
        selected_platform = existing_platform
        source = "existing"
    elif linux_host and (session_type == "wayland" or wayland_display):
        selected_platform = ""
        source = "auto-wayland"
    elif linux_host and (session_type == "x11" or display):
        selected_platform = "xcb"
        os.environ["QT_QPA_PLATFORM"] = selected_platform
        source = "auto-x11"
    else:
        selected_platform = ""
        # Windows and other hosts keep Qt's native platform selection.
        source = "auto-default" if linux_host else "auto-native"

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


def _format_renderer_log_value(value: object) -> str:
    if value is None:
        return "<unset>"

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)

    text = str(value).strip()
    return text or "<unset>"


def _renderer_log_targets() -> list[str]:
    targets = [RENDERER_DIAGNOSTICS_LOG]
    launch_log = (os.environ.get("WLIB_LAUNCH_LOG") or "").strip()
    if launch_log and launch_log not in targets:
        targets.append(launch_log)
    appimage_log = (os.environ.get("WLIB_APPIMAGE_LAUNCH_LOG") or "").strip()
    if appimage_log and appimage_log not in targets:
        targets.append(appimage_log)
    return targets


def _trim_renderer_log_file(path: str) -> None:
    # Keep only the newest half once the log passes the cap. Never rewrite
    # through a symlink: that would truncate whatever file it points at.
    try:
        if os.path.islink(path) or os.path.getsize(path) <= RENDERER_DIAGNOSTICS_MAX_BYTES:
            return
        with open(path, "rb") as log_file:
            _ = log_file.seek(-(RENDERER_DIAGNOSTICS_MAX_BYTES // 2), os.SEEK_END)
            tail = log_file.read()
        newline = tail.find(b"\n")
        if newline != -1:
            tail = tail[newline + 1 :]
        with open(path, "wb") as log_file:
            _ = log_file.write(tail)
    except OSError as e:
        print(f"[wLib] Failed to trim renderer diagnostics {path}: {e}")


def log_renderer_diagnostics(stage: str, details: dict[str, object]) -> None:
    record_renderer_stage(stage, details)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    header = f"[wLib] Renderer diagnostics ({stage})"
    lines = [header]
    for key, value in details.items():
        lines.append(f"  {key}={_format_renderer_log_value(value)}")

    print("\n".join(lines))

    entry_lines = [f"=== {timestamp} | renderer:{stage} ==="]
    for key, value in details.items():
        entry_lines.append(f"{key}={_format_renderer_log_value(value)}")

    entry_text = "\n".join(entry_lines) + "\n"
    with renderer_log_lock:
        for target in _renderer_log_targets():
            try:
                target_dir = os.path.dirname(target)
                if target_dir:
                    os.makedirs(target_dir, exist_ok=True)
                with open(target, "a", encoding="utf-8") as log_file:
                    _ = log_file.write(entry_text)
                _trim_renderer_log_file(target)
            except OSError as e:
                print(f"[wLib] Failed to write renderer diagnostics to {target}: {e}")


def collect_renderer_environment_snapshot(*, dev_mode: bool) -> dict[str, object]:
    crash_guard_path = os.path.join(APP_DATA_DIR, ".gpu_crash_guard")
    return {
        "dev_mode": dev_mode,
        "session_type": os.environ.get("XDG_SESSION_TYPE") or "",
        "display": os.environ.get("DISPLAY") or "",
        "wayland_display": os.environ.get("WAYLAND_DISPLAY") or "",
        "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM") or "",
        "qt_quick_backend": os.environ.get("QT_QUICK_BACKEND") or "",
        "qsg_rhi_backend": os.environ.get("QSG_RHI_BACKEND") or "",
        "qt_opengl": os.environ.get("QT_OPENGL") or "",
        "qtwebengine_flags": os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS") or "",
        "libgl_always_software": os.environ.get("LIBGL_ALWAYS_SOFTWARE") or "",
        "gallium_driver": os.environ.get("GALLIUM_DRIVER") or "",
        "wlib_qpa_platform_override": os.environ.get("WLIB_QPA_PLATFORM") or "",
        "gpu_crash_guard_path": crash_guard_path,
        "gpu_crash_guard_present": os.path.isfile(crash_guard_path),
        "renderer_log": RENDERER_DIAGNOSTICS_LOG,
        "launch_log": os.environ.get("WLIB_LAUNCH_LOG") or "",
        "appimage_launch_log": os.environ.get("WLIB_APPIMAGE_LAUNCH_LOG") or "",
    }


def collect_pywebview_module_snapshot(
    webview_module: WebviewModule | None,
) -> dict[str, object]:
    qt_backend_path = ""
    qt_backend_renderer = ""
    qt_backend_webengine = ""

    try:
        qt_backend_module = importlib.import_module("webview.platforms.qt")
        qt_backend_path = str(getattr(qt_backend_module, "__file__", "") or "")
        qt_backend_renderer = str(getattr(qt_backend_module, "renderer", "") or "")
        qt_backend_webengine = str(getattr(qt_backend_module, "is_webengine", "") or "")
    except Exception as e:
        qt_backend_path = f"<error: {e}>"

    return {
        "pywebview_module_path": getattr(webview_module, "__file__", "")
        if webview_module
        else "",
        "pywebview_renderer": getattr(webview_module, "renderer", "")
        if webview_module
        else "",
        "pywebview_qt_backend_path": qt_backend_path,
        "pywebview_qt_backend_renderer": qt_backend_renderer,
        "pywebview_qt_backend_is_webengine": qt_backend_webengine,
    }


def _extract_native_renderer_details(
    window: RendererDiagnosticsWindow,
) -> dict[str, object]:
    native_window = window.native
    native_webview: RuntimeQtWebView | None = None
    native_page = None
    native_profile = None
    native_error = ""

    try:
        if native_window is not None:
            native_webview = native_window.webview
    except Exception as e:
        native_error = str(e)

    if native_webview is not None:
        try:
            native_page = native_webview.page()
        except Exception as e:
            native_error = str(e)

    if native_page is not None:
        try:
            native_profile = native_page.profile()
        except Exception as e:
            native_error = str(e)

    details: dict[str, object] = {
        "native_window_class": type(native_window).__name__ if native_window else "",
        "native_webview_class": type(native_webview).__name__ if native_webview else "",
        "native_page_class": type(native_page).__name__ if native_page else "",
        "native_profile_class": type(native_profile).__name__ if native_profile else "",
    }
    if native_error:
        details["native_introspection_error"] = native_error

    if native_profile is not None:
        try:
            details["qt_profile_storage_path"] = native_profile.persistentStoragePath()
        except Exception as e:
            details["qt_profile_storage_path_error"] = str(e)

        try:
            details["qt_profile_cache_path"] = native_profile.cachePath()
        except Exception as e:
            details["qt_profile_cache_path_error"] = str(e)

        try:
            details["qt_profile_http_user_agent"] = native_profile.httpUserAgent()
        except Exception as e:
            details["qt_profile_http_user_agent_error"] = str(e)

    return details


def _strip_url_query(value: object) -> object:
    # The window URL carries the extension import/open query, which can hold a
    # game title and F95 URL. Diagnostics must never expose library content.
    if not isinstance(value, str) or not value:
        return value
    from urllib.parse import unquote, urlsplit, urlunsplit

    parsed = urlsplit(value)
    # Decode the file path too so redact_paths() can match a home folder that
    # contains spaces or non-ASCII characters (%20, ...).
    path = unquote(parsed.path) if parsed.scheme == "file" else parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _probe_browser_renderer(window: RendererDiagnosticsWindow) -> dict[str, object]:
    probe_script = """
(() => {
  const probe = (kind) => {
    try {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext(kind, { antialias: true, failIfMajorPerformanceCaveat: false });
      if (!gl) {
        return null;
      }

      const debug = gl.getExtension('WEBGL_debug_renderer_info');
      const result = {
        context: kind,
        vendor: String(gl.getParameter(gl.VENDOR) || ''),
        renderer: String(gl.getParameter(gl.RENDERER) || ''),
        version: String(gl.getParameter(gl.VERSION) || ''),
        shadingLanguageVersion: String(gl.getParameter(gl.SHADING_LANGUAGE_VERSION) || ''),
      };

      if (debug) {
        result.unmaskedVendor = String(gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) || '');
        result.unmaskedRenderer = String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) || '');
      }

      return result;
    } catch (error) {
      return { context: kind, error: String(error) };
    }
  };

  return {
    location: window.location.href,
    userAgent: navigator.userAgent,
    hardwareConcurrency: navigator.hardwareConcurrency || null,
    webgl2: probe('webgl2'),
    webgl: probe('webgl'),
  };
})()
"""

    try:
        probe_result = window.evaluate_js(probe_script)
    except Exception as e:
        return {"browser_probe_error": str(e)}

    if not isinstance(probe_result, dict):
        return {"browser_probe_result": probe_result}

    typed_probe_result = cast(dict[str, object], probe_result)

    details: dict[str, object] = {
        "page_location": _strip_url_query(typed_probe_result.get("location")),
        "js_user_agent": typed_probe_result.get("userAgent"),
        "js_hardware_concurrency": typed_probe_result.get("hardwareConcurrency"),
    }

    webgl2 = typed_probe_result.get("webgl2")
    webgl = typed_probe_result.get("webgl")
    selected_probe: dict[str, object] | None = None
    if isinstance(webgl2, dict):
        selected_probe = cast(dict[str, object], webgl2)
    elif isinstance(webgl, dict):
        selected_probe = cast(dict[str, object], webgl)

    details["webgl2_probe"] = webgl2
    details["webgl_probe"] = webgl
    if selected_probe is not None:
        details["detected_webgl_context"] = selected_probe.get("context")
        details["detected_webgl_vendor"] = selected_probe.get(
            "unmaskedVendor"
        ) or selected_probe.get("vendor")
        details["detected_webgl_renderer"] = selected_probe.get(
            "unmaskedRenderer"
        ) or selected_probe.get("renderer")
        details["detected_webgl_version"] = selected_probe.get("version")

    return details


def log_runtime_renderer_diagnostics() -> None:
    window = cast(RendererDiagnosticsWindow | None, window_ref)
    if window is None:
        log_renderer_diagnostics("runtime", {"error": "window_ref is not available"})
        return

    if not window.events.shown.wait(30):
        log_renderer_diagnostics(
            "runtime", {"error": "timed out waiting for window to show"}
        )
        return

    if not window.events.loaded.wait(60):
        log_renderer_diagnostics(
            "runtime",
            {
                "error": "timed out waiting for webview content to load",
                **_extract_native_renderer_details(window),
            },
        )
        return

    runtime_details = {
        "pywebview_renderer": getattr(load_webview_module(), "renderer", ""),
        **_extract_native_renderer_details(window),
        **_probe_browser_renderer(window),
    }
    log_renderer_diagnostics("runtime", runtime_details)


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


def start_runtime_renderer_diagnostics() -> None:
    # PyWebView runs the ``func`` passed to start() on a non-daemon thread and
    # Python waits for it at exit. The probe waits up to 30 s + 60 s for window
    # events, so closing the window before the page loads would keep the
    # process alive for over a minute. Run the probe on a daemon thread.
    threading.Thread(
        target=log_runtime_renderer_diagnostics,
        name="wlib-renderer-diagnostics",
        daemon=True,
    ).start()


def wait_for_port_free(port: int, timeout: float = 10.0) -> bool:
    """Wait until nothing listens on 127.0.0.1:port; True once it is free.

    A wLib that is still closing keeps its page server on this port after it
    released the single-instance port, and a window started meanwhile loads
    from the old server or none at all."""
    deadline = time.monotonic() + timeout
    while True:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
        finally:
            probe.close()
        time.sleep(0.2)


LEGACY_WEBVIEW_STORAGE = os.path.join("~", ".pywebview")
LEGACY_WEBVIEW_CACHE_DIRS = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GrShaderCache",
    "ShaderCache",
)


def adopt_legacy_webview_storage(storage_path: str) -> bool:
    """Copy the profile older versions left in ~/.pywebview, once.

    pywebview's Qt backend reads its storage path when it is imported, and
    the startup diagnostics imported it before start(), so the profile
    (saved UI state, cookies) went to ~/.pywebview instead. Copied, not
    moved: other pywebview apps use that folder too.
    """
    legacy = os.path.expanduser(LEGACY_WEBVIEW_STORAGE)
    try:
        if os.listdir(storage_path) or not os.path.isdir(
            os.path.join(legacy, "Local Storage")
        ):
            return False
        _ = shutil.copytree(
            legacy,
            storage_path,
            dirs_exist_ok=True,
            # Chromium rebuilds its caches; only the profile data is worth copying.
            ignore=shutil.ignore_patterns(*LEGACY_WEBVIEW_CACHE_DIRS),
        )
    except OSError as exc:
        print(f"[wLib] Could not copy the old WebView profile: {exc}")
        return False
    return True


def point_qt_backend_at_storage(storage_path: str) -> None:
    """Make an already-imported Qt backend use wLib's storage folder."""
    qt_backend = sys.modules.get("webview.platforms.qt")
    if qt_backend is not None:
        setattr(qt_backend, "_profile_storage_path", storage_path)


def start_webview(
    webview_module: WebviewModule, *, dev_mode: bool, icon_path: str | None
) -> None:
    storage_path = get_webview_storage_path()
    _ = adopt_legacy_webview_storage(storage_path)
    point_qt_backend_at_storage(storage_path)

    if dev_mode:
        webview_module.start(
            func=start_runtime_renderer_diagnostics,
            gui="qt",
            debug=False,
            http_server=True,
            private_mode=False,
            storage_path=storage_path,
            icon=icon_path,
        )
        return

    # The fixed port keeps the page origin, and so its saved UI state, the
    # same across runs. If the old process never lets go, a free port still
    # gives a working window.
    port: int | None = (
        PYWEBVIEW_HTTP_PORT if wait_for_port_free(PYWEBVIEW_HTTP_PORT) else None
    )
    if port is None:
        print(f"[wLib] Port {PYWEBVIEW_HTTP_PORT} is still in use; using a free port.")
    webview_module.start(
        func=start_runtime_renderer_diagnostics,
        gui="qt",
        debug=False,
        http_server=True,
        http_port=port,
        private_mode=False,
        storage_path=storage_path,
        icon=icon_path,
    )


class ExtensionRequestHandler(BaseHTTPRequestHandler):
    # Idle or stalled clients would otherwise hold a server thread forever.
    timeout = 10  # pyright: ignore[reportUnannotatedClassAttribute]
    close_connection: bool

    @override
    def parse_request(self) -> bool:
        if not super().parse_request():
            return False

        hosts = self.headers.get_all("Host", [])
        if len(hosts) == 1 and hosts[0].lower() in {
            "localhost:8183",
            "127.0.0.1:8183",
        }:
            return True

        self._discard_request_body()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        _ = self.wfile.write(b'{"success": false, "error": "Host not allowed"}')
        return False

    def _discard_request_body(self) -> None:
        # Closing with unread request data makes the OS reset the connection
        # (always on Windows), so the client sees an abort instead of our reply.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if not 0 < length <= MAX_DISCARDED_BODY_BYTES:
            return
        try:
            while length > 0:
                chunk = self.rfile.read(min(length, 64 * 1024))
                if not chunk:
                    break
                length -= len(chunk)
        except OSError:
            pass

    def _runner_request(self) -> bool:
        """True for a request from wLib's own Steam runner (see steam_runner.runner_token)."""
        import secrets

        from core.steam_runner import RUNNER_HEADER, runner_token

        sent = self.headers.get(RUNNER_HEADER) or ""
        try:
            expected = runner_token()
        except OSError:
            return False
        return bool(sent) and secrets.compare_digest(sent, expected)

    def _wait_for_database(self) -> bool:
        # main() serves this port before Api() has run init_db(), so database
        # endpoints wait for the schema instead of reading it mid-migration.
        ready = getattr(getattr(self, "server", None), "database_ready", None)
        return not isinstance(ready, threading.Event) or ready.wait(
            DATABASE_READY_TIMEOUT
        )

    def _find_matching_game(
        self, url: object, ids: Sequence[str] = ()
    ) -> dict[str, object] | None:
        if not isinstance(url, str) or not url.strip():
            return None

        from core.database import find_game_by_f95_url, get_game
        from core.game_lookup import resolve_page

        thread_game = find_game_by_f95_url(url)
        if thread_game:
            return thread_game
        matches = resolve_page(url, ids)["matches"]
        return get_game(matches[0]) if matches else None

    def _build_check_payload(
        self, url: object, ids: Sequence[str] = ()
    ) -> dict[str, object]:
        matching_game = (
            self._find_matching_game(url, ids) if ids else self._find_matching_game(url)
        )
        payload: dict[str, object] = {"exists": matching_game is not None}

        if matching_game is not None:
            if isinstance(matching_game.get("id"), int):
                payload["gameId"] = matching_game["id"]
            if (
                extract_thread_id(url)
                and not matching_game.get("f95_url")
                and isinstance(matching_game.get("id"), int)
            ):
                from core.game_lookup import resolve_page

                if len(resolve_page(str(url), ids)["matches"]) == 1:
                    payload["canAttachThread"] = True
            payload["wishlisted"] = not str(matching_game.get("exe_path") or "").strip()
            play_status = matching_game.get("play_status")
            if isinstance(play_status, str) and play_status.strip():
                payload["playStatus"] = play_status.strip()

        return payload

    def _prepare_game_payload(self, raw: object) -> dict[str, object]:
        from core.game_lookup import resolve_page
        from core.store_links import coerce_store_links

        if not isinstance(raw, dict):
            raise ValueError("Request body must be a JSON object")
        payload = dict(cast(dict[str, object], raw))
        page_url = str(payload.get("pageUrl") or payload.get("url") or "")
        links = coerce_store_links(payload.get("links"))
        if not extract_thread_id(payload.get("url")) and not links:
            raise ValueError("An F95 thread URL or store link is required")
        resolved = resolve_page(
            page_url, [f"{link['source']}:{link['id']}" for link in links]
        )
        links = coerce_store_links([*links, *resolved["ids"]])
        for key, value in resolved["catalog_entry"].items():
            if value:
                payload[key] = value
        payload["url"] = (
            normalize_thread_url(payload.get("url"))
            if extract_thread_id(payload.get("url"))
            else ""
        )
        payload["pageUrl"] = page_url
        payload["links"] = links
        if not str(payload.get("title") or "").strip() or not (payload["url"] or links):
            raise ValueError("A title and an F95 thread URL or store link are required")
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
        self.send_header(
            "Access-Control-Allow-Headers", f"Content-Type, {EXTENSION_REQUEST_HEADER}"
        )

    def _reject_origin(self) -> None:
        self._discard_request_body()
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

        self._discard_request_body()
        self.send_response(200, "ok")
        self._send_cors_headers(allowed_origin)
        self.end_headers()

    def _is_bridge_origin_allowed(self, origin: str) -> bool:
        return origin.startswith(
            ("chrome-extension://", "moz-extension://")
        )

    def _handle_bridge_upgrade(self) -> None:
        upgrade = (self.headers.get("Upgrade") or "").lower()
        if "websocket" not in upgrade:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(
                b'{"success": false, "error": "WebSocket upgrade required"}'
            )
            return

        origin = (self.headers.get("Origin") or "").strip()
        if not self._is_bridge_origin_allowed(origin):
            self._reject_origin()
            return

        key = (self.headers.get("Sec-WebSocket-Key") or "").strip()
        if not key:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(
                b'{"success": false, "error": "Missing Sec-WebSocket-Key"}'
            )
            return

        try:
            from core.extension_ws import websocket_accept_key

            accept = websocket_accept_key(key)
        except Exception:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(
                b'{"success": false, "error": "Invalid Sec-WebSocket-Key"}'
            )
            return

        self._discard_request_body()
        # RFC 6455 requires an HTTP/1.1 status line; Firefox drops the socket
        # on BaseHTTPRequestHandler's default "HTTP/1.0 101" while Chromium
        # accepts it.
        self.protocol_version: str = "HTTP/1.1"
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        sock = cast(socket.socket, self.request)
        try:
            sock.settimeout(60)
        except OSError:
            pass
        try:
            from core import extension_bridge

            extension_bridge.serve_bridge_connection(sock, self.rfile, self.wfile)
        except Exception as exc:
            print(f"[wLib] Bridge connection failed: {exc}")
        finally:
            self.close_connection = True

    def do_GET(self) -> None:
        from urllib.parse import urlparse as _urlparse

        if _urlparse(self.path).path.startswith("/covers/"):
            from core.cover_cards import card_path
            from core.game_covers import COVER_TYPES, cover_path, cached_cover_path

            name = _urlparse(self.path).path.removeprefix("/covers/")
            if name.startswith("cards/"):
                path = card_path(name.removeprefix("cards/"))
            elif name.startswith("cache/"):
                path = cached_cover_path(name.removeprefix("cache/"))
            else:
                path = cover_path(name)
            try:
                if path is None:
                    raise FileNotFoundError
                data = path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", COVER_TYPES[path.suffix[1:]])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.end_headers()
            _ = self.wfile.write(data)
            return

        if _urlparse(self.path).path == "/api/bridge":
            self._handle_bridge_upgrade()
            return

        allowed_origin = self._get_allowed_origin()
        if allowed_origin is None:
            self._reject_origin()
            return

        self._discard_request_body()
        from urllib.parse import urlparse, parse_qs

        sign_in = OAUTH_CALLBACKS.get(urlparse(self.path).path)
        if sign_in is not None:
            method, label = sign_in
            query = parse_qs(urlparse(self.path).query)
            callback_api = cast(ExtensionServer, self.server).sync_api
            result = (
                cast(
                    Callable[[str, str], dict[str, object]],
                    getattr(callback_api, method),
                )(query.get("code", [""])[0], query.get("state", [""])[0])
                if callback_api is not None
                else {"success": False}
            )
            self.send_response(200 if result.get("success") else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.end_headers()
            message = (
                f"{label} connected. You can close this tab."
                if result.get("success")
                else f"{label} sign-in failed or expired. Return to wLib and connect again."
            )
            _ = self.wfile.write(
                f"<!doctype html><html lang='en'><meta charset='utf-8'><title>wLib {label}</title><p>{message}</p></html>".encode(
                    "utf-8"
                )
            )
        elif urlparse(self.path).path == "/api/check":
            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)
            check_url = query.get("url", [""])[0]

            check_payload: dict[str, object] = {"exists": False}
            if check_url and not self._wait_for_database():
                self.send_response(503)
                self._send_cors_headers(allowed_origin)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                not_ready = {
                    "exists": False,
                    "error": "Application is not ready",
                    "app_version": APP_VERSION,
                }
                _ = self.wfile.write(json.dumps(not_ready).encode())
                return
            if check_url:
                try:
                    check_payload = self._build_check_payload(
                        check_url, query.get("id", [])
                    )
                except Exception as e:
                    print(f"[wLib] Database error during extension check: {e}")
                    check_payload = {"exists": False}

            check_payload["app_version"] = APP_VERSION
            sync_api = getattr(getattr(self, "server", None), "sync_api", None)
            if sync_api is None:
                sync_api = api_ref
            if sync_api is not None:
                try:
                    sync_status = sync_api.get_startup_extension_sync_status()
                    installed_version = sync_status.get("installed_version")
                    if sync_status.get("success") and installed_version:
                        check_payload["extension_version"] = installed_version
                except Exception:
                    pass

            self.send_response(200)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(check_payload).encode())
        elif urlparse(self.path).path == "/api/running":
            if not self._runner_request():
                self._reject_origin()
                return
            ready = api_ref is not None and window_ref is not None and self._wait_for_database()
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            games = api_ref.get_running_games() if ready and api_ref is not None else []
            _ = self.wfile.write(json.dumps({"success": ready, "games": games}).encode())
        elif urlparse(self.path).path == "/api/open":
            from urllib.parse import urlparse, parse_qs

            # Any web page can fire this GET without an Origin (e.g. <img src>);
            # require an allowed Origin or the header only the extension sends.
            if not allowed_origin and not self.headers.get(EXTENSION_REQUEST_HEADER):
                self._reject_origin()
                return

            query = parse_qs(urlparse(self.path).query)
            game_url = query.get("url", [""])[0]
            resolved_url = normalize_thread_url(game_url) or game_url
            resolved_game_id: int | None = None

            success = True
            error_message = ""
            if window_ref:
                try:
                    active_window = cast(ExtensionWindow, window_ref)
                    if game_url:
                        matching_game = (
                            self._find_matching_game(game_url, query.get("id", []))
                            if query.get("id")
                            else self._find_matching_game(game_url)
                        )
                        if matching_game:
                            candidate_id = matching_game.get("id")
                            resolved_game_id = (
                                candidate_id if isinstance(candidate_id, int) else None
                            )
                            resolved_f95_url = matching_game.get("f95_url")
                            if isinstance(resolved_f95_url, str) and resolved_f95_url:
                                resolved_url = resolved_f95_url

                    # Raising alone leaves a minimized or hidden window where
                    # it is; bring it back first.
                    try:
                        cast(_ShowableWindow, cast(object, active_window)).show()
                        if window_minimized.is_set():
                            cast(_ShowableWindow, cast(object, active_window)).restore()
                    except Exception:
                        pass
                    active_window.on_top = True
                    active_window.on_top = False
                    # Tell the frontend to open the edit modal for this game
                    if resolved_url:
                        import base64

                        resolved_url_text = str(resolved_url)
                        b64_url = base64.b64encode(
                            resolved_url_text.encode("utf-8")
                        ).decode("utf-8")
                        id_field = (
                            f", gameId: {resolved_game_id}"
                            if resolved_game_id is not None
                            else ""
                        )
                        js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-open', {{ detail: {{ url: decodeURIComponent(escape(atob('{b64_url}'))){id_field} }} }}));"
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
            if resolved_game_id is not None:
                payload["gameId"] = resolved_game_id
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

        try:
            body_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            body_length = 0
        if body_length > MAX_EXTENSION_BODY_BYTES:
            # Reply first, then drop the body: closing with it unread would
            # reset the connection before the client reads the 413.
            self.close_connection = True
            self.send_response(413)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            _ = self.wfile.write(
                b'{"success": false, "error": "Request body is too large"}'
            )
            self.wfile.flush()
            self._discard_request_body()
            return

        if self.path == "/api/launch-check":
            if not self._runner_request():
                self._reject_origin()
                return
            check_status, check_result = 200, {"success": True}
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                raw_check = cast(object, json.loads(self.rfile.read(length)))
                if not isinstance(raw_check, dict):
                    raise ValueError("Invalid launch check")
                check = cast(dict[str, object], raw_check)
                launch_id = check.get("gameId")
                if not isinstance(launch_id, int) or isinstance(launch_id, bool) or launch_id <= 0:
                    raise ValueError("Invalid game ID")
                if api_ref is not None:
                    reason = api_ref.reserve_runner_launch(
                        launch_id,
                        str(check.get("prefix") or ""),
                        sandboxed=check.get("sandboxed") is True,
                        umu=check.get("umu") is True,
                        blocked=check.get("blocked") is True,
                        title=str(check.get("title") or ""),
                    )
                    if reason:
                        check_result = {"success": False, "error": reason}
            except (ValueError, TypeError, OSError):
                check_status, check_result = 400, {"success": False, "error": "Invalid launch check"}
            self.send_response(check_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(check_result).encode())
            return
        if self.path == "/api/runner-event":
            if not self._runner_request():
                self._reject_origin()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                raw = cast(object, json.loads(self.rfile.read(length)))
                if not isinstance(raw, dict):
                    raise ValueError("Invalid runner event")
                payload = cast(dict[str, object], raw)
                if payload.get("state") not in ("started", "ended", "error"):
                    raise ValueError("Invalid runner event")
                if not window_ref:
                    raise ValueError("Application window is not ready")
                ended_id = payload.get("gameId")
                if (
                    payload.get("state") in ("ended", "error")
                    and api_ref is not None
                    and isinstance(ended_id, int)
                ):
                    api_ref.release_runner_launch(ended_id)
                active_window = cast(ExtensionWindow, window_ref)
                _ = active_window.evaluate_js("window.dispatchEvent(new Event('wlib-refresh-library'))")
                if payload.get("state") == "error":
                    detail = json.dumps({"error": str(payload.get("error") or "Launch failed")})
                    _ = active_window.evaluate_js(f"window.dispatchEvent(new CustomEvent('wlib-launch-error', {{detail: {detail}}}))")
                runner_ok = True
                runner_status = 200
            except (ValueError, TypeError, OSError):
                runner_ok = False
                runner_status = 400
            except Exception:
                logging.getLogger(__name__).exception("Runner event failed")
                runner_ok = False
                runner_status = 500
            self.send_response(runner_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps({"success": runner_ok}).encode())
        elif self.path == "/api/wishlist":
            status = 200
            wishlist_response: dict[str, object]
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= content_length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                raw_payload = cast(object, json.loads(self.rfile.read(content_length).decode("utf-8")))
                if not isinstance(raw_payload, dict):
                    raise ValueError("Request body must be a JSON object")
                # Preparing the payload resolves store links, which reads the
                # database; wait for startup init_db() so an early wishlist
                # gets the documented 503 instead of a raw SQLite error.
                if not self._wait_for_database() or api_ref is None:
                    status = 503
                    wishlist_response = {"success": False, "error": "Application is not ready"}
                else:
                    payload = self._prepare_game_payload(
                        cast(dict[str, object], raw_payload)
                    )
                    wishlist_response = api_ref.add_game(
                        title=str(payload.get("title") or ""),
                        exe_path="",
                        f95_url=str(payload.get("url") or ""),
                        version=str(payload.get("version") or ""),
                        cover_image=str(payload.get("coverImage") or ""),
                        tags=", ".join(
                            str(item) for item in cast(list[object], payload["tags"])
                        )
                        if isinstance(payload.get("tags"), list)
                        else str(payload.get("tags") or ""),
                        rating=str(payload.get("rating") or ""),
                        developer=str(payload.get("developer") or ""),
                        engine=str(payload.get("engine") or ""),
                        links=payload.get("links"),
                    )
                    if wishlist_response.get("attached") is True:
                        wishlist_response = {
                            **wishlist_response,
                            "success": False,
                            "error": "Game is already in wLib; F95 thread attached",
                            "error_code": "duplicate_url",
                        }
                    if wishlist_response.get("error_code") == "duplicate_url":
                        status = 409
                    elif not wishlist_response.get("success"):
                        status = 400
                    elif window_ref:
                        try:
                            active_window = cast(ExtensionWindow, window_ref)
                            _ = active_window.evaluate_js(
                                "window.dispatchEvent(new Event('wlib-refresh-library'))"
                            )
                        except Exception as e:
                            print(f"[wLib] Failed to refresh library after wishlist: {e}")
            except Exception as e:
                status = 400
                wishlist_response = {"success": False, "error": str(e)}

            self.send_response(status)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(wishlist_response).encode())
        elif self.path == "/api/add":
            status = 200
            response: dict[str, object] = {"success": True}
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= content_length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                post_data = self.rfile.read(content_length)
                payload = cast(object, json.loads(post_data.decode("utf-8")))

                # We have the payload. Send it to the Vue frontend via PyWebView javascript evaluation
                if window_ref:
                    payload = self._prepare_game_payload(payload)
                    import base64

                    active_window = cast(ExtensionWindow, window_ref)

                    b64_json = base64.b64encode(
                        json.dumps(payload).encode("utf-8")
                    ).decode("utf-8")
                    js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-add', {{ detail: JSON.parse(decodeURIComponent(escape(atob('{b64_json}')))) }}));"
                    _ = active_window.evaluate_js(js_code)
                else:
                    status = 503
                    response = {"success": False, "error": "Application window is not ready"}
            except Exception as e:
                status = 400
                response = {"success": False, "error": str(e)}

            self.send_response(status)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(response).encode())
        elif self.path == "/api/thread-links":
            status = 200
            response = {"success": True}
            try:
                from core.database import find_game_by_f95_url, replace_game_links
                from core.store_links import coerce_store_links

                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                raw = cast(object, json.loads(self.rfile.read(length).decode("utf-8")))
                if not isinstance(raw, dict):
                    raise ValueError("Request body must be a JSON object")
                body = cast(dict[str, object], raw)
                if not extract_thread_id(body.get("url")) or not isinstance(
                    body.get("links"), list
                ):
                    raise ValueError("A thread URL and links list are required")
                if not self._wait_for_database():
                    status = 503
                    response = {"success": False, "error": "Application is not ready"}
                elif (game := find_game_by_f95_url(body.get("url"))) is None:
                    status = 404
                    response = {"success": False, "error": "Game not found"}
                else:
                    replace_game_links(
                        int(str(game["id"])),
                        "f95_post",
                        coerce_store_links(body["links"]),
                    )
            except Exception as exc:
                status = 400
                response = {"success": False, "error": str(exc)}
            self.send_response(status)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()


class ExtensionServer(ThreadingHTTPServer):
    # The port doubles as the single-instance lock. Windows SO_REUSEADDR would
    # let a second wLib bind it while the first is still listening.
    allow_reuse_address: bool = sys.platform != "win32"
    sync_api: Api | None = None
    # Set by main() while Api() has not run init_db() yet.
    database_ready: threading.Event | None = None


def bind_extension_server() -> ThreadingHTTPServer | None:
    try:
        return ExtensionServer(("127.0.0.1", 8183), ExtensionRequestHandler)
    except OSError as e:
        print(f"Failed to start extension HTTP server: {e}")
        return None


window_minimized = threading.Event()


class _DestroyableWindow(Protocol):
    def destroy(self) -> None: ...


class _ShowableWindow(Protocol):
    def show(self) -> None: ...

    def restore(self) -> None: ...


class _EventHook(Protocol):
    def __iadd__(self, handler: Callable[[], None]) -> "_EventHook": ...


def track_minimized(window: object) -> None:
    """Keep window_minimized current, so "Open in wLib" restores only a
    minimized window (restoring a maximized one would shrink it)."""
    events = cast(object, getattr(window, "events", None))
    for name, handler in (
        ("minimized", window_minimized.set),
        ("restored", window_minimized.clear),
        ("maximized", window_minimized.clear),
    ):
        hook = cast(_EventHook | None, getattr(events, name, None))
        if hook is not None:
            hook += handler


def focus_running_instance() -> bool:
    """Ask a wLib already holding port 8183 to show its window; False if it isn't wLib."""
    import urllib.request

    request = urllib.request.Request(
        "http://127.0.0.1:8183/api/open", headers={EXTENSION_REQUEST_HEADER: "1"}
    )
    # Never route this loopback call through a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with cast(IO[bytes], opener.open(request, timeout=3)) as response:
            payload = cast(object, json.loads(response.read()))
            return isinstance(payload, dict) and "success" in cast(
                dict[str, object], payload
            )
    except Exception:
        # A stale or foreign process may hold the port and answer with
        # something other than HTTP/JSON (BadStatusLine, truncated body, ...).
        # Treat every failure as "not wLib" so this launch can start normally.
        return False


def gaming_mode_start_url(url: str, gaming_mode: bool) -> str:
    """Make the Gaming Mode flag available before the frontend mounts."""
    if not gaming_mode:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}gamingMode=1"


def start_vite_dev_server() -> subprocess.Popen[bytes] | None:
    print("Starting Vite dev server...")
    # Change dir to the 'ui' folder relative to main.py
    ui_dir = os.path.join(os.path.dirname(__file__), "ui")
    if os.path.exists(ui_dir):
        # We start process and let it run
        return subprocess.Popen(
            ["npm.cmd" if os.name == "nt" else "npm", "run", "dev"],
            cwd=ui_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return None


def stop_vite_dev_server(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    try:
        process.terminate()
    except Exception as e:
        print(f"[wLib] Failed to stop Vite dev server: {e}")


def get_bundle_root() -> str:
    if getattr(sys, "frozen", False):
        return str(
            getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        )
    return os.path.dirname(os.path.abspath(__file__))


def cleanup_legacy_browser_data() -> None:
    data_dir = os.path.realpath(get_data_dir())
    for path in get_legacy_browser_dirs():
        try:
            resolved = os.path.realpath(path)
            if (
                os.path.commonpath([data_dir, resolved]) != data_dir
                or resolved == data_dir
            ):
                print(
                    f"[wLib] Skipping legacy browser directory outside data directory: {path}"
                )
                continue
            if os.path.isdir(path):
                shutil.rmtree(path)
                print(f"[wLib] Removed legacy browser directory: {path}")
        except Exception as exc:
            print(f"[wLib] Failed to remove legacy browser directory {path}: {exc}")


def _startup_cleanup() -> None:
    cleanup_legacy_browser_data()
    try:
        from core.game_sandbox import prune_removed_sandboxes

        _ = prune_removed_sandboxes()
    except Exception as exc:
        print(f"[wLib] Failed to prune removed sandboxes: {exc}")


def cleanup_legacy_browser_data_async() -> None:
    threading.Thread(target=_startup_cleanup, daemon=True).start()


def run_packaged_smoke_test() -> int:
    """Validate a source or frozen runtime without opening the desktop UI."""
    diagnostics: dict[str, object] = {
        "success": False,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "bundle_root": get_bundle_root(),
        "data_dir": APP_DATA_DIR,
    }
    try:
        for module_name in (
            "certifi",
            "webview",
            # Loads the Qt WebEngine binaries; a Qt core / WebEngine version
            # mismatch only fails here, not on `import webview`.
            "PyQt6.QtWebEngineWidgets",
            "cryptography.hazmat.primitives.ciphers.aead",
        ):
            _ = importlib.import_module(module_name)

        from core.database import init_db

        init_db()
        bundle_root = get_bundle_root()
        required_assets = (
            os.path.join(bundle_root, "ui", "dist", "index.html"),
            os.path.join(bundle_root, "extension", "manifest.json"),
            os.path.join(bundle_root, "wlib.png"),
        )
        missing_assets = [path for path in required_assets if not os.path.isfile(path)]
        if missing_assets:
            raise FileNotFoundError(
                "Missing packaged assets: " + ", ".join(missing_assets)
            )

        diagnostics.update(
            {
                "success": True,
                "assets": list(required_assets),
            }
        )
        print(json.dumps(diagnostics, ensure_ascii=False))
        return 0
    except Exception as error:
        diagnostics["error"] = str(error)
        print(json.dumps(diagnostics, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if _smoke_data_dir:
            shutil.rmtree(_smoke_data_dir, ignore_errors=True)


def data_dir_problem(path: str) -> str | None:
    """Why wLib cannot keep its library in `path`, or None when it can."""
    try:
        os.makedirs(path, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".wlib-write-check-"):
            pass
    except OSError as exc:
        reason = exc.strerror or str(exc)
        return f"wLib cannot write to its data folder {path}: {reason}."
    return None


def show_fatal_startup_error(message: str) -> None:
    """Tell the user why wLib quit before its window could open.

    The app log may be what cannot be written, so this uses stderr and the
    desktop's own dialog or notification tools, whichever exists.
    """
    print(f"[wLib] {message}", file=sys.stderr)
    title = "wLib cannot start"
    if is_windows():
        # The Windows build has no console, so stderr goes nowhere.
        try:
            import ctypes

            load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
            message_box = load_dll("user32").MessageBoxW
            # MB_OK | MB_ICONERROR | MB_SETFOREGROUND
            _ = cast(object, message_box(None, message, title, 0x10 | 0x10000))
        except (OSError, AttributeError):
            pass
        return
    for command in (
        ["zenity", "--error", f"--title={title}", f"--text={message}"],
        ["kdialog", "--title", title, "--error", message],
        ["notify-send", "--urgency=critical", title, message],
    ):
        if shutil.which(command[0]) is None:
            continue
        try:
            _ = subprocess.run(command, check=False, timeout=600)
        except (OSError, subprocess.SubprocessError):
            continue
        return


def install_shutdown_signals(close: Callable[[], None]) -> bool:
    """Close the window on SIGTERM, SIGHUP or SIGINT, so shutdown runs.

    Python runs signal handlers only between bytecodes of the main thread,
    which the Qt event loop does not give back. The C-level handler still
    writes the signal number to the wakeup fd at once, so a thread waiting
    on that pipe closes the window, and the normal shutdown path saves
    playtime and finishes the last sync. A signal repeated after five
    seconds (a stuck shutdown) exits at once.
    """
    import signal

    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        return False
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    _ = signal.set_wakeup_fd(write_fd, warn_on_full_buffer=False)
    numbers = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
    for number in numbers:
        _ = signal.signal(number, lambda _signum, _frame: None)

    def watch() -> None:
        first: float | None = None
        while True:
            try:
                data = os.read(read_fd, 1)
            except OSError:
                return
            if not data:
                return
            if data[0] not in numbers:
                continue
            now = time.monotonic()
            if first is None:
                first = now
                print(f"[wLib] Received signal {data[0]}; closing.")
                try:
                    close()
                except Exception as exc:
                    print(f"[wLib] Could not close the window: {exc}")
            elif now - first >= 5:
                # A terminal closing sends SIGHUP and the launcher script
                # forwards SIGTERM; only a later repeat means "quit now".
                os._exit(128 + data[0])

    threading.Thread(target=watch, name="shutdown-signals", daemon=True).start()
    return True


def restore_ctrl_c() -> None:
    """Let Ctrl+C stop startup even when the launcher started wLib with it ignored.

    `wlib.sh` runs the app as a background job so it can forward SIGTERM, and
    a shell starts background jobs with SIGINT ignored; Python then leaves it
    ignored. Once the window exists, `install_shutdown_signals` takes over.
    """
    import signal

    if threading.current_thread() is threading.main_thread() and (
        signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    ):
        _ = signal.signal(signal.SIGINT, signal.default_int_handler)


def main() -> None:
    restore_ctrl_c()
    ssl_summary = configure_ssl_certificates()

    global window_ref, api_ref, DEV_MODE

    # A second copy would share the database.
    extension_server = bind_extension_server()
    if extension_server is None and focus_running_instance():
        print("[wLib] wLib is already running; showing its window instead.")
        return

    problem = data_dir_problem(APP_DATA_DIR)
    if problem:
        if extension_server is not None:
            extension_server.server_close()
        show_fatal_startup_error(
            f"{problem} Make the folder writable, or set WLIB_DATA_DIR to another folder."
        )
        raise SystemExit(1)

    _ = setup_app_log(APP_DATA_DIR)
    if is_linux():
        from core.steam_script import ensure_launch_script

        try:
            _ = ensure_launch_script()
        except OSError as exc:
            print(f"[wLib] Failed to update Steam launch script: {exc}")
    print(f"[wLib] SSL certificates: {ssl_summary or 'system defaults'}")
    database_ready = threading.Event()
    if extension_server is not None:
        # Serve before the slow WebView/API startup. The port is the
        # single-instance lock, so a second launch during this window must
        # still get a reply and exit instead of opening a second window that
        # shares the database.
        print("Starting extension HTTP receiver on port 8183...")
        cast(ExtensionServer, extension_server).database_ready = database_ready
        threading.Thread(
            target=extension_server.serve_forever, daemon=True
        ).start()
    _ = configure_qt_runtime_environment()
    ensure_packaged_webview_cache_fresh()
    webview_module = load_webview_module()

    if webview_module is None:
        raise RuntimeError("pywebview is required to run wLib")

    log_renderer_diagnostics(
        "startup",
        {
            **collect_renderer_environment_snapshot(dev_mode=DEV_MODE),
            **collect_pywebview_module_snapshot(webview_module),
        },
    )

    api = Api()
    api._start_steam_sync(os.path.join(get_bundle_root(), "wlib.png"))  # pyright: ignore[reportPrivateUsage]
    api_ref = api
    database_ready.set()
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
    script_dir = get_bundle_root()

    url = os.path.join(script_dir, "ui", "dist", "index.html")
    vite_process = None

    if DEV_MODE:
        url = VITE_DEV_SERVER
        vite_process = start_vite_dev_server()
        # Give Vite a second to start
        time.sleep(2)

    from core import steam_env

    url = gaming_mode_start_url(url, steam_env.gaming_mode())

    library_sync_started = False
    try:
        window = webview_module.create_window(
            "wLib - Game Manager",
            url,
            js_api=api,
            width=1200,
            height=800,
            background_color="#101014",
        )
        window_ref = window
        _ = install_shutdown_signals(
            cast(_DestroyableWindow, cast(object, window)).destroy
        )
        track_minimized(window)
        api.set_window(window)
        cleanup_legacy_browser_data_async()

        if extension_server is not None:
            cast(ExtensionServer, extension_server).sync_api = api
            api.library_sync.server_available = True

        def start_cover_cache() -> None:
            if cast(RendererDiagnosticsWindow, cast(object, window)).events.loaded.wait(60):
                api.cover_cache.start()
                api.cover_cards.start()

        threading.Thread(target=start_cover_cache, daemon=True).start()
        api.library_sync.start()
        library_sync_started = True

        # Start the PyWebView UI loop
        # We set debug=False so it doesn't open the Web Inspector automatically
        icon_path = os.path.join(script_dir, "wlib.png")
        start_webview(
            webview_module,
            dev_mode=DEV_MODE,
            icon_path=icon_path if os.path.exists(icon_path) else None,
        )
    finally:
        if api.steam_sync is not None:
            api.steam_sync.stop()
        api.cover_cache.stop()
        api.cover_cards.stop()
        # Release the port before the final sync can take up to 10 seconds:
        # relaunching wLib while it shuts down must be able to bind it instead
        # of finding this closing instance and exiting.
        if extension_server is not None:
            try:
                extension_server.shutdown()
                extension_server.server_close()
            except Exception as e:
                print(f"[wLib] Failed to stop extension HTTP server: {e}")
        # The UI loop has ended. Stop playtime UI events and write the final
        # playtime delta before the final library sync can take ten seconds;
        # atexit already does this, but that is too late for the last sync and
        # can race a playtime tick against a closed window.
        try:
            api.launcher.shutdown()
        except Exception as e:
            print(f"[wLib] Failed to flush playtime on shutdown: {e}")
        if library_sync_started:
            api.library_sync.finish(timeout=10)
        # Cleanup Vite server when startup fails or the window closes,
        # otherwise the npm dev process leaks.
        stop_vite_dev_server(vite_process)


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        raise SystemExit(run_packaged_smoke_test())
    main()
