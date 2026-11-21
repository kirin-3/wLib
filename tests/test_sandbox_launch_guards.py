"""Sandbox start failures, offline Wine helpers and symlinked Wine runners."""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from core import game_sandbox
from core.database import CompanionTool
from core.launcher import Launcher, RunningGame, WineContext, _sandbox_start_failure

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="bwrap runs only on Linux hosts"
)


def _wait(proc: "subprocess.Popen[bytes]") -> None:
    try:
        _ = proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_start_failure_without_marker_reports_status(tmp_path):
    marker = str(tmp_path / game_sandbox.SANDBOX_STARTED_NAME)
    proc = subprocess.Popen(["sh", "-c", "exit 3"])
    try:
        assert _sandbox_start_failure(proc, marker) == 3
    finally:
        _wait(proc)


def test_game_exit_after_marker_is_not_a_start_failure(tmp_path):
    marker = str(tmp_path / game_sandbox.SANDBOX_STARTED_NAME)
    proc = subprocess.Popen(["sh", "-c", ': > "$1"; exit 3', "sh", marker])
    try:
        assert _sandbox_start_failure(proc, marker) is None
    finally:
        _wait(proc)


def test_running_game_with_marker_returns_at_once(tmp_path):
    marker = str(tmp_path / game_sandbox.SANDBOX_STARTED_NAME)
    proc = subprocess.Popen(["sh", "-c", ': > "$1"; sleep 5', "sh", marker])
    try:
        assert _sandbox_start_failure(proc, marker, timeout=4) is None
        assert proc.poll() is None
    finally:
        proc.kill()
        _wait(proc)


def test_supervisor_writes_the_marker_in_home_first():
    assert game_sandbox.SUPERVISOR.startswith(
        f': > "$HOME/{game_sandbox.SANDBOX_STARTED_NAME}" 2>/dev/null; "$@";'
    )


def _sandboxed_native_launch(monkeypatch, tmp_path, recheck_status):
    home = tmp_path / "sandbox-home"
    home.mkdir()
    stale = home / game_sandbox.SANDBOX_STARTED_NAME
    _ = stale.write_text("")
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "core.launcher.game_sandbox.prepare_sandbox_home", lambda _: str(home)
    )
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    calls: list[bool] = []

    def status(*_args: object, recheck: bool = False, **_kw: object):
        calls.append(recheck)
        if recheck:
            return recheck_status
        return {"status": "ready", "path": "/usr/bin/bwrap", "reason": ""}

    monkeypatch.setattr(game_sandbox, "get_sandbox_status", status)
    proc = MagicMock(pid=4321, returncode=1)
    proc.poll.return_value = 1
    launcher = Launcher()
    with (
        patch("os.path.exists", return_value=True),
        patch("os.path.isdir", return_value=False),
        patch("os.access", return_value=True),
        patch("core.launcher.get_setting", return_value=""),
        patch("subprocess.Popen", return_value=proc),
        patch("threading.Thread") as thread,
    ):
        result = launcher.launch(
            "/games/game.sh", game_id=12, sandbox_enabled=True, launch_mode="native"
        )
    assert not stale.exists(), "a stale marker must be removed before launch"
    assert calls[-1] is True
    assert 12 not in launcher._running
    # No playtime tracking for a launch that never started.
    thread.assert_not_called()
    return result


def test_sandbox_that_cannot_start_reports_the_probe_reason(monkeypatch, tmp_path):
    result = _sandboxed_native_launch(
        monkeypatch,
        tmp_path,
        {
            "status": "unusable",
            "path": "/usr/bin/bwrap",
            "reason": "No user namespaces",
        },
    )
    assert result == {
        "success": False,
        "code": "sandbox_unusable",
        "error": "No user namespaces",
    }


def test_sandbox_that_cannot_start_with_ready_probe_reports_status(
    monkeypatch, tmp_path
):
    result = _sandboxed_native_launch(
        monkeypatch,
        tmp_path,
        {"status": "ready", "path": "/usr/bin/bwrap", "reason": ""},
    )
    assert result["success"] is False
    assert "exited before the game started (status 1)" in str(result["error"])


def _tool() -> CompanionTool:
    return CompanionTool(
        id=2,
        name="Helper",
        kind="wine",
        path="/tools/tool.exe",
        arguments="",
        show_on_all_games=True,
        sort_order=0,
    )


def _offline_run(bwrap: str) -> RunningGame:
    context = WineContext(["/usr/bin/wine"], {"WINEPREFIX": "/pfx"})
    return RunningGame(
        MagicMock(),
        None,
        wine_context=context,
        game_exe="/games/game.exe",
        network_blocked=True,
        network_bwrap=bwrap,
    )


def test_wine_tool_of_offline_game_runs_without_network(monkeypatch):
    monkeypatch.setattr("core.host_platform.is_flatpak", lambda: False)
    monkeypatch.setattr("core.launcher.host_x11_sockets", lambda: ())
    launcher = Launcher()
    launcher._running[1] = _offline_run("/usr/bin/bwrap")
    with (
        patch("subprocess.Popen") as popen,
        patch("os.path.isfile", return_value=True),
        patch("threading.Thread"),
    ):
        popen.return_value.poll.return_value = None
        assert launcher.launch_companion_tool(1, _tool())["success"]
    argv = popen.call_args.args[0]
    assert argv[:3] == ["/usr/bin/bwrap", "--unshare-user", "--unshare-net"]
    assert argv[-3:] == ["--", "/usr/bin/wine", "/tools/tool.exe"]


