# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import sys
import time
from unittest.mock import MagicMock, mock_open, patch

import pytest

from core.launcher import Launcher, RunningGame, _ElevatedProcess, _split_windows_command_line


@pytest.fixture(autouse=True)
def _linux_launcher_by_default(monkeypatch):
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    # Tests that don't patch settings must not read the user's real database.
    monkeypatch.setattr("core.launcher.get_setting", lambda _key: None)


@pytest.mark.parametrize("command, expected, env_value", [
    ("onscripter %command%", ["onscripter", "/opt/game/game.exe"], None),
    ("xsystem35", ["xsystem35"], None),
    ("FOO=1 xsystem35", ["xsystem35"], "1"),
    ("FOO=1", None, None),
    ("", None, None),
])
def test_custom_command(monkeypatch, command, expected, env_value):
    monkeypatch.setenv("WINEPREFIX", "/bad/prefix")
    with (patch("os.path.exists", return_value=True),
          patch("core.launcher.get_setting", return_value="false"),
          patch("subprocess.Popen") as popen,
          patch("threading.Thread")):
        result = Launcher().launch("/opt/game/game.exe", command, launch_mode="custom")
    if expected is None:
        assert result["success"] is False
        assert "needs a command" in result["error"]
        popen.assert_not_called()
    else:
        assert result["success"] is True
        assert popen.call_args.args[0] == expected
        assert popen.call_args.kwargs["cwd"] == "/opt/game"
        env = popen.call_args.kwargs["env"]
        assert "WINEPREFIX" not in env
        assert env.get("FOO") == env_value


def test_custom_command_keeps_user_wine_variables(monkeypatch):
    # The host's WINEPREFIX is stripped, but one the user typed must survive.
    monkeypatch.setenv("WINEPREFIX", "/bad/prefix")
    with (patch("os.path.exists", return_value=True),
          patch("core.launcher.get_setting", return_value="false"),
          patch("subprocess.Popen") as popen,
          patch("threading.Thread")):
        result = Launcher().launch(
            "/opt/game/game.exe",
            "WINEPREFIX=/my/pfx STEAM_COMPAT_DATA_PATH=/my/compat proton run %command%",
            launch_mode="custom",
        )
    assert result["success"] is True
    assert popen.call_args.args[0] == ["proton", "run", "/opt/game/game.exe"]
    env = popen.call_args.kwargs["env"]
    assert env["WINEPREFIX"] == "/my/pfx"
    assert env["STEAM_COMPAT_DATA_PATH"] == "/my/compat"


def test_user_dll_overrides_come_after_defaults(monkeypatch):
    # Wine keeps the last entry per DLL, so the user's value must come last to win.
    monkeypatch.delenv("WINEDLLOVERRIDES", raising=False)
    with (patch("os.path.exists", side_effect=lambda path: path == "/opt/game/game.exe"),
          patch("os.makedirs"),
          patch("core.launcher.get_setting", return_value=""),
          patch("subprocess.Popen") as popen,
          patch("threading.Thread")):
        result = Launcher().launch(
            "/opt/game/game.exe",
            "WINEDLLOVERRIDES=winhttp=b %command%",
            launch_mode="wine_proton",
        )
    assert result["success"] is True
    assert (
        popen.call_args.kwargs["env"]["WINEDLLOVERRIDES"]
        == "mscoree=n,b;msvcrt=b,n;winhttp=n,b;winhttp=b"
    )


def test_custom_command_is_unsupported_on_windows(monkeypatch):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    with patch("os.path.exists", return_value=True), patch("subprocess.Popen") as popen:
        result = Launcher().launch(r"C:\Games\game.exe", "xsystem35", launch_mode="custom")
    assert result["code"] == "unsupported_platform"
    popen.assert_not_called()


