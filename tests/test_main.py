# SPDX-License-Identifier: GPL-3.0-or-later
import builtins
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.client import HTTPConnection
from io import BytesIO
from email.message import Message
import os
import sys
import json
import threading
from typing import cast
from unittest.mock import MagicMock
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main


def _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path):
    app_data_dir = tmp_path / "data"
    cache_home = tmp_path / "cache"
    monkeypatch.setattr(main, "APP_DATA_DIR", str(app_data_dir))
    monkeypatch.setattr(main, "get_cache_root", lambda: str(cache_home))
    monkeypatch.setattr(main, "is_linux", lambda: True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", "/tmp/wlib-bin")
    monkeypatch.setattr(main, "get_bundle_root", lambda: str(tmp_path / "bundle"))
    webview_storage = app_data_dir / main.PYWEBVIEW_STORAGE_DIR_NAME
    return {
        "app_data_dir": app_data_dir,
        "cache_path": cache_home
        / "wlib-bin"
        / main.QTWEBENGINE_CACHE_DIR_NAME
        / main.PYWEBVIEW_QT_PROFILE_NAME,
        "legacy_cache_path": cache_home / main.PACKAGED_WEBVIEW_CACHE_DIR_NAME,
        "marker_path": app_data_dir / main.PYWEBVIEW_CACHE_VERSION_MARKER,
        "webview_storage": webview_storage,
    }


def _make_extension_handler(path, matching_game=None, headers=None):
    handler = main.ExtensionRequestHandler.__new__(main.ExtensionRequestHandler)
    handler.path = path
    message_headers = Message()
    for key, value in (headers or {}).items():
        message_headers[key] = value
    handler.headers = message_headers
    handler.__dict__["wfile"] = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler._find_matching_game = MagicMock(return_value=matching_game)
    return handler


def test_cover_route_is_read_only_without_cors(tmp_path, monkeypatch):
    from core import database

    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    folder = tmp_path / "covers"
    folder.mkdir()
    name = "a" * 32 + ".png"
    (folder / name).write_bytes(b"\x89PNG\r\n\x1a\nimage")
    handler = _make_extension_handler(
        "/covers/" + name, headers={"Origin": "https://untrusted.example"}
    )
    handler.do_GET()
    handler.send_response.assert_called_once_with(200)
    headers = dict(call.args for call in handler.send_header.call_args_list)
    assert headers["Content-Type"] == "image/png"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "immutable" in headers["Cache-Control"]
    assert "Access-Control-Allow-Origin" not in headers
    assert handler.wfile.getvalue() == (folder / name).read_bytes()


@pytest.mark.parametrize(
    "path",
    [
        "/covers/../wlib.db",
        "/covers/",
        "/covers/" + "b" * 32 + ".jpg",
        "/covers/%2e%2e/wlib.db",
    ],
)
def test_cover_route_rejects_invalid_names(path):
    handler = _make_extension_handler(path)
    handler.send_error = MagicMock()
    handler.do_GET()
    handler.send_error.assert_called_once_with(404)


def test_dropbox_callback_without_origin(monkeypatch):
    handler = _make_extension_handler("/dropbox/callback?code=one&state=two")
    handler.server = MagicMock()
    callback = handler.server.sync_api.complete_dropbox_callback
    callback.return_value = {"success": True}
    handler.do_GET()
    callback.assert_called_once_with("one", "two")
    handler.send_response.assert_called_once_with(200)
    assert b"You can close this tab" in handler.wfile.getvalue()


@pytest.mark.parametrize(
    "path,method,label",
    [
        ("/dropbox/callback", "complete_dropbox_callback", "Dropbox"),
        ("/gdrive/callback", "complete_gdrive_callback", "Google Drive"),
    ],
)
@pytest.mark.parametrize("success,status", [(True, 200), (False, 400)])
def test_sync_oauth_callbacks_over_http(path, method, label, success, status):
    server = main.ExtensionServer(("127.0.0.1", 0), main.ExtensionRequestHandler)
    server.sync_api = MagicMock()
    getattr(server.sync_api, method).return_value = {"success": success}
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    connection = HTTPConnection(*server.server_address, timeout=3)
    try:
        connection.request(
            "GET", f"{path}?code=one&state=two", headers={"Host": "localhost:8183"}
        )
        response = connection.getresponse()
        page = response.read().decode()
        assert response.status == status
        assert response.getheader("Content-Security-Policy") == "default-src 'none'"
        assert (f"{label} connected" in page) is success
        assert (f"{label} sign-in failed" in page) is not success
        getattr(server.sync_api, method).assert_called_once_with("one", "two")
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dropbox_callback_origin_rules_unchanged():
    handler = _make_extension_handler(
        "/dropbox/callback?code=one&state=two",
        headers={"Origin": "https://untrusted.example"},
    )
    handler.server = MagicMock()
    handler.do_GET()
    handler.send_response.assert_called_once_with(403)
    handler.server.sync_api.complete_dropbox_callback.assert_not_called()


def test_configure_qt_runtime_environment_prefers_wayland(monkeypatch):
    monkeypatch.setattr(main, "is_linux", lambda: True)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WLIB_QPA_PLATFORM", raising=False)

    result = main.configure_qt_runtime_environment()

    assert result["qt_qpa_platform"] == ""
    assert result["source"] == "auto-wayland"
    assert "QT_QPA_PLATFORM" not in os.environ


def test_configure_qt_runtime_environment_sets_xcb_for_x11(monkeypatch):
    monkeypatch.setattr(main, "is_linux", lambda: True)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WLIB_QPA_PLATFORM", raising=False)

    result = main.configure_qt_runtime_environment()

    assert result["qt_qpa_platform"] == "xcb"
    assert result["source"] == "auto-x11"
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_configure_qt_runtime_environment_keeps_native_platform_on_windows(
    monkeypatch,
):
    monkeypatch.setattr(main, "is_linux", lambda: False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WLIB_QPA_PLATFORM", raising=False)

    result = main.configure_qt_runtime_environment()

    assert result["qt_qpa_platform"] == ""
    assert result["source"] == "auto-native"
    assert "QT_QPA_PLATFORM" not in os.environ


def test_configure_qt_runtime_environment_honors_override(monkeypatch):
    monkeypatch.setattr(main, "is_linux", lambda: True)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("WLIB_QPA_PLATFORM", "xcb")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)

    result = main.configure_qt_runtime_environment()

    assert result["qt_qpa_platform"] == "xcb"
    assert result["source"] == "override"
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_configure_ssl_certificates_drops_missing_configured_bundles(
    monkeypatch, tmp_path
):
    missing = str(tmp_path / "missing-bundle.pem")
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        monkeypatch.setenv(name, missing)
    monkeypatch.setattr(main.os.path, "isfile", lambda _path: False)

    assert main.configure_ssl_certificates() is None

    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        assert name not in os.environ


def test_configure_ssl_certificates_uses_a_valid_requests_bundle(
    monkeypatch, tmp_path
):
    bundle = tmp_path / "corporate-ca.pem"
    bundle.write_text("certificate", encoding="utf-8")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)

    assert main.configure_ssl_certificates() == str(bundle)

    assert os.environ["SSL_CERT_FILE"] == str(bundle)
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)
    assert os.environ["CURL_CA_BUNDLE"] == str(bundle)


def test_cleanup_legacy_browser_data_removes_owned_dirs_only(monkeypatch, tmp_path):
    data = tmp_path / "data"
    monkeypatch.setenv("WLIB_DATA_DIR", str(data))
    owned = [data / "browser_session", data / "playwright"]
    shared = tmp_path / "cache" / "ms-playwright"
    for path in [*owned, shared]:
        path.mkdir(parents=True)
        (path / "keep.txt").write_text("data")
    monkeypatch.setenv("WLIB_PLAYWRIGHT_BROWSERS_PATH", str(shared))
    main.cleanup_legacy_browser_data()
    assert all(not path.exists() for path in owned)
    assert (shared / "keep.txt").exists()


