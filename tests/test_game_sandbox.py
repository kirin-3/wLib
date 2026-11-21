# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import subprocess
from unittest.mock import Mock

import pytest

from core import game_sandbox as sandbox


@pytest.fixture(autouse=True)
def clean_probe(monkeypatch):
    sandbox._probe_cache.clear()
    sandbox._overlay_probe_cache.clear()
    monkeypatch.setattr(sandbox, "get_setting", lambda _: "")
    yield
    sandbox._probe_cache.clear()
    sandbox._overlay_probe_cache.clear()


def test_linux_missing(monkeypatch):
    which = Mock(return_value=None)
    monkeypatch.setattr(sandbox, "host_which", which)
    assert sandbox.get_sandbox_status(platform="linux")["status"] == "missing"
    assert sandbox.get_sandbox_status(platform="linux")["status"] == "missing"
    assert which.call_count == 2


@pytest.mark.parametrize(
    "stderr,namespace_denied",
    [
        ("bwrap: Creating new namespace failed: Operation not permitted", True),
        ("bwrap: No permissions to create new namespace", True),
        ("bwrap: error setting up uid map", True),
        ("bwrap: Creating new namespace: Permission denied", True),
        ("bwrap: Can't bind mount /: Permission denied", False),
        ("bwrap: Can't mount proc: Invalid argument", False),
    ],
)
def test_linux_bwrap_probe_failure(monkeypatch, stderr, namespace_denied):
    monkeypatch.setattr(sandbox, "host_which", lambda _: "/usr/bin/bwrap")
    run = Mock(
        side_effect=[
            Mock(returncode=0, stdout="bubblewrap 0.13.1", stderr=""),
            Mock(returncode=1, stdout="", stderr=stderr + "\n"),
        ]
    )
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    result = sandbox.get_sandbox_status(platform="linux")
    assert result["status"] == "unusable"
    assert result["path"] == "/usr/bin/bwrap"
    assert result["version"] == "bubblewrap 0.13.1"
    assert stderr in result["reason"]
    if namespace_denied:
        assert "The system blocks unprivileged user namespaces." in result["reason"]
        assert "Installing bubblewrap does not fix this." in result["reason"]
    else:
        assert result["reason"] == "Bubblewrap could not start a sandbox: " + stderr


def test_linux_bwrap_probe_timeout(monkeypatch):
    monkeypatch.setattr(sandbox, "host_which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        Mock(
            side_effect=[
                Mock(returncode=0, stdout="bubblewrap 0.13.1", stderr=""),
                subprocess.TimeoutExpired("bwrap", 10),
            ]
        ),
    )
    result = sandbox.get_sandbox_status(platform="linux")
    assert result["status"] == "unusable"
    assert "timed out" in result["reason"]


@pytest.mark.parametrize(
    "version_code,namespace_code,status",
    [(0, 0, "ready"), (1, 0, "unusable"), (0, 1, "unusable")],
)
def test_linux_probe_cache_and_flatpak(
    monkeypatch, version_code, namespace_code, status
):
    monkeypatch.setattr(sandbox, "host_which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox, "host_command", lambda cmd: ["flatpak-spawn", "--host", *cmd]
    )
    run = Mock(
        side_effect=lambda cmd, **_: Mock(
            returncode=version_code if "--version" in cmd else namespace_code,
            stdout="bubblewrap 0.13.1",
            stderr="blocked",
        )
    )
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    result = sandbox.get_sandbox_status(platform="linux")
    assert result["status"] == status
    count = run.call_count
    assert sandbox.get_sandbox_status(platform="linux") == result
    assert run.call_count == count * (1 if status == "ready" else 2)
    sandbox.get_sandbox_status(platform="linux", recheck=True)
    assert run.call_count == count * (2 if status == "ready" else 3)
    assert run.call_args.args[0][:2] == ["flatpak-spawn", "--host"]
    if version_code == 0:
        assert run.call_args.args[0][2:] == [
            "/usr/bin/bwrap",
            *sandbox.BWRAP_NAMESPACE_OPTIONS,
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "true",
        ]
    assert run.call_args.kwargs["timeout"] == 10


def test_ready_cache_returns_copies_and_serializes_probes(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(sandbox, "host_which", lambda _: "/usr/bin/bwrap")
    run = Mock(return_value=Mock(returncode=0, stdout="bubblewrap 0.13.1", stderr=""))
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: sandbox.get_sandbox_status(platform="linux"), range(8))
        )
    assert all(result["status"] == "ready" for result in results)
    assert run.call_count == 2
    results[0]["status"] = "missing"
    assert sandbox.get_sandbox_status(platform="linux")["status"] == "ready"


@pytest.mark.parametrize("status", ["missing", "unusable", "ready"])
def test_only_real_probes_are_logged(monkeypatch, capsys, status):
    reason = "" if status == "ready" else "Probe failed\nwith details"
    result = dict(
        status=status,
        tool="bwrap",
        path="/usr/bin/bwrap",
        version="0.13",
        reason=reason,
    )
    monkeypatch.setattr(sandbox, "_probe", Mock(return_value=result))
    sandbox.get_sandbox_status(platform="linux")
    line = capsys.readouterr().out
    assert line == (
        f"[wLib] Sandbox check: status={status} tool=bwrap "
        f"path='/usr/bin/bwrap' version='0.13' reason={reason!r}\n"
    )
    sandbox.get_sandbox_status(platform="linux")
    assert capsys.readouterr().out == ("" if status == "ready" else line)
    sandbox.get_sandbox_status(platform="linux", recheck=True)
    assert capsys.readouterr().out == line


@pytest.mark.parametrize(
    "custom,found,expected",
    [
        (r"D:\Portable\Start.exe", r"D:\Portable\Start.exe", "ready"),
        ("", r"C:\Program Files (x86)\Sandboxie Plus\Start.exe", "ready"),
        ("", r"C:\Program Files\Sandboxie\Start.exe", "ready"),
        ("", "", "missing"),
        (r"D:\Portable\SandMan.exe", "", "unusable"),
    ],
)
def test_windows_detection(monkeypatch, custom, found, expected):
    monkeypatch.setattr(sandbox.os.path, "isfile", lambda p: p == found)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    result = sandbox.get_sandbox_status(custom, platform="windows")
    assert result["status"] == expected
    if expected == "ready":
        assert result["path"] == found


def test_bwrap_mounts_and_ordering():
    argv = sandbox.build_bwrap_argv(
        ["wine", "/games/game.exe"],
        game_dir="/games",
        home="/home/u",
        sandbox_home="/data/sandbox/1/home",
        cwd="/games",
        prefix="/prefix",
        readonly_paths=("/proton", "/runner"),
        environment={
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "XAUTHORITY": "/run/user/1000/xauth",
        },
    )
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    for triple in [
        ["--bind", "/games", "/games"],
        ["--bind", "/prefix", "/prefix"],
        ["--bind", "/data/sandbox/1/home", "/home/u"],
        ["--ro-bind", "/proton", "/proton"],
        ["--ro-bind", "/runner", "/runner"],
        ["--dev-bind-try", "/dev/ntsync", "/dev/ntsync"],
    ]:
        assert triple in triples
    assert "/run/user/1000/bus" not in argv
    assert argv[-6:] == [
        "sh",
        "-c",
        sandbox.SUPERVISOR,
        "sh",
        "wine",
        "/games/game.exe",
    ]
    nix = sandbox.build_bwrap_argv(
        ["true"],
        game_dir="/games",
        home="/home/u",
        sandbox_home="/sandbox",
        cwd="/games",
        nixos=True,
    )
    assert "/nix" in nix and "/run/current-system" in nix
    assert "/lib" not in nix and "/lib64" not in nix
    assert nix.index("/run") < nix.index("/run/current-system")


