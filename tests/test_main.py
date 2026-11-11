# SPDX-License-Identifier: GPL-3.0-or-later
import builtins
import http.client
from io import BytesIO
import os
import sys
import json
import threading
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main

_real_install_shutdown_signals = main.install_shutdown_signals
_real_data_dir_problem = main.data_dir_problem


@pytest.fixture(autouse=True)
def _no_process_wide_startup_effects(monkeypatch):
    """main() must not replace pytest's signal handlers or probe the real data dir."""
    monkeypatch.setattr(main, "install_shutdown_signals", lambda close: False)
    monkeypatch.setattr(main, "data_dir_problem", lambda path: None)


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
        steam_sync = None
        _start_steam_sync = MagicMock()
        cover_cache = MagicMock()
        cover_cards = MagicMock()
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
        steam_sync = None
        _start_steam_sync = MagicMock()
        cover_cache = MagicMock()
        cover_cards = MagicMock()
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
        steam_sync = None
        _start_steam_sync = MagicMock()
        cover_cache = MagicMock()
        cover_cards = MagicMock()
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
        steam_sync = None
        _start_steam_sync = MagicMock()
        cover_cache = MagicMock()
        cover_cards = MagicMock()
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


def test_main_gives_the_extension_server_the_api_and_the_window(monkeypatch):
    from core import extension_server as server_module

    window = MagicMock()
    fake_webview = MagicMock()
    fake_webview.create_window.return_value = window
    seen: dict[str, object] = {}

    class FakeApi:
        steam_sync = None
        _start_steam_sync = MagicMock()
        cover_cache = MagicMock()
        cover_cards = MagicMock()
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

    class RecordingEvent(threading.Event):
        def set(self):
            # The handler waits on this event, then reads api_ref.
            seen["api_at_ready"] = server_module.api_ref
            super().set()

    def fake_start_webview(*_args, **_kwargs):
        seen["api"] = server_module.api_ref
        seen["window"] = server_module.window_ref

    monkeypatch.setattr(server_module, "api_ref", None)
    monkeypatch.setattr(server_module, "window_ref", None)
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
    monkeypatch.setattr(main.threading, "Event", RecordingEvent)
    monkeypatch.setattr(main, "bind_extension_server", lambda: None)
    monkeypatch.setattr(main, "load_webview_module", lambda: fake_webview)
    monkeypatch.setattr(main, "start_webview", fake_start_webview)
    monkeypatch.setattr(main, "DEV_MODE", False)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])

    main.main()

    assert isinstance(seen["api"], FakeApi)
    assert seen["api_at_ready"] is seen["api"]
    assert seen["window"] is window


def test_main_terminates_vite_when_window_creation_fails(monkeypatch):
    vite_process = MagicMock()
    extension_server = MagicMock()
    fake_webview = MagicMock()
    fake_webview.create_window.side_effect = RuntimeError("no display")

    class FakeApi:
        steam_sync = None
        _start_steam_sync = MagicMock()
        cover_cache = MagicMock()
        cover_cards = MagicMock()
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


def test_gaming_mode_start_url_for_production_and_dev():
    for url in ('/tmp/ui/dist/index.html', 'http://localhost:5173'):
        assert main.gaming_mode_start_url(url, True) == f'{url}?gamingMode=1'
        assert main.gaming_mode_start_url(url, False) == url


def test_wait_for_port_free_waits_for_a_closing_instance():
    import socket as socket_module

    holder = socket_module.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    try:
        assert main.wait_for_port_free(port, timeout=0.3) is False
    finally:
        holder.close()
    assert main.wait_for_port_free(port, timeout=1) is True


def test_data_dir_problem_reports_a_read_only_folder(tmp_path):
    assert _real_data_dir_problem(str(tmp_path / "new")) is None
    blocker = tmp_path / "file"
    blocker.write_text("x")
    problem = _real_data_dir_problem(str(blocker / "data"))
    assert problem and str(blocker / "data") in problem