def test_cleanup_legacy_browser_data_logs_failure_and_continues(
    monkeypatch, tmp_path, capsys
):
    data = tmp_path / "data"
    monkeypatch.setenv("WLIB_DATA_DIR", str(data))
    for name in ("browser_session", "playwright"):
        (data / name).mkdir(parents=True)
    removal = MagicMock(side_effect=[PermissionError("locked"), None])
    monkeypatch.setattr(main.shutil, "rmtree", removal)
    main.cleanup_legacy_browser_data()
    assert removal.call_count == 2
    assert "locked" in capsys.readouterr().out


def test_cleanup_legacy_browser_data_refuses_outside_paths(monkeypatch, tmp_path):
    data = tmp_path / "data"
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("WLIB_DATA_DIR", str(data))
    monkeypatch.setattr(main, "get_legacy_browser_dirs", lambda: [str(shared)])
    removal = MagicMock()
    monkeypatch.setattr(main.shutil, "rmtree", removal)
    main.cleanup_legacy_browser_data()
    removal.assert_not_called()


def test_get_packaged_webview_cache_path_uses_xdg_cache_home(monkeypatch, tmp_path):
    cache_home = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setattr("core.host_platform.sys.platform", "linux")

    cache_path = main.get_packaged_webview_cache_path()

    assert cache_path == os.path.join(
        str(cache_home), main.PACKAGED_WEBVIEW_CACHE_DIR_NAME
    )


def test_get_packaged_webview_profile_cache_paths_uses_qt_profile_cache_location(
    monkeypatch, tmp_path
):
    cache_home = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setattr("core.host_platform.sys.platform", "linux")
    monkeypatch.setattr(main, "is_linux", lambda: True)
    monkeypatch.setattr(main.sys, "executable", "/opt/wlib/wlib-bin")

    cache_paths = main.get_packaged_webview_profile_cache_paths()

    assert cache_paths == [
        os.path.join(
            str(cache_home),
            "wlib-bin",
            main.QTWEBENGINE_CACHE_DIR_NAME,
            main.PYWEBVIEW_QT_PROFILE_NAME,
        )
    ]


def test_get_packaged_webview_profile_cache_paths_on_windows(monkeypatch, tmp_path):
    cache_home = tmp_path / "cache"
    monkeypatch.setattr(main, "is_linux", lambda: False)
    monkeypatch.setattr(main, "get_cache_root", lambda: str(cache_home))

    assert main.get_packaged_webview_profile_cache_paths() == [
        os.path.join(
            str(cache_home),
            main.QTWEBENGINE_CACHE_DIR_NAME,
            main.PYWEBVIEW_QT_PROFILE_NAME,
        )
    ]


def test_ensure_packaged_webview_cache_fresh_clears_first_launch_cache(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True, exist_ok=True)
    (paths["cache_path"] / "stale-cache").write_text("old", encoding="utf-8")
    paths["webview_storage"].mkdir(parents=True, exist_ok=True)
    (paths["webview_storage"] / "prefs").write_text("keep", encoding="utf-8")

    main.ensure_packaged_webview_cache_fresh()

    assert not paths["cache_path"].exists()
    assert paths["marker_path"].read_text(encoding="utf-8").strip() == main.APP_VERSION
    assert (paths["webview_storage"] / "prefs").read_text(encoding="utf-8") == "keep"


def test_ensure_packaged_webview_cache_fresh_clears_legacy_cache(monkeypatch, tmp_path):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True, exist_ok=True)
    (paths["cache_path"] / "profile-cache").write_text("old", encoding="utf-8")
    paths["legacy_cache_path"].mkdir(parents=True, exist_ok=True)
    (paths["legacy_cache_path"] / "legacy-cache").write_text("old", encoding="utf-8")

    main.ensure_packaged_webview_cache_fresh()

    assert not paths["cache_path"].exists()
    assert not paths["legacy_cache_path"].exists()
    assert paths["marker_path"].read_text(encoding="utf-8").strip() == main.APP_VERSION