@pytest.mark.parametrize("logging, callback", [(False, False), (True, False), (False, True)])
def test_running_registry_duplicate_and_exit(monkeypatch, tmp_path, logging, callback):
    target = tmp_path / "game.jar"
    target.touch()
    process = MagicMock(pid=321)
    process.poll.return_value = None
    process.wait.return_value = 0
    on_exit = MagicMock() if callback else None
    with (patch("subprocess.Popen", return_value=process) as popen,
          patch("core.launcher.get_setting", return_value="true" if logging else "false"),
          patch("builtins.open", mock_open()),
          patch("threading.Thread") as thread):
        launcher = Launcher()
        assert launcher.launch(str(target), game_id=1, on_exit_callback=on_exit)["success"]
        assert launcher.get_running_games() == [1]
        assert popen.call_args.kwargs["start_new_session"] is True
        assert launcher.launch(str(target), game_id=1)["code"] == "already_running"
        popen.assert_called_once()
        thread.call_args.kwargs["target"]()
        assert launcher.get_running_games() == []
        if on_exit:
            assert on_exit.call_args.args[1] is True


def test_registry_cleanup_preserves_new_process(monkeypatch):
    with (patch("os.path.exists", return_value=True),
          patch("core.launcher.get_setting", return_value="false"),
          patch("subprocess.Popen") as popen,
          patch("threading.Thread") as thread):
        popen.return_value.poll.return_value = 0
        launcher = Launcher()
        launcher.launch("/tmp/game.jar", game_id=1)
        replacement = RunningGame(MagicMock(pid=456), None)
        launcher._running[1] = replacement
        thread.call_args.kwargs["target"]()
        assert launcher._running[1] is replacement


@pytest.mark.parametrize("runner, layout, expected_server, prefix", [
    ("", None, "/usr/bin/wineserver", "/prefix"),
    ("wine", None, "/usr/bin/wineserver", "/prefix"),
    ("/lutris/bin/wine", None, "/lutris/bin/wineserver", "/prefix"),
    ("/opt/wine/bin/wine", None, "/usr/bin/wineserver", "/prefix"),
    ("/ge/proton", "files", "/ge/files/bin/wineserver", "/prefix/pfx"),
    ("/ge/proton", "dist", "/ge/dist/bin/wineserver", "/prefix/pfx"),
])
def test_stop_wine_prefix_and_process_group(monkeypatch, runner, layout, expected_server, prefix):
    import signal
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    killpg = MagicMock()
    monkeypatch.setattr("os.killpg", killpg, raising=False)
    with (patch("os.path.exists", return_value=True),
          patch("os.path.isdir", return_value=False),
          patch("os.path.isfile", side_effect=lambda path: path == expected_server),
          patch("core.launcher.shutil.which", return_value="/usr/bin/wineserver"),
          patch("core.launcher.get_setting", side_effect=lambda key: {"wine_prefix_path": "/prefix", "proton_path": runner}.get(key, "false")),
          patch("subprocess.Popen", return_value=MagicMock(pid=123)),
          patch("subprocess.run") as run,
          patch("threading.Thread") as thread,
          patch("core.launcher.time.sleep")):
        launcher = Launcher()
        assert launcher.launch("/tmp/game.exe", game_id=7)["success"]
        assert launcher.stop(7)["success"]
        assert run.call_args.args[0] == [expected_server, "-k"]
        assert run.call_args.kwargs["env"]["WINEPREFIX"] == prefix
        killpg.assert_called_with(123, signal.SIGTERM)
        thread.call_args.kwargs["target"]()
        killpg.assert_called_with(123, signal.SIGKILL)


def test_stop_skips_wineserver_kill_while_prefix_is_shared(monkeypatch):
    import signal
    launcher = Launcher()
    kill_info = ("/usr/bin/wineserver", {"WINEPREFIX": "/prefix"})
    launcher._running[1] = RunningGame(MagicMock(pid=123), kill_info)
    launcher._running[2] = RunningGame(MagicMock(pid=456), kill_info)
    killpg = MagicMock()
    monkeypatch.setattr("os.killpg", killpg, raising=False)
    with patch("subprocess.run") as run, patch("threading.Thread"):
        assert launcher.stop(1)["success"]
        run.assert_not_called()
        killpg.assert_called_with(123, signal.SIGTERM)
        del launcher._running[1]
        assert launcher.stop(2)["success"]
        assert run.call_args.args[0] == ["/usr/bin/wineserver", "-k"]


