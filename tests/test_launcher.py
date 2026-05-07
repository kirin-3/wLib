# pyright: reportMissingImports=false
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
    assert kwargs["cwd"] == "/opt/game"


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
    assert kwargs["cwd"] == "/home/user"


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
        patch("os.makedirs"),
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
        assert kwargs["cwd"] == "/tmp"


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


@patch("os.path.exists")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
@patch.dict(
    "os.environ",
    {
        "APPIMAGE": "/tmp/wLib.AppImage",
        "LD_LIBRARY_PATH": "/tmp/.mount_wLib/usr/lib",
        "LD_LIBRARY_PATH_ORIG": "/usr/lib:/opt/lib",
        "WINEPREFIX": "/tmp/from-env",
        "WINEDLLOVERRIDES": "winhttp=n,b",
        "STEAM_COMPAT_DATA_PATH": "/tmp/steam-compat",
        "STEAM_COMPAT_CLIENT_INSTALL_PATH": "/tmp/steam-client",
        "STEAM_COMPAT_INSTALL_PATH": "/tmp/install",
        "UMU_ID": "from-env",
    },
    clear=True,
)
def test_launch_native_mode_uses_host_binary_without_wine_settings(
    mock_get_setting, mock_popen, mock_access, mock_exists
):
    """Linux Native should run directly and ignore stale Wine/Proton settings."""
    mock_exists.return_value = True
    mock_access.return_value = True

    def get_setting_side_effect(key):
        assert key == "enable_logging"
        return "false"

    mock_get_setting.side_effect = get_setting_side_effect
    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    result = launcher.launch(
        "/opt/game/Game.x86_64",
        command_line_args="gamemoderun %command% --fullscreen",
        auto_inject_ce=True,
        custom_prefix="/tmp/custom-prefix",
        proton_version="/tmp/proton",
        launch_mode="native",
    )

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["gamemoderun", "/opt/game/Game.x86_64", "--fullscreen"]
    assert kwargs["cwd"] == "/opt/game"
    env = kwargs["env"]
    assert "APPIMAGE" not in env
    assert env["LD_LIBRARY_PATH"] == "/usr/lib:/opt/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert "WINEPREFIX" not in env
    assert "WINEDLLOVERRIDES" not in env
    assert "STEAM_COMPAT_DATA_PATH" not in env
    assert "STEAM_COMPAT_CLIENT_INSTALL_PATH" not in env
    assert "STEAM_COMPAT_INSTALL_PATH" not in env
    assert "UMU_ID" not in env


@patch("os.path.exists")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
@patch.dict(
    "os.environ",
    {
        "APPDIR": "/tmp/.mount_wLib",
        "ARGV0": "/tmp/wLib.AppImage",
        "LD_LIBRARY_PATH": "/tmp/.mount_wLib/usr/lib",
        "LD_LIBRARY_PATH_ORIG": "/usr/lib",
        "WINEPREFIX": "/tmp/from-env",
        "STEAM_COMPAT_DATA_PATH": "/tmp/steam-compat",
    },
    clear=True,
)
def test_launch_auto_detect_native_script_uses_clean_host_environment(
    mock_get_setting, mock_popen, mock_access, mock_exists
):
    mock_exists.return_value = True
    mock_access.return_value = True
    mock_get_setting.return_value = "false"
    mock_popen.return_value = MagicMock()

    result = Launcher().launch("/opt/game/run.sh")

    assert result["success"] is True
    args, kwargs = mock_popen.call_args
    assert args[0] == ["/opt/game/run.sh"]
    env = kwargs["env"]
    assert "APPDIR" not in env
    assert "ARGV0" not in env
    assert env["LD_LIBRARY_PATH"] == "/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert "WINEPREFIX" not in env
    assert "STEAM_COMPAT_DATA_PATH" not in env


@patch("os.path.exists")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
def test_launch_command_substitution_applies_leading_environment_assignments(
    mock_get_setting, mock_popen, mock_access, mock_exists
):
    mock_exists.return_value = True
    mock_access.return_value = True
    mock_get_setting.return_value = "false"
    mock_popen.return_value = MagicMock()

    result = Launcher().launch(
        "/opt/game/run.sh",
        command_line_args="MESA_GLTHREAD=true gamemoderun %command% --fullscreen",
    )

    assert result["success"] is True
    args, kwargs = mock_popen.call_args
    assert args[0] == ["gamemoderun", "/opt/game/run.sh", "--fullscreen"]
    assert kwargs["env"]["MESA_GLTHREAD"] == "true"


@patch("os.path.exists")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
def test_launch_native_mode_rejects_windows_executable_without_fallback(
    mock_get_setting, mock_popen, mock_access, mock_exists
):
    """Linux Native should fail clearly for Windows targets instead of falling back."""
    mock_exists.return_value = True
    mock_access.return_value = True
    mock_get_setting.return_value = "false"

    launcher = Launcher()
    result = launcher.launch("/opt/game/game.exe", launch_mode="native")

    assert result["success"] is False
    assert "cannot run Windows" in str(result.get("error", ""))
    mock_popen.assert_not_called()
    assert mock_get_setting.call_count == 1