def test_ensure_packaged_webview_cache_fresh_updates_changed_version(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True, exist_ok=True)
    (paths["cache_path"] / "old-cache").write_text("old", encoding="utf-8")
    paths["marker_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["marker_path"].write_text("0.0.1\n", encoding="utf-8")

    main.ensure_packaged_webview_cache_fresh()

    assert not paths["cache_path"].exists()
    assert paths["marker_path"].read_text(encoding="utf-8").strip() == main.APP_VERSION


def test_ensure_packaged_webview_cache_fresh_preserves_unchanged_version_cache(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True, exist_ok=True)
    cache_file = paths["cache_path"] / "current-cache"
    cache_file.write_text("current", encoding="utf-8")
    paths["marker_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["marker_path"].write_text(main.APP_VERSION + "\n", encoding="utf-8")

    main.ensure_packaged_webview_cache_fresh()

    assert cache_file.read_text(encoding="utf-8") == "current"
    assert paths["marker_path"].read_text(encoding="utf-8").strip() == main.APP_VERSION


def test_ensure_packaged_webview_cache_fresh_clears_cache_for_rebuilt_ui(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True, exist_ok=True)
    (paths["cache_path"] / "old-ui").write_text("old", encoding="utf-8")
    paths["marker_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["marker_path"].write_text(main.APP_VERSION + "\n", encoding="utf-8")
    index_html = tmp_path / "bundle" / "ui" / "dist" / "index.html"
    index_html.parent.mkdir(parents=True)
    index_html.write_text("<script src=new.js>", encoding="utf-8")

    main.ensure_packaged_webview_cache_fresh()

    assert not paths["cache_path"].exists()
    marker = paths["marker_path"].read_text(encoding="utf-8").strip()
    assert marker.startswith(main.APP_VERSION + "+")

    (paths["cache_path"] / "new-ui").parent.mkdir(parents=True)
    (paths["cache_path"] / "new-ui").write_text("new", encoding="utf-8")
    main.ensure_packaged_webview_cache_fresh()
    assert (paths["cache_path"] / "new-ui").exists()


def test_ensure_packaged_webview_cache_fresh_skips_source_runtime(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True, exist_ok=True)
    cache_file = paths["cache_path"] / "source-cache"
    cache_file.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(main.sys, "frozen", False, raising=False)
    monkeypatch.setattr(main.sys, "executable", sys.executable)

    main.ensure_packaged_webview_cache_fresh()

    assert cache_file.read_text(encoding="utf-8") == "keep"
    assert not paths["marker_path"].exists()


def test_ensure_packaged_webview_cache_fresh_skips_unexpected_frozen_executable(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True, exist_ok=True)
    cache_file = paths["cache_path"] / "other-cache"
    cache_file.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(main.sys, "executable", "/tmp/other-bin")

    main.ensure_packaged_webview_cache_fresh()

    assert cache_file.read_text(encoding="utf-8") == "keep"
    assert not paths["marker_path"].exists()


def test_ensure_packaged_webview_cache_fresh_marks_missing_cache_success(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)

    main.ensure_packaged_webview_cache_fresh()

    assert not paths["cache_path"].exists()
    assert paths["marker_path"].read_text(encoding="utf-8").strip() == main.APP_VERSION


def test_ensure_packaged_webview_cache_fresh_keeps_marker_when_cache_unknown(
    monkeypatch, tmp_path, capsys
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "get_packaged_webview_profile_cache_paths", lambda: [])

    main.ensure_packaged_webview_cache_fresh()

    output = capsys.readouterr().out
    assert "Failed to identify packaged WebView profile cache paths" in output
    assert not paths["marker_path"].exists()


def test_ensure_packaged_webview_cache_fresh_keeps_marker_on_delete_failure(
    monkeypatch, tmp_path, capsys
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True, exist_ok=True)
    paths["marker_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["marker_path"].write_text("0.0.1\n", encoding="utf-8")

    def fail_rmtree(path: str) -> None:
        raise OSError(f"locked: {path}")

    monkeypatch.setattr(main.shutil, "rmtree", fail_rmtree)

    main.ensure_packaged_webview_cache_fresh()

    output = capsys.readouterr().out
    assert "Failed to clear packaged WebView cache" in output
    assert paths["cache_path"].exists()
    assert paths["marker_path"].read_text(encoding="utf-8").strip() == "0.0.1"


def test_ensure_packaged_webview_cache_fresh_continues_on_marker_write_failure(
    monkeypatch, tmp_path, capsys
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True, exist_ok=True)
    (paths["cache_path"] / "stale-cache").write_text("old", encoding="utf-8")
    real_open = builtins.open

    def fail_marker_write(path, mode="r", *args, **kwargs):
        if os.fspath(path) == str(paths["marker_path"]) and "w" in mode:
            raise OSError("read-only marker")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail_marker_write)

    main.ensure_packaged_webview_cache_fresh()

    output = capsys.readouterr().out
    assert "Failed to write WebView cache version marker" in output
    assert not paths["cache_path"].exists()
    assert not paths["marker_path"].exists()


def test_renderer_probe_strips_library_content_from_page_location():
    window = MagicMock()
    window.evaluate_js.return_value = {
        "location": (
            "file:///opt/wlib/ui/dist/index.html#/?action=import"
            "&f95url=https%3A%2F%2Ff95zone.to%2Fthreads%2Fsecret-game.123%2F"
            "&title=Secret+Game"
        ),
        "userAgent": "Test agent",
        "webgl2": None,
        "webgl": None,
    }

    details = main._probe_browser_renderer(window)

    assert details["page_location"] == "file:///opt/wlib/ui/dist/index.html"
    assert "Secret" not in json.dumps(details)
    assert "f95zone" not in json.dumps(details)


def test_renderer_probe_decodes_file_paths_for_home_redaction():
    window = MagicMock()
    window.evaluate_js.return_value = {
        "location": (
            "file:///home/John%20Doe/wLib/ui/dist/index.html#/?action=import"
            "&title=Hidden"
        ),
        "userAgent": "Test agent",
        "webgl2": None,
        "webgl": None,
    }

    details = main._probe_browser_renderer(window)

    assert details["page_location"] == "file:///home/John Doe/wLib/ui/dist/index.html"
    assert "Hidden" not in json.dumps(details)


def test_extract_native_renderer_details_includes_profile_cache_path():
    class FakeProfile:
        def persistentStoragePath(self):
            return "/tmp/wlib-storage"

        def cachePath(self):
            return "/tmp/wlib-cache"

        def httpUserAgent(self):
            return "Fake User Agent"

    class FakePage:
        def profile(self):
            return FakeProfile()

    class FakeWebView:
        def page(self):
            return FakePage()

    class FakeNativeWindow:
        webview = FakeWebView()

    window = MagicMock()
    window.native = FakeNativeWindow()

    details = main._extract_native_renderer_details(window)

    assert details["qt_profile_storage_path"] == "/tmp/wlib-storage"
    assert details["qt_profile_cache_path"] == "/tmp/wlib-cache"
    assert details["qt_profile_http_user_agent"] == "Fake User Agent"


def test_main_runs_webview_cache_cleanup_before_importing_webview(monkeypatch):
    call_order: list[str] = []
    fake_window = MagicMock()
    fake_webview = MagicMock()
    fake_webview.create_window.return_value = fake_window

    class FakeApi:
        library_sync = MagicMock()
        launcher = MagicMock()

        def sync_extension_files(self):
            return {"success": True}

        def set_startup_extension_sync_status(self, _status):
            return None

        def set_window(self, _window):
            return None

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            return None

    monkeypatch.setattr(main, "configure_ssl_certificates", lambda: None)
    monkeypatch.setattr(main, "setup_app_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "configure_qt_runtime_environment", lambda: {})
    monkeypatch.setattr(
        main,
        "ensure_packaged_webview_cache_fresh",
        lambda: call_order.append("cleanup"),
    )
    monkeypatch.setattr(
        main,
        "load_webview_module",
        lambda: call_order.append("load_webview") or fake_webview,
    )
    monkeypatch.setattr(
        main, "log_renderer_diagnostics", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        main, "collect_renderer_environment_snapshot", lambda dev_mode: {}
    )
    monkeypatch.setattr(main, "collect_pywebview_module_snapshot", lambda _module: {})
    monkeypatch.setattr(main, "Api", FakeApi)
    monkeypatch.setattr(main, "cleanup_legacy_browser_data_async", lambda: None)
    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    monkeypatch.setattr(main, "bind_extension_server", MagicMock())
    monkeypatch.setattr(
        main, "start_webview", lambda *_args, **_kwargs: call_order.append("start")
    )
    monkeypatch.setattr(main, "DEV_MODE", False)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])

    main.main()

    assert call_order[:2] == ["cleanup", "load_webview"]


def test_extension_server_starts_before_slow_webview_startup(monkeypatch):
    order: list[str] = []
    server = MagicMock()
    server.serve_forever.side_effect = lambda **_kwargs: order.append("serve")
    fake_webview = MagicMock()
    fake_webview.create_window.return_value = MagicMock()

    class FakeApi:
        library_sync = MagicMock()
        launcher = MagicMock()

        def sync_extension_files(self):
            return {"success": True}

        def set_startup_extension_sync_status(self, _status):
            return None

        def set_window(self, _window):
            return None

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    monkeypatch.setattr(main, "configure_ssl_certificates", lambda: None)
    monkeypatch.setattr(main, "setup_app_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "configure_qt_runtime_environment", lambda: {})
    monkeypatch.setattr(main, "ensure_packaged_webview_cache_fresh", lambda: None)
    monkeypatch.setattr(
        main,
        "load_webview_module",
        lambda: order.append("load") or fake_webview,
    )
    monkeypatch.setattr(main, "log_renderer_diagnostics", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main, "collect_renderer_environment_snapshot", lambda dev_mode: {}
    )
    monkeypatch.setattr(main, "collect_pywebview_module_snapshot", lambda _m: {})
    monkeypatch.setattr(main, "Api", FakeApi)
    monkeypatch.setattr(main, "cleanup_legacy_browser_data_async", lambda: None)
    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    monkeypatch.setattr(main, "bind_extension_server", lambda: server)
    monkeypatch.setattr(
        main, "start_webview", lambda *_args, **_kwargs: order.append("start")
    )
    monkeypatch.setattr(main, "DEV_MODE", False)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])

    main.main()

    # A second launch during WebView startup must reach the server and exit;
    # otherwise both copies open windows and share the database.
    assert "serve" in order
    assert order.index("serve") < order.index("load")


def test_get_webview_storage_path_creates_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "APP_DATA_DIR", str(tmp_path))

    storage_path = main.get_webview_storage_path()

    assert storage_path == os.path.join(str(tmp_path), main.PYWEBVIEW_STORAGE_DIR_NAME)
    assert os.path.isdir(storage_path)


