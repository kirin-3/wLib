import os
from unittest.mock import patch

from core import launcher, umu

assert launcher


def test_locate_umu_prefers_host_path(tmp_path, monkeypatch):
    copy = tmp_path / "umu" / "umu-run"
    copy.parent.mkdir()
    copy.write_text("local")
    copy.chmod(0o755)
    monkeypatch.setattr(umu, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(umu, "host_which", lambda name: "/host/bin/umu-run")
    assert umu.locate_umu() == ("/host/bin/umu-run", "system")


def test_locate_umu_uses_download_or_reports_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(umu, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(umu, "host_which", lambda name: None)
    assert umu.locate_umu() == ("", "")
    copy = tmp_path / "umu" / "umu-run"
    copy.parent.mkdir()
    copy.write_text("local")
    copy.chmod(0o755)
    assert umu.locate_umu() == (os.fspath(copy), "wlib")


def test_status_uses_host_lookup_and_command(monkeypatch):
    monkeypatch.setattr(
        umu,
        "host_which",
        lambda name: {"umu-run": "/host/umu-run", "python3": "/host/python3"}.get(name),
    )
    monkeypatch.setattr(
        umu, "host_command", lambda argv: ["flatpak-spawn", "--host", *argv]
    )
    with patch.object(umu.subprocess, "run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "umu 1.4.4\n"
        assert umu.get_umu_status() == {
            "source": "system",
            "path": "/host/umu-run",
            "version": "umu 1.4.4",
            "python3": True,
        }
        assert run.call_args.args[0] == [
            "flatpak-spawn",
            "--host",
            "/host/umu-run",
            "--version",
        ]


def test_download_archive_validation(tmp_path, monkeypatch):
    import hashlib
    import io
    import json
    import tarfile

    import pytest

    class Response(io.BytesIO):
        headers = {}

    def archive_for(member_name, kind=tarfile.REGTYPE):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            member = tarfile.TarInfo(member_name)
            member.type = kind
            if kind == tarfile.REGTYPE:
                member.size = 4
                tar.addfile(member, io.BytesIO(b"good"))
            else:
                member.linkname = "elsewhere"
                tar.addfile(member)
        return stream.getvalue()

    monkeypatch.setattr(umu, "get_data_dir", lambda: str(tmp_path))
    for name, kind, digest_mode, succeeds in [
        ("umu/umu-run", tarfile.REGTYPE, "good", True),
        ("umu/umu-run", tarfile.REGTYPE, "bad", False),
        ("umu/umu-run", tarfile.REGTYPE, "missing", False),
        ("umu/umu-run", tarfile.SYMTYPE, "good", False),
        ("umu/umu-run", tarfile.LNKTYPE, "good", False),
        ("/umu/umu-run", tarfile.REGTYPE, "good", False),
        ("../umu/umu-run", tarfile.REGTYPE, "good", False),
    ]:
        data = archive_for(name, kind)
        digest = hashlib.sha256(data).hexdigest()
        asset = {
            "name": "umu-launcher-test-zipapp.tar",
            "browser_download_url": "https://example.test/archive",
        }
        if digest_mode != "missing":
            asset["digest"] = "sha256:" + ("0" * 64 if digest_mode == "bad" else digest)
        release = json.dumps({"assets": [asset]}).encode()
        monkeypatch.setattr(
            umu.urllib.request,
            "urlopen",
            lambda request, **kwargs: Response(
                release if request.full_url == umu._RELEASE_URL else data
            ),
        )
        destination = tmp_path / "umu" / "umu-run"
        if destination.exists():
            destination.unlink()
        if succeeds:
            assert umu.download_release(lambda *_: None) == str(destination)
            assert destination.read_bytes() == b"good"
            assert destination.stat().st_mode & 0o777 == 0o755
        else:
            with pytest.raises(ValueError):
                umu.download_release(lambda *_: None)
            assert not destination.exists()
        assert (
            not list((tmp_path / "umu").glob(".umu-*"))
            if (tmp_path / "umu").exists()
            else True
        )


def test_download_checks_optional_checksum(tmp_path, monkeypatch):
    import hashlib
    import io
    import json
    import tarfile

    import pytest

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        member = tarfile.TarInfo("umu/umu-run")
        member.size = 4
        tar.addfile(member, io.BytesIO(b"good"))
    data = stream.getvalue()
    name = "umu-launcher-test-zipapp.tar"
    release = json.dumps(
        {
            "assets": [
                {
                    "name": name,
                    "browser_download_url": "https://example.test/archive",
                    "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                },
                {
                    "name": name + ".sha512sum",
                    "browser_download_url": "https://example.test/checksum",
                },
            ]
        }
    ).encode()

    class Response(io.BytesIO):
        headers = {}

    monkeypatch.setattr(umu, "get_data_dir", lambda: str(tmp_path))
    checksum = hashlib.sha512(data).hexdigest()

    def fake_urlopen(request, **kwargs):
        return Response(
            {
                umu._RELEASE_URL: release,
                "https://example.test/archive": data,
                "https://example.test/checksum": (checksum + "  " + name).encode(),
            }[request.full_url]
        )

    monkeypatch.setattr(umu.urllib.request, "urlopen", fake_urlopen)
    destination = tmp_path / "umu" / "umu-run"
    umu.download_release(lambda *_: None)
    assert destination.read_bytes() == b"good"
    checksum = "0" * 128
    with pytest.raises(ValueError, match="sha512"):
        umu.download_release(lambda *_: None)
    assert destination.read_bytes() == b"good"
    assert not list(destination.parent.glob(".umu-*"))


def test_required_runtime_and_installed_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    runner = tmp_path / "runner"
    runner.mkdir()
    manifest = runner / "toolmanifest.vdf"
    for appid, runtime in [("4183110", "steamrt4"), ("1628350", "sniper")]:
        manifest.write_text(f'"require_tool_appid" "{appid}"')
        status = umu.runtime_status(str(runner / "proton"))
        assert status["runtime"] == runtime
        assert not status["ready"]
        platform = tmp_path / "umu" / runtime / (runtime + "_platform_test")
        platform.mkdir(parents=True)
        # A platform folder alone may be a half-finished install.
        assert not umu.runtime_status(str(runner / "proton"))["ready"]
        (tmp_path / "umu" / runtime / "_v2-entry-point").write_text("")
        assert umu.runtime_status(str(runner / "proton"))["ready"]
    marker_only = tmp_path / "umu" / "steamrt4"
    for path in marker_only.iterdir():
        if path.is_file():
            path.unlink()
    (marker_only / ".installed.ok").write_text("ok")
    manifest.write_text('"require_tool_appid" "4183110"')
    assert umu.runtime_status(str(runner / "proton"))["ready"]
    assert umu.required_runtime("GE-Proton") == "steamrt4"
    assert umu.required_runtime("UMU-Proton") == "steamrt4"
    assert umu.required_runtime("") == "steamrt4"


def test_prepare_reports_failure_classes_and_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(umu, "host_command", lambda cmd, **kwargs: cmd)
    monkeypatch.setattr(umu, "umu_data_dir", lambda: str(tmp_path / "umu"))
    cases = [
        (1, b"No space left on device", "disk"),
        (1, b"network connection failed", "network"),
        (127, b"python3: not found", "python"),
        (1, b"unknown problem", "other"),
    ]
    for returncode, output, expected in cases:

        class FakeProcess:
            pid = 123

            def __init__(self, command, **kwargs):
                assert command == ["/host/umu-run", "createprefix"]
                assert kwargs["env"]["WINEPREFIX"] == str(tmp_path / "prefix")
                assert kwargs["env"]["PROTONPATH"] == "GE-Proton"
                kwargs["stdout"].write(output)
                self.returncode = returncode

            def poll(self):
                return self.returncode

        monkeypatch.setattr(umu.subprocess, "Popen", FakeProcess)
        job = umu.UmuRuntimePrepare()
        job._run("/host/umu-run", str(tmp_path / "prefix"), "GE-Proton")
        assert job.status()["error_class"] == expected
        assert not job.status()["running"]


def test_prepare_with_plain_wine_runner_uses_umu_proton(tmp_path, monkeypatch):
    monkeypatch.setattr(umu, "host_command", lambda cmd, **kwargs: cmd)
    monkeypatch.setattr(umu, "umu_data_dir", lambda: str(tmp_path / "umu"))
    monkeypatch.setenv("PROTONPATH", "/stale")
    environments = []

    class FakeProcess:
        pid = 123
        returncode = 0

        def __init__(self, command, **kwargs):
            environments.append(kwargs["env"])

        def poll(self):
            return 0

    monkeypatch.setattr(umu.subprocess, "Popen", FakeProcess)
    job = umu.UmuRuntimePrepare()
    job._run("/host/umu-run", str(tmp_path / "prefix"), "wine")
    assert "PROTONPATH" not in environments[0]
    assert not job.status()["error"]


def test_proton_path_value():
    assert umu.proton_path_value("GE-Proton") == "GE-Proton"
    assert umu.proton_path_value("UMU-Proton") == ""
    assert umu.proton_path_value("") == ""
    assert umu.proton_path_value("wine") == ""
    assert umu.proton_path_value("/usr/bin/wine") == ""
    assert umu.proton_path_value("/runners/GE-Proton11/proton") == "/runners/GE-Proton11"


def test_prepare_cancel_terminates_process(tmp_path, monkeypatch):
    monkeypatch.setattr(umu, "host_command", lambda cmd, **kwargs: cmd)
    monkeypatch.setattr(umu, "umu_data_dir", lambda: str(tmp_path / "umu"))
    signals = []
    monkeypatch.setattr(umu.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    class FakeProcess:
        pid = 123
        returncode = None

        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return None

        def wait(self, timeout=None):
            self.returncode = -15
            return self.returncode

    monkeypatch.setattr(umu.subprocess, "Popen", FakeProcess)
    job = umu.UmuRuntimePrepare()
    job._cancel.set()
    job._run("/host/umu-run", str(tmp_path / "prefix"), "UMU-Proton")
    assert job.status()["cancelled"]
    assert signals == [(123, umu.signal.SIGTERM)]


def test_umu_invocation_prefix_environment_and_guard(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(umu, "get_data_dir", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(umu, "locate_umu", lambda: ("/usr/bin/umu-run", "system"))
    prefix = tmp_path / "wine"
    (prefix / "drive_c").mkdir(parents=True)
    command, env = umu.build_umu_invocation(
        "/games/demo.exe",
        prefix=str(prefix),
        runner="/runner/proton",
        game_id=7,
        game_dir="/games",
        environment={
            "UMU_ID": "old",
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": "bad",
            "WINEDLLOVERRIDES": "x",
        },
        log_dir="/logs",
    )
    assert command[0] == "python3" and command[-1] == "/games/demo.exe"
    assert env["WINEPREFIX"] == str(prefix / "proton_compat")
    assert env["PROTONPATH"] == "/runner"
    assert env["GAMEID"] == "umu-wlib7" and env["UMU_CONTAINER_NSENTER"] == "1"
    assert (
        env["PROTON_VERB"] == "waitforexitandrun" and env["UMU_RUNTIME_UPDATE"] == "0"
    )
    assert env["PROTON_LOG_DIR"] == "/logs" and env["WINEDLLOVERRIDES"] == "x"
    assert "UMU_ID" not in env and "STEAM_COMPAT_CLIENT_INSTALL_PATH" not in env
    compat = prefix / "proton_compat"
    (compat / "pfx").symlink_to(".")
    assert env["STEAM_COMPAT_INSTALL_PATH"] == "/games"
    _, env = umu.build_umu_invocation(
        "/games/demo.exe", prefix=str(prefix), runner="GE-Proton", sandboxed=False
    )
    assert env["PROTONPATH"] == "GE-Proton"
    _, env = umu.build_umu_invocation(
        "winetricks",
        prefix=str(prefix),
        runner="/runner/proton",
        environment={
            "STEAM_COMPAT_INSTALL_PATH": "/stale",
            "UMU_CONTAINER_NSENTER": "1",
        },
    )
    # Installers have no game folder or game id: umu picks the install path
    # and the call never tries to join another container.
    assert "STEAM_COMPAT_INSTALL_PATH" not in env
    assert "UMU_CONTAINER_NSENTER" not in env and env["GAMEID"] == "umu-default"
    (compat / "pfx").unlink()
    (compat / "pfx").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="would replace"):
        umu.build_umu_invocation(
            "/games/demo.exe", prefix=str(prefix), runner="UMU-Proton"
        )
    (compat / "pfx").unlink()
    (compat / "pfx").mkdir()
    _, env = umu.build_umu_invocation(
        "/games/demo.exe",
        prefix=str(prefix),
        runner="/runner/proton",
        sandboxed=True,
        environment={"UMU_CONTAINER_NSENTER": "1"},
    )
    assert env["PROTONPATH"] == "/runner" and "UMU_CONTAINER_NSENTER" not in env
    _, env = umu.build_umu_invocation(
        "/games/demo.exe", prefix=str(prefix), runner="UMU-Proton", environment={}
    )
    assert "PROTONPATH" not in env


def test_resolve_runner_and_sandbox_codename(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(umu, "host_which", lambda name: "/usr/bin/wine")
    assert umu.resolve_runner("") == "wine"
    monkeypatch.setattr(umu, "host_which", lambda name: None)
    assert umu.resolve_runner("") == "UMU-Proton"
    monkeypatch.setattr(umu, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(umu, "umu_data_dir", lambda: str(tmp_path / "umu"))
    monkeypatch.setattr(
        umu.os.path,
        "expanduser",
        lambda path: str(tmp_path / "emptyhome") if path == "~" else path,
    )
    with pytest.raises(ValueError, match="Settings"):
        umu.resolve_codename("GE-Proton")
    build = tmp_path / "proton" / "GE-Proton11-11"
    build.mkdir(parents=True)
    (build / "proton").write_text("")
    assert umu.resolve_codename("GE-Proton") == str(build)


def test_wrapper_keeps_existing_install_marker_offline(tmp_path):
    import subprocess
    import sys

    package = tmp_path / "umu"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "umu_util.py").write_text(
        "import os\ndef has_runtime_installed(local):\n return os.environ.get('INSTALLED') == '1'\n"
    )
    (package / "umu_run.py").write_text(
        "def setup_umu(local, *args):\n pass\n"
        "def write_install_marker(path):\n print('marker written')\n"
    )
    entry = tmp_path / "umu-run"
    entry.write_text("from umu import umu_run\numu_run.write_install_marker('rt')\n")
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        (
            __import__("pathlib").Path(umu.__file__).parent / "umu_launch_wrapper.py"
        ).read_text()
    )
    env = dict(
        os.environ,
        WLIB_UMU_RUN=str(entry),
        PYTHONPATH=str(tmp_path),
        UMU_RUNTIME_UPDATE="0",
        INSTALLED="1",
    )

    def run():
        return subprocess.run(
            [sys.executable, str(wrapper)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    assert "marker written" not in run()
    env["INSTALLED"] = "0"
    assert "marker written" in run()


def test_wrapper_uses_runtime_check_when_available(tmp_path):
    import subprocess
    import sys

    package = tmp_path / "umu"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "umu_run.py").write_text(
        "def setup_umu(local, *args):\n print('original setup')\n"
    )
    (package / "umu_util.py").write_text(
        "import os\ndef has_runtime_installed(local):\n return os.environ.get('INSTALLED') == '1'\n"
    )
    entry = tmp_path / "umu-run"
    entry.write_text(
        "from umu.umu_run import setup_umu\nsetup_umu('runtime')\nprint('entry ran')\n"
    )
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        (
            __import__("pathlib").Path(umu.__file__).parent / "umu_launch_wrapper.py"
        ).read_text()
    )
    env = dict(
        os.environ,
        WLIB_UMU_RUN=str(entry),
        PYTHONPATH=str(tmp_path),
        UMU_RUNTIME_UPDATE="0",
        INSTALLED="1",
    )

    def run():
        return subprocess.run(
            [sys.executable, str(wrapper)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    output = run()
    assert (
        "check skipped" in output
        and "original setup" not in output
        and "entry ran" in output
    )
    env["INSTALLED"] = "0"
    assert "original setup" in run()
    env["INSTALLED"] = "1"
    env["UMU_RUNTIME_UPDATE"] = "1"
    assert "original setup" in run()
    env["UMU_RUNTIME_UPDATE"] = "0"
    (package / "umu_util.py").write_text("")
    assert "patch unavailable" in run() and "original setup" in run()