def test_bwrap_gives_a_private_x11_folder_with_host_sockets():
    argv = sandbox.build_bwrap_argv(
        ["true"],
        game_dir="/games",
        home="/home/u",
        sandbox_home="/s",
        cwd="/games",
        x11_sockets=("X0", "X1"),
    )
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    pairs = [argv[i : i + 2] for i in range(len(argv) - 1)]
    tmp = pairs.index(["--tmpfs", "/tmp"])
    folder = pairs.index(["--dir", "/tmp/.X11-unix"])
    assert tmp < folder
    for name in ("X0", "X1"):
        path = f"/tmp/.X11-unix/{name}"
        assert triples.index(["--ro-bind-try", path, path]) > folder
    # The host folder belongs to root (nobody in the namespace): never bind it.
    assert ["--ro-bind-try", "/tmp/.X11-unix", "/tmp/.X11-unix"] not in triples
    assert not any(t[0].endswith("bind") and t[1] == "/tmp/.X11-unix" for t in triples)
    empty = sandbox.build_bwrap_argv(
        ["true"], game_dir="/games", home="/home/u", sandbox_home="/s", cwd="/games"
    )
    assert "--dir" in empty and not any(a.startswith("/tmp/.X11-unix/") for a in empty)


def test_bwrap_blocks_the_session_bus_autolaunch():
    argv = sandbox.build_bwrap_argv(
        ["true"], game_dir="/games", home="/home/u", sandbox_home="/s", cwd="/games"
    )
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    pairs = [argv[i : i + 2] for i in range(len(argv) - 1)]
    assert [
        "--setenv",
        "DBUS_SESSION_BUS_ADDRESS",
        "unix:path=/run/wlib-no-session-bus",
    ] in triples
    assert ["--setenv", "NO_AT_BRIDGE", "1"] in triples
    assert ["--unsetenv", "DBUS_SESSION_BUS_ADDRESS"] not in pairs
    assert ["--unsetenv", "DBUS_SYSTEM_BUS_ADDRESS"] in pairs


def test_supervisor_ignores_bus_helpers():
    names = sandbox._BUS_HELPER_PATTERN.split("|")
    assert all(len(name) <= 15 for name in names)
    for name in ("dbus-daemon", "dbus-launch", "at-spi-bus-laun", "at-spi2-registr"):
        assert name in names
    script = sandbox.SUPERVISOR
    assert 'case "$pid" in 1|$$) continue;; esac' in script
    assert f'case "$comm" in {sandbox._BUS_HELPER_PATTERN}) continue;; esac' in script
    assert '[ "$n" -eq 0 ] && { kill -KILL -1 2>/dev/null; exit "$result"; }' in script


def test_bwrap_mounts_system_certificates_and_audio_config():
    argv = sandbox.build_bwrap_argv(
        ["true"], game_dir="/games", home="/home/u", sandbox_home="/s", cwd="/games"
    )
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    for path in ("/etc/ca-certificates", "/etc/machine-id", "/etc/alsa", "/etc/pulse"):
        assert ["--ro-bind-try", path, path] in triples


@pytest.mark.parametrize(
    "path,exposed",
    [
        ("/home/u", True),
        ("/home", True),
        ("/", True),
        ("/home/u/Games/MyGame", False),
        ("/games", False),
    ],
)
def test_writable_mounts_never_expose_home(path, exposed):
    assert sandbox.exposes_home(path, "/home/u") is exposed
    if exposed:
        with pytest.raises(ValueError, match="whole home folder"):
            sandbox.check_writable_mounts(("/games", path), "/home/u")
    else:
        sandbox.check_writable_mounts((path, ""), "/home/u")


def test_mounts_never_expose_the_wlib_data_dir(monkeypatch, tmp_path):
    data = tmp_path / "data"
    sandbox_root = data / "sandbox"
    sandbox_root.mkdir(parents=True)
    (data / "wlib.db").write_text("library")
    monkeypatch.setenv("WLIB_DATA_DIR", str(data))
    home = str(tmp_path / "home")

    for path in (str(data), str(sandbox_root)):
        with pytest.raises(ValueError, match="whole home folder"):
            sandbox.check_writable_mounts((path,), home)
        with pytest.raises(ValueError, match="whole home folder"):
            _ = sandbox.build_bwrap_argv(
                ["true"],
                game_dir="/games",
                home=home,
                sandbox_home="/sandbox-home",
                cwd="/games",
                readonly_paths=(path,),
            )

    # The default Wine prefix and a Proton runtime live below the data dir;
    # mounting those subtrees exposes neither the database nor other sandboxes.
    sandbox.check_writable_mounts((str(data / "prefix"),), home)
    runtime = str(data / "proton" / "GE-Proton")
    argv = sandbox.build_bwrap_argv(
        ["true"],
        game_dir="/games",
        home=home,
        sandbox_home="/sandbox-home",
        cwd="/games",
        readonly_paths=(runtime,),
    )
    assert ["--ro-bind", runtime, runtime] in [
        argv[i : i + 3] for i in range(len(argv) - 2)
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap runs only on Linux hosts")
@pytest.mark.parametrize(
    "executable,folder",
    [
        ("/opt/wine-ge/bin/wine", "/opt/wine-ge"),
        (
            "/home/u/.local/share/wLib/proton/GE/proton",
            "/home/u/.local/share/wLib/proton/GE",
        ),
        ("/home/u/.local/bin/wine", "/home/u/.local/bin"),
        ("/home/u/bin/wine", "/home/u/bin"),
    ],
)
def test_runtime_folder_stays_out_of_home_top_level(executable, folder):
    assert sandbox.runtime_folder(executable, "/home/u") == folder


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap runs only on Linux hosts")
@pytest.mark.parametrize(
    "exe,mode,folder",
    [
        ("/games/game", "native", "/games"),
        ("/games/www/Game.exe", "rpgmaker_linux", "/games"),
        ("/games/Game.exe", "custom", "/games"),
    ],
)
def test_game_folder_resolution(exe, mode, folder):
    assert sandbox.resolve_game_folder(exe, mode) == folder


def test_sandbox_home_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path))
    assert sandbox.prepare_sandbox_home(12) == str(tmp_path / "sandbox/12/home")
    assert (tmp_path / "sandbox/12/home").is_dir()


def integration_argv(tmp_path, command):
    import shutil
    import subprocess
    import os

    bwrap = shutil.which("bwrap")
    unshare = shutil.which("unshare")
    if not bwrap or not unshare:
        pytest.skip("bubblewrap/user namespace tools unavailable")
    probe = subprocess.run([unshare, "-Ur", "true"], capture_output=True)
    if probe.returncode:
        pytest.skip("unprivileged user namespaces unavailable")
    game = tmp_path / "game"
    home = tmp_path / "home"
    game.mkdir()
    home.mkdir()
    return sandbox.build_bwrap_argv(
        command,
        game_dir=str(game),
        home=os.path.expanduser("~"),
        sandbox_home=str(home),
        cwd=str(game),
        bwrap=bwrap,
    )