def test_wine_paths_expand_home_like_installers(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    with (patch("os.path.exists", side_effect=lambda path: path == "/opt/game/game.exe"),
          patch("os.path.isdir", return_value=False),
          patch("core.launcher.get_setting", return_value=""),
          patch("subprocess.Popen") as popen,
          patch("threading.Thread")):
        result = Launcher().launch(
            "/opt/game/game.exe",
            custom_prefix="~/pfx",
            proton_version="~/wine/bin/wine",
            launch_mode="wine_proton",
        )
    assert result["success"] is True
    assert popen.call_args.args[0][0] == "/home/u/wine/bin/wine"
    assert popen.call_args.kwargs["env"]["WINEPREFIX"] == "/home/u/pfx"


@pytest.mark.parametrize("exe_path, header", [
    ("/opt/game/game.sh", b"#!/bin/sh"),
    ("/opt/game/Game.x86_64", b"\x7fELF"),
])
def test_non_executable_native_files_ask_for_chmod_instead_of_wine(exe_path, header):
    with (patch("os.path.exists", return_value=True),
          patch("os.access", return_value=False),
          patch("builtins.open", mock_open(read_data=header)),
          patch("core.launcher.get_setting", return_value="false"),
          patch("subprocess.Popen") as popen):
        result = Launcher().launch(exe_path)
    assert result["success"] is False
    assert "chmod +x" in result["error"]
    popen.assert_not_called()


def test_logging_falls_back_to_temp_dir_for_read_only_game_folder():
    import posixpath
    import tempfile
    opened = []

    def fake_open(path, *_args, **_kwargs):
        if path.startswith("/opt/game/"):
            raise PermissionError(path)
        opened.append(path)
        return MagicMock()

    with (patch("os.path.exists", return_value=True),
          patch("core.launcher.get_setting", side_effect=lambda key: "true" if key == "enable_logging" else "false"),
          patch("builtins.open", side_effect=fake_open),
          patch("subprocess.Popen") as popen,
          patch("threading.Thread")):
        result = Launcher().launch("/opt/game/game.jar")
    assert result["success"] is True
    assert opened == [posixpath.join(tempfile.gettempdir(), "game_wlib.log")]
    popen.assert_called_once()


def test_stop_native_missing_and_windows_error(monkeypatch):
    launcher = Launcher()
    assert launcher.stop(1)["code"] == "not_running"
    launcher._running[1] = RunningGame(MagicMock(pid=123), None)
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        result = launcher.stop(1)
        assert result["success"] is False
        assert "administrator rights" in result["error"]
        run.assert_called_once()
        assert run.call_args.args[0] == ["taskkill", "/T", "/F", "/PID", "123"]
        assert launcher.get_running_games() == [1]
        run.return_value.returncode = 0
        assert launcher.stop(1)["success"]


def test_html_game_is_untracked():
    with patch("os.path.exists", return_value=True), patch("subprocess.Popen"):
        launcher = Launcher()
        assert launcher.launch("/tmp/game.html", game_id=1)["success"]
        assert launcher.get_running_games() == []


def test_stop_native_process_group_without_wineserver(monkeypatch):
    import signal
    launcher = Launcher()
    launcher._running[1] = RunningGame(MagicMock(pid=123), None)
    killpg = MagicMock()
    monkeypatch.setattr("os.killpg", killpg, raising=False)
    with patch("subprocess.run") as run, patch("threading.Thread"):
        assert launcher.stop(1)["success"]
    killpg.assert_called_once_with(123, signal.SIGTERM)
    run.assert_not_called()


def test_concurrent_duplicate_launch_starts_only_one_process():
    import threading
    original_thread = threading.Thread
    results = []
    launcher = Launcher()
    with (patch("os.path.exists", return_value=True),
          patch("core.launcher.get_setting", return_value="false"),
          patch("subprocess.Popen") as popen,
          patch("threading.Thread")):
        workers = [original_thread(target=lambda: results.append(launcher.launch("/tmp/game.jar", game_id=1))) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=2)
            assert not worker.is_alive()
        popen.assert_called_once()
    assert sum(bool(result["success"]) for result in results) == 1
    assert any(result.get("code") == "already_running" for result in results)


def test_slow_start_does_not_hold_registry_lock():
    import threading
    original_thread = threading.Thread
    started, release = threading.Event(), threading.Event()

    def slow_popen(*_args, **_kwargs):  # stands in for a pending UAC prompt
        started.set()
        _ = release.wait(timeout=2)
        return MagicMock(pid=5)

    launcher = Launcher()
    with (patch("os.path.exists", return_value=True),
          patch("core.launcher.get_setting", return_value="false"),
          patch("subprocess.Popen", side_effect=slow_popen),
          patch("threading.Thread")):
        worker = original_thread(target=launcher.launch, args=("/tmp/game.jar",), kwargs={"game_id": 1})
        worker.start()
        assert started.wait(timeout=2)
        assert launcher.get_running_games() == [1]
        assert launcher.stop(1)["code"] == "starting"
        release.set()
        worker.join(timeout=2)
    proc = launcher._running[1].proc
    assert proc is not None and proc.pid == 5


def test_failed_start_releases_reservation():
    with (patch("os.path.exists", return_value=True),
          patch("core.launcher.get_setting", return_value="false"),
          patch("subprocess.Popen", side_effect=OSError("boom"))):
        launcher = Launcher()
        assert launcher.launch("/tmp/game.jar", game_id=1)["success"] is False
        assert launcher.get_running_games() == []


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


@pytest.mark.parametrize("proton_version", ["", "/usr/bin/proton"])
def test_launch_logging_limits_wine_debug_output(monkeypatch, proton_version):
    monkeypatch.setenv("WINEDEBUG", "+all")
    with (
        patch("os.path.exists", return_value=True),
        patch("os.path.isdir", return_value=False),
        patch(
            "core.launcher.get_setting",
            side_effect=lambda key: "true" if key == "enable_logging" else "",
        ),
        patch("builtins.open"),
        patch("subprocess.Popen") as popen,
        patch("threading.Thread"),
    ):
        result = Launcher().launch(
            "/tmp/game.exe",
            custom_prefix="/tmp/prefix",
            proton_version=proton_version,
            launch_mode="wine_proton",
        )

    assert result["success"] is True
    assert popen.call_args.kwargs["env"]["WINEDEBUG"] == "-all,err+all,warn+all"


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


@patch("os.path.exists", return_value=True)
@patch("subprocess.Popen")
@patch("core.launcher.get_setting", return_value="false")
def test_windows_auto_launches_executable_directly(
    mock_get_setting, mock_popen, mock_exists, monkeypatch
):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    mock_popen.return_value = MagicMock()

    result = Launcher().launch(
        r"C:\Games\Visual Novel\game.exe",
        command_line_args='--profile "日本 語"',
    )

    assert result["success"] is True
    command = mock_popen.call_args.args[0]
    kwargs = mock_popen.call_args.kwargs
    assert command == [
        r"C:\Games\Visual Novel\game.exe",
        "--profile",
        "日本 語",
    ]
    assert kwargs["cwd"] == r"C:\Games\Visual Novel"
    assert "WINEPREFIX" not in kwargs["env"]


@patch("os.path.exists", return_value=True)
@patch("subprocess.Popen")
@patch("core.launcher.get_setting", return_value="false")
def test_windows_batch_uses_explicit_command_processor(
    mock_get_setting, mock_popen, mock_exists, monkeypatch
):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    mock_popen.return_value = MagicMock()

    result = Launcher().launch(r"C:\Games\start.cmd", command_line_args="--safe")

    assert result["success"] is True
    assert mock_popen.call_args.args[0] == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
        "call",
        r"C:\Games\start.cmd",
        "--safe",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--profile", "Player One"],
        ["--name", 'a "quoted" value'],
        ["--directory", "C:\\path with spaces\\"],
        ["--empty", ""],
        ["--unicode", "日本 語"],
    ],
)
def test_windows_command_line_parser_round_trips_standard_quoting(arguments):
    encoded = subprocess.list2cmdline(arguments)

    assert _split_windows_command_line(encoded) == arguments