def test_wine_tool_of_offline_game_is_refused_without_wrapper(monkeypatch):
    launcher = Launcher()
    launcher._running[1] = _offline_run("")
    with (
        patch("subprocess.Popen") as popen,
        patch("os.path.isfile", return_value=True),
    ):
        result = launcher.launch_companion_tool(1, _tool())
    assert result["success"] is False
    popen.assert_not_called()


def test_offline_wine_game_records_network_wrapper(monkeypatch, tmp_path):
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    monkeypatch.setattr(
        game_sandbox,
        "get_sandbox_status",
        lambda *a, **k: {"status": "ready", "path": "/usr/bin/bwrap", "reason": ""},
    )
    monkeypatch.setattr("core.launcher.host_x11_sockets", lambda: ())
    prefix = tmp_path / "pfx"
    prefix.mkdir()
    launcher = Launcher()
    with (
        patch("os.path.exists", return_value=True),
        patch("core.launcher.host_which", return_value="/usr/bin/wineserver"),
        patch(
            "core.launcher.get_setting",
            side_effect=lambda key: {
                "wine_prefix_path": str(prefix),
                "proton_path": "/usr/bin/wine",
            }.get(key, ""),
        ),
        patch("subprocess.Popen") as popen,
        patch("threading.Thread"),
    ):
        popen.return_value.poll.return_value = None
        result = launcher.launch(
            "/games/game.exe", game_id=7, block_network=True, launch_mode="wine_proton"
        )
    assert result["success"], result
    assert launcher._running[7].network_bwrap == "/usr/bin/bwrap"
    assert popen.call_args.args[0][:3] == [
        "/usr/bin/bwrap",
        "--unshare-user",
        "--unshare-net",
    ]


def test_offline_cheat_engine_injection_runs_without_network(monkeypatch, tmp_path):
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    monkeypatch.setattr(
        game_sandbox,
        "get_sandbox_status",
        lambda *a, **k: {"status": "ready", "path": "/usr/bin/bwrap", "reason": ""},
    )
    monkeypatch.setattr("core.launcher.host_x11_sockets", lambda: ())
    ce_dir = tmp_path / "ce"
    ce_dir.mkdir()
    ce_exe = ce_dir / "lunarengine-x86_64.exe"
    _ = ce_exe.write_bytes(b"MZ")
    prefix = tmp_path / "pfx"
    prefix.mkdir()
    game_proc = MagicMock(pid=111)
    game_proc.poll.return_value = None
    calls: list[list[str]] = []

    def fake_popen(cmd, **_kwargs):
        calls.append(list(cmd))
        return game_proc

    def fake_thread(*, target, daemon):
        # Run only Cheat Engine injection; the playtime tracker would block.
        if getattr(target, "__name__", "") == "inject_ce":
            target()
        return MagicMock()

    monkeypatch.setattr("threading.Thread", fake_thread)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    real_exists = os.path.exists
    with (
        patch(
            "os.path.exists",
            side_effect=lambda p: p == "/games/game.exe" or real_exists(p),
        ),
        patch("core.launcher.host_which", return_value="/usr/bin/wineserver"),
        patch("core.launcher.get_cheat_engine_dir", return_value=str(ce_dir)),
        patch(
            "core.launcher.get_setting",
            side_effect=lambda key: {
                "wine_prefix_path": str(prefix),
                "proton_path": "/usr/bin/wine",
            }.get(key, ""),
        ),
        patch("subprocess.Popen", side_effect=fake_popen),
    ):
        result = Launcher().launch(
            "/games/game.exe",
            game_id=7,
            block_network=True,
            auto_inject_ce=True,
            launch_mode="wine_proton",
        )
    assert result["success"], result
    ce_calls = [cmd for cmd in calls if str(ce_exe) in cmd]
    assert len(ce_calls) == 1
    assert ce_calls[0][:3] == ["/usr/bin/bwrap", "--unshare-user", "--unshare-net"]
    assert ce_calls[0][-3:] == ["--", "/usr/bin/wine", str(ce_exe)]


def test_sandboxed_wine_runs_symlinked_runner_by_real_path(monkeypatch, tmp_path):
    real_bin = tmp_path / "opt" / "custom-wine" / "bin"
    real_bin.mkdir(parents=True)
    real_wine = real_bin / "wine"
    _ = real_wine.write_text("#!/bin/sh\n")
    link_dir = tmp_path / "links"
    link_dir.mkdir()
    link = link_dir / "wine"
    link.symlink_to(real_wine)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WLIB_DATA_DIR", str(data))
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    monkeypatch.setattr(
        "core.launcher.game_sandbox.prepare_sandbox_home",
        lambda _: str(tmp_path / "sandbox-home"),
    )
    monkeypatch.setattr(
        game_sandbox,
        "get_sandbox_status",
        lambda *a, **k: {"status": "ready", "path": "/usr/bin/bwrap", "reason": ""},
    )
    games = tmp_path / "games"
    games.mkdir()
    game = games / "game.exe"
    _ = game.write_bytes(b"MZ")
    launcher = Launcher()
    with (
        patch("core.launcher.host_which", return_value=""),
        patch(
            "core.launcher.get_setting",
            side_effect=lambda key: {"proton_path": str(link)}.get(key, ""),
        ),
        patch("subprocess.Popen") as popen,
        patch("threading.Thread"),
    ):
        # Exit status 0: not a start failure, and no wait for the marker.
        popen.return_value.poll.return_value = 0
        result = launcher.launch(
            str(game), game_id=9, sandbox_enabled=True, launch_mode="wine_proton"
        )
    assert result["success"], result
    argv = popen.call_args.args[0]
    assert str(real_wine) in argv
    assert str(link) not in argv
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    assert ["--ro-bind", str(real_bin.parent), str(real_bin.parent)] in triples