def test_supervisor_keeps_setsid_child_alive(tmp_path):
    import subprocess
    import time

    argv = integration_argv(tmp_path, ["sh", "-c", "setsid sleep 3 & exit 0"])
    start = time.monotonic()
    result = subprocess.run(argv, capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr
    assert 2.8 <= time.monotonic() - start < 7


def test_supervisor_exits_with_a_bus_helper_left(tmp_path):
    import shutil
    import subprocess
    import time

    sleep = shutil.which("sleep")
    if not sleep:
        pytest.skip("sleep unavailable")
    argv = integration_argv(tmp_path, ["sh", "-c", "./dbus-daemon 30 & exit 0"])
    helper = tmp_path / "game" / "dbus-daemon"
    shutil.copy(sleep, helper)
    if subprocess.run([str(helper), "0"], capture_output=True).returncode:
        pytest.skip("sleep does not run under another name")
    start = time.monotonic()
    # The helper holds the output pipe, so this returns only once it is gone.
    result = subprocess.run(argv, capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - start < 4


def test_sigterm_ends_sandbox_tree(tmp_path):
    import subprocess
    import time
    import os
    import signal

    argv = integration_argv(tmp_path, ["sh", "-c", "setsid sleep 30 & wait"])
    proc = subprocess.Popen(
        argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    try:
        time.sleep(0.3)
        assert proc.poll() is None
        start = time.monotonic()
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=2)
        assert time.monotonic() - start < 2
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_sandboxie_configuration_replaces_passthrough(monkeypatch, tmp_path):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path))
    run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    start = r"C:\Sandboxie-Plus\Start.exe"
    assert sandbox.configure_sandboxie_box(start, 12, r"D:\Games\Story") == "wLib_12"
    commands = [call.args[0] for call in run.call_args_list]
    ini = r"C:\Sandboxie-Plus\SbieIni.exe"
    assert commands == [
        [ini, "set", "wLib_12", "Enabled", "y"],
        [ini, "set", "wLib_12", "FileRootPath", str(tmp_path / "sandbox/12")],
        [ini, "set", "wLib_12", "AutoDelete", "n"],
        [ini, "set", "wLib_12", "ClosedFilePath", ""],
        [ini, "set", "wLib_12", "NotifyInternetAccessDenied", ""],
        [ini, "set", "wLib_12", "OpenFilePath", r"D:\Games\Story" + chr(92)],
        [start, "/reload"],
    ]
    run.return_value.returncode = 1
    with pytest.raises(RuntimeError):
        sandbox.configure_sandboxie_box(start, 12, "new game folder")


@pytest.mark.parametrize(
    "output,expected", [("0\n", []), ("2\n123\n456\n", [123, 456])]
)
def test_sandboxie_pid_count_is_not_a_pid(monkeypatch, output, expected):
    monkeypatch.setattr(sandbox, "_sandboxie_call", lambda *_: output)
    assert sandbox.list_box_pids("Start.exe", "wLib_12") == expected


@pytest.mark.parametrize("output", ["", "2\n123\n", "garbage"])
def test_sandboxie_bad_process_list_is_not_empty(monkeypatch, output):
    monkeypatch.setattr(sandbox, "_sandboxie_call", lambda *_: output)
    with pytest.raises(RuntimeError):
        sandbox.list_box_pids("Start.exe", "wLib_12")


def test_arch_install_information_and_cancel(monkeypatch):
    monkeypatch.setattr(sandbox, "get_host_name", lambda *args: "linux")
    monkeypatch.setattr(
        sandbox,
        "host_which",
        lambda name: "/usr/bin/" + name if name in ("pacman", "pkexec") else None,
    )
    info = sandbox.get_sandbox_install_info()
    assert info["command"] == "sudo pacman -S bubblewrap"
    assert info["pkexec_available"] and info["can_install"]
    run = Mock(
        return_value=Mock(returncode=126, stderr="Authentication cancelled", stdout="")
    )
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    monkeypatch.setattr(
        sandbox, "host_command", lambda cmd: ["flatpak-spawn", "--host", *cmd]
    )
    with pytest.raises(RuntimeError, match="cancelled"):
        sandbox.install_sandbox_tool("bwrap")
    assert run.call_args.args[0] == [
        "flatpak-spawn",
        "--host",
        "/usr/bin/pkexec",
        "/usr/bin/pacman",
        "-S",
        "--noconfirm",
        "bubblewrap",
    ]


@pytest.mark.parametrize("host,tool", [("linux", "sandboxie"), ("windows", "bwrap")])
def test_installers_reject_wrong_host_before_starting(monkeypatch, host, tool):
    monkeypatch.setattr(sandbox, "get_host_name", lambda *args: host)
    command = Mock()
    monkeypatch.setattr(sandbox, "_install_command", command)
    with pytest.raises(ValueError, match="unsupported_platform"):
        sandbox.install_sandbox_tool(tool)
    command.assert_not_called()


@pytest.mark.parametrize("has_winget", [True, False])
def test_windows_install_routes(monkeypatch, has_winget):
    monkeypatch.setattr(sandbox, "get_host_name", lambda *args: "windows")
    monkeypatch.setattr(
        sandbox, "host_which", lambda _: "winget.exe" if has_winget else None
    )
    command = Mock()
    download = Mock()
    monkeypatch.setattr(sandbox, "_install_command", command)
    monkeypatch.setattr(sandbox, "_download_sandboxie_installer", download)
    sandbox.install_sandbox_tool("sandboxie")
    if has_winget:
        assert command.call_args.args[0][:5] == [
            "winget.exe",
            "install",
            "--id",
            "Sandboxie.Plus",
            "-e",
        ]
        download.assert_not_called()
    else:
        download.assert_called_once()
        command.assert_not_called()


@pytest.mark.parametrize("valid_signature", [False, True])
def test_direct_installer_verification_and_elevation_cancel(
    monkeypatch, tmp_path, valid_signature
):
    import io
    import json
    import tempfile
    import urllib.request
    import platform

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    release = {
        "assets": [
            {
                "name": "Sandboxie-Plus-x64-v1.exe",
                "browser_download_url": "https://github.com/sandboxie-plus/Sandboxie/releases/download/v1/installer.exe",
            }
        ]
    }

    def response(url, **kwargs):
        return io.BytesIO(
            json.dumps(release).encode() if not isinstance(url, str) else b"installer"
        )

    monkeypatch.setattr(urllib.request, "urlopen", response)
    signature = {
        "Status": "Valid" if valid_signature else "NotSigned",
        "Publisher": "CN=NOVASYNC LABS PTE. LTD., O=NOVASYNC LABS PTE. LTD.",
    }
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        Mock(return_value=Mock(returncode=0, stdout=json.dumps(signature), stderr="")),
    )
    install = Mock(side_effect=RuntimeError("Elevation cancelled"))
    monkeypatch.setattr(sandbox, "_install_command", install)
    with pytest.raises(
        RuntimeError, match="cancelled" if valid_signature else "signature"
    ):
        sandbox._download_sandboxie_installer()
    assert not list(tmp_path.iterdir())
    assert install.call_count == int(valid_signature)


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap runs only on Linux hosts")
def test_bwrap_refuses_read_only_mounts_that_expose_home(tmp_path):
    home = str(tmp_path / "home")
    for path in (home, os.path.dirname(home), "/"):
        with pytest.raises(ValueError, match="whole home folder"):
            sandbox.build_bwrap_argv(
                ["true"],
                game_dir="/games",
                home=home,
                sandbox_home="/sandbox",
                cwd="/games",
                readonly_paths=(path,),
            )
    argv = sandbox.build_bwrap_argv(
        ["true"],
        game_dir="/games",
        home=home,
        sandbox_home="/sandbox",
        cwd="/games",
        readonly_paths=("/opt/proton",),
    )
    assert ["--ro-bind", "/opt/proton", "/opt/proton"] in [
        argv[i : i + 3] for i in range(len(argv) - 2)
    ]


