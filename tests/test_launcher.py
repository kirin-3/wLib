import os
import pytest
from unittest.mock import patch, MagicMock, mock_open
from core.launcher import Launcher

@pytest.fixture
def launcher():
    return Launcher()

@patch('core.launcher.get_setting')
@patch('os.makedirs')
@patch('os.path.exists')
@patch('subprocess.Popen')
def test_launch_default_wine(mock_popen, mock_exists, mock_makedirs, mock_get_setting, launcher):
    # Setup
    mock_exists.return_value = True
    def side_effect(key):
        return ""
    mock_get_setting.side_effect = side_effect

    # Run
    exe_path = "/path/to/game.exe"
    launcher.launch(exe_path)

    # Assert
    # Should use 'wine' and create default prefix
    expected_prefix = os.path.expanduser("~/.local/share/wLib/prefix")
    mock_makedirs.assert_called_with(expected_prefix, exist_ok=True)

    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    cmd = args[0]
    env = kwargs['env']

    assert cmd == ["wine", exe_path]
    assert env["WINEPREFIX"] == expected_prefix

@patch('core.launcher.get_setting')
@patch('os.makedirs')
@patch('os.path.exists')
@patch('subprocess.Popen')
def test_launch_proton(mock_popen, mock_exists, mock_makedirs, mock_get_setting, launcher):
    # Setup
    mock_exists.return_value = True
    proton_path = "/home/user/.local/share/wLib/proton/GE-Proton8-25/proton"

    def get_setting_side_effect(key):
        if key == "proton_path": return proton_path
        if key == "wine_prefix_path": return "/custom/prefix"
        return ""
    mock_get_setting.side_effect = get_setting_side_effect

    # Run
    exe_path = "/path/to/game.exe"
    launcher.launch(exe_path)

    # Assert
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    cmd = args[0]
    env = kwargs['env']

    # Check if 'run' argument is appended for proton
    assert cmd == [proton_path, "run", exe_path]
    assert env["STEAM_COMPAT_DATA_PATH"] == "/custom/prefix"
    # Proton expects this
    assert env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] == "/tmp/wlib"

@patch('core.launcher.get_setting')
@patch('os.makedirs')
@patch('os.path.exists')
@patch('subprocess.Popen')
def test_rpgmaker_overrides(mock_popen, mock_exists, mock_makedirs, mock_get_setting, launcher):
    # Setup
    # Mock exists to return True for nw.dll and www folder checks
    def exists_side_effect(path):
        if "nw.dll" in path or "www" in path:
            return True
        # Also need to return True for the exe itself
        if path == "/path/to/game/Game.exe":
            return True
        return False

    mock_exists.side_effect = exists_side_effect
    mock_get_setting.return_value = ""

    # Run
    exe_path = "/path/to/game/Game.exe"
    launcher.launch(exe_path)

    # Assert
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    env = kwargs['env']

    assert "libglesv2=d;libegl=d;winegstreamer=d" in env["WINEDLLOVERRIDES"]

@patch('core.launcher.get_setting')
@patch('os.makedirs')
@patch('os.path.exists')
def test_launch_file_not_found(mock_exists, mock_makedirs, mock_get_setting, launcher):
    mock_exists.return_value = False
    result = launcher.launch("/nonexistent/game.exe")
    assert result is False

@patch('builtins.open', new_callable=mock_open)
@patch('core.launcher.get_setting')
@patch('os.makedirs')
@patch('os.path.exists')
@patch('subprocess.Popen')
def test_logging_enabled(mock_popen, mock_exists, mock_makedirs, mock_get_setting, mock_file, launcher):
    # Setup
    mock_exists.return_value = True
    def side_effect(key):
        if key == "enable_logging": return "true"
        return ""
    mock_get_setting.side_effect = side_effect

    # Run
    exe_path = "/path/to/game.exe"
    launcher.launch(exe_path)

    # Assert
    mock_file.assert_called_once() # Should open a log file
    args, kwargs = mock_popen.call_args
    env = kwargs['env']

    assert env["PROTON_LOG"] == "1"
    assert env["WINEDEBUG"] == "+all"
