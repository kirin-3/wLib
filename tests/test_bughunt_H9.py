# SPDX-License-Identifier: GPL-3.0-or-later
"""Stopping one Wine game must not end another game on the same prefix."""

import signal
from unittest.mock import MagicMock, patch

import pytest

from core.launcher import Launcher, RunningGame

KILL_INFO = ("/usr/bin/wineserver", {"WINEPREFIX": "/prefix"})


@pytest.fixture
def killpg(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("os.killpg", mock, raising=False)
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    # No /proc view of the fake group: fall back to signalling the group.
    monkeypatch.setattr("core.launcher._wine_group_pids", lambda _pgid: None)
    return mock


def test_stop_spares_prefix_of_a_steam_shortcut_game(killpg):
    launcher = Launcher()
    launcher._running[1] = RunningGame(
        MagicMock(pid=123), KILL_INFO, prefix="/prefix", host_native=False
    )
    assert (
        launcher.reserve_external(
            2, RunningGame(None, None, prefix="/prefix", title="Steam game")
        )
        is None
    )
    with patch("subprocess.run") as run, patch("threading.Thread"):
        assert launcher.stop(1)["success"]
    run.assert_not_called()
    killpg.assert_called_with(123, signal.SIGTERM)


def test_stop_kills_prefix_after_the_steam_game_ended(killpg):
    launcher = Launcher()
    launcher._running[1] = RunningGame(
        MagicMock(pid=123), KILL_INFO, prefix="/prefix", host_native=False
    )
    assert (
        launcher.reserve_external(
            2, RunningGame(None, None, prefix="/prefix", title="Steam game")
        )
        is None
    )
    # The runner died without reporting its end.
    launcher.external_alive = lambda _game_id: False
    with patch("subprocess.run") as run, patch("threading.Thread"):
        assert launcher.stop(1)["success"]
    assert run.call_args.args[0] == ["/usr/bin/wineserver", "-k"]


def test_uncertain_owner_never_ends_the_whole_prefix(killpg):
    # The Steam runner's launcher sees only its own game.
    launcher = Launcher()
    launcher.prefix_owner_uncertain = True
    launcher._running[1] = RunningGame(
        MagicMock(pid=123), KILL_INFO, prefix="/prefix", host_native=False
    )
    with patch("subprocess.run") as run, patch("threading.Thread"):
        assert launcher.stop(1)["success"]
    run.assert_not_called()
    killpg.assert_called_with(123, signal.SIGTERM)


def test_launch_on_prefix_is_refused_while_wineserver_k_runs(killpg):
    launcher = Launcher()
    launcher._running[1] = RunningGame(
        MagicMock(pid=123), KILL_INFO, prefix="/prefix", host_native=False
    )
    answers: list[str | None] = []

    def wineserver_kill(*_args: object, **_kwargs: object) -> MagicMock:
        answers.append(
            launcher.reserve_external(
                2, RunningGame(None, None, prefix="/prefix", title="Steam game")
            )
        )
        return MagicMock(returncode=0)

    with (
        patch("subprocess.run", side_effect=wineserver_kill),
        patch("threading.Thread"),
    ):
        assert launcher.stop(1)["success"]
    assert answers and answers[0] is not None and "closing" in answers[0]
    # Once the kill returned, the prefix is free again.
    assert not launcher._killing_prefixes
    assert (
        launcher.reserve_external(
            2, RunningGame(None, None, prefix="/prefix", title="Steam game")
        )
        is None
    )


def test_steam_runner_marks_prefix_owner_uncertain(monkeypatch, tmp_path):
    from core import database, game_launch, launcher, steam_runner

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path))
    database.init_db()
    monkeypatch.setattr(database, "init_db", lambda: None)
    monkeypatch.setattr(database, "get_game", lambda _: {"id": 8, "exe_path": "/game"})
    monkeypatch.setattr(steam_runner, "_window_request", lambda *_: None)
    monkeypatch.setattr(steam_runner, "report_error", lambda *_: None)
    monkeypatch.setattr(steam_runner, "create_record", lambda _: True)
    monkeypatch.setattr(steam_runner, "remove_record", lambda _: None)
    seen: list[bool] = []

    def launch(runner_launcher, _game_id, _request, callback):
        seen.append(runner_launcher.prefix_owner_uncertain)
        callback({"isFinal": True})
        return {"success": True}

    monkeypatch.setattr(game_launch, "launch_tracked", launch)
    monkeypatch.setattr(launcher.Launcher, "shutdown", lambda self: None)
    assert steam_runner.run(8) == 0
    assert seen == [True]