@pytest.mark.skipif(sys.platform != "win32", reason="requires cmd.exe")
def test_windows_batch_path_with_spaces_launches_successfully(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    monkeypatch.setattr("core.launcher.get_setting", lambda _key: "false")
    game_directory = tmp_path / "Visual Novel"
    game_directory.mkdir()
    marker = game_directory / "launched.txt"
    batch_file = game_directory / "start game.cmd"
    batch_file.write_text(
        f'@echo launched>"{marker}"\n',
        encoding="utf-8",
    )

    result = Launcher().launch(str(batch_file))

    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert result["success"] is True
    assert marker.read_text(encoding="utf-8").strip() == "launched"


@patch("os.path.exists", return_value=True)
@patch("subprocess.Popen")
@patch("core.launcher.get_setting", return_value="false")
def test_windows_command_substitution_preserves_wrapper_order(
    mock_get_setting, mock_popen, mock_exists, monkeypatch
):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    mock_popen.return_value = MagicMock()

    result = Launcher().launch(
        r"C:\Games\game.exe", command_line_args="wrapper %command% --debug"
    )

    assert result["success"] is True
    assert mock_popen.call_args.args[0] == [
        "wrapper",
        r"C:\Games\game.exe",
        "--debug",
    ]


@patch("os.path.exists", return_value=True)
@patch("subprocess.Popen")
@patch("core.launcher.get_setting", return_value="false")
def test_windows_rejects_imported_linux_launch_mode(
    mock_get_setting, mock_popen, mock_exists, monkeypatch
):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)

    result = Launcher().launch(r"C:\Games\game.exe", launch_mode="wine_proton")

    assert result["success"] is False
    assert result["code"] == "unsupported_platform"
    assert "only available on Linux" in str(result["error"])
    mock_popen.assert_not_called()