def test_start_webview_uses_persistent_storage_for_packaged_runtime(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(main, "APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        main,
        "RENDERER_DIAGNOSTICS_LOG",
        os.path.join(str(tmp_path), "renderer-diagnostics.log"),
    )
    webview_module = MagicMock()

    main.start_webview(webview_module, dev_mode=False, icon_path="/tmp/wlib.png")

    webview_module.start.assert_called_once()
    kwargs = webview_module.start.call_args.kwargs
    assert kwargs["http_server"] is True
    assert kwargs["http_port"] == main.PYWEBVIEW_HTTP_PORT
    assert kwargs["private_mode"] is False
    assert kwargs["storage_path"] == os.path.join(
        str(tmp_path), main.PYWEBVIEW_STORAGE_DIR_NAME
    )
    assert kwargs["icon"] == "/tmp/wlib.png"
    assert kwargs["func"] is main.start_runtime_renderer_diagnostics


def test_start_webview_preserves_dev_mode_without_fixed_http_port(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(main, "APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        main,
        "RENDERER_DIAGNOSTICS_LOG",
        os.path.join(str(tmp_path), "renderer-diagnostics.log"),
    )
    webview_module = MagicMock()

    main.start_webview(webview_module, dev_mode=True, icon_path=None)

    webview_module.start.assert_called_once()
    kwargs = webview_module.start.call_args.kwargs
    assert kwargs["http_server"] is True
    assert kwargs["private_mode"] is False
    assert kwargs["storage_path"] == os.path.join(
        str(tmp_path), main.PYWEBVIEW_STORAGE_DIR_NAME
    )
    assert "http_port" not in kwargs
    assert kwargs["icon"] is None
    assert kwargs["func"] is main.start_runtime_renderer_diagnostics


def test_start_webview_runs_renderer_diagnostics_on_a_daemon_thread(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    observed: list[bool] = []

    def probe() -> None:
        observed.append(threading.current_thread().daemon)
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(main, "log_runtime_renderer_diagnostics", probe)
    webview_module = MagicMock()

    main.start_webview(webview_module, dev_mode=False, icon_path=None)

    func = webview_module.start.call_args.kwargs["func"]
    assert func is main.start_runtime_renderer_diagnostics
    assert func is not probe
    func()
    assert started.wait(2)
    assert observed == [True]
    release.set()


def test_renderer_log_targets_include_appimage_log(monkeypatch, tmp_path):
    renderer_log = tmp_path / "renderer-diagnostics.log"
    appimage_log = tmp_path / "appimage-launch.log"
    monkeypatch.setattr(main, "RENDERER_DIAGNOSTICS_LOG", str(renderer_log))
    monkeypatch.setenv("WLIB_APPIMAGE_LAUNCH_LOG", str(appimage_log))

    assert main._renderer_log_targets() == [str(renderer_log), str(appimage_log)]


def test_renderer_log_targets_include_shared_launcher_log(monkeypatch, tmp_path):
    renderer_log = tmp_path / "renderer-diagnostics.log"
    launch_log = tmp_path / "wlib-launch.log"
    appimage_log = tmp_path / "appimage-launch.log"
    monkeypatch.setattr(main, "RENDERER_DIAGNOSTICS_LOG", str(renderer_log))
    monkeypatch.setenv("WLIB_LAUNCH_LOG", str(launch_log))
    monkeypatch.setenv("WLIB_APPIMAGE_LAUNCH_LOG", str(appimage_log))

    assert main._renderer_log_targets() == [
        str(renderer_log),
        str(launch_log),
        str(appimage_log),
    ]


def test_log_renderer_diagnostics_writes_renderer_and_launcher_logs(
    monkeypatch, tmp_path, capsys
):
    app_data_dir = tmp_path / "appdata"
    renderer_log = app_data_dir / "renderer-diagnostics.log"
    launch_log = tmp_path / "wlib-launch.log"
    monkeypatch.setattr(main, "APP_DATA_DIR", str(app_data_dir))
    monkeypatch.setattr(main, "RENDERER_DIAGNOSTICS_LOG", str(renderer_log))
    monkeypatch.setenv("WLIB_LAUNCH_LOG", str(launch_log))

    main.log_renderer_diagnostics(
        "startup",
        {
            "qt_qpa_platform": "xcb",
            "qt_quick_backend": "opengl",
            "gpu_crash_guard_present": False,
        },
    )

    output = capsys.readouterr().out
    assert "Renderer diagnostics (startup)" in output
    assert "qt_qpa_platform=xcb" in output
    assert "qt_quick_backend=opengl" in renderer_log.read_text(encoding="utf-8")
    assert "qt_quick_backend=opengl" in launch_log.read_text(encoding="utf-8")


def test_collect_renderer_environment_snapshot_reports_crash_guard(
    monkeypatch, tmp_path
):
    app_data_dir = tmp_path / "appdata"
    crash_guard = app_data_dir / ".gpu_crash_guard"
    app_data_dir.mkdir()
    crash_guard.write_text("", encoding="utf-8")
    monkeypatch.setattr(main, "APP_DATA_DIR", str(app_data_dir))
    monkeypatch.setattr(
        main,
        "RENDERER_DIAGNOSTICS_LOG",
        str(app_data_dir / "renderer-diagnostics.log"),
    )
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    monkeypatch.setenv("QT_QUICK_BACKEND", "opengl")

    snapshot = main.collect_renderer_environment_snapshot(dev_mode=False)

    assert snapshot["qt_qpa_platform"] == "xcb"
    assert snapshot["qt_quick_backend"] == "opengl"
    assert snapshot["gpu_crash_guard_present"] is True
    assert snapshot["gpu_crash_guard_path"] == str(crash_guard)


def test_extension_check_payload_includes_play_status_for_matches():
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game={
            "id": 1,
            "title": "Demo",
            "f95_url": "https://f95zone.to/threads/demo.123/",
            "play_status": "Playing",
            "exe_path": "/tmp/demo.sh",
        },
    )

    handler.do_GET()

    response_body = cast(BytesIO, handler.wfile).getvalue().decode("utf-8")

    assert json.loads(response_body) == {
        "exists": True,
        "playStatus": "Playing",
        "wishlisted": False,
        "gameId": 1,
        "app_version": main.APP_VERSION,
    }


def test_extension_check_reports_wishlisted_for_empty_executable():
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game={"exe_path": "  "},
    )
    handler.do_GET()
    assert json.loads(handler.wfile.getvalue()) == {
        "exists": True,
        "wishlisted": True,
        "app_version": main.APP_VERSION,
    }


def test_extension_check_payload_omits_play_status_for_missing_matches():
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game=None,
    )

    handler.do_GET()

    response_body = cast(BytesIO, handler.wfile).getvalue().decode("utf-8")

    assert json.loads(response_body) == {
        "exists": False,
        "app_version": main.APP_VERSION,
    }


def _mock_startup_sync(monkeypatch, status):
    sync_api = MagicMock()
    sync_api.get_startup_extension_sync_status.return_value = status
    monkeypatch.setattr(main, "api_ref", sync_api)
    return sync_api


def test_extension_check_includes_app_and_extension_versions(monkeypatch):
    _mock_startup_sync(monkeypatch, {"success": True, "installed_version": "1.0.11"})
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game={
            "id": 1,
            "title": "Demo",
            "f95_url": "https://f95zone.to/threads/demo.123/",
            "play_status": "Playing",
            "exe_path": "/tmp/demo.sh",
        },
    )

    handler.do_GET()

    assert json.loads(cast(BytesIO, handler.wfile).getvalue().decode("utf-8")) == {
        "exists": True,
        "playStatus": "Playing",
        "wishlisted": False,
        "gameId": 1,
        "app_version": main.APP_VERSION,
        "extension_version": "1.0.11",
    }