def test_umu_runtime_mounts_are_restricted(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    data = home / ".local" / "share" / "umu"
    runtime = data / "steamrt4"
    ref = runtime / "steamrt4_platform_1" / "files" / ".ref"
    ref.parent.mkdir(parents=True)
    ref.touch()
    (runtime / "var").mkdir()
    var = tmp_path / "umu-var"
    var.mkdir()
    game = tmp_path / "game"
    game.mkdir()
    sandbox_home = tmp_path / "sandbox-home"
    sandbox_home.mkdir()
    argv = sandbox.build_bwrap_argv(
        ["umu-run", "game.exe"],
        game_dir=str(game),
        home=str(home),
        sandbox_home=str(sandbox_home),
        cwd=str(game),
        umu_data=str(data),
        umu_runtime=str(runtime),
        umu_var=str(var),
    )

    def has_sequence(parts):
        return any(argv[i : i + len(parts)] == parts for i in range(len(argv)))

    assert has_sequence(["--ro-bind", str(data), str(data)])
    assert has_sequence(["--bind", str(ref), str(ref)])
    assert has_sequence(["--bind", str(var), str(runtime / "var")])
    assert has_sequence(["--setenv", "XDG_DATA_HOME", str(data.parent)])


def test_umu_runtime_cannot_overlap_writable_game_or_prefix(tmp_path):
    home = tmp_path / "home"
    data = home / ".local" / "share" / "umu"
    runtime = data / "steamrt4"
    var = tmp_path / "var"
    game = tmp_path / "game"
    home.mkdir()
    game.mkdir()
    var.mkdir()
    runtime.mkdir(parents=True)
    common = dict(
        home=str(home),
        sandbox_home=str(tmp_path / "sandbox"),
        cwd=str(game),
        umu_data=str(data),
        umu_runtime=str(runtime),
        umu_var=str(var),
    )
    with pytest.raises(ValueError, match="overlaps"):
        sandbox.build_bwrap_argv(["game"], game_dir=str(data), **common)
    with pytest.raises(ValueError, match="overlaps"):
        sandbox.build_bwrap_argv(
            ["game"], game_dir=str(game), prefix=str(runtime), **common
        )


@pytest.mark.skipif(sys.platform == "win32", reason="bubblewrap is Linux-only")
def test_sandbox_parent_thread_outlives_bridge_thread():
    import threading
    import time
    from core.launcher import _spawn_sandbox_process

    if sandbox.get_sandbox_status(platform="linux")["status"] != "ready":
        pytest.skip("bubblewrap is unavailable")
    processes = []

    def bridge_call():
        processes.append(
            _spawn_sandbox_process(
                lambda: subprocess.Popen(
                    [
                        "bwrap",
                        "--die-with-parent",
                        "--ro-bind",
                        "/",
                        "/",
                        "--proc",
                        "/proc",
                        "--dev",
                        "/dev",
                        "sh",
                        "-c",
                        "sleep 2",
                    ],
                    start_new_session=True,
                )
            )
        )

    bridge = threading.Thread(target=bridge_call)
    bridge.start()
    bridge.join(timeout=5)
    assert not bridge.is_alive() and processes
    try:
        time.sleep(0.2)
        assert processes[0].poll() is None
        assert processes[0].wait(timeout=5) == 0
    finally:
        if processes[0].poll() is None:
            processes[0].terminate()
            processes[0].wait(timeout=5)


def test_system_symlinks_and_network_argv(monkeypatch):
    monkeypatch.setattr(
        sandbox.os.path, "islink", lambda path: path in ("/bin", "/lib")
    )
    monkeypatch.setattr(sandbox.os, "readlink", lambda path: "usr" + path)
    argv = sandbox.build_bwrap_argv(
        ["game"],
        game_dir="/games",
        home="/home/u",
        sandbox_home="/s",
        cwd="/games",
        network=True,
    )
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    assert ["--symlink", "usr/lib", "/lib"] in triples
    assert ["--symlink", "usr/bin", "/bin"] in triples
    assert ["--ro-bind-try", "/lib64", "/lib64"] in triples
    assert "--unshare-net" in argv
    assert sandbox.build_network_bwrap_argv(["game", "arg"], cwd="/games") == [
        "bwrap",
        "--unshare-user",
        "--unshare-net",
        "--dev-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp/.X11-unix",
        "--chdir",
        "/games",
        "--",
        "game",
        "arg",
    ]
    assert sandbox.build_network_bwrap_argv(
        ["game"], cwd="/games", x11_sockets=("X0", "X2")
    ) == [
        "bwrap",
        "--unshare-user",
        "--unshare-net",
        "--dev-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp/.X11-unix",
        "--ro-bind-try",
        "/tmp/.X11-unix/X0",
        "/tmp/.X11-unix/X0",
        "--ro-bind-try",
        "/tmp/.X11-unix/X2",
        "/tmp/.X11-unix/X2",
        "--chdir",
        "/games",
        "--",
        "game",
    ]


def test_sandboxie_network_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path))
    call = Mock(return_value="")
    monkeypatch.setattr(sandbox, "_sandboxie_call", call)
    sandbox.configure_sandboxie_box(r"C:\Sandboxie\Start.exe", 1, r"D:\Game", True)
    assert any(
        args.args[1]
        == [
            "set",
            "wLib_1",
            "ClosedFilePath",
            "!<InternetAccess>,InternetAccessDevices",
        ]
        for args in call.call_args_list
    )
    # A blocked game must not make Sandboxie open a message window (SBIE1307).
    assert any(
        args.args[1] == ["set", "wLib_1", "NotifyInternetAccessDenied", "n"]
        for args in call.call_args_list
    )
    call.reset_mock()
    sandbox.configure_sandboxie_box(r"C:\Sandboxie\Start.exe", 1, r"D:\Game", False)
    assert any(
        args.args[1] == ["set", "wLib_1", "ClosedFilePath", ""]
        for args in call.call_args_list
    )
    assert any(
        args.args[1] == ["set", "wLib_1", "NotifyInternetAccessDenied", ""]
        for args in call.call_args_list
    )


def test_bwrap_uses_host_system_links_when_given(monkeypatch):
    # Flatpak's own /lib must not decide the host's mounts.
    monkeypatch.setattr(sandbox.os.path, "islink", lambda _p: True)
    argv = sandbox.build_bwrap_argv(
        ["true"],
        game_dir="/games",
        home="/home/u",
        sandbox_home="/s",
        cwd="/games",
        system_links={"/lib": "usr/lib"},
    )
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    assert ["--symlink", "usr/lib", "/lib"] in triples
    assert ["--ro-bind-try", "/lib64", "/lib64"] in triples


