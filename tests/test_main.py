import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main


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
