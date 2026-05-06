import builtins
from io import BytesIO
from email.message import Message
import os
import sys
import json
from typing import cast
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main


def _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path):
    app_data_dir = tmp_path / "data"
    cache_home = tmp_path / "cache"
    monkeypatch.setattr(main, "APP_DATA_DIR", str(app_data_dir))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", "/tmp/wlib-bin")
    return {
        "app_data_dir": app_data_dir,
        "cache_path": cache_home / main.PACKAGED_WEBVIEW_CACHE_DIR_NAME,
        "marker_path": app_data_dir / main.PYWEBVIEW_CACHE_VERSION_MARKER,
        "webview_storage": app_data_dir / main.PYWEBVIEW_STORAGE_DIR_NAME,
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


def test_configure_qt_runtime_environment_prefers_wayland(monkeypatch):
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
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WLIB_QPA_PLATFORM", raising=False)

    result = main.configure_qt_runtime_environment()

    assert result["qt_qpa_platform"] == "xcb"
    assert result["source"] == "auto-x11"
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_configure_qt_runtime_environment_honors_override(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("WLIB_QPA_PLATFORM", "xcb")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)

    result = main.configure_qt_runtime_environment()

    assert result["qt_qpa_platform"] == "xcb"
    assert result["source"] == "override"
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_ensure_playwright_browsers_refuses_recursive_installer_in_frozen(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(main, "playwright_browsers_path", str(tmp_path))
    monkeypatch.setattr(main.importlib, "import_module", lambda _: object())
    monkeypatch.setattr(
        main,
        "_get_playwright_install_command",
        lambda: ["/tmp/wlib-bin", "-m", "playwright", "install", "chromium"],
    )
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", "/tmp/wlib-bin")

    run_mock = MagicMock()
    monkeypatch.setattr(main.subprocess, "run", run_mock)

    result = main.ensure_playwright_browsers()

    assert result is False
    run_mock.assert_not_called()


def test_ensure_playwright_browsers_uses_driver_command_in_frozen(
    monkeypatch, tmp_path
):
    install_cmd = [
        "/tmp/playwright-driver",
        "/tmp/playwright-cli",
        "install",
        "chromium",
    ]

    monkeypatch.setattr(main, "playwright_browsers_path", str(tmp_path))
    monkeypatch.setattr(main.importlib, "import_module", lambda _: object())
    monkeypatch.setattr(main, "_get_playwright_install_command", lambda: install_cmd)
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", "/tmp/wlib-bin")

    run_mock = MagicMock()
    monkeypatch.setattr(main.subprocess, "run", run_mock)

    result = main.ensure_playwright_browsers()

    assert result is True
    run_mock.assert_called_once()
    assert run_mock.call_args.args[0] == install_cmd


def test_get_packaged_webview_cache_path_uses_xdg_cache_home(monkeypatch, tmp_path):
    cache_home = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))

    cache_path = main.get_packaged_webview_cache_path()

    assert cache_path == os.path.join(
        str(cache_home), main.PACKAGED_WEBVIEW_CACHE_DIR_NAME
    )


def test_ensure_packaged_webview_cache_fresh_clears_first_launch_cache(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True)
    (paths["cache_path"] / "stale-cache").write_text("old", encoding="utf-8")
    paths["webview_storage"].mkdir(parents=True)
    (paths["webview_storage"] / "prefs").write_text("keep", encoding="utf-8")

    main.ensure_packaged_webview_cache_fresh()

    assert not paths["cache_path"].exists()
    assert paths["marker_path"].read_text(encoding="utf-8").strip() == main.APP_VERSION
    assert (paths["webview_storage"] / "prefs").read_text(encoding="utf-8") == "keep"


def test_ensure_packaged_webview_cache_fresh_updates_changed_version(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True)
    (paths["cache_path"] / "old-cache").write_text("old", encoding="utf-8")
    paths["marker_path"].parent.mkdir(parents=True)
    paths["marker_path"].write_text("0.0.1\n", encoding="utf-8")

    main.ensure_packaged_webview_cache_fresh()

    assert not paths["cache_path"].exists()
    assert paths["marker_path"].read_text(encoding="utf-8").strip() == main.APP_VERSION


def test_ensure_packaged_webview_cache_fresh_preserves_unchanged_version_cache(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True)
    cache_file = paths["cache_path"] / "current-cache"
    cache_file.write_text("current", encoding="utf-8")
    paths["marker_path"].parent.mkdir(parents=True)
    paths["marker_path"].write_text(main.APP_VERSION + "\n", encoding="utf-8")

    main.ensure_packaged_webview_cache_fresh()

    assert cache_file.read_text(encoding="utf-8") == "current"
    assert paths["marker_path"].read_text(encoding="utf-8").strip() == main.APP_VERSION


def test_ensure_packaged_webview_cache_fresh_skips_source_runtime(
    monkeypatch, tmp_path
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True)
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
    paths["cache_path"].mkdir(parents=True)
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


def test_ensure_packaged_webview_cache_fresh_keeps_marker_on_delete_failure(
    monkeypatch, tmp_path, capsys
):
    paths = _configure_packaged_webview_cache_runtime(monkeypatch, tmp_path)
    paths["cache_path"].mkdir(parents=True)
    paths["marker_path"].parent.mkdir(parents=True)
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
    paths["cache_path"].mkdir(parents=True)
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


def test_main_runs_webview_cache_cleanup_before_importing_webview(monkeypatch):
    call_order: list[str] = []
    fake_window = MagicMock()
    fake_webview = MagicMock()
    fake_webview.create_window.return_value = fake_window

    class FakeApi:
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
    monkeypatch.setattr(main, "configure_playwright_browsers_path", lambda: "")
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
    monkeypatch.setattr(main, "log_renderer_diagnostics", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "collect_renderer_environment_snapshot", lambda dev_mode: {})
    monkeypatch.setattr(main, "collect_pywebview_module_snapshot", lambda _module: {})
    monkeypatch.setattr(main, "Api", FakeApi)
    monkeypatch.setattr(main, "ensure_playwright_browsers_async", lambda: None)
    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        main, "start_webview", lambda *_args, **_kwargs: call_order.append("start")
    )
    monkeypatch.setattr(main, "DEV_MODE", False)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])

    main.main()

    assert call_order[:2] == ["cleanup", "load_webview"]


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
    assert kwargs["func"] is main.log_runtime_renderer_diagnostics


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
    assert kwargs["func"] is main.log_runtime_renderer_diagnostics


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
        },
    )

    handler.do_GET()

    response_body = cast(BytesIO, handler.wfile).getvalue().decode("utf-8")

    assert json.loads(response_body) == {
        "exists": True,
        "playStatus": "Playing",
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
    }
