import sys

import pytest

from core.api import Api
from core.database import init_db


def test_umu_api_and_unsupported_host(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    init_db()
    api = Api()
    monkeypatch.setattr("core.api.is_linux", lambda: True)
    monkeypatch.setattr("core.umu.host_which", lambda name: None)
    assert api.get_umu_status()["source"] == ""
    assert api.get_umu_runtime_status()["ready"] is False
    assert api.prepare_umu_runtime()["success"] is False
    assert api.cancel_umu_runtime_prepare()["success"] is False
    monkeypatch.setattr("core.api.is_linux", lambda: False)
    for method in (
        api.get_umu_status,
        api.download_umu,
        api.get_umu_download_status,
        api.prepare_umu_runtime,
        api.get_umu_runtime_status,
        api.cancel_umu_runtime_prepare,
    ):
        assert method()["success"] is False


def test_proton_installers_use_umu(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    import core.api as api_module

    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(api_module, "is_linux", lambda: True)
    monkeypatch.setattr("core.umu.locate_umu", lambda: ("/host/umu-run", "system"))
    monkeypatch.setattr("core.umu.runtime_status", lambda _: {"ready": True})
    monkeypatch.setattr("core.umu._wrapper_path", lambda: "/host/wrapper")
    monkeypatch.setattr(api_module, "host_which", lambda _: None)

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(api_module.threading, "Thread", InlineThread)
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs["env"]))
        return MagicMock(returncode=0)

    monkeypatch.setattr("subprocess.run", run)
    init_db()
    api = Api()
    runner = tmp_path / "GE-Proton" / "proton"
    (runner.parent / "protonfixes").mkdir(parents=True)
    runner.touch()
    prefix = tmp_path / "prefix"
    # Missing bundled Winetricks is rejected without requiring host Winetricks.
    assert not api.install_rpgmaker_dependencies(str(prefix), str(runner))["success"]
    (runner.parent / "protonfixes" / "winetricks").touch()
    assert api.install_rpgmaker_dependencies(str(prefix), str(runner))["success"]
    assert commands[0][0] == [
        "python3",
        "/host/wrapper",
        "winetricks",
        "-q",
        "corefonts",
    ]
    assert commands[0][1]["WINEPREFIX"] == str(prefix)
    assert "WINE" not in commands[0][1] and "WINESERVER" not in commands[0][1]
    commands.clear()
    rtp_dir = tmp_path / "rtps"
    rtp_dir.mkdir()
    (rtp_dir / "setup.exe").write_bytes(b"MZ")
    monkeypatch.setattr(api_module, "get_rtp_dir", lambda: str(rtp_dir))
    monkeypatch.setattr(
        api,
        "_get_rtp_packages",
        lambda: [
            {"name": "test", "filename": "setup.exe", "url": "https://example.test"}
        ],
    )
    monkeypatch.setattr(api, "_verify_rtp_packages", lambda _: {"test": True})
    assert api.install_rpgmaker_rtp(str(prefix), str(runner))["success"]
    assert commands[0][0] == [
        "python3",
        "/host/wrapper",
        str(rtp_dir / "setup.exe"),
        "/S",
        "/v/qn",
    ]
    assert commands[0][1]["WINEPREFIX"] == str(prefix)


