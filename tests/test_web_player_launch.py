# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from core import game_sandbox
from core.web_launch import resolve_web_player
from core.launcher import Launcher


@pytest.mark.parametrize(
    ("target", "mode", "per_game", "setting", "windows", "expected"),
    [
        ("/g/index.html", "auto", "", True, False, "builtin"),
        ("/g/index.htm", "native", "", None, False, "builtin"),
        ("/g/index.html", "auto", "", False, False, "browser"),
        ("/g/index.html", "auto", "builtin", False, False, "builtin"),
        ("/g/index.html", "auto", "browser", True, False, "browser"),
        ("/g/index.html", "auto", "bogus", False, False, "browser"),
        ("/g/GAME.SWF", "auto", "", False, False, "builtin"),
        ("/g/game.swf", "native", "browser", False, False, "builtin"),
        ("/g/index.html", "wine_proton", "", True, False, None),
        ("/g/index.html", "custom", "builtin", True, False, None),
        ("/g/index.html", "rpgmaker_linux", "", True, False, None),
        ("/g/game.swf", "wine_proton", "", True, False, None),
        ("/g/game.exe", "auto", "", True, False, None),
        ("", "auto", "", True, False, None),
        ("/g/index.html", "auto", "", True, True, "builtin"),
        ("/g/index.html", "wine_proton", "", False, True, "browser"),
        ("C:\\g\\game.swf", "auto", "browser", True, True, "builtin"),
    ],
)
def test_resolve_web_player(target, mode, per_game, setting, windows, expected):
    assert resolve_web_player(target, mode, per_game, setting, windows=windows) == expected


@pytest.fixture
def game_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    monkeypatch.setattr("core.launcher.is_flatpak", lambda: False)
    folder = tmp_path / "game"
    folder.mkdir()
    (folder / "index.html").write_text("<html></html>")
    (folder / "game.swf").write_bytes(b"FWS")
    return folder


def _launch(game_dir, name="index.html", **kwargs):
    settings = kwargs.pop("settings", {})
    popen = MagicMock()
    popen.return_value.pid = 4242
    popen.return_value.poll.return_value = None
    with (
        patch("core.launcher.get_setting", side_effect=lambda key: settings.get(key)),
        patch(
            "core.launcher.web_player_devtools_enabled",
            side_effect=lambda: settings.get("web_player_devtools") != "false",
        ),
        patch("core.launcher.game_sandbox.get_sandbox_status") as status,
        patch("subprocess.Popen", popen),
        patch("threading.Thread"),
        patch("core.database.update_setting") as update_setting,
    ):
        status.return_value = {"status": "ready", "tool": "bwrap", "path": "/usr/bin/bwrap", "version": "", "reason": ""}
        launcher = Launcher()
        result = launcher.launch(str(game_dir / name), game_id=kwargs.pop("game_id", 7), game_title="My Game", **kwargs)
    return result, popen, update_setting, launcher


@pytest.mark.skipif(sys.platform == "win32", reason="simulates a Linux host; POSIX paths only")
def test_builtin_player_is_a_tracked_process_with_first_use_note(game_dir):
    result, popen, update_setting, launcher = _launch(game_dir)
    assert result["success"] and result.get("tracks_playtime", True)
    assert result["notice"] == "html_player_first_use"
    update_setting.assert_called_once_with("html_player_notice_shown", "true")
    argv = popen.call_args.args[0]
    assert "--play-web" in argv
    assert argv[argv.index("--root") + 1] == str(game_dir)
    assert argv[argv.index("--entry") + 1] == "index.html"
    assert argv[argv.index("--title") + 1] == "My Game"
    assert argv[argv.index("--profile") + 1] == game_sandbox.web_player_folder(7)
    assert os.path.isdir(game_sandbox.web_player_folder(7))
    assert "--flash" not in argv and "--offline" not in argv
    assert argv[argv.index("--links") + 1] == "open"
    assert "QTWEBENGINE_DISABLE_SANDBOX" not in popen.call_args.kwargs["env"]
    assert 7 in launcher._running


def test_first_use_note_shows_once_and_not_for_explicit_choice(game_dir):
    result, *_ = _launch(game_dir, settings={"html_player_notice_shown": "true"})
    assert "notice" not in result
    result, *_ = _launch(game_dir, html_player="builtin")
    assert "notice" not in result


