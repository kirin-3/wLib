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
