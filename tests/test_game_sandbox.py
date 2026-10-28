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
    monkeypatch.setattr(sandbox, "get_setting", lambda _: "")
    yield
    sandbox._probe_cache.clear()


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
        ["--ro-bind-try", "/lib", "/lib"],
    ]:
        assert triple in triples
    assert argv.index("--tmpfs") < argv.index("/tmp/.X11-unix")
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


@pytest.mark.skipif(sys.platform == "win32", reason="bwrap runs only on Linux hosts")
@pytest.mark.parametrize(
    "executable,folder",
    [
        ("/opt/wine-ge/bin/wine", "/opt/wine-ge"),
        ("/home/u/.local/share/wLib/proton/GE/proton", "/home/u/.local/share/wLib/proton/GE"),
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
    monkeypatch.setattr(sandbox,'get_host_name',lambda *args:'linux')
    monkeypatch.setattr(sandbox,'host_which',lambda name:'/usr/bin/'+name if name in ('pacman','pkexec') else None)
    info=sandbox.get_sandbox_install_info()
    assert info['command']=='sudo pacman -S bubblewrap'
    assert info['pkexec_available'] and info['can_install']
    run=Mock(return_value=Mock(returncode=126,stderr='Authentication cancelled',stdout=''))
    monkeypatch.setattr(sandbox.subprocess,'run',run)
    monkeypatch.setattr(sandbox,'host_command',lambda cmd:['flatpak-spawn','--host',*cmd])
    with pytest.raises(RuntimeError,match='cancelled'):
        sandbox.install_sandbox_tool('bwrap')
    assert run.call_args.args[0]==['flatpak-spawn','--host','/usr/bin/pkexec','/usr/bin/pacman','-S','--noconfirm','bubblewrap']


@pytest.mark.parametrize('host,tool',[('linux','sandboxie'),('windows','bwrap')])
def test_installers_reject_wrong_host_before_starting(monkeypatch,host,tool):
    monkeypatch.setattr(sandbox,'get_host_name',lambda *args:host)
    command=Mock()
    monkeypatch.setattr(sandbox,'_install_command',command)
    with pytest.raises(ValueError,match='unsupported_platform'):
        sandbox.install_sandbox_tool(tool)
    command.assert_not_called()


@pytest.mark.parametrize('has_winget',[True,False])
def test_windows_install_routes(monkeypatch,has_winget):
    monkeypatch.setattr(sandbox,'get_host_name',lambda *args:'windows')
    monkeypatch.setattr(sandbox,'host_which',lambda _: 'winget.exe' if has_winget else None)
    command=Mock()
    download=Mock()
    monkeypatch.setattr(sandbox,'_install_command',command)
    monkeypatch.setattr(sandbox,'_download_sandboxie_installer',download)
    sandbox.install_sandbox_tool('sandboxie')
    if has_winget:
        assert command.call_args.args[0][:5]==['winget.exe','install','--id','Sandboxie.Plus','-e']
        download.assert_not_called()
    else:
        download.assert_called_once()
        command.assert_not_called()


@pytest.mark.parametrize('valid_signature',[False,True])
def test_direct_installer_verification_and_elevation_cancel(monkeypatch,tmp_path,valid_signature):
    import io
    import json
    import tempfile
    import urllib.request
    import platform
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    monkeypatch.setattr(platform,'machine',lambda:'AMD64')
    release={'assets':[{'name':'Sandboxie-Plus-x64-v1.exe','browser_download_url':'https://github.com/sandboxie-plus/Sandboxie/releases/download/v1/installer.exe'}]}
    def response(url,**kwargs):
        return io.BytesIO(json.dumps(release).encode() if not isinstance(url,str) else b'installer')
    monkeypatch.setattr(urllib.request,'urlopen',response)
    signature={'Status':'Valid' if valid_signature else 'NotSigned','Publisher':'CN=NOVASYNC LABS PTE. LTD., O=NOVASYNC LABS PTE. LTD.'}
    monkeypatch.setattr(sandbox.subprocess,'run',Mock(return_value=Mock(returncode=0,stdout=json.dumps(signature),stderr='')))
    install=Mock(side_effect=RuntimeError('Elevation cancelled'))
    monkeypatch.setattr(sandbox,'_install_command',install)
    with pytest.raises(RuntimeError,match='cancelled' if valid_signature else 'signature'):
        sandbox._download_sandboxie_installer()
    assert not list(tmp_path.iterdir())
    assert install.call_count==int(valid_signature)


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