def test_swf_target_uses_flash_page_without_note(game_dir):
    result, popen, *_ = _launch(game_dir, name="game.swf")
    argv = popen.call_args.args[0]
    assert result["success"] and "--flash" in argv and "notice" not in result


def test_system_browser_choice_keeps_untracked_xdg_open(game_dir):
    result, popen, _, launcher = _launch(game_dir, html_player="browser", sandbox_enabled=True, block_network=True)
    assert result["success"] and result["tracks_playtime"] is False
    assert popen.call_args.args[0][0] == "xdg-open"
    assert launcher.get_running_games() == []


def test_setting_off_selects_system_browser(game_dir):
    result, popen, *_ = _launch(game_dir, settings={"html_builtin_player": "false"})
    assert popen.call_args.args[0][0] == "xdg-open" and result["tracks_playtime"] is False


def test_no_internet_and_sandbox_flags(game_dir, monkeypatch):
    monkeypatch.setattr("core.launcher.web_player_runtime_paths", lambda: ())
    result, popen, *_ = _launch(game_dir, sandbox_enabled=True, block_network=True)
    assert result["success"]
    argv = popen.call_args.args[0]
    assert argv[0] == "/usr/bin/bwrap" and "--unshare-net" in argv
    player = argv[argv.index("--play-web") - 2 :]
    assert "--offline" in player and player[player.index("--links") + 1] == "ignore"
    assert popen.call_args.kwargs["env"]["QTWEBENGINE_DISABLE_SANDBOX"] == "1"
    profile = game_sandbox.web_player_folder(7)
    assert ["--bind", profile, profile] in [argv[i : i + 3] for i in range(len(argv) - 2)]


def test_devtools_flag_follows_setting(game_dir):
    _, popen, *_ = _launch(game_dir)
    assert "--devtools" in popen.call_args.args[0]
    _, popen, *_ = _launch(game_dir, settings={"web_player_devtools": "false"})
    argv = popen.call_args.args[0]
    assert "--play-web" in argv and "--devtools" not in argv
    _, popen, *_ = _launch(game_dir, settings={"web_player_devtools": "true"})
    assert "--devtools" in popen.call_args.args[0]


def test_devtools_flag_inside_sandboxed_argv(game_dir, monkeypatch):
    monkeypatch.setattr("core.launcher.web_player_runtime_paths", lambda: ())
    result, popen, *_ = _launch(game_dir, sandbox_enabled=True)
    assert result["success"]
    argv = popen.call_args.args[0]
    assert argv[0] == "/usr/bin/bwrap"
    assert "--devtools" in argv[argv.index("--play-web") :]


@pytest.mark.skipif(sys.platform == "win32", reason="simulates a Linux host; POSIX paths only")
def test_non_html_targets_are_unaffected(game_dir):
    script = game_dir / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    result, popen, *_ = _launch(game_dir, name="run.sh")
    assert result["success"] and "--play-web" not in popen.call_args.args[0]


def test_bwrap_binds_profile_read_write_and_runtime_read_only():
    argv = game_sandbox.build_bwrap_argv(
        ["wlib", "--play-web"],
        game_dir="/games/g",
        home="/home/u",
        sandbox_home="/data/sandbox/1/home",
        cwd="/games/g",
        readonly_paths=("/opt/wlib",),
        writable_paths=("/data/web-player/1",),
    )
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    assert ["--ro-bind", "/opt/wlib", "/opt/wlib"] in triples
    assert ["--bind", "/data/web-player/1", "/data/web-player/1"] in triples


def test_bwrap_refuses_writable_home():
    with pytest.raises(ValueError):
        game_sandbox.build_bwrap_argv(
            ["x"], game_dir="/games/g", home="/home/u", sandbox_home="/s", cwd="/", writable_paths=("/home/u",)
        )


def test_sandbox_environment_keeps_player_qt_variables():
    env = {"QT_QPA_PLATFORM": "xcb", "SECRET_TOKEN": "t", "QTWEBENGINE_DISABLE_SANDBOX": "1"}
    host = {"QT_QPA_PLATFORM": "xcb", "SECRET_TOKEN": "t"}
    assert game_sandbox.sandbox_environment(env, host) == {"QTWEBENGINE_DISABLE_SANDBOX": "1"}
    kept = game_sandbox.sandbox_environment(env, host, game_sandbox.PLAYER_ENV_NAMES)
    assert kept == {"QT_QPA_PLATFORM": "xcb", "QTWEBENGINE_DISABLE_SANDBOX": "1"}


