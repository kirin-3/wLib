import subprocess
from unittest.mock import MagicMock, patch

import pytest

from core.launcher import Launcher, _batch_command, _windows_command_line

COMSPEC = r"C:\Windows\System32\cmd.exe"


def _cmd_exe_command(command_line: str) -> str:
    """The text cmd.exe runs for `/d /s /c`: /s removes the first and last quote."""
    prefix = COMSPEC + " /d /s /c "
    assert command_line.startswith(prefix)
    text = command_line[len(prefix) :]
    assert text.startswith('"') and text.endswith('"')
    return text[1:-1]


def _unquoted_specials(text: str) -> str:
    found: list[str] = []
    quoted = False
    for character in text:
        if character == '"':
            quoted = not quoted
        elif not quoted and character in "&|<>^":
            found.append(character)
    return "".join(found)


def _launch_batch(monkeypatch, path: str, arguments: str = "") -> object:
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    monkeypatch.setenv("COMSPEC", COMSPEC)
    with (
        patch("os.path.exists", return_value=True),
        patch("core.launcher.get_setting", return_value="false"),
        patch("subprocess.Popen", return_value=MagicMock()) as popen,
    ):
        result = Launcher().launch(path, command_line_args=arguments)
    assert result["success"] is True
    return popen.call_args.args[0]


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Games\A&B\Start.cmd",
        r"C:\Games\A|B<C>\Start.bat",
        r"C:\My Games\A&B\go.cmd",
    ],
)
def test_batch_path_special_characters_stay_quoted(monkeypatch, path):
    command_line = _launch_batch(monkeypatch, path)

    assert isinstance(command_line, str)
    command = _cmd_exe_command(command_line)
    assert command == f'call "{path}"'
    assert _unquoted_specials(command) == ""


def test_batch_arguments_and_wrappers_keep_their_places(monkeypatch):
    command_line = _launch_batch(
        monkeypatch,
        r"C:\Games\A&B\Start.cmd",
        'wrapper.exe "--x y" %command% --safe "Player One" a&b',
    )

    assert isinstance(command_line, str)
    assert command_line.startswith('wrapper.exe "--x y" ' + COMSPEC)
    command = _cmd_exe_command(command_line[len('wrapper.exe "--x y" ') :])
    assert command == r'call "C:\Games\A&B\Start.cmd" --safe "Player One" "a&b"'
    assert _unquoted_specials(command) == ""


def test_batch_command_doubles_inner_quotes():
    assert _batch_command(r"C:\x.cmd", ['say "hi"', ""]) == (
        '"call "C:\\x.cmd" "say ""hi""" """'
    )


def test_windows_command_line_keeps_plain_arguments_standard():
    plain = [r"C:\Program Files\game.exe", "--name", 'a "b"', ""]

    assert _windows_command_line(plain) == subprocess.list2cmdline(plain)
    assert (
        _windows_command_line(["cmd.exe", "/c", _batch_command(r"C:\A&B\s.cmd", [])])
        == r'cmd.exe /c "call "C:\A&B\s.cmd""'
    )


def _elevation_required() -> OSError:
    error = OSError("Elevation required")
    error.winerror = 740
    return error


def test_elevated_launch_refuses_environment_overrides(monkeypatch):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    launcher = Launcher()
    with (
        patch("os.path.exists", return_value=True),
        patch("core.launcher.get_setting", return_value="false"),
        patch("subprocess.Popen", side_effect=_elevation_required()),
        patch("core.launcher._ElevatedProcess") as elevate,
    ):
        result = launcher.launch(
            r"C:\Games\game.exe",
            command_line_args="GAME_CONFIG=alternate %command%",
            game_id=7,
        )

    assert result["success"] is False
    assert "GAME_CONFIG" in str(result["error"])
    assert "administrator" in str(result["error"])
    elevate.assert_not_called()
    assert launcher.get_running_games() == []


def test_elevated_launch_without_overrides_still_elevates(monkeypatch):
    monkeypatch.setattr("core.launcher.is_windows", lambda: True)
    process = MagicMock()
    process.wait.return_value = 0
    with (
        patch("os.path.exists", return_value=True),
        patch("core.launcher.get_setting", return_value="false"),
        patch("subprocess.Popen", side_effect=_elevation_required()),
        patch("core.launcher._ElevatedProcess", return_value=process) as elevate,
        patch("threading.Thread"),
    ):
        result = Launcher().launch(
            r"C:\Games\game.exe", command_line_args="%command% --safe"
        )

    assert result["success"] is True
    elevate.assert_called_once_with([r"C:\Games\game.exe", "--safe"], r"C:\Games")