def test_extension_check_without_url_still_reports_versions(monkeypatch):
    _mock_startup_sync(monkeypatch, {"success": True, "installed_version": "1.0.11"})
    handler = _make_extension_handler("/api/check")

    handler.do_GET()

    assert json.loads(cast(BytesIO, handler.wfile).getvalue().decode("utf-8")) == {
        "exists": False,
        "app_version": main.APP_VERSION,
        "extension_version": "1.0.11",
    }


def test_extension_check_reports_versions_on_lookup_error(monkeypatch):
    _mock_startup_sync(monkeypatch, {"success": True, "installed_version": "1.0.11"})
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/"
    )
    handler._find_matching_game = MagicMock(side_effect=RuntimeError("db down"))

    handler.do_GET()

    assert json.loads(cast(BytesIO, handler.wfile).getvalue().decode("utf-8")) == {
        "exists": False,
        "app_version": main.APP_VERSION,
        "extension_version": "1.0.11",
    }


@pytest.mark.parametrize(
    "status",
    [
        {"success": False, "error": "sync failed"},
        {"success": True, "installed_version": ""},
        {"success": True},
    ],
)
def test_extension_check_omits_extension_version_when_sync_failed(monkeypatch, status):
    _mock_startup_sync(monkeypatch, status)
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game={"exe_path": "  "},
    )

    handler.do_GET()

    payload = json.loads(cast(BytesIO, handler.wfile).getvalue().decode("utf-8"))
    assert payload["app_version"] == main.APP_VERSION
    assert "extension_version" not in payload


@pytest.fixture
def extension_server(tmp_path, monkeypatch):
    from core import database

    # Requests resolve links against the library, so never touch the real one.
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "extension.db"))
    database.init_db()
    server = main.ThreadingHTTPServer(("127.0.0.1", 0), main.ExtensionRequestHandler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/check?url=https://f95zone.to/threads/demo.123/"),
        ("POST", "/api/add"),
        ("OPTIONS", "/api/add"),
    ],
)
@pytest.mark.parametrize(
    "hosts,origin,dev_mode,status",
    [
        (("localhost:8183",), None, False, 200),
        (("127.0.0.1:8183",), None, False, 200),
        (("LOCALHOST:8183",), "chrome-extension://wlib", False, 200),
        (("localhost:8183",), "moz-extension://wlib", False, 200),
        (("localhost:8183",), "http://localhost:5173", True, 200),
        (("localhost:8183",), "http://localhost:5173", False, 403),
        (("localhost:8183",), "https://example.com", True, 403),
        (("example.com:8183",), None, False, 403),
        (("example.com:8183",), "chrome-extension://wlib", False, 403),
        (("localhost",), None, False, 403),
        (("localhost:8184",), None, True, 403),
        ((), None, False, 403),
        (("localhost:8183", "example.com:8183"), None, False, 403),
    ],
)
def test_extension_request_validates_host_and_origin(
    monkeypatch, extension_server, method, path, hosts, origin, dev_mode, status
):
    lookup = MagicMock(return_value={"play_status": "Playing"})
    window = MagicMock()
    monkeypatch.setattr(main.ExtensionRequestHandler, "_find_matching_game", lookup)
    monkeypatch.setattr(main, "window_ref", window)
    monkeypatch.setattr(main, "DEV_MODE", dev_mode)
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.putrequest(method, path, skip_host=True)
        for host in hosts:
            connection.putheader("Host", host)
        if origin is not None:
            connection.putheader("Origin", origin)
        request_body = json.dumps(
            {"title": "Game", "url": "https://f95zone.to/threads/123/"}
        ).encode()
        connection.putheader("Content-Length", str(len(request_body)))
        connection.endheaders(request_body)
        response = connection.getresponse()
        body = response.read()
        assert response.status == status
        if status == 403:
            assert json.loads(body)["success"] is False
            assert response.getheader("Access-Control-Allow-Origin") is None
            lookup.assert_not_called()
            window.evaluate_js.assert_not_called()
        else:
            assert response.getheader("Access-Control-Allow-Origin") == origin
    finally:
        connection.close()


@pytest.mark.parametrize(
    "headers,status",
    [
        ({}, 403),  # what <img src="http://127.0.0.1:8183/api/open?..."> sends
        ({"X-wLib-Extension": "1"}, 200),
        ({"Origin": "chrome-extension://wlib"}, 200),
    ],
)
def test_extension_open_requires_extension_origin_or_header(
    monkeypatch, extension_server, headers, status
):
    window = MagicMock()
    monkeypatch.setattr(main, "window_ref", window)
    monkeypatch.setattr(
        main.ExtensionRequestHandler, "_find_matching_game", lambda _self, _url: None
    )
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request(
            "GET",
            "/api/open?url=https://f95zone.to/threads/demo.123/",
            headers={"Host": "localhost:8183", **headers},
        )
        response = connection.getresponse()
        _ = response.read()
        assert response.status == status
        assert window.evaluate_js.called is (status == 200)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "window,content_length,status",
    [
        (MagicMock(), "abc", 400),
        (MagicMock(), "-1", 400),
        (None, "2", 503),
    ],
)
def test_extension_add_always_replies_with_failure(
    monkeypatch, extension_server, window, content_length, status
):
    monkeypatch.setattr(main, "window_ref", window)
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.putrequest("POST", "/api/add", skip_host=True)
        connection.putheader("Host", "localhost:8183")
        connection.putheader("Content-Length", content_length)
        # No body when the length is invalid: the server can't read it, and
        # unread data makes Windows reset the connection before the reply.
        connection.endheaders(b"{}" if content_length == "2" else None)
        response = connection.getresponse()
        assert response.status == status
        assert json.loads(response.read())["success"] is False
    finally:
        connection.close()


def test_extension_wishlist_saves_without_executable_and_rejects_duplicate(
    monkeypatch, tmp_path, extension_server
):
    from core.database import get_all_games

    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wishlist.db"))
    api = main.Api()
    monkeypatch.setattr(
        api.scraper, "get_thread_metadata", lambda *_args: {"success": False}
    )
    monkeypatch.setattr(main, "api_ref", api)
    window = MagicMock()
    monkeypatch.setattr(main, "window_ref", window)
    body = json.dumps(
        {
            "title": "Wishlist demo",
            "url": "https://f95zone.to/threads/wishlist.555/",
            "version": "1.0",
            "coverImage": "https://example.com/cover.jpg",
            "tags": "sandbox",
            "rating": "4",
            "developer": "Studio",
            "engine": "Ren'Py",
        }
    )

    def send():
        connection = HTTPConnection(*extension_server.server_address, timeout=3)
        try:
            connection.request(
                "POST",
                "/api/wishlist",
                body,
                headers={"Host": "localhost:8183", "Origin": "chrome-extension://wlib"},
            )
            reply = connection.getresponse()
            return reply.status, json.loads(reply.read())
        finally:
            connection.close()

    status, payload = send()
    assert status == 200 and payload["success"] is True
    game = get_all_games()[0]
    assert game["title"] == "Wishlist demo"
    assert game["exe_path"] == ""
    assert game["engine"] == "Ren'Py"
    assert game["tags"] == "sandbox"
    assert window.evaluate_js.called

    status, payload = send()
    assert status == 409 and payload["error_code"] == "duplicate_url"
    assert len(get_all_games()) == 1