def test_flatpak_host_layout_is_read_from_the_host(monkeypatch):
    from core import host_platform

    monkeypatch.setattr(host_platform, "is_flatpak", lambda: True)
    completed = subprocess.CompletedProcess(
        [], 0, stdout="nix\nlink /bin usr/bin\n", stderr=""
    )
    run = Mock(return_value=completed)
    monkeypatch.setattr(host_platform.subprocess, "run", run)
    assert host_platform.host_system_layout() == (True, {"/bin": "usr/bin"})
    assert "flatpak-spawn" in run.call_args.args[0]


def test_host_x11_sockets_reads_the_folder(monkeypatch):
    from core import host_platform

    monkeypatch.setattr(host_platform, "is_flatpak", lambda: False)
    listdir = Mock(return_value=["X1", "X0", "X0-lock", "Xa", "x2", ".hidden"])
    monkeypatch.setattr(host_platform.os, "listdir", listdir)
    assert host_platform.host_x11_sockets() == ("X0", "X1")
    assert listdir.call_args.args[0] == "/tmp/.X11-unix"
    monkeypatch.setattr(host_platform.os, "listdir", Mock(side_effect=OSError))
    assert host_platform.host_x11_sockets() == ()


def test_host_x11_sockets_asks_the_host_in_flatpak(monkeypatch):
    from core import host_platform

    monkeypatch.setattr(host_platform, "is_flatpak", lambda: True)
    completed = subprocess.CompletedProcess([], 0, stdout="X0\nX1\nnot-x\n", stderr="")
    run = Mock(return_value=completed)
    monkeypatch.setattr(host_platform.subprocess, "run", run)
    assert host_platform.host_x11_sockets() == ("X0", "X1")
    assert "flatpak-spawn" in run.call_args.args[0]
    assert run.call_args.args[0][-1] == "/tmp/.X11-unix"
    run.return_value = subprocess.CompletedProcess([], 1, stdout="X0\n", stderr="")
    assert host_platform.host_x11_sockets() == ()
    run.side_effect = subprocess.TimeoutExpired("flatpak-spawn", 10)
    assert host_platform.host_x11_sockets() == ()
    run.side_effect = OSError
    assert host_platform.host_x11_sockets() == ()


def test_wine_service_names_fit_proc_comm():
    assert all(len(name) <= 15 for name in sandbox._WINE_SERVICE_PATTERN.split("|"))


def test_sandbox_environment_drops_host_secrets():
    host = {
        "PATH": "/usr/bin",
        "DISPLAY": ":0",
        "GITHUB_TOKEN": "secret",
        "SSH_AUTH_SOCK": "/run/ssh",
        "LC_ALL": "C",
        "DXVK_HUD": "1",
    }
    env = {**host, "WINEPREFIX": "/p", "LANG": "ja_JP.UTF-8", "MY_FLAG": "1"}
    result = sandbox.sandbox_environment(env, host)
    assert "GITHUB_TOKEN" not in result and "SSH_AUTH_SOCK" not in result
    assert result["PATH"] == "/usr/bin" and result["DISPLAY"] == ":0"
    assert result["WINEPREFIX"] == "/p" and result["LC_ALL"] == "C"
    # Set by wLib or the user's launch options, so kept.
    assert result["MY_FLAG"] == "1" and result["LANG"] == "ja_JP.UTF-8"
    assert "--die-with-parent" not in sandbox.BWRAP_NAMESPACE_OPTIONS


def test_sandboxie_tools_run_without_a_console_window(monkeypatch):
    run = Mock(return_value=Mock(returncode=0, stdout="0\n", stderr=""))
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    monkeypatch.setattr(sandbox, "no_console_window", lambda: 0x08000000)
    sandbox.terminate_box(r"C:\Sandboxie\Start.exe", "wLib_1")
    assert run.call_args.kwargs["creationflags"] == 0x08000000


def _run_gamescope_shim(argv, tmp_path, env):
    """Run the shim with a fake bwrap that prints its arguments, one per line."""
    assert argv[:3] == ["sh", "-c", sandbox.GAMESCOPE_BIND_SHIM]
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    fake.chmod(0o755)
    argv = [*argv[:6], str(fake), *argv[7:]]
    completed = subprocess.run(
        argv,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.splitlines()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh shim")
def test_gamescope_sandbox_binds_only_gamescope_display(tmp_path):
    argv = sandbox.build_bwrap_argv(
        ["game"],
        game_dir="/games",
        home="/home/u",
        sandbox_home="/s",
        cwd="/games",
        environment={
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "WAYLAND_DISPLAY": "wayland-1",
            "XAUTHORITY": "/home/u/.Xauthority",
        },
        x11_sockets=("X0", "X1"),
        gamescope=True,
    )
    inner = argv[6:]
    assert "/tmp/.X11-unix/X0" not in inner
    assert "/run/user/1000/wayland-1" not in inner
    assert "/home/u/.Xauthority" not in inner
    assert "/run/user/1000/pipewire-0" in inner
    out = _run_gamescope_shim(
        argv,
        tmp_path,
        {
            "DISPLAY": ":3",
            "XAUTHORITY": "/run/user/1000/xauth_ab cd",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0",
        },
    )
    triples = [out[i : i + 3] for i in range(len(out) - 2)]
    pairs = [out[i : i + 2] for i in range(len(out) - 1)]
    folder = pairs.index(["--dir", "/tmp/.X11-unix"])
    run_tmpfs = pairs.index(["--tmpfs", "/run"])
    x_bind = triples.index(["--ro-bind-try", "/tmp/.X11-unix/X3", "/tmp/.X11-unix/X3"])
    auth = triples.index(
        ["--ro-bind-try", "/run/user/1000/xauth_ab cd", "/run/user/1000/xauth_ab cd"]
    )
    wayland = triples.index(
        ["--ro-bind-try", "/run/user/1000/gamescope-0", "/run/user/1000/gamescope-0"]
    )
    assert folder < run_tmpfs < x_bind < auth < wayland < out.index("--")
    assert [a for a in out if a.startswith("/tmp/.X11-unix/")] == [
        "/tmp/.X11-unix/X3"
    ] * 2
    assert out[-1] == "game"
    # Every original argument is kept, in order.
    added = {
        x_bind,
        x_bind + 1,
        x_bind + 2,
        auth,
        auth + 1,
        auth + 2,
        wayland,
        wayland + 1,
        wayland + 2,
    }
    assert [a for i, a in enumerate(out) if i not in added] == inner[1:]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh shim")
def test_gamescope_network_wrapper_binds_x_socket_after_tmpfs(tmp_path):
    argv = sandbox.build_network_bwrap_argv(
        ["game", "a b"], cwd="/games", x11_sockets=("X0",), gamescope=True
    )
    out = _run_gamescope_shim(
        argv,
        tmp_path,
        {
            "DISPLAY": "unix:2.0",
            "XAUTHORITY": "/x",
            "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0",
            "XDG_RUNTIME_DIR": "/run/user/1",
        },
    )
    assert out == [
        "--unshare-user",
        "--unshare-net",
        "--dev-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp/.X11-unix",
        "--ro-bind-try",
        "/tmp/.X11-unix/X2",
        "/tmp/.X11-unix/X2",
        "--chdir",
        "/games",
        "--",
        "game",
        "a b",
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh shim")
def test_gamescope_shim_without_display_adds_nothing(tmp_path):
    argv = sandbox.build_network_bwrap_argv(["game"], cwd="/games", gamescope=True)
    out = _run_gamescope_shim(argv, tmp_path, {})
    assert out == [
        "--unshare-user",
        "--unshare-net",
        "--dev-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp/.X11-unix",
        "--chdir",
        "/games",
        "--",
        "game",
    ]


def _mangohud_config(pairs, home):
    return next(
        src for src, dst in pairs if dst == os.path.join(home, ".config", "MangoHud")
    )


def test_mangohud_mounts_default_and_custom_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "is_flatpak", lambda: False)
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    home = str(tmp_path / "home")
    pairs = sandbox.mangohud_mounts({}, home)
    for path in sandbox.MANGOHUD_SYS_CLASSES:
        assert (path, path) in pairs
    assert _mangohud_config(pairs, home) == os.path.realpath(
        os.path.join(home, ".config", "MangoHud")
    )
    custom = str(tmp_path / "xdg")
    pairs = sandbox.mangohud_mounts({"XDG_CONFIG_HOME": custom}, home)
    assert _mangohud_config(pairs, home) == os.path.realpath(
        os.path.join(custom, "MangoHud")
    )
    # HOST_XDG_CONFIG_HOME is only for Flatpak.
    pairs = sandbox.mangohud_mounts({"HOST_XDG_CONFIG_HOME": custom}, home)
    assert _mangohud_config(pairs, home) == os.path.realpath(
        os.path.join(home, ".config", "MangoHud")
    )


def test_mangohud_mounts_flatpak_host_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "is_flatpak", lambda: True)
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    home = str(tmp_path / "home")
    host = str(tmp_path / "host-config")
    pairs = sandbox.mangohud_mounts(
        {"HOST_XDG_CONFIG_HOME": host, "XDG_CONFIG_HOME": str(tmp_path / "app")}, home
    )
    assert _mangohud_config(pairs, home) == os.path.realpath(
        os.path.join(host, "MangoHud")
    )


