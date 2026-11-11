# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import MagicMock

from core.api import Api
from core import steam_env
from core.host_platform import get_platform_capabilities
from api_patching import patch_api_name


def test_steam_keyboard_requires_linux_and_gaming_mode(monkeypatch):
    api = Api()
    patch_api_name(monkeypatch, "is_linux", lambda: False)
    monkeypatch.setattr(steam_env, 'gaming_mode', lambda: True)
    assert api.open_steam_keyboard()['code'] == 'unsupported'
    patch_api_name(monkeypatch, "is_linux", lambda: True)
    monkeypatch.setattr(steam_env, 'gaming_mode', lambda: False)
    assert api.open_steam_keyboard()['code'] == 'unsupported'


def test_steam_keyboard_uses_host_command(monkeypatch):
    api = Api()
    patch_api_name(monkeypatch, "is_linux", lambda: True)
    monkeypatch.setattr(steam_env, 'gaming_mode', lambda: True)
    host = MagicMock(return_value=['host', 'steam'])
    popen = MagicMock()
    patch_api_name(monkeypatch, "host_command", host)
    monkeypatch.setattr('subprocess.Popen', popen)
    assert api.open_steam_keyboard()['success'] is True
    host.assert_called_once_with(['steam', 'steam://open/keyboard'])
    assert popen.call_args.args[0] == ['host', 'steam']
    assert popen.call_args.kwargs['start_new_session'] is True
    assert get_platform_capabilities('linux')['gaming_mode'] is True
    assert get_platform_capabilities('win32')['gaming_mode'] is False