@patch("os.path.exists", return_value=True)
@patch("core.launcher.open_windows_system_target", return_value=(True, ""))
@patch("core.launcher.get_setting", return_value="false")
def test_windows_html_uses_system_association(
    mock_get_setting, mock_open, mock_exists, monkeypatch
):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)

    result = Launcher().launch(r"C:\Games\index.html")

    assert result["success"] is True
    mock_open.assert_called_once_with(r"C:\Games\index.html")


@patch("os.path.exists", return_value=True)
@patch("subprocess.Popen")
@patch("core.launcher.get_setting", return_value="false")
def test_windows_jar_uses_java_with_appended_arguments(
    mock_get_setting, mock_popen, mock_exists, monkeypatch
):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    mock_popen.return_value = MagicMock()

    result = Launcher().launch(
        r"C:\Games\日本語\game.jar",
        command_line_args='--profile "Player One"',
    )

    assert result["success"] is True
    assert mock_popen.call_args.args[0] == [
        "java",
        "-jar",
        r"C:\Games\日本語\game.jar",
        "--profile",
        "Player One",
    ]
    assert mock_popen.call_args.kwargs["cwd"] == r"C:\Games\日本語"


@patch("os.path.exists", return_value=True)
@patch("subprocess.Popen")
@patch("core.launcher.get_setting", return_value="false")
@pytest.mark.parametrize(
    "elevated, logging", [(False, False), (True, False), (True, True)]
)
def test_windows_launch_reports_periodic_and_final_playtime(
    mock_get_setting, mock_popen, mock_exists, monkeypatch, elevated, logging
):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    mock_get_setting.return_value = "true" if logging else "false"
    process = MagicMock()
    process.poll.side_effect = [None, 0]
    process.wait.side_effect = [subprocess.TimeoutExpired("game", 60)]
    mock_popen.return_value = process
    if elevated:
        error = OSError("Elevation required")
        error.winerror = 740
        mock_popen.side_effect = error
    callbacks: list[tuple[int, bool]] = []

    def run_thread_immediately(*, target, daemon):
        assert daemon is True
        target()
        return MagicMock()

    monkeypatch.setattr("threading.Thread", run_thread_immediately)
    clock = MagicMock(side_effect=[100.0, 161.0, 165.0])
    monkeypatch.setattr("time.time", clock)

    def elevated_process(*_args):
        clock.assert_not_called()  # Start timing after the elevation prompt returns.
        return process

    with (
        patch("core.launcher._ElevatedProcess", side_effect=elevated_process) as elevate,
        patch("builtins.open") as open_log,
    ):
        result = Launcher().launch(
            r"C:\Games\game.exe",
            on_exit_callback=lambda delta, final=True: callbacks.append((delta, final)),
        )

    assert result["success"] is True
    assert callbacks == [(61, False), (4, True)]
    if elevated:
        elevate.assert_called_once_with([r"C:\Games\game.exe"], r"C:\Games")
    else:
        elevate.assert_not_called()
    if logging:
        open_log.return_value.close.assert_called_once()
        assert "cannot be captured" in open_log.return_value.write.call_args.args[0]