@pytest.mark.parametrize("value", ["", "relative/config"])
def test_mangohud_mounts_ignore_relative_or_empty(monkeypatch, tmp_path, value):
    monkeypatch.setattr(sandbox, "is_flatpak", lambda: False)
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    home = str(tmp_path / "home")
    pairs = sandbox.mangohud_mounts(
        {
            "XDG_CONFIG_HOME": value,
            "MANGOHUD_CONFIGFILE": value,
            "MANGOHUD_PRESETSFILE": value,
        },
        home,
    )
    assert _mangohud_config(pairs, home) == os.path.realpath(
        os.path.join(home, ".config", "MangoHud")
    )
    assert len(pairs) == len(sandbox.MANGOHUD_SYS_CLASSES) + 1


@pytest.mark.skipif(os.name == "nt", reason="symlinks need Linux")
def test_mangohud_mounts_follow_symlinks(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "is_flatpak", lambda: False)
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    dotfiles = tmp_path / "dotfiles" / "MangoHud"
    dotfiles.mkdir(parents=True)
    (home / ".config" / "MangoHud").symlink_to(dotfiles)
    conf = tmp_path / "dotfiles" / "my.conf"
    conf.write_text("fps")
    link = tmp_path / "conf-link"
    link.symlink_to(conf)
    pairs = sandbox.mangohud_mounts({"MANGOHUD_CONFIGFILE": str(link)}, str(home))
    assert (str(dotfiles), str(home / ".config" / "MangoHud")) in pairs
    assert (str(conf), str(link)) in pairs


@pytest.mark.skipif(os.name == "nt", reason="symlinks need Linux")
def test_mangohud_mounts_refuse_symlink_to_home(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "is_flatpak", lambda: False)
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config" / "MangoHud").symlink_to(home)
    with pytest.raises(ValueError, match="MangoHud configuration") as error:
        _ = sandbox.mangohud_mounts({}, str(home))
    assert "whole home folder" in str(error.value)


def test_bwrap_mangohud_binds_follow_home_and_readonly():
    pairs = (
        ("/sys/class/drm", "/sys/class/drm"),
        ("/cfg/MangoHud", "/home/u/.config/MangoHud"),
    )
    argv = sandbox.build_bwrap_argv(
        ["true"],
        game_dir="/games",
        home="/home/u",
        sandbox_home="/s",
        cwd="/games",
        readonly_paths=("/runner",),
        mangohud=pairs,
    )
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    home_bind = triples.index(["--bind", "/s", "/home/u"])
    runner = triples.index(["--ro-bind", "/runner", "/runner"])
    drm = triples.index(["--ro-bind-try", "/sys/class/drm", "/sys/class/drm"])
    config = triples.index(
        ["--ro-bind-try", "/cfg/MangoHud", "/home/u/.config/MangoHud"]
    )
    assert home_bind < runner < drm < config
    plain = sandbox.build_bwrap_argv(
        ["true"], game_dir="/games", home="/home/u", sandbox_home="/s", cwd="/games"
    )
    assert not any(arg.startswith("/sys/class") for arg in plain)


def test_runner_overlay_supported_success_caches_and_uses_host_command(monkeypatch):
    monkeypatch.setattr(
        sandbox, "host_command", lambda cmd: ["flatpak-spawn", "--host", *cmd]
    )
    run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(sandbox.subprocess, "run", run)

    assert sandbox.runner_overlay_supported("/usr/bin/bwrap", "/opt/nwjs") is True
    assert sandbox.runner_overlay_supported("/usr/bin/bwrap", "/opt/nwjs") is True
    assert run.call_count == 1
    assert run.call_args.args[0] == [
        "flatpak-spawn",
        "--host",
        "/usr/bin/bwrap",
        *sandbox.BWRAP_NAMESPACE_OPTIONS,
        "--ro-bind",
        "/",
        "/",
        "--overlay-src",
        "/opt/nwjs",
        "--tmp-overlay",
        "/opt/nwjs",
        "true",
    ]
    assert run.call_args.kwargs["timeout"] == 10

    # A different root or bwrap path gets its own cache entry.
    assert sandbox.runner_overlay_supported("/usr/bin/bwrap", "/mnt/nwjs") is True
    assert sandbox.runner_overlay_supported("/alt/bwrap", "/opt/nwjs") is True
    assert run.call_count == 3


def test_runner_overlay_supported_failure_logs_once_and_caches(monkeypatch, capsys):
    run = Mock(
        return_value=Mock(
            returncode=1, stdout="", stderr="Unknown option --overlay-src\n"
        )
    )
    monkeypatch.setattr(sandbox.subprocess, "run", run)

    assert sandbox.runner_overlay_supported("/usr/bin/bwrap", "/opt/nwjs") is False
    first_log = capsys.readouterr().out
    assert "Runner overlay unavailable" in first_log
    assert "Unknown option --overlay-src" in first_log

    assert sandbox.runner_overlay_supported("/usr/bin/bwrap", "/opt/nwjs") is False
    assert run.call_count == 1
    assert capsys.readouterr().out == ""


