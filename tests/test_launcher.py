import pytest
import os
import subprocess
from unittest.mock import patch, MagicMock
from core.launcher import Launcher


@patch("os.path.exists")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
def test_launch_native_script(mock_get_setting, mock_popen, mock_access, mock_exists):
    """Test launching a native .sh script directly."""
    mock_exists.return_value = True
    mock_access.return_value = True
    mock_get_setting.return_value = "false"

    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    result = launcher.launch("/opt/game/run.sh", command_line_args="--test mode")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["/opt/game/run.sh", "--test", "mode"]


@patch("os.path.exists")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
def test_launch_java_archive(mock_get_setting, mock_popen, mock_exists):
    """Test launching a .jar file."""
    mock_exists.return_value = True
    mock_get_setting.return_value = "false"

    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    result = launcher.launch("/home/user/game.jar", command_line_args="-Xmx1G")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["java", "-jar", "/home/user/game.jar", "-Xmx1G"]


@patch("os.path.exists")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
def test_launch_proton_prefix_isolation(mock_get_setting, mock_popen, mock_exists):
    """Test that proton creates isolated prefix logic correctly."""

    # Custom side effect for exists/isdir
    def exists_side_effect(path):
        if "/home/user/.wine/drive_c" in path:
            return True
        if "/home/user/.wine/pfx" in path:
            return False
        return True

    mock_exists.side_effect = exists_side_effect

    # Mock settings
    def get_setting_side_effect(key):
        if key == "proton_path":
            return "/usr/bin/proton"
        if key == "wine_prefix_path":
            return "/home/user/.wine"
        return "false"

    mock_get_setting.side_effect = get_setting_side_effect

    # Prevent os.makedirs from doing anything
    with (
        patch("os.makedirs") as mock_makedirs,
        patch("os.path.isdir", side_effect=exists_side_effect),
    ):
        launcher = Launcher()
        result = launcher.launch("/tmp/game.exe")

        assert result["success"] is True
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        env = kwargs["env"]

        # It should append "proton_compat" to avoid colliding with the standard wine prefix
        assert "proton_compat" in env["STEAM_COMPAT_DATA_PATH"]
        assert env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] == "/tmp/wlib"
