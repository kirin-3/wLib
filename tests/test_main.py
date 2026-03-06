import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main


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
    monkeypatch.setattr(main, "PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
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

    monkeypatch.setattr(main, "PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
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
