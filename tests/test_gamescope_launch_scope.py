"""Gamescope wrapping: which arguments count, and how long a Wine launch keeps it open."""

import os
import signal
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from core.launcher import GAMESCOPE_WINE_WAIT, Launcher

GAMESCOPE_PIXEL = {
    "window_mode": "fullscreen",
    "output_resolution": "",
    "game_resolution": "816x624",
    "scaler": "integer",
    "filter": "nearest",
    "fps_limit": "",
    "args": [],
}
GAMESCOPE_PREFIX = ["/usr/bin/gamescope", "-f", "-w", "816", "-h", "624", "-S", "integer", "-F", "nearest", "--"]


@pytest.fixture
def gamescope_host(monkeypatch, tmp_path):
    monkeypatch.setattr("core.launcher.atexit.register", lambda _callback: None)
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    monkeypatch.setattr("core.launcher.get_setting", lambda _key: None)
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("core.launcher.gamescope_mod.in_gamescope_session", lambda: False)
    monkeypatch.setattr("core.launcher.display_info.screen_size", lambda: None)


def _launch_command(mode: str, args: str) -> list[str]:
    with (patch("os.path.exists", return_value=True),
          patch("os.access", return_value=True),
          patch("core.launcher.host_which", side_effect=lambda name: f"/usr/bin/{name}"),
          patch("subprocess.Popen") as popen, patch("threading.Thread")):
        result = Launcher().launch("/opt/game/game.sh", args, launch_mode=mode, gamescope=GAMESCOPE_PIXEL)
    assert result["success"]
    return popen.call_args.args[0]


@pytest.mark.parametrize(("mode", "args"), [
    ("native", "%command% --profile gamescope"),
    ("native", "--profile gamescope"),
])
def test_game_argument_named_gamescope_keeps_the_wrapper(gamescope_host, mode, args):
    command = _launch_command(mode, args)
    assert command[: len(GAMESCOPE_PREFIX)] == GAMESCOPE_PREFIX
    assert command[len(GAMESCOPE_PREFIX):] == ["/opt/game/game.sh", "--profile", "gamescope"]


@pytest.mark.parametrize(("mode", "args"), [
    ("native", "/usr/bin/gamescope -f -- %command%"),
    ("custom", "/usr/bin/gamescope -f -- /opt/game/game.sh"),
])
def test_command_prefix_that_runs_gamescope_skips_the_wrapper(gamescope_host, mode, args):
    command = _launch_command(mode, args)
    assert command[0] == "/usr/bin/gamescope"
    assert command.count("/usr/bin/gamescope") == 1


def _wait_for(path, timeout=10.0):
    end = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > end:
            raise AssertionError(f"{path} did not appear")
        time.sleep(0.05)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_wine_wait_follows_the_game_group_not_the_prefix(tmp_path):
    """The waiter ends when the game's processes end, even while wineserver lives on."""
    ws_called = tmp_path / "ws-called"
    ws = tmp_path / "wineserver"
    # A wineserver -w that never returns, like one kept alive by another game.
    ws.write_text(f"#!/bin/sh\ntouch {ws_called}\nexec sleep 1000\n")
    ws.chmod(0o755)
    # A Wine background process in the group, named like Wine's explorer.
    explorer = tmp_path / "explorer.exe"
    explorer.symlink_to("/bin/sleep")
    game_pid_file = tmp_path / "game.pid"
    # A launcher stub: starts the real game and a background process, then exits.
    stub = f'"{explorer}" 1000 & sleep 1000 & echo $! > "{game_pid_file}.tmp"; mv "{game_pid_file}.tmp" "{game_pid_file}"; exit 7'
    waiter = subprocess.Popen(
        ["sh", "-c", GAMESCOPE_WINE_WAIT, "sh", str(ws), "sh", "-c", stub],
        start_new_session=True,
    )
    try:
        _wait_for(game_pid_file)
        with pytest.raises(subprocess.TimeoutExpired):
            _ = waiter.wait(timeout=1.5)
        os.kill(int(game_pid_file.read_text()), signal.SIGTERM)
        assert waiter.wait(timeout=10) == 7
        assert not ws_called.exists()
    finally:
        try:
            os.killpg(waiter.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _ = waiter.wait(timeout=10)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_wine_wait_ignores_the_gamescope_reaper(tmp_path):
    """gamescope 3.15+ starts the waiter through gamescopereaper, in the same group;
    the reaper waits for the waiter, so counting it would keep gamescope open forever."""
    ws = tmp_path / "wineserver"
    ws.write_text("#!/bin/sh\nexec sleep 1000\n")
    ws.chmod(0o755)
    reaper = tmp_path / "gamescopereaper"
    reaper.symlink_to("/bin/sleep")
    leader = subprocess.Popen(
        ["sh", "-c", '"$1" 1000 & shift; exec sh -c "$@"', "sh", str(reaper),
         GAMESCOPE_WINE_WAIT, "sh", str(ws), "sh", "-c", "exit 7"],
        start_new_session=True,
    )
    try:
        assert leader.wait(timeout=10) == 7
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _ = leader.wait(timeout=10)