@patch("os.path.exists")
@patch("os.path.isdir")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
def test_launch_wine_proton_mode_forces_compat_runtime(
    mock_get_setting, mock_popen, mock_access, mock_isdir, mock_exists
):
    """Wine/Proton mode should bypass native detection and use compatibility runtime."""

    def exists_side_effect(path):
        return path == "/opt/game/run.sh"

    def get_setting_side_effect(key):
        return {
            "enable_logging": "false",
            "proton_path": "",
            "wine_prefix_path": "/tmp/wlib-prefix",
        }.get(key)

    mock_exists.side_effect = exists_side_effect
    mock_isdir.return_value = False
    mock_access.return_value = True
    mock_get_setting.side_effect = get_setting_side_effect
    mock_popen.return_value = MagicMock()

    launcher = Launcher()
    result = launcher.launch("/opt/game/run.sh", launch_mode="wine_proton")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["wine", "/opt/game/run.sh"]
    assert kwargs["cwd"] == "/opt/game"
    assert kwargs["env"]["WINEPREFIX"] == "/tmp/wlib-prefix"


@patch("os.path.exists")
@patch("os.path.isdir")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
@patch.dict(
    "os.environ",
    {
        "APPIMAGE": "/tmp/wLib.AppImage",
        "LD_LIBRARY_PATH": "/tmp/.mount_wLib/usr/lib",
        "LD_LIBRARY_PATH_ORIG": "/usr/lib",
    },
    clear=True,
)
def test_launch_wine_proton_mode_uses_host_environment(
    mock_get_setting, mock_popen, mock_access, mock_isdir, mock_exists
):
    mock_exists.side_effect = lambda path: path == "/opt/game/game.exe"
    mock_isdir.return_value = False
    mock_access.return_value = False

    def get_setting_side_effect(key):
        return {
            "enable_logging": "false",
            "proton_path": "/opt/GE-Proton/proton",
            "wine_prefix_path": "/tmp/wlib-prefix",
        }.get(key)

    mock_get_setting.side_effect = get_setting_side_effect
    mock_popen.return_value = MagicMock()

    result = Launcher().launch("/opt/game/game.exe", launch_mode="wine_proton")

    assert result["success"] is True
    args, kwargs = mock_popen.call_args
    assert args[0] == ["/opt/GE-Proton/proton", "run", "/opt/game/game.exe"]
    env = kwargs["env"]
    assert "APPIMAGE" not in env
    assert env["LD_LIBRARY_PATH"] == "/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert env["STEAM_COMPAT_DATA_PATH"] == "/tmp/wlib-prefix"
    assert env["STEAM_COMPAT_INSTALL_PATH"] == "/opt/game"
    assert env["UMU_ID"] == "wlib"


@patch("os.path.exists")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
def test_launch_native_mode_rejects_non_executable_target(
    mock_get_setting, mock_popen, mock_access, mock_exists
):
    mock_exists.return_value = True
    mock_access.return_value = False
    mock_get_setting.return_value = "false"

    launcher = Launcher()
    result = launcher.launch("/opt/game/Game.x86_64", launch_mode="native")

    assert result["success"] is False
    assert "host-native file" in str(result.get("error", ""))
    mock_popen.assert_not_called()


@patch("core.launcher.shutil.which")
@patch("os.path.isfile")
@patch("os.access")
@patch("core.launcher.get_setting")
def test_rpgmaker_linux_runner_status_detects_path_install(
    mock_get_setting, mock_access, mock_isfile, mock_which
):
    mock_get_setting.return_value = ""
    mock_which.return_value = "/usr/local/bin/rpgmaker-linux"
    mock_isfile.side_effect = lambda path: path == "/usr/local/bin/rpgmaker-linux"
    mock_access.return_value = True

    status = Launcher().get_rpgmaker_linux_runner_status()

    assert status["available"] is True
    assert status["path"] == "/usr/local/bin/rpgmaker-linux"
    assert status["source"] == "path"


@patch("os.path.exists")
@patch("os.path.isfile")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
@patch.dict(
    "os.environ",
    {
        "APPIMAGE": "/tmp/wLib.AppImage",
        "LD_LIBRARY_PATH": "/tmp/.mount_wLib/usr/lib",
        "WINEPREFIX": "/tmp/from-env",
        "WINEDLLOVERRIDES": "winhttp=n,b",
        "STEAM_COMPAT_DATA_PATH": "/tmp/steam-compat",
        "STEAM_COMPAT_CLIENT_INSTALL_PATH": "/tmp/steam-client",
    },
    clear=True,
)
def test_launch_rpgmaker_linux_runner_uses_game_directory_and_clean_env(
    mock_get_setting, mock_popen, mock_access, mock_isfile, mock_exists
):
    mock_exists.side_effect = lambda path: path == "/games/foo/Game.exe"
    mock_isfile.side_effect = lambda path: path == "/opt/rpgmaker-linux"
    mock_access.return_value = True

    def get_setting_side_effect(key):
        return {
            "enable_logging": "false",
            "rpgmaker_linux_runner_path": "/opt/rpgmaker-linux",
        }.get(key, "")

    mock_get_setting.side_effect = get_setting_side_effect
    mock_popen.return_value = MagicMock()

    result = Launcher().launch(
        "/games/foo/Game.exe",
        run_japanese_locale=True,
        launch_mode="rpgmaker_linux",
    )

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == [
        "/opt/rpgmaker-linux",
        "--gamepath",
        "/games/foo",
    ]
    assert kwargs["cwd"] == "/games/foo"
    env = kwargs["env"]
    assert env["LC_ALL"] == "ja_JP.UTF-8"
    assert "WINEPREFIX" not in env
    assert "WINEDLLOVERRIDES" not in env
    assert "STEAM_COMPAT_DATA_PATH" not in env
    assert "STEAM_COMPAT_CLIENT_INSTALL_PATH" not in env
    assert "APPIMAGE" not in env
    assert "LD_LIBRARY_PATH" not in env