def test_sandboxie_box_opens_profile_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(game_sandbox, "_sandboxie_call", lambda exe, args: calls.append(args) or "")
    game_sandbox.configure_sandboxie_box(
        "C:\\Sandboxie\\Start.exe", 3, "C:\\games\\g", extra_write_paths=("C:\\data\\web-player\\3",)
    )
    assert ["append", "wLib_3", "OpenFilePath", "C:\\data\\web-player\\3\\"] in calls


def test_flatpak_player_argv_sandboxed():
    argv = game_sandbox.flatpak_player_argv(
        ["/app/bin/wlib", "--play-web", "--root", "/g"],
        game_dir="/g",
        profile="/p",
        network_blocked=True,
        environment={"QTWEBENGINE_DISABLE_SANDBOX": "1", "QT_QPA_PLATFORM": "wayland", "HOME": "/h"},
    )
    assert argv[:3] == ["flatpak-spawn", "--sandbox", "--watch-bus"]
    for flag in ("--sandbox-expose-path=/g", "--sandbox-expose-path=/p", "--sandbox-flag=share-display", "--sandbox-flag=share-sound", "--sandbox-flag=share-gpu", "--no-network", "--env=QTWEBENGINE_DISABLE_SANDBOX=1"):
        assert flag in argv
    assert not any(part.startswith("--env=HOME") for part in argv)
    assert argv[argv.index("--") + 1 :] == ["/app/bin/wlib", "--play-web", "--root", "/g"]
    assert "--no-network" not in game_sandbox.flatpak_player_argv(["x"], game_dir="/g", profile="/p", network_blocked=False)


def test_flatpak_runs_player_in_app_not_on_host(game_dir, monkeypatch):
    monkeypatch.setattr("core.launcher.is_flatpak", lambda: True)
    monkeypatch.setattr("core.host_platform.is_flatpak", lambda: True)
    result, popen, *_ = _launch(game_dir)
    argv = popen.call_args.args[0]
    assert result["success"] and argv[0] != "flatpak-spawn" and "--play-web" in argv


def test_flatpak_sandboxed_uses_sub_sandbox_and_reports_early_exit(game_dir, monkeypatch):
    monkeypatch.setattr("core.launcher.is_flatpak", lambda: True)
    monkeypatch.setattr("core.launcher.game_sandbox.get_sandbox_status", MagicMock(side_effect=AssertionError("bwrap must not be probed")))
    popen = MagicMock()
    popen.return_value.pid = 99
    popen.return_value.returncode = 1
    popen.return_value.wait.return_value = 1
    with (
        patch("core.launcher.get_setting", return_value=None),
        patch("core.launcher.web_player_devtools_enabled", return_value=True),
        patch("subprocess.Popen", popen),
        patch("threading.Thread"),
    ):
        result = Launcher().launch(str(game_dir / "index.html"), game_id=5, sandbox_enabled=True, block_network=True)
    argv = popen.call_args.args[0]
    assert argv[:2] == ["flatpak-spawn", "--sandbox"] and "--no-network" in argv
    assert "--devtools" in argv
    assert not result["success"] and "exited before its window opened" in result["error"]
    assert 5 not in Launcher()._running


def test_flatpak_player_argv_network_only_keeps_app_permissions():
    argv = game_sandbox.flatpak_player_argv(
        ["/app/bin/wlib", "--play-web"], game_dir="/g", profile="/p", network_blocked=True, sandbox=False
    )
    assert argv[:2] == ["flatpak-spawn", "--watch-bus"] and "--no-network" in argv
    assert not any(part.startswith(("--sandbox", "--sandbox-expose-path")) for part in argv)


def test_flatpak_no_internet_without_sandbox_still_blocks_network(game_dir, monkeypatch):
    monkeypatch.setattr("core.launcher.is_flatpak", lambda: True)
    result, popen, *_ = _launch(game_dir, block_network=True)
    argv = popen.call_args.args[0]
    assert argv[:2] == ["flatpak-spawn", "--watch-bus"] and "--no-network" in argv
    assert "--offline" in argv