def test_runner_overlay_supported_clears_on_sandbox_recheck(monkeypatch):
    monkeypatch.setattr(sandbox, "host_which", lambda _: "/usr/bin/bwrap")
    overlay_results = iter(
        [
            Mock(returncode=1, stdout="", stderr="overlay failed"),
            Mock(returncode=0, stdout="", stderr=""),
        ]
    )

    def fake_run(cmd, **_kwargs):
        if "--overlay-src" in cmd:
            return next(overlay_results)
        return Mock(returncode=0, stdout="bubblewrap 0.13.1", stderr="")

    run = Mock(side_effect=fake_run)
    monkeypatch.setattr(sandbox.subprocess, "run", run)

    assert sandbox.runner_overlay_supported("/usr/bin/bwrap", "/opt/nwjs") is False
    assert sandbox.runner_overlay_supported("/usr/bin/bwrap", "/opt/nwjs") is False
    assert sum(1 for call in run.call_args_list if "--overlay-src" in call.args[0]) == 1

    sandbox.get_sandbox_status(platform="linux", recheck=True)
    assert sandbox.runner_overlay_supported("/usr/bin/bwrap", "/opt/nwjs") is True
    assert sum(1 for call in run.call_args_list if "--overlay-src" in call.args[0]) == 2


def test_runner_root_mounts_overlay(monkeypatch, tmp_path):
    root = str(tmp_path / "nwjs")
    monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: True)
    assert sandbox.runner_root_mounts(root, bwrap="/usr/bin/bwrap") == [
        "--overlay-src",
        root,
        "--tmp-overlay",
        root,
    ]


@pytest.mark.skipif(os.name == "nt", reason="symlinks need Linux")
def test_runner_root_mounts_fallback(monkeypatch, tmp_path):
    root = tmp_path / "nwjs"
    v1 = root / "nwjs" / "0.40.0"
    v2 = root / "nwjs" / "0.86.0"
    (v1 / "locales").mkdir(parents=True)
    (v1 / "nw").write_text("binary")
    (v1 / "package.json").write_text("{}")
    (v1 / "www").symlink_to("/games/other/www")
    (v1 / "Game.exe").symlink_to("/games/other/Game.exe")
    v2.mkdir(parents=True)
    (v2 / "nw").write_text("binary")
    (v2 / "www").mkdir()  # even a real directory named www is left out
    (root / "nwjs" / "README.txt").write_text("ignore non-dir")
    (root / "nwjs" / "latest").symlink_to(v2)

    monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: False)
    args = sandbox.runner_root_mounts(str(root), bwrap="/usr/bin/bwrap")
    assert args == [
        "--ro-bind",
        str(root),
        str(root),
        "--tmpfs",
        str(v1),
        "--ro-bind",
        str(v1 / "locales"),
        str(v1 / "locales"),
        "--ro-bind",
        str(v1 / "nw"),
        str(v1 / "nw"),
        "--tmpfs",
        str(v2),
        "--ro-bind",
        str(v2 / "nw"),
        str(v2 / "nw"),
    ]


def test_runner_root_mounts_fallback_without_nwjs_dir(monkeypatch, tmp_path):
    root = tmp_path / "runner"
    root.mkdir()
    monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: False)
    assert sandbox.runner_root_mounts(str(root), bwrap="/usr/bin/bwrap") == [
        "--ro-bind",
        str(root),
        str(root),
    ]


def test_bwrap_runner_root_follows_readonly_paths(monkeypatch, tmp_path):
    runner = tmp_path / "nwjs"
    version = runner / "nwjs" / "0.86.0"
    version.mkdir(parents=True)
    (version / "nw").write_text("bin")
    home = str(tmp_path / "home")
    game_dir = str(tmp_path / "games")

    monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: True)
    argv = sandbox.build_bwrap_argv(
        ["true"],
        game_dir=game_dir,
        home=home,
        sandbox_home=str(tmp_path / "sandbox"),
        cwd=game_dir,
        readonly_paths=("/extra-ro",),
        runner_root=str(runner),
    )
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    quads = [argv[i : i + 4] for i in range(len(argv) - 3)]
    ro_idx = triples.index(["--ro-bind", "/extra-ro", "/extra-ro"])
    overlay_idx = quads.index(
        ["--overlay-src", str(runner), "--tmp-overlay", str(runner)]
    )
    game_idx = triples.index(["--bind", game_dir, game_dir])
    assert ro_idx < overlay_idx < game_idx

    monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: False)
    fallback_argv = sandbox.build_bwrap_argv(
        ["true"],
        game_dir=game_dir,
        home=home,
        sandbox_home=str(tmp_path / "sandbox"),
        cwd=game_dir,
        readonly_paths=("/extra-ro",),
        runner_root=str(runner),
    )
    fb_triples = [fallback_argv[i : i + 3] for i in range(len(fallback_argv) - 2)]
    fb_pairs = [fallback_argv[i : i + 2] for i in range(len(fallback_argv) - 1)]
    fb_ro = fb_triples.index(["--ro-bind", "/extra-ro", "/extra-ro"])
    fb_root = fb_triples.index(["--ro-bind", str(runner), str(runner)])
    fb_tmpfs = fb_pairs.index(["--tmpfs", str(version)])
    fb_nw = fb_triples.index(["--ro-bind", str(version / "nw"), str(version / "nw")])
    fb_game = fb_triples.index(["--bind", game_dir, game_dir])
    assert fb_ro < fb_root < fb_tmpfs < fb_nw < fb_game


@pytest.mark.parametrize("bad_root", ["/home/u", "/home", "/"])
def test_bwrap_runner_root_refuses_home_exposure(monkeypatch, bad_root):
    probe = Mock()
    monkeypatch.setattr(sandbox, "runner_overlay_supported", probe)
    with pytest.raises(ValueError, match="whole home folder"):
        _ = sandbox.build_bwrap_argv(
            ["true"],
            game_dir="/games",
            home="/home/u",
            sandbox_home="/sandbox",
            cwd="/games",
            runner_root=bad_root,
        )
    probe.assert_not_called()


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap runs only on Linux hosts")
@pytest.mark.parametrize("use_overlay", [True, False])
def test_bwrap_runner_root_integration(monkeypatch, tmp_path, use_overlay):
    import shlex
    import shutil

    bwrap = shutil.which("bwrap")
    unshare = shutil.which("unshare")
    if not bwrap or not unshare:
        pytest.skip("bubblewrap/user namespace tools unavailable")
    probe = subprocess.run([unshare, "-Ur", "true"], capture_output=True)
    if probe.returncode:
        pytest.skip("unprivileged user namespaces unavailable")

    runner_root = tmp_path / "runner"
    version_dir = runner_root / "nwjs" / "0.86.0"
    (version_dir / "locales").mkdir(parents=True)
    (version_dir / "locales" / "en-US.pak").write_text("pak-ok")
    nw = version_dir / "nw"
    nw.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        'printf "%s|%s|%s|%s\\n" '
        '"$(cat www/index.html)" '
        '"$(cat package.json)" '
        '"$(cat root.txt)" '
        '"$(cat locales/en-US.pak)"\n'
    )
    nw.chmod(0o755)
    (version_dir / "www").symlink_to("/nonexistent/previous-game/www")
    (version_dir / "package.json").write_text('{"main":"old"}')
    (version_dir / "stale-link").symlink_to("/nonexistent/stale")

    game = tmp_path / "game"
    (game / "www").mkdir(parents=True)
    (game / "www" / "index.html").write_text("selected-game")
    (game / "root.txt").write_text("root-ok")
    sandbox_home = tmp_path / "home"
    sandbox_home.mkdir()

    if use_overlay:
        if not sandbox.runner_overlay_supported(bwrap, str(runner_root)):
            pytest.skip("bubblewrap overlay unavailable on this host")
    else:
        monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: False)

    script = (
        f"set -e; cd {shlex.quote(str(version_dir))}; "
        'for f in *; do [ -L "$f" ] && rm -f "$f"; done; '
        f"rm -rf www && ln -s {shlex.quote(str(game / 'www'))} www; "
        f"ln -s {shlex.quote(str(game / 'root.txt'))} root.txt; "
        'printf \'{"main":"www/index.html"}\' > package.json; '
        "./nw"
    )
    argv = sandbox.build_bwrap_argv(
        ["sh", "-c", script],
        game_dir=str(game),
        home=os.path.expanduser("~"),
        sandbox_home=str(sandbox_home),
        cwd=str(game),
        runner_root=str(runner_root),
        bwrap=bwrap,
    )
    if use_overlay:
        assert "--tmp-overlay" in argv
    else:
        assert "--tmp-overlay" not in argv
        assert "--tmpfs" in argv

    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip()
        == 'selected-game|{"main":"www/index.html"}|root-ok|pak-ok'
    )

    # Host runner tree must be completely unchanged.
    assert (version_dir / "www").is_symlink()
    assert os.readlink(version_dir / "www") == "/nonexistent/previous-game/www"
    assert (version_dir / "package.json").read_text() == '{"main":"old"}'
    assert (version_dir / "stale-link").is_symlink()
    assert os.readlink(version_dir / "stale-link") == "/nonexistent/stale"
    assert not (version_dir / "root.txt").exists()
    assert not (version_dir / "root.txt").is_symlink()