@pytest.mark.parametrize(
    ("origin", "oversized", "expected_status"),
    [
        ("https://example.com", False, 403),
        ("chrome-extension://wlib", True, 400),
    ],
)
def test_extension_wishlist_rejects_web_origin_and_oversized_body(
    monkeypatch, extension_server, origin, oversized, expected_status
):
    api = MagicMock()
    monkeypatch.setattr(main, "api_ref", api)
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.putrequest("POST", "/api/wishlist", skip_host=True)
        connection.putheader("Host", "localhost:8183")
        connection.putheader("Origin", origin)
        connection.putheader(
            "Content-Length",
            str(main.MAX_EXTENSION_BODY_BYTES + 1) if oversized else "2",
        )
        connection.endheaders(b"{}" if not oversized else None)
        reply = connection.getresponse()
        assert reply.status == expected_status
        assert json.loads(reply.read())["success"] is False
        api.add_game.assert_not_called()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "", "url": "https://f95zone.to/threads/demo.1/"},
        {"title": "Demo", "url": ""},
        {"title": "Demo", "url": "https://example.com/threads/demo.1/"},
    ],
)
def test_extension_wishlist_requires_title_and_thread_url(
    monkeypatch, extension_server, payload
):
    api = MagicMock()
    monkeypatch.setattr(main, "api_ref", api)
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request(
            "POST",
            "/api/wishlist",
            json.dumps(payload),
            headers={"Host": "localhost:8183", "Origin": "chrome-extension://wlib"},
        )
        reply = connection.getresponse()
        assert reply.status == 400
        assert json.loads(reply.read())["success"] is False
        api.add_game.assert_not_called()
    finally:
        connection.close()


def test_extension_check_remains_responsive_while_open_waits(
    monkeypatch, extension_server
):
    evaluating = threading.Event()
    release = threading.Event()

    def evaluate_js(_script):
        evaluating.set()
        assert release.wait(5)

    window = MagicMock()
    window.evaluate_js.side_effect = evaluate_js
    monkeypatch.setattr(main, "window_ref", window)
    monkeypatch.setattr(
        main.ExtensionRequestHandler,
        "_find_matching_game",
        lambda _self, _url: None,
    )

    def request(path):
        address, port = extension_server.server_address
        with urlopen(
            Request(
                f"http://{address}:{port}{path}",
                headers={"Host": "localhost:8183", "X-wLib-Extension": "1"},
            ),
            timeout=3,
        ) as response:
            return json.loads(response.read())

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending_open = executor.submit(
            request, "/api/open?url=https://f95zone.to/threads/demo.123/"
        )
        try:
            assert evaluating.wait(3)
            assert request("/api/check?url=__ping__") == {
                "exists": False,
                "app_version": main.APP_VERSION,
            }
        finally:
            release.set()
        assert pending_open.result(timeout=3) == {"success": True}


def test_bind_extension_server_uses_threaded_loopback_server(monkeypatch):
    server_factory = MagicMock()
    monkeypatch.setattr(main, "ExtensionServer", server_factory)

    assert main.bind_extension_server() is server_factory.return_value
    server_factory.assert_called_once_with(
        ("127.0.0.1", 8183), main.ExtensionRequestHandler
    )


def test_second_instance_cannot_share_the_extension_port():
    first = main.ExtensionServer(("127.0.0.1", 0), main.ExtensionRequestHandler)
    try:
        with pytest.raises(OSError):
            main.ExtensionServer(first.server_address, main.ExtensionRequestHandler)
    finally:
        first.server_close()


def test_main_focuses_running_instance_instead_of_starting(monkeypatch):
    load_webview = MagicMock()
    setup_log = MagicMock()
    monkeypatch.setattr(main, "setup_app_log", setup_log)
    monkeypatch.setattr(main, "configure_ssl_certificates", lambda: None)
    monkeypatch.setattr(main, "bind_extension_server", lambda: None)
    monkeypatch.setattr(main, "focus_running_instance", lambda: True)
    monkeypatch.setattr(main, "load_webview_module", load_webview)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])

    main.main()

    load_webview.assert_not_called()
    setup_log.assert_not_called()


def test_packaged_smoke_does_not_create_logs(tmp_path, monkeypatch):
    from core import database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setattr(main, "APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "get_bundle_root", lambda: str(tmp_path))
    # Qt WebEngine needs system GL libraries that CI runners do not ship.
    import_module = main.importlib.import_module
    monkeypatch.setattr(
        main.importlib,
        "import_module",
        lambda name: MagicMock() if name.startswith("PyQt6.") else import_module(name),
    )
    for relative in ("ui/dist/index.html", "extension/manifest.json", "wlib.png"):
        asset = tmp_path / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text("fixture")
    assert main.run_packaged_smoke_test() == 0
    assert not (tmp_path / "logs").exists()


def test_renderer_stage_is_recorded(tmp_path, monkeypatch):
    from core.diagnostics import get_renderer_stages

    monkeypatch.setattr(
        main, "_renderer_log_targets", lambda: [str(tmp_path / "renderer.log")]
    )
    main.log_renderer_diagnostics("startup", {"gpu_vendor": "test GPU"})
    assert get_renderer_stages()["startup"] == {"gpu_vendor": "test GPU"}


def test_start_vite_dev_server_uses_platform_npm_command(monkeypatch):
    process = MagicMock()
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(main.subprocess, "Popen", popen)
    assert main.start_vite_dev_server() is process
    assert popen.call_args.args[0] == [
        "npm.cmd" if os.name == "nt" else "npm",
        "run",
        "dev",
    ]


@pytest.mark.parametrize(
    "reply,expected",
    [
        (b'{"success": true}', True),
        (b'{"success": false, "error": "Application window is not ready"}', True),
        (b"<html>other app</html>", False),
        (b'["success"]', False),
        (URLError("refused"), False),
        (http.client.BadStatusLine("garbage"), False),
    ],
)
def test_focus_running_instance_only_trusts_wlib_replies(monkeypatch, reply, expected):
    opener = MagicMock()
    if isinstance(reply, Exception):
        opener.open.side_effect = reply
    else:
        opener.open.return_value = BytesIO(reply)
    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: opener)

    assert main.focus_running_instance() is expected
    request = opener.open.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8183/api/open"
    assert request.get_header("X-wlib-extension") == "1"


def test_focus_running_instance_treats_a_truncated_reply_as_not_wlib(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.side_effect = http.client.IncompleteRead(b"partial")
    opener = MagicMock()
    opener.open.return_value = response
    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: opener)

    assert main.focus_running_instance() is False


def test_log_renderer_diagnostics_does_not_kill_startup_when_data_dir_unwritable(
    monkeypatch, tmp_path, capsys
):
    data_file = tmp_path / "file-not-dir"
    data_file.write_text("x", encoding="utf-8")
    monkeypatch.setattr(main, "APP_DATA_DIR", str(data_file))
    monkeypatch.setattr(
        main, "RENDERER_DIAGNOSTICS_LOG", str(data_file / "renderer.log")
    )

    # Must not raise even though APP_DATA_DIR cannot be created.
    main.log_renderer_diagnostics("startup", {"k": "v"})

    assert "Renderer diagnostics (startup)" in capsys.readouterr().out