def test_flatpak_sub_sandbox_keeps_session_bus_for_the_portal(game_dir, monkeypatch):
    from core.database import init_db

    init_db()  # the first web-player launch saves a notice flag in settings
    monkeypatch.setattr("core.launcher.is_flatpak", lambda: True)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    popen = MagicMock()
    popen.return_value.pid = 99

    def still_running(timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired("flatpak-spawn", timeout)
        return 0

    popen.return_value.wait.side_effect = still_running
    with (
        patch("core.launcher.get_setting", return_value=None),
        patch("subprocess.Popen", popen),
        patch("threading.Thread"),
    ):
        result = Launcher().launch(str(game_dir / "index.html"), game_id=5, sandbox_enabled=True)
    assert result["success"]
    assert popen.call_args.kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"


def test_flatpak_player_skips_host_performance_tools(game_dir, monkeypatch):
    monkeypatch.setattr("core.launcher.is_flatpak", lambda: True)
    monkeypatch.setattr("core.launcher.host_which", lambda name: f"/usr/bin/{name}")
    result, popen, *_ = _launch(game_dir, gamemode=True, mangohud=True)
    argv = popen.call_args.args[0]
    assert result["success"] and "/usr/bin/mangohud" not in argv and "/usr/bin/gamemoderun" not in argv


# ── Video check before the built-in player starts ──


@pytest.fixture
def h264_game(game_dir, monkeypatch):
    from test_web_video import mp4, sample_entry

    from core import web_video

    (game_dir / "videos").mkdir()
    (game_dir / "videos" / "intro.mp4").write_bytes(mp4(sample_entry(b"avc1"), sample_entry(b"mp4a", 0x40)))
    monkeypatch.setattr(web_video, "ffmpeg_info", lambda refresh=False: {"available": False, "reason": "missing"})
    return game_dir


@pytest.mark.skipif(sys.platform == "win32", reason="simulates a Linux host; POSIX paths only")
def test_h264_videos_stop_the_player(h264_game):
    result, popen, _, launcher = _launch(h264_game, check_web_media=True)
    assert result["success"] is False and result["code"] == "web_media_unsupported"
    assert result["media"] == {"count": 1, "ffmpeg": {"available": False, "reason": "missing"}}
    popen.assert_not_called()
    assert launcher.get_running_games() == []


@pytest.mark.skipif(sys.platform == "win32", reason="simulates a Linux host; POSIX paths only")
def test_vp9_and_av1_videos_start_the_player(game_dir):
    from test_web_video import mp4, sample_entry

    (game_dir / "a.mp4").write_bytes(mp4(sample_entry(b"vp09"), sample_entry(b"Opus")))
    (game_dir / "b.mp4").write_bytes(mp4(sample_entry(b"av01")))
    result, popen, *_ = _launch(game_dir, check_web_media=True)
    assert result["success"] and "--play-web" in popen.call_args.args[0]


@pytest.mark.skipif(sys.platform == "win32", reason="simulates a Linux host; POSIX paths only")
def test_browser_and_swf_launches_are_not_checked(h264_game):
    result, popen, *_ = _launch(h264_game, html_player="browser", check_web_media=True)
    assert result["success"] and popen.call_args.args[0][0] == "xdg-open"
    result, popen, *_ = _launch(h264_game, name="game.swf", check_web_media=True)
    assert result["success"] and "--flash" in popen.call_args.args[0]


@pytest.mark.skipif(sys.platform == "win32", reason="simulates a Linux host; POSIX paths only")
def test_play_anyway_and_launches_without_the_flag_start_the_player(h264_game):
    from core import web_video

    result, popen, *_ = _launch(h264_game)
    assert result["success"] and "--play-web" in popen.call_args.args[0]
    _ = web_video.set_play_anyway(7, str(h264_game))
    result, popen, *_ = _launch(h264_game, check_web_media=True)
    assert result["success"] and "--play-web" in popen.call_args.args[0]


def test_launch_tracked_passes_the_flag_only_when_asked(tmp_path):
    from core.database import init_db
    from core.game_launch import launch_tracked

    init_db()
    target = tmp_path / "index.html"
    target.write_text("<html></html>")
    launcher = MagicMock()
    launcher.launch.return_value = {"success": False, "error": "x"}
    _ = launch_tracked(launcher, None, {"exe_path": str(target)})
    assert "check_web_media" not in launcher.launch.call_args.kwargs
    _ = launch_tracked(launcher, None, {"exe_path": str(target), "check_web_media": True})
    assert launcher.launch.call_args.kwargs["check_web_media"] is True
