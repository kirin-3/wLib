# pyright: reportMissingImports=false
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


@patch("os.path.exists")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
def test_launch_command_substitution(
    mock_get_setting, mock_popen, mock_access, mock_exists
):
    """Test Steam-style %command% substitution."""
    mock_exists.return_value = True
    mock_access.return_value = True
    mock_get_setting.return_value = "false"

    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    # Test with %command% in args
    result = launcher.launch(
        "/opt/game/run.sh",
        command_line_args="gamemoderun gamescope -W 1920 -H 1080 -- %command% -developer",
    )

    assert result["success"] is True
    args, kwargs = mock_popen.call_args
    assert args[0] == [
        "gamemoderun",
        "gamescope",
        "-W",
        "1920",
        "-H",
        "1080",
        "--",
        "/opt/game/run.sh",
        "-developer",
    ]

    # Test without %command% (fallback behavior)
    launcher.launch("/opt/game/run.sh", command_line_args="-developer")

    args, kwargs = mock_popen.call_args
    assert args[0] == ["/opt/game/run.sh", "-developer"]


def test_launch_rejects_empty_executable_path():
    launcher = Launcher()
    result = launcher.launch("   ")

    assert result["success"] is False
    assert "non-empty string" in str(result.get("error", ""))


@patch("os.path.exists")
def test_launch_rejects_invalid_command_line(mock_exists):
    mock_exists.return_value = True

    launcher = Launcher()
    result = launcher.launch("/opt/game/run.sh", command_line_args='"unterminated')

    assert result["success"] is False
    assert "Invalid command line arguments" in str(result.get("error", ""))


@patch("os.path.exists")
@patch("subprocess.Popen")
@patch("os.path.abspath")
def test_launch_html_game(mock_abspath, mock_popen, mock_exists):
    """Test launching an HTML game opens with xdg-open."""
    mock_exists.return_value = True
    mock_abspath.return_value = "/home/user/games/index.html"
    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    result = launcher.launch("/home/user/games/index.html")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["xdg-open", "file:///home/user/games/index.html"]


@patch("os.path.exists")
@patch("subprocess.Popen")
@patch("os.path.abspath")
def test_launch_html_game_htm_extension(mock_abspath, mock_popen, mock_exists):
    """Test launching an HTML game with .htm extension."""
    mock_exists.return_value = True
    mock_abspath.return_value = "/home/user/games/game.htm"
    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    result = launcher.launch("/home/user/games/game.htm")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["xdg-open", "file:///home/user/games/game.htm"]


@patch("os.path.exists")
@patch("subprocess.Popen")
@patch("os.path.abspath")
def test_launch_html_game_case_insensitive(mock_abspath, mock_popen, mock_exists):
    """Test launching an HTML game with uppercase extension."""
    mock_exists.return_value = True
    mock_abspath.return_value = "/home/user/games/INDEX.HTML"
    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    result = launcher.launch("/home/user/games/INDEX.HTML")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["xdg-open", "file:///home/user/games/INDEX.HTML"]


def test_launch_html_file_not_found():
    """Test launching a non-existent HTML game returns error."""
    launcher = Launcher()
    result = launcher.launch("/nonexistent/game.html")

    assert result["success"] is False
    assert "not found" in str(result.get("error", "")).lower()


@patch("os.path.exists")
@patch("subprocess.Popen")
def test_launch_html_no_playtime_tracking(mock_popen, mock_exists):
    """Test HTML games don't trigger playtime tracking callback."""
    mock_exists.return_value = True
    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    callback_called = False

    def test_callback(delta, is_final):
        nonlocal callback_called
        callback_called = True

    result = launcher.launch(
        "/home/user/games/index.html", on_exit_callback=test_callback
    )

    assert result["success"] is True
    # Callback should not be triggered for HTML games
    # (The browser process is not tracked)
    assert callback_called is False


@patch("os.path.exists")
@patch("subprocess.Popen")
@patch("os.path.abspath")
@patch.dict(
    "os.environ",
    {
        "APPIMAGE": "/tmp/wLib.AppImage",
        "APPDIR": "/tmp/.mount_wLib",
        "LD_LIBRARY_PATH": "/tmp/.mount_wLib/usr/lib",
        "PATH": "/usr/bin:/bin",
    },
)
def test_launch_html_from_appimage(mock_abspath, mock_popen, mock_exists):
    """Test HTML games remove AppImage environment variables."""
    mock_exists.return_value = True
    mock_abspath.return_value = "/home/user/games/index.html"
    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    result = launcher.launch("/home/user/games/index.html")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args

    # Verify AppImage variables are removed from environment
    env = kwargs["env"]
    assert "APPIMAGE" not in env
    assert "APPDIR" not in env
    # LD_LIBRARY_PATH should be removed or reset
    assert (
        "LD_LIBRARY_PATH" not in env
        or env["LD_LIBRARY_PATH"] != "/tmp/.mount_wLib/usr/lib"
    )