@patch("os.path.exists")
@patch("os.path.isfile")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
def test_launch_rpgmaker_linux_runner_supports_substitution_and_runner_args(
    mock_get_setting, mock_popen, mock_access, mock_isfile, mock_exists
):
    mock_exists.side_effect = lambda path: path == "/games/foo/Game.exe"
    mock_isfile.side_effect = lambda path: path == "/opt/rpgmaker-linux"
    mock_access.return_value = True

    def get_setting_side_effect(key):
        return {
            "enable_logging": "false",
            "rpgmaker_linux_runner_path": "/opt/rpgmaker-linux",
        }.get(key, "")

    mock_get_setting.side_effect = get_setting_side_effect
    mock_popen.return_value = MagicMock()

    result = Launcher().launch(
        "/games/foo/Game.exe",
        command_line_args="gamemoderun %command% --mounttype cicpoffs --nwjsversion 0.40.0",
        launch_mode="rpgmaker_linux",
    )

    assert result["success"] is True
    args, kwargs = mock_popen.call_args
    assert args[0] == [
        "gamemoderun",
        "/opt/rpgmaker-linux",
        "--gamepath",
        "/games/foo",
        "--mounttype",
        "cicpoffs",
        "--nwjsversion",
        "0.40.0",
    ]


@patch("os.path.exists")
@patch("os.path.isfile")
@patch("subprocess.Popen")
@patch("core.launcher.shutil.which")
@patch("core.launcher.get_setting")
def test_launch_rpgmaker_linux_runner_fails_when_missing(
    mock_get_setting, mock_which, mock_popen, mock_isfile, mock_exists
):
    mock_exists.side_effect = lambda path: path == "/games/foo/Game.exe"
    mock_isfile.return_value = False
    mock_which.return_value = None

    def get_setting_side_effect(key):
        return {"enable_logging": "false", "rpgmaker_linux_runner_path": ""}.get(
            key, ""
        )

    mock_get_setting.side_effect = get_setting_side_effect

    with patch.object(Launcher, "_read_upstream_custom_runner_path", return_value=""):
        result = Launcher().launch(
            "/games/foo/Game.exe", launch_mode="rpgmaker_linux"
        )

    assert result["success"] is False
    assert "not installed or configured" in str(result.get("error", ""))
    mock_popen.assert_not_called()


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

    with patch("core.launcher.get_setting", return_value="false"):
        launcher = Launcher()
        result = launcher.launch("/home/user/games/index.html")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["xdg-open", "file:///home/user/games/index.html"]
    assert kwargs["cwd"] == "/home/user/games"


@patch("os.path.exists")
@patch("subprocess.Popen")
@patch("os.path.abspath")
def test_launch_html_game_htm_extension(mock_abspath, mock_popen, mock_exists):
    """Test launching an HTML game with .htm extension."""
    mock_exists.return_value = True
    mock_abspath.return_value = "/home/user/games/game.htm"
    mock_popen.return_value = MagicMock()

    with patch("core.launcher.get_setting", return_value="false"):
        launcher = Launcher()
        result = launcher.launch("/home/user/games/game.htm")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["xdg-open", "file:///home/user/games/game.htm"]
    assert kwargs["cwd"] == "/home/user/games"


@patch("os.path.exists")
@patch("subprocess.Popen")
@patch("os.path.abspath")
def test_launch_html_game_case_insensitive(mock_abspath, mock_popen, mock_exists):
    """Test launching an HTML game with uppercase extension."""
    mock_exists.return_value = True
    mock_abspath.return_value = "/home/user/games/INDEX.HTML"
    mock_popen.return_value = MagicMock()

    with patch("core.launcher.get_setting", return_value="false"):
        launcher = Launcher()
        result = launcher.launch("/home/user/games/INDEX.HTML")

    assert result["success"] is True
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["xdg-open", "file:///home/user/games/INDEX.HTML"]
    assert kwargs["cwd"] == "/home/user/games"


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

    def test_callback(delta: int, is_final: bool = True) -> None:
        nonlocal callback_called
        callback_called = True

    with patch("core.launcher.get_setting", return_value="false"):
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

    with patch("core.launcher.get_setting", return_value="false"):
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