def test_main_terminates_vite_when_webview_fails(monkeypatch):
    vite_process = MagicMock()
    extension_server = MagicMock()
    monkeypatch.setattr(main, "configure_ssl_certificates", lambda: None)
    monkeypatch.setattr(main, "setup_app_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "configure_qt_runtime_environment", lambda: {})
    monkeypatch.setattr(main, "ensure_packaged_webview_cache_fresh", lambda: None)
    monkeypatch.setattr(main, "log_renderer_diagnostics", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main, "collect_renderer_environment_snapshot", lambda dev_mode: {}
    )
    monkeypatch.setattr(main, "collect_pywebview_module_snapshot", lambda _m: {})
    monkeypatch.setattr(main, "bind_extension_server", lambda: extension_server)
    fake_webview = MagicMock()
    fake_webview.create_window.return_value = MagicMock()
    monkeypatch.setattr(main, "load_webview_module", lambda: fake_webview)
    monkeypatch.setattr(main, "cleanup_legacy_browser_data_async", lambda: None)
    monkeypatch.setattr(main, "DEV_MODE", True)
    monkeypatch.setattr(main, "start_vite_dev_server", lambda: vite_process)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])

    class FakeApi:
        library_sync = MagicMock()
        launcher = MagicMock()

        def sync_extension_files(self):
            return {"success": True}

        def set_startup_extension_sync_status(self, _s):
            return None

        def set_window(self, _w):
            return None

    monkeypatch.setattr(main, "Api", FakeApi)

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    def fail_webview(*_a, **_k):
        raise RuntimeError("webview failed")

    monkeypatch.setattr(main, "start_webview", fail_webview)

    with pytest.raises(RuntimeError, match="webview failed"):
        main.main()

    vite_process.terminate.assert_called_once()
    extension_server.shutdown.assert_called_once()
    extension_server.server_close.assert_called_once()


def test_main_flushes_playtime_before_the_final_sync(monkeypatch):
    order: list[str] = []
    fake_webview = MagicMock()
    fake_webview.create_window.return_value = MagicMock()

    class FakeApi:
        library_sync = MagicMock()
        launcher = MagicMock()

        def sync_extension_files(self):
            return {"success": True}

        def set_startup_extension_sync_status(self, _status):
            return None

        def set_window(self, _window):
            return None

    FakeApi.launcher.shutdown.side_effect = lambda: order.append("playtime")
    FakeApi.library_sync.finish.side_effect = lambda timeout: order.append("sync")

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            return None

    monkeypatch.setattr(main, "configure_ssl_certificates", lambda: None)
    monkeypatch.setattr(main, "setup_app_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "configure_qt_runtime_environment", lambda: {})
    monkeypatch.setattr(main, "ensure_packaged_webview_cache_fresh", lambda: None)
    monkeypatch.setattr(main, "log_renderer_diagnostics", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main, "collect_renderer_environment_snapshot", lambda dev_mode: {}
    )
    monkeypatch.setattr(main, "collect_pywebview_module_snapshot", lambda _m: {})
    monkeypatch.setattr(main, "Api", FakeApi)
    monkeypatch.setattr(main, "cleanup_legacy_browser_data_async", lambda: None)
    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    monkeypatch.setattr(main, "bind_extension_server", lambda: None)
    monkeypatch.setattr(main, "load_webview_module", lambda: fake_webview)
    monkeypatch.setattr(main, "start_webview", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "DEV_MODE", False)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])

    main.main()

    assert order == ["playtime", "sync"]


def test_main_terminates_vite_when_window_creation_fails(monkeypatch):
    vite_process = MagicMock()
    extension_server = MagicMock()
    fake_webview = MagicMock()
    fake_webview.create_window.side_effect = RuntimeError("no display")

    class FakeApi:
        library_sync = MagicMock()
        launcher = MagicMock()

        def sync_extension_files(self):
            return {"success": True}

        def set_startup_extension_sync_status(self, _status):
            return None

        def set_window(self, _window):
            return None

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            return None

    monkeypatch.setattr(main, "configure_ssl_certificates", lambda: None)
    monkeypatch.setattr(main, "setup_app_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "configure_qt_runtime_environment", lambda: {})
    monkeypatch.setattr(main, "ensure_packaged_webview_cache_fresh", lambda: None)
    monkeypatch.setattr(main, "log_renderer_diagnostics", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main, "collect_renderer_environment_snapshot", lambda dev_mode: {}
    )
    monkeypatch.setattr(main, "collect_pywebview_module_snapshot", lambda _m: {})
    monkeypatch.setattr(main, "Api", FakeApi)
    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    monkeypatch.setattr(main, "bind_extension_server", lambda: extension_server)
    monkeypatch.setattr(main, "load_webview_module", lambda: fake_webview)
    monkeypatch.setattr(main, "start_vite_dev_server", lambda: vite_process)
    monkeypatch.setattr(main, "DEV_MODE", True)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])

    with pytest.raises(RuntimeError, match="no display"):
        main.main()

    vite_process.terminate.assert_called_once()
    extension_server.shutdown.assert_called_once()
    extension_server.server_close.assert_called_once()
    FakeApi.library_sync.finish.assert_not_called()


@pytest.mark.parametrize("path", ["/api/checkpoint", "/api/check-evil", "/api/openx"])
def test_extension_get_rejects_prefix_squat_paths(monkeypatch, extension_server, path):
    window = MagicMock()
    monkeypatch.setattr(main, "window_ref", window)
    monkeypatch.setattr(
        main.ExtensionRequestHandler, "_find_matching_game", lambda _s, _u: None
    )
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request(
            "GET",
            f"{path}?url=https://f95zone.to/threads/demo.123/",
            headers={"Host": "localhost:8183", "Origin": "chrome-extension://wlib"},
        )
        response = connection.getresponse()
        _ = response.read()
        assert response.status == 404
        window.evaluate_js.assert_not_called()
    finally:
        connection.close()


def test_log_renderer_diagnostics_bounds_log_size(monkeypatch, tmp_path):
    renderer_log = tmp_path / "renderer-diagnostics.log"
    monkeypatch.setattr(main, "RENDERER_DIAGNOSTICS_LOG", str(renderer_log))
    monkeypatch.delenv("WLIB_LAUNCH_LOG", raising=False)
    monkeypatch.delenv("WLIB_APPIMAGE_LAUNCH_LOG", raising=False)
    with open(renderer_log, "w", encoding="utf-8") as f:
        for i in range(20000):
            _ = f.write(f"old-line-{i:05d} padding-padding-padding-padding\n")
    assert renderer_log.stat().st_size > main.RENDERER_DIAGNOSTICS_MAX_BYTES

    main.log_renderer_diagnostics("startup", {"probe": "new-entry"})

    content = renderer_log.read_text(encoding="utf-8")
    assert "probe=new-entry" in content
    assert content.startswith("old-line-")
    assert renderer_log.stat().st_size <= main.RENDERER_DIAGNOSTICS_MAX_BYTES


def test_trim_renderer_log_does_not_follow_symlinks(monkeypatch, tmp_path):
    log_path = str(tmp_path / "linked.log")
    monkeypatch.setattr(main.os.path, "islink", lambda p: p == log_path)
    monkeypatch.setattr(main.os.path, "getsize", lambda p: pytest.fail("followed link"))

    main._trim_renderer_log_file(log_path)


def test_clear_webview_cache_path_removes_symlink_not_target(monkeypatch, tmp_path):
    link_path = str(tmp_path / "cache-link")
    monkeypatch.setattr(main.os.path, "lexists", lambda p: p == link_path)
    monkeypatch.setattr(main.os.path, "islink", lambda p: p == link_path)
    monkeypatch.setattr(main.os.path, "isdir", lambda p: p == link_path)
    removed: list[str] = []
    monkeypatch.setattr(main.os, "remove", removed.append)
    monkeypatch.setattr(main.shutil, "rmtree", lambda *a, **k: pytest.fail("rmtree called"))

    main._clear_webview_cache_path(link_path)

    assert removed == [link_path]


def test_extension_handler_times_out_slow_clients():
    assert main.ExtensionRequestHandler.timeout == 10


def _bridge_request(server, *, origin=None, host="localhost:8183", upgrade=True):
    connection = HTTPConnection(*server.server_address, timeout=3)
    try:
        connection.putrequest("GET", "/api/bridge", skip_host=True)
        connection.putheader("Host", host)
        if origin is not None:
            connection.putheader("Origin", origin)
        if upgrade:
            connection.putheader("Upgrade", "websocket")
            connection.putheader("Connection", "Upgrade")
            connection.putheader(
                "Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ=="
            )
            connection.putheader("Sec-WebSocket-Version", "13")
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        accept = response.getheader("Sec-WebSocket-Accept")
        return response.status, body, accept
    finally:
        connection.close()