@pytest.mark.parametrize("windows, winerror", [(True, 5), (True, 2), (False, 740)])
def test_launch_does_not_elevate_other_errors(monkeypatch, windows, winerror):
    monkeypatch.setattr("core.launcher.is_windows", lambda: windows)
    error = OSError("Launch failed")
    error.winerror = winerror
    with (
        patch("os.path.exists", return_value=True),
        patch("os.access", return_value=True),
        patch("core.launcher.get_setting", return_value="false"),
        patch("subprocess.Popen", side_effect=error),
        patch("core.launcher._ElevatedProcess") as elevate,
    ):
        result = Launcher().launch(r"C:\Games\game.exe" if windows else "/game.sh")

    assert result == {"success": False, "error": "Launch failed"}
    elevate.assert_not_called()


def test_windows_elevation_cancellation_does_not_track_playtime(monkeypatch):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    elevation_required = OSError("Elevation required")
    elevation_required.winerror = 740
    cancelled = OSError("The operation was canceled by the user")
    cancelled.winerror = 1223
    callback = MagicMock()
    with (
        patch("os.path.exists", return_value=True),
        patch("core.launcher.get_setting", return_value="false"),
        patch("subprocess.Popen", side_effect=elevation_required),
        patch("core.launcher._ElevatedProcess", side_effect=cancelled),
        patch("threading.Thread") as thread,
    ):
        result = Launcher().launch(r"C:\Games\game.exe", on_exit_callback=callback)

    assert result == {"success": False, "error": str(cancelled)}
    callback.assert_not_called()
    thread.assert_not_called()


