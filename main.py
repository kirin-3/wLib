# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import importlib
import json
import os
import ssl
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import IO, TYPE_CHECKING, Protocol, cast, final
from collections.abc import Callable

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
        raise SystemExit(2) from None
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
        raise SystemExit(2) from None
    raise SystemExit(web_player.run(_player_args))

# The torrent engine runs in its own process so a native crash cannot take the
# app down. It loads neither the API, the database nor Qt.
if __name__ == "__main__" and "--torrent-helper" in sys.argv:
    from core.app_log import setup_app_log
    from core.downloads import torrent_helper
    from core.host_platform import get_data_dir

    _ = setup_app_log(get_data_dir(), rotate=False)
    raise SystemExit(torrent_helper.run())

from core.api import APP_VERSION, Api  # noqa: E402
from core.app_log import setup_app_log  # noqa: E402
from core.diagnostics import record_renderer_stage  # noqa: E402
from core import extension_server as extension_server_module  # noqa: E402
from core.extension_server import (  # noqa: E402
    DEV_MODE,
    EXTENSION_REQUEST_HEADER,
    VITE_DEV_SERVER,
    ExtensionServer,
    bind_extension_server,
    window_minimized,
)
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


_screen_tracker: object | None = None


def track_screen_size() -> None:
    """Record the wLib window's screen size (physical pixels) for gamescope.

    Runs once the window shows, then on the GUI thread whenever the window
    moves to another screen. Qt objects are read only on the GUI thread.
    """
    global _screen_tracker
    window = cast(RendererDiagnosticsWindow | None, window_ref)
    if window is None or not window.events.shown.wait(30):
        return
    native = cast(object, window.native)
    try:
        from PyQt6 import QtCore as qt_core
        from PyQt6.QtCore import QCoreApplication, QObject, Qt, pyqtSignal
        from PyQt6.QtGui import QScreen, QWindow
    except Exception:
        return
    app = QCoreApplication.instance()
    if app is None or native is None:
        return
    from core import display_info

    # pyqtSlot has no type hints; give it one so the methods keep theirs.
    slot = cast(
        Callable[..., Callable[[Callable[..., None]], Callable[..., None]]],
        cast(object, getattr(qt_core, "pyqtSlot")),
    )

    @final
    class _ScreenTracker(QObject):
        attach = pyqtSignal()

        def __init__(self) -> None:
            super().__init__()
            # Queued: the slot runs on the GUI thread, which owns this object.
            connect = cast(Callable[[object, Qt.ConnectionType], object], self.attach.connect)
            _ = connect(self._attach, Qt.ConnectionType.QueuedConnection)

        # Real Qt slots: a queued call to a plain Python method would run in
        # the thread that connected it, not in this object's (GUI) thread.
        @slot()
        def _attach(self) -> None:
            handle_getter = getattr(native, "windowHandle", None)
            handle = handle_getter() if callable(handle_getter) else None
            if not isinstance(handle, QWindow):
                return
            connect = cast(Callable[[object], object], handle.screenChanged.connect)
            _ = connect(self._update)
            self._update(handle.screen())

        @slot(QScreen)
        def _update(self, screen: QScreen | None) -> None:
            if screen is None:
                display_info.set_screen_size(None, None)
                return
            size = screen.geometry().size()
            ratio = screen.devicePixelRatio()
            display_info.set_screen_size(round(size.width() * ratio), round(size.height() * ratio))

    tracker = _ScreenTracker()
    _ = tracker.moveToThread(app.thread())
    _screen_tracker = tracker
    tracker.attach.emit()


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


class _DestroyableWindow(Protocol):
    def destroy(self) -> None: ...


class _EventHook(Protocol):
    def __iadd__(self, handler: Callable[[], None]) -> _EventHook: ...


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

    global window_ref

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

    from core.database import DatabaseDamagedError

    try:
        api = Api()
    except DatabaseDamagedError as exc:
        if extension_server is not None:
            extension_server.server_close()
        show_fatal_startup_error(str(exc))
        raise SystemExit(1) from exc
    api._start_steam_sync(os.path.join(get_bundle_root(), "wlib.png"))  # pyright: ignore[reportPrivateUsage]
    extension_server_module.api_ref = api
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
        extension_server_module.window_ref = window
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
        threading.Thread(target=track_screen_size, daemon=True).start()
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