def test_bridge_upgrades_for_extension_origin(extension_server, monkeypatch):
    served = {}
    served_event = threading.Event()

    def fake_serve(sock, rfile, wfile):
        served["timeout"] = sock.gettimeout()
        served_event.set()
        # Close immediately so the handler thread exits; the 101 was sent.
        try:
            sock.shutdown(2)
        except OSError:
            pass

    monkeypatch.setattr("core.extension_bridge.serve_bridge_connection", fake_serve)
    status, _body, accept = _bridge_request(
        extension_server, origin="chrome-extension://wlib"
    )
    assert status == 101
    assert accept == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    # The 101 reaches the client before the handler thread calls the bridge.
    assert served_event.wait(5)
    assert served.get("timeout") == 60


def test_bridge_upgrades_for_firefox_origin(extension_server, monkeypatch):
    monkeypatch.setattr(
        "core.extension_bridge.serve_bridge_connection",
        lambda *_args, **_kwargs: None,
    )
    status, _body, _accept = _bridge_request(
        extension_server, origin="moz-extension://wlib"
    )
    assert status == 101


def test_bridge_rejects_web_origin(extension_server):
    status, body, _accept = _bridge_request(
        extension_server, origin="https://example.com"
    )
    assert status == 403
    assert json.loads(body)["success"] is False


def test_bridge_rejects_missing_origin(extension_server):
    status, body, _accept = _bridge_request(extension_server, origin=None)
    assert status == 403
    assert json.loads(body)["success"] is False


def test_bridge_rejects_missing_upgrade(extension_server):
    status, _body, _accept = _bridge_request(
        extension_server, origin="chrome-extension://wlib", upgrade=False
    )
    assert status == 400


def test_bridge_rejects_bad_host(extension_server):
    status, body, _accept = _bridge_request(
        extension_server,
        origin="chrome-extension://wlib",
        host="attacker.test:8183",
    )
    assert status == 403
    assert json.loads(body)["success"] is False


def test_store_page_wishlist_check_open_and_thread_links(
    monkeypatch, tmp_path, extension_server
):
    from urllib.parse import urlencode
    from core.api import Api
    from core import database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "links.db"))
    database.init_db()
    api = Api()
    monkeypatch.setattr(api, "_fetch_added_game_metadata", lambda *_: None)
    monkeypatch.setattr(main, "api_ref", api)
    window = MagicMock()
    monkeypatch.setattr(main, "window_ref", window)
    headers = {"Host": "localhost:8183", "Origin": "chrome-extension://wlib"}
    page = "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html"
    payload = {"title": "Work", "pageUrl": page, "links": [{"url": page}]}
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request("POST", "/api/wishlist", json.dumps(payload), headers)
        response = connection.getresponse()
        assert response.status == 200
        game_id = json.loads(response.read())["id"]
        connection.request(
            "GET", "/api/check?" + urlencode({"url": page}), headers=headers
        )
        response = connection.getresponse()
        checked = json.loads(response.read())
        assert (
            checked["exists"] and checked["gameId"] == game_id and checked["wishlisted"]
        )
        connection.request(
            "GET", "/api/open?" + urlencode({"url": page}), headers=headers
        )
        response = connection.getresponse()
        assert json.loads(response.read())["gameId"] == game_id
        assert "gameId:" in window.evaluate_js.call_args[0][0]
        connection.request("POST", "/api/wishlist", json.dumps(payload), headers)
        response = connection.getresponse()
        assert response.status == 409
        response.read()
        connection.request(
            "GET",
            "/api/check?"
            + urlencode(
                {"url": "https://f95zone.to/threads/123/", "id": "dlsite:RJ123"}
            ),
            headers=headers,
        )
        response = connection.getresponse()
        assert json.loads(response.read())["canAttachThread"]
        assert not database.get_game(game_id)["f95_url"]
        payload["url"] = "https://f95zone.to/threads/123/"
        payload["pageUrl"] = payload["url"]
        connection.request("POST", "/api/wishlist", json.dumps(payload), headers)
        response = connection.getresponse()
        assert response.status == 409
        assert json.loads(response.read())["attached"]
        assert len(database.get_all_games()) == 1
        connection.request(
            "POST",
            "/api/thread-links",
            json.dumps(
                {"url": payload["url"], "links": [{"url": "https://vndb.org/v1"}]}
            ),
            headers,
        )
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        assert {link["source"] for link in database.get_game_links(game_id)} == {
            "dlsite",
            "vndb",
        }
        connection.request(
            "POST",
            "/api/thread-links",
            "{}",
            {"Host": "localhost:8183", "Origin": "https://foreign.example"},
        )
        response = connection.getresponse()
        assert response.status == 403
        response.read()
    finally:
        connection.close()


def test_wishlist_waits_for_startup_database(
    monkeypatch, extension_server, tmp_path
):
    from core import database

    # The app serves this port before Api() has run init_db(). A wishlist call
    # in that window must get the documented 503, not a raw SQLite error from
    # resolving the payload's store links against a schema-less database.
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "not-initialized.db"))
    monkeypatch.setattr(main, "api_ref", None)
    monkeypatch.setattr(main, "DATABASE_READY_TIMEOUT", 0.05)
    extension_server.database_ready = threading.Event()
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request(
            "POST",
            "/api/wishlist",
            json.dumps(
                {
                    "title": "Wishlist demo",
                    "url": "https://f95zone.to/threads/wishlist.555/",
                }
            ),
            headers={"Host": "localhost:8183", "Origin": "chrome-extension://wlib"},
        )
        response = connection.getresponse()
        assert response.status == 503
        assert json.loads(response.read()) == {
            "success": False,
            "error": "Application is not ready",
        }
    finally:
        connection.close()


def test_database_endpoints_wait_until_startup_has_run_init_db(
    monkeypatch, extension_server
):
    window = MagicMock()
    monkeypatch.setattr(main, "window_ref", None)
    monkeypatch.setattr(main, "DATABASE_READY_TIMEOUT", 0.05)
    ready = threading.Event()
    extension_server.database_ready = ready
    find_calls: list[object] = []
    monkeypatch.setattr(
        main.ExtensionRequestHandler,
        "_build_check_payload",
        lambda _s, url, _ids: find_calls.append(url) or {"exists": False},
    )
    headers = {"Host": "localhost:8183", "Origin": "chrome-extension://wlib"}
    links = json.dumps({"url": "https://f95zone.to/threads/123/", "links": []})
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        # The instance lock still answers while the database is not ready.
        connection.request("GET", "/api/open", headers=headers)
        response = connection.getresponse()
        assert json.loads(response.read())["success"] is False
        connection.request(
            "GET", "/api/check?url=https://f95zone.to/threads/123/", headers=headers
        )
        response = connection.getresponse()
        assert response.status == 503
        not_ready = json.loads(response.read())
        assert not_ready["error"] == "Application is not ready"
        assert not_ready["app_version"] == main.APP_VERSION
        connection.request("POST", "/api/thread-links", links, headers)
        response = connection.getresponse()
        assert response.status == 503
        response.read()
        assert find_calls == []

        ready.set()
        monkeypatch.setattr(main, "window_ref", window)
        connection.request(
            "GET", "/api/check?url=https://f95zone.to/threads/123/", headers=headers
        )
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        connection.request("POST", "/api/thread-links", links, headers)
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        assert find_calls == ["https://f95zone.to/threads/123/"]
    finally:
        connection.close()