@pytest.mark.parametrize(
    "shell_result, handle", [(True, 0x12345678), (False, 0), (True, 0)]
)
def test_elevated_process_preserves_arguments_and_owns_handle(
    monkeypatch, shell_result, handle
):
    import ctypes

    command = [r"C:\Games\日本 語\game.exe", "", "Player One", 'a "quote"', "C:\\dir\\"]
    directory = r"C:\Games\日本 語"
    winapi = MagicMock()
    winapi.WaitForSingleObject.side_effect = [0x102, 0x102, 0]
    winapi.GetExitCodeProcess.return_value = 17
    monkeypatch.setitem(sys.modules, "_winapi", winapi)

    def shell_execute(pointer):
        info = pointer._obj
        assert info.cbSize == ctypes.sizeof(info)
        assert info.fMask == 0x40 | 0x100 | 0x400
        assert info.lpVerb == "runas"
        assert info.lpFile == command[0]
        assert _split_windows_command_line(info.lpParameters) == command[1:]
        assert info.lpDirectory == directory
        assert info.nShow == 1
        info.hProcess = handle
        return shell_result

    shell32 = MagicMock()
    shell32.GetProcessId.return_value = 1234
    shell32.ShellExecuteExW.side_effect = shell_execute
    with (
        patch("ctypes.WinDLL", return_value=shell32, create=True) as load_dll,
        patch("ctypes.WinError", return_value=OSError("UAC canceled"), create=True),
    ):
        if not shell_result or not handle:
            with pytest.raises(
                OSError, match="UAC canceled" if not shell_result else "handle"
            ):
                _ElevatedProcess(command, directory)
            winapi.CloseHandle.assert_not_called()
            return
        process = _ElevatedProcess(command, directory)

    assert load_dll.call_count == 2
    assert process.pid == 1234
    assert process.poll() is None
    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=60)
    assert process.wait() == 17
    assert process.poll() == 17
    assert [call.args for call in winapi.WaitForSingleObject.call_args_list] == [
        (handle, 0),
        (handle, 60000),
        (handle, 0xFFFFFFFF),
    ]
    winapi.CloseHandle.assert_not_called()
    del process
    winapi.CloseHandle.assert_called_once_with(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows process handles")
def test_elevated_process_native_handle_without_uac(tmp_path):
    import _winapi
    import ctypes

    native_execute = ctypes.WinDLL("shell32", use_last_error=True).ShellExecuteExW

    def execute_without_uac(pointer):
        info = pointer._obj
        assert info.lpVerb == "runas"
        info.lpVerb = "open"  # Exercise the real API without prompting for elevation.
        info.nShow = 0
        native_execute.argtypes = [ctypes.POINTER(type(info))]
        native_execute.restype = ctypes.c_int
        return native_execute(pointer)

    shell32 = MagicMock()
    shell32.ShellExecuteExW.side_effect = execute_without_uac
    with patch("ctypes.WinDLL", return_value=shell32):
        process = _ElevatedProcess(
            [sys.executable, "-c", "raise SystemExit(23)"], str(tmp_path)
        )

    assert process.wait(timeout=5) == 23
    assert process.poll() == 23
    handle = process._handle
    del process
    with pytest.raises(OSError):
        _winapi.GetExitCodeProcess(handle)


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
@patch("core.launcher.sys.executable", "/opt/wlib/wlib-bin")
@patch.dict(
    "os.environ",
    {
        "LD_LIBRARY_PATH": "/opt/wlib/_internal:/vendor/lib",
        "WINEPREFIX": "/tmp/from-env",
        "PATH": "/usr/bin:/bin",
    },
    clear=True,
)
def test_launch_native_mode_strips_pyinstaller_runtime_library_path(
    mock_get_setting, mock_popen, mock_access, mock_exists
):
    mock_exists.return_value = True
    mock_access.return_value = True
    mock_get_setting.return_value = "false"
    mock_popen.return_value = MagicMock()

    result = Launcher().launch("/opt/game/Game.x86_64", launch_mode="native")

    assert result["success"] is True
    args, kwargs = mock_popen.call_args
    assert args[0] == ["/opt/game/Game.x86_64"]
    env = kwargs["env"]
    assert env["LD_LIBRARY_PATH"] == "/vendor/lib"
    assert "WINEPREFIX" not in env


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
@patch("os.path.isdir")
@patch("os.access")
@patch("subprocess.Popen")
@patch("core.launcher.get_setting")
@patch("core.launcher.sys.executable", "/opt/wlib/wlib-bin")
@patch.dict(
    "os.environ",
    {
        "LD_LIBRARY_PATH": "/opt/wlib/_internal",
        "PATH": "/usr/bin:/bin",
    },
    clear=True,
)
def test_launch_wine_proton_mode_strips_pyinstaller_runtime_library_path(
    mock_get_setting, mock_popen, mock_access, mock_isdir, mock_exists
):
    mock_exists.side_effect = lambda path: path == "/opt/game/game.exe"
    mock_isdir.return_value = False
    mock_access.return_value = False

    def get_setting_side_effect(key):
        return {
            "enable_logging": "false",
            "proton_path": "",
            "wine_prefix_path": "/tmp/wlib-prefix",
        }.get(key)

    mock_get_setting.side_effect = get_setting_side_effect
    mock_popen.return_value = MagicMock()

    result = Launcher().launch("/opt/game/game.exe", launch_mode="wine_proton")

    assert result["success"] is True
    args, kwargs = mock_popen.call_args
    assert args[0] == ["wine", "/opt/game/game.exe"]
    env = kwargs["env"]
    assert "LD_LIBRARY_PATH" not in env
    assert env["WINEPREFIX"] == "/tmp/wlib-prefix"


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
@patch("core.launcher.sys.executable", "/opt/wlib/wlib-bin")
@patch.dict(
    "os.environ",
    {
        "LD_LIBRARY_PATH": "/opt/wlib/_internal:/usr/local/lib",
        "WINEPREFIX": "/tmp/from-env",
        "PATH": "/usr/bin:/bin",
    },
    clear=True,
)
def test_launch_rpgmaker_linux_runner_strips_pyinstaller_runtime_library_path(
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

    result = Launcher().launch("/games/foo/Game.exe", launch_mode="rpgmaker_linux")

    assert result["success"] is True
    mock_popen.assert_called_once()
    _args, kwargs = mock_popen.call_args
    env = kwargs["env"]
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert "WINEPREFIX" not in env


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