def test_main_shows_an_error_when_the_data_dir_is_read_only(monkeypatch):
    shown = []
    monkeypatch.setattr(main, "configure_ssl_certificates", lambda: "")
    monkeypatch.setattr(main, "bind_extension_server", lambda: None)
    monkeypatch.setattr(main, "focus_running_instance", lambda: False)
    monkeypatch.setattr(main, "data_dir_problem", lambda path: "cannot write")
    monkeypatch.setattr(main, "show_fatal_startup_error", shown.append)
    monkeypatch.setattr(main, "setup_app_log", lambda *_a, **_k: pytest.fail("logged"))
    with pytest.raises(SystemExit) as exit_info:
        main.main()
    assert exit_info.value.code == 1
    assert shown and shown[0].startswith("cannot write")


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_sigterm_closes_the_window_through_the_shutdown_path(tmp_path):
    import subprocess

    script = (
        "import os, signal, sys, threading, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import main\n"
        "closed = threading.Event()\n"
        "assert main.install_shutdown_signals(closed.set)\n"
        "os.kill(os.getpid(), signal.SIGTERM)\n"
        "print('closed' if closed.wait(5) else 'not closed', flush=True)\n"
    )
    root = os.path.dirname(os.path.dirname(__file__))
    result = subprocess.run(
        [sys.executable, "-c", script, root],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "WLIB_DATA_DIR": str(tmp_path)},
        check=False,
    )
    # The process survives the signal and closes the window instead.
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("closed")
    assert "not closed" not in result.stdout


def test_fatal_startup_error_uses_a_message_box_on_windows(monkeypatch):
    import ctypes

    shown = []

    class _User32:
        def MessageBoxW(self, hwnd, text, title, flags):  # noqa: N802
            shown.append((text, title))
            return 1

    monkeypatch.setattr(main, "is_windows", lambda: True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: _User32(), raising=False)
    monkeypatch.setattr(main.shutil, "which", lambda _name: pytest.fail("no Linux tools"))
    main.show_fatal_startup_error("The data folder is read-only.")
    assert shown == [("The data folder is read-only.", "wLib cannot start")]


def test_webview_profile_goes_to_the_data_folder(tmp_path, monkeypatch):
    import types

    home = tmp_path / "home"
    legacy = home / ".pywebview"
    (legacy / "Local Storage").mkdir(parents=True)
    (legacy / "Local Storage" / "leveldb").write_text("ui state")
    (legacy / "GPUCache").mkdir()
    (legacy / "GPUCache" / "data_0").write_bytes(b"cache")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    storage = tmp_path / "data" / "webview"
    storage.mkdir(parents=True)

    assert main.adopt_legacy_webview_storage(str(storage)) is True
    assert (storage / "Local Storage" / "leveldb").read_text() == "ui state"
    assert (legacy / "Local Storage" / "leveldb").exists()  # copied, not moved
    assert not (storage / "GPUCache").exists()  # caches are rebuilt, not copied
    # Once the data folder has a profile it is never overwritten.
    (legacy / "Local Storage" / "leveldb").write_text("other app")
    assert main.adopt_legacy_webview_storage(str(storage)) is False
    assert (storage / "Local Storage" / "leveldb").read_text() == "ui state"

    backend = types.ModuleType("webview.platforms.qt")
    backend._profile_storage_path = str(legacy)  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, "webview.platforms.qt", backend)
    main.point_qt_backend_at_storage(str(storage))
    assert backend._profile_storage_path == str(storage)  # pyright: ignore[reportAttributeAccessIssue]


def test_main_reports_a_damaged_database_and_exits(monkeypatch):
    from core.database import DatabaseDamagedError

    shown = []
    server = MagicMock()

    def damaged_api():
        raise DatabaseDamagedError("The library database is damaged.")

    monkeypatch.setattr(main, "configure_ssl_certificates", lambda: None)
    monkeypatch.setattr(main, "setup_app_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "configure_qt_runtime_environment", lambda: {})
    monkeypatch.setattr(main, "ensure_packaged_webview_cache_fresh", lambda: None)
    monkeypatch.setattr(main, "load_webview_module", lambda: MagicMock())
    monkeypatch.setattr(main, "log_renderer_diagnostics", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "collect_renderer_environment_snapshot", lambda dev_mode: {})
    monkeypatch.setattr(main, "collect_pywebview_module_snapshot", lambda _module: {})
    monkeypatch.setattr(main, "Api", damaged_api)
    monkeypatch.setattr(main.threading, "Thread", MagicMock())
    monkeypatch.setattr(main, "bind_extension_server", lambda: server)
    monkeypatch.setattr(main, "show_fatal_startup_error", shown.append)
    monkeypatch.setattr(main, "start_webview", lambda *_a, **_k: pytest.fail("started"))
    monkeypatch.setattr(main.sys, "argv", ["main.py"])

    with pytest.raises(SystemExit) as exit_info:
        main.main()

    assert exit_info.value.code == 1
    assert shown == ["The library database is damaged."]
    server.server_close.assert_called_once()
