# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import MagicMock

from core.api import Api
from core import steam_env
from core.host_platform import get_platform_capabilities
from api_patching import patch_api_name


def test_steam_keyboard_requires_linux_and_gaming_mode(monkeypatch):
    api = Api()
    patch_api_name(monkeypatch, "is_linux", lambda: False)
    monkeypatch.setattr(steam_env, "gaming_mode", lambda: True)
    assert api.open_steam_keyboard()["code"] == "unsupported"
    patch_api_name(monkeypatch, "is_linux", lambda: True)
    monkeypatch.setattr(steam_env, "gaming_mode", lambda: False)
    assert api.open_steam_keyboard()["code"] == "unsupported"


def test_steam_keyboard_uses_host_command(monkeypatch):
    api = Api()
    patch_api_name(monkeypatch, "is_linux", lambda: True)
    monkeypatch.setattr(steam_env, "gaming_mode", lambda: True)
    host = MagicMock(return_value=["host", "steam"])
    popen = MagicMock()
    patch_api_name(monkeypatch, "host_command", host)
    monkeypatch.setattr("subprocess.Popen", popen)
    assert api.open_steam_keyboard()["success"] is True
    host.assert_called_once_with(["steam", "steam://open/keyboard"])
    assert popen.call_args.args[0] == ["host", "steam"]
    assert popen.call_args.kwargs["start_new_session"] is True
    assert get_platform_capabilities("linux")["gaming_mode"] is True
    assert get_platform_capabilities("win32")["gaming_mode"] is False


def test_controller_presence_event_and_focus_recheck(monkeypatch, tmp_path):
    from queue import Queue

    from core import controllers

    monkeypatch.setattr(steam_env, "gaming_mode", lambda: False)
    monkeypatch.setattr(controllers.sys, "platform", "linux")
    api = Api()
    api.window = MagicMock()
    events = Queue()
    api.window.evaluate_js.side_effect = events.put
    check = MagicMock(return_value=False)
    api.controller_watcher = controllers.ControllerWatcher(
        api._controller_presence_changed,
        check=check,
        dev_root=tmp_path / "missing",
    )

    assert api.get_controller_presence() == {"success": True, "present": None}
    api.controller_watcher.start()
    try:
        assert events.get(timeout=1) == (
            "window.dispatchEvent(new CustomEvent('wlib-controller-presence', "
            '{detail: {"present": false}}));'
        )
        check.return_value = True
        assert api.set_window_focused(True) == {"success": True}
        assert events.get(timeout=1) == (
            "window.dispatchEvent(new CustomEvent('wlib-controller-presence', "
            '{detail: {"present": true}}));'
        )
        assert api.get_controller_presence() == {"success": True, "present": True}
        assert check.call_count == api.window.evaluate_js.call_count == 2
    finally:
        api.controller_watcher.stop()