def test_runner_codenames_are_available_and_valid(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("core.api.is_linux", lambda: True)
    init_db()
    api = Api()
    runners = api.get_available_runners()["runners"]
    assert {"name": "GE-Proton (latest)", "path": "GE-Proton"} in runners
    assert {"name": "UMU-Proton", "path": "UMU-Proton"} in runners
    assert api.check_path("GE-Proton", "runner")["ok"]
    assert api.check_path("UMU-Proton", "runner")["ok"]


@pytest.mark.skipif(sys.platform == "win32", reason="Wine prefix tools and Wine launches run only on Linux hosts")
def test_prefix_tools_wine_commands_and_locks(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from core.database import add_game

    monkeypatch.setattr("core.api.is_linux", lambda: True)
    monkeypatch.setattr("core.api.host_which", lambda name: f"/usr/bin/{name}")
    process = MagicMock()
    process.poll.return_value = None
    popen = MagicMock(return_value=process)
    monkeypatch.setattr("core.api.subprocess.Popen", popen)
    api = Api()
    prefix = str(tmp_path / "prefix")
    assert api.open_wine_prefix_tool("winecfg", prefix, "/usr/bin/wine")["success"]
    assert popen.call_args.args[0] == ["/usr/bin/wine", "winecfg"]
    assert popen.call_args.kwargs["env"]["WINEPREFIX"] == prefix
    assert not api.open_wine_prefix_tool("winecfg", prefix, "/usr/bin/wine")["success"]
    assert not api.install_rpgmaker_dependencies(prefix, "/usr/bin/wine")["success"]
    assert not api.install_rpgmaker_rtp(prefix, "/usr/bin/wine")["success"]
    game = tmp_path / "game.exe"
    game.write_bytes(b"MZ")
    game_id = add_game("Example", str(game))
    monkeypatch.setattr("core.umu.resolve_runner", lambda _: "/usr/bin/wine")
    assert api.launch_game(game_id, str(game), custom_prefix=prefix, proton_version="/usr/bin/wine")["code"] == "prefix_in_use"
    process.poll.return_value = 0
    assert api.open_wine_prefix_tool("winetricks", prefix, "/usr/bin/wine")["success"]
    assert popen.call_args.args[0] == ["/usr/bin/winetricks", "--gui"]
    monkeypatch.setattr("core.api.is_linux", lambda: False)
    assert not api.open_wine_prefix_tool("winecfg", prefix, "")["success"]


def test_prefix_tools_proton_environment_and_refusals(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr("core.api.is_linux", lambda: True)
    monkeypatch.setattr("core.umu.locate_umu", lambda: ("/host/umu-run", "system"))
    monkeypatch.setattr("core.umu.runtime_status", lambda _: {"ready": True})
    monkeypatch.setattr("core.umu._wrapper_path", lambda: "/host/wrapper")
    monkeypatch.setattr("core.api.host_which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
    runner = tmp_path / "GE-Proton" / "proton"
    (runner.parent / "protonfixes").mkdir(parents=True)
    runner.touch()
    (runner.parent / "protonfixes" / "winetricks").touch()
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "drive_c").mkdir()
    popen = MagicMock(return_value=MagicMock(poll=lambda: None))
    monkeypatch.setattr("core.api.subprocess.Popen", popen)
    api = Api()
    assert api.open_wine_prefix_tool("winetricks", str(prefix), str(runner))["success"]
    assert popen.call_args.args[0] == ["python3", "/host/wrapper", "winetricks", "--gui"]
    env = popen.call_args.kwargs["env"]
    assert env["WINEPREFIX"] == str(prefix / "proton_compat")
    assert env["GAMEID"] == "umu-default"
    assert "UMU_CONTAINER_NSENTER" not in env
    assert not api.open_wine_prefix_tool("bad", str(prefix), str(runner))["success"]
    api._prefix_tool = None
    assert api.open_wine_prefix_tool("winecfg", str(prefix), str(runner))["success"]
    assert popen.call_args.args[0] == ["python3", "/host/wrapper", "winecfg"]
    api._prefix_tool = None
    (runner.parent / "protonfixes" / "winetricks").unlink()
    assert "GE-Proton" in api.open_wine_prefix_tool("winetricks", str(prefix), str(runner))["error"]
    monkeypatch.setattr("core.api.host_which", lambda _: None)
    assert "zenity or kdialog" in api.open_wine_prefix_tool("winetricks", str(prefix), str(runner))["error"]
    monkeypatch.setattr(api.launcher, "game_using_prefix", lambda _: "Running game")
    assert "Running game" in api.open_wine_prefix_tool("winecfg", str(prefix), str(runner))["error"]


def test_prefix_tool_refusals(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr("core.api.is_linux", lambda: True)
    api = Api()
    prefix = str(tmp_path / "prefix")
    monkeypatch.setattr("core.api.host_which", lambda _: None)
    assert "Wine is not installed" in api.open_wine_prefix_tool("winecfg", prefix, "/usr/bin/wine")["error"]
    assert "Wine is not installed" in api.open_wine_prefix_tool("winetricks", prefix, "/usr/bin/wine")["error"]
    monkeypatch.setattr("core.api.host_which", lambda name: "/usr/bin/wine" if name == "wine" else None)
    assert "zenity or kdialog" in api.open_wine_prefix_tool("winetricks", prefix, "/usr/bin/wine")["error"]
    monkeypatch.setattr("core.api.host_which", lambda name: f"/usr/bin/{name}")
    api._deps_install_status["running"] = True
    assert "install" in api.open_wine_prefix_tool("winecfg", prefix, "/usr/bin/wine")["error"]
    api._deps_install_status["running"] = False
    api._rtp_install_status["running"] = True
    assert "install" in api.open_wine_prefix_tool("winecfg", prefix, "/usr/bin/wine")["error"]
    api._rtp_install_status["running"] = False
    monkeypatch.setattr("core.api.subprocess.Popen", MagicMock(side_effect=OSError("spawn failed")))
    assert "spawn failed" in api.open_wine_prefix_tool("winecfg", prefix, "/usr/bin/wine")["error"]
    monkeypatch.setattr("core.umu.locate_umu", lambda: ("", ""))
    assert "umu-launcher is missing" in api.open_wine_prefix_tool("winecfg", prefix, "/fake/proton")["error"]


@pytest.mark.skipif(sys.platform == "win32", reason="Wine runtime installs run only on Linux hosts")
def test_runtime_installs_and_launches_respect_shared_prefix(tmp_path, monkeypatch):
    from core.game_sandbox import normalize_prefix
    from core.launcher import RunningGame

    monkeypatch.setattr("core.api.is_linux", lambda: True)
    monkeypatch.setattr("core.api.host_which", lambda name: f"/usr/bin/{name}")
    api = Api()
    prefix = str(tmp_path / "prefix")
    api.launcher._running[1] = RunningGame(None, None, prefix=normalize_prefix(prefix), title="Playing")
    for result in (
        api.install_rpgmaker_dependencies(prefix, "/usr/bin/wine"),
        api.install_rpgmaker_rtp(prefix, "/usr/bin/wine"),
    ):
        assert result["success"] is False and "Playing" in result["error"]
    api.launcher._running.clear()
    api._deps_install_status["running"] = True
    api._deps_install_prefix = normalize_prefix(prefix)
    assert api._prefix_tool_conflict(normalize_prefix(prefix)) is not None
    assert api._prefix_tool_conflict(normalize_prefix(str(tmp_path / "other"))) is None