def _runner_versions(tmp_path, times):
    root = tmp_path / "runner"
    for name, mtime in times.items():
        folder = root / "nwjs" / name
        folder.mkdir(parents=True)
        os.utime(folder, ns=(mtime, mtime))
    return root


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlink times")
def test_runner_fallback_nwjs_path_picks_newest_like_ls_t(tmp_path):
    root = _runner_versions(
        tmp_path, {"nwjs-v0.112.0": 1_000, "nwjs-v0.99.0": 2_000, "sdk": 500}
    )
    (root / "nwjs" / "link").symlink_to(root / "nwjs" / "sdk")
    os.utime(root / "nwjs" / "link", ns=(9_000, 9_000), follow_symlinks=False)

    assert sandbox.runner_fallback_nwjs_path(str(root), ["runner"], {}) == str(
        root / "nwjs" / "nwjs-v0.99.0"
    )


def test_runner_fallback_nwjs_path_breaks_ties_by_name(tmp_path):
    root = _runner_versions(tmp_path, {"b": 1_000, "a": 1_000})

    assert sandbox.runner_fallback_nwjs_path(str(root), [], {}) == str(
        root / "nwjs" / "a"
    )


@pytest.mark.parametrize(
    ("command", "environment"),
    [
        (["runner", "--nwjsversion", "0.40.0"], {}),
        (["runner", "--chooselatestnwjs"], {}),
        (["runner", "--nwjspath", "/x"], {}),
        (["runner"], {"NWJSPATH": "/user/choice"}),
    ],
)
def test_runner_fallback_nwjs_path_respects_user_choice(tmp_path, command, environment):
    root = _runner_versions(tmp_path, {"a": 1_000})

    assert sandbox.runner_fallback_nwjs_path(str(root), command, environment) == ""


def test_runner_fallback_nwjs_path_without_versions(tmp_path):
    assert sandbox.runner_fallback_nwjs_path(str(tmp_path), [], {}) == ""


@pytest.mark.parametrize("overlay", [True, False])
def test_bwrap_runner_root_pins_nwjs_folder_only_in_fallback(
    monkeypatch, tmp_path, overlay
):
    root = _runner_versions(tmp_path, {"old": 1_000, "new": 2_000})
    monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: overlay)

    argv = sandbox.build_bwrap_argv(
        ["runner", "--gamepath", "/games"],
        game_dir="/games",
        home=str(tmp_path / "home"),
        sandbox_home=str(tmp_path / "sandbox"),
        cwd="/games",
        runner_root=str(root),
    )

    pinned = ["--setenv", "NWJSPATH", str(root / "nwjs" / "new")]
    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    assert (pinned in triples) is not overlay


def test_bwrap_runner_root_binds_install_path_file_and_mkxpz_overlay(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    home = tmp_path / "home"
    custom_dir = tmp_path / "custom-rpgm"
    mkxpz_folder = custom_dir / "mkxp-z"
    mkxpz_folder.mkdir(parents=True)
    runner_root = custom_dir / "nwjs" / "nwjs"
    runner_root.mkdir(parents=True)
    cfg = home / ".config" / "defrpgmakerlinuxpath.txt"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f"{custom_dir}/\n", encoding="utf-8")

    monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: True)
    argv = sandbox.build_bwrap_argv(
        ["runner", "--gamepath", "/games"],
        game_dir="/games",
        home=str(home),
        sandbox_home=str(tmp_path / "sandbox"),
        cwd="/games",
        runner_root=str(runner_root),
    )

    triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
    quads = [argv[i : i + 4] for i in range(len(argv) - 3)]
    home_bind_idx = triples.index(["--bind", str(tmp_path / "sandbox"), str(home)])
    cfg_bind_idx = triples.index(["--ro-bind", str(cfg), str(cfg)])
    assert home_bind_idx < cfg_bind_idx
    assert [
        "--overlay-src",
        str(mkxpz_folder),
        "--tmp-overlay",
        str(mkxpz_folder),
    ] in quads


def test_bwrap_runner_root_skips_mkxpz_when_overlay_unsupported_or_missing(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    home = tmp_path / "home"
    runner_root = home / "desktopapps" / "nwjs" / "nwjs"
    runner_root.mkdir(parents=True)

    # Folder missing: no mkxp-z overlay mount.
    monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: True)
    argv_missing = sandbox.build_bwrap_argv(
        ["runner"],
        game_dir="/games",
        home=str(home),
        sandbox_home=str(tmp_path / "sandbox"),
        cwd="/games",
        runner_root=str(runner_root),
    )
    mkxpz_folder = home / "desktopapps" / "mkxp-z"
    assert str(mkxpz_folder) not in argv_missing

    # Folder present, overlay unsupported: no mkxp-z mount at all.
    mkxpz_folder.mkdir(parents=True)
    monkeypatch.setattr(
        sandbox,
        "runner_overlay_supported",
        lambda _b, root: root != str(mkxpz_folder),
    )
    argv_no_overlay = sandbox.build_bwrap_argv(
        ["runner"],
        game_dir="/games",
        home=str(home),
        sandbox_home=str(tmp_path / "sandbox"),
        cwd="/games",
        runner_root=str(runner_root),
    )
    assert str(mkxpz_folder) not in argv_no_overlay


def test_bwrap_runner_root_refuses_install_dir_equal_to_home(monkeypatch, tmp_path):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    home = tmp_path / "home"
    runner_root = home / "nwjs" / "nwjs"
    runner_root.mkdir(parents=True)
    cfg = home / ".config" / "defrpgmakerlinuxpath.txt"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f"{home}\n", encoding="utf-8")

    monkeypatch.setattr(sandbox, "runner_overlay_supported", lambda _b, _r: True)
    with pytest.raises(ValueError, match="whole home folder"):
        _ = sandbox.build_bwrap_argv(
            ["runner"],
            game_dir="/games",
            home=str(home),
            sandbox_home=str(tmp_path / "sandbox"),
            cwd="/games",
            runner_root=str(runner_root),
        )
