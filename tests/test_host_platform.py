# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import sys
from unittest.mock import MagicMock, patch

import pytest

from core.host_platform import (
    get_cache_root,
    get_data_dir,
    get_platform_capabilities,
    get_legacy_browser_dirs,
)


def test_windows_paths_use_local_app_data(tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    env = {"LOCALAPPDATA": str(local_app_data)}

    assert get_data_dir(platform="win32", environ=env) == os.path.join(
        str(local_app_data), "wLib"
    )
    assert get_cache_root(platform="win32", environ=env) == os.path.join(
        str(local_app_data), "wLib", "cache"
    )


def test_windows_paths_fall_back_to_user_home(tmp_path):
    assert get_data_dir(
        platform="win32", environ={}, home=str(tmp_path)
    ) == os.path.join(str(tmp_path), "AppData", "Local", "wLib")


def test_linux_paths_preserve_xdg_defaults(tmp_path):
    data_home = tmp_path / "data"
    cache_home = tmp_path / "cache"
    env = {"XDG_DATA_HOME": str(data_home), "XDG_CACHE_HOME": str(cache_home)}

    # Isolated home: a real ~/.local/share/wLib would win as the legacy library.
    assert get_data_dir(
        platform="linux", environ=env, home=str(tmp_path / "home")
    ) == os.path.join(str(data_home), "wLib")
    assert get_cache_root(platform="linux", environ=env) == str(cache_home)


def test_linux_paths_preserve_home_fallbacks(tmp_path):
    assert get_data_dir(
        platform="linux", environ={}, home=str(tmp_path)
    ) == os.path.join(str(tmp_path), ".local", "share", "wLib")


def test_linux_xdg_data_home_keeps_existing_legacy_library(tmp_path):
    home = tmp_path / "home"
    legacy_dir = home / ".local" / "share" / "wLib"
    legacy_dir.mkdir(parents=True)
    xdg_home = tmp_path / "xdg-data"
    env = {"XDG_DATA_HOME": str(xdg_home)}

    assert get_data_dir(platform="linux", environ=env, home=str(home)) == str(
        legacy_dir
    )

    (xdg_home / "wLib").mkdir(parents=True)
    assert get_data_dir(platform="linux", environ=env, home=str(home)) == str(
        xdg_home / "wLib"
    )


def test_explicit_path_overrides_isolate_smoke_runtime(tmp_path):
    data_dir = tmp_path / "isolated-data"
    cache_dir = tmp_path / "isolated-cache"
    env = {"WLIB_DATA_DIR": str(data_dir), "WLIB_CACHE_DIR": str(cache_dir)}

    assert get_data_dir(platform="linux", environ=env) == str(data_dir)
    assert get_cache_root(platform="linux", environ=env) == str(cache_dir)


def test_legacy_browser_dirs_only_include_app_owned_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WLIB_PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "shared"))
    assert get_legacy_browser_dirs() == [
        str(tmp_path / "data" / "browser_session"),
        str(tmp_path / "data" / "playwright"),
    ]
    assert "playwright_browsers_path" not in get_platform_capabilities()


def test_platform_capabilities_hide_linux_mutations_on_windows(monkeypatch, tmp_path):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    capabilities = get_platform_capabilities("win32")

    assert capabilities["platform"] == "windows"
    assert capabilities["native_windows_launch"] is True
    assert capabilities["wine_proton"] is False
    assert capabilities["runtime_installers"] is False
    assert capabilities["rpgmaker_linux"] is False
    assert capabilities["launch_modes"] == ["auto"]
    assert "custom" in get_platform_capabilities("linux")["launch_modes"]


def test_open_windows_system_target_platform_parameter():
    from core.host_platform import open_windows_system_target

    opened, error = open_windows_system_target("https://example.com", platform="linux")
    assert opened is False
    assert "Windows system associations are unavailable" in error


def _simulate_flatpak(monkeypatch, enabled=True):
    import core.host_platform as host_platform

    real_isfile = os.path.isfile

    def _isfile(path):
        if path == host_platform.FLATPAK_INFO_PATH:
            return enabled
        try:
            return real_isfile(path)
        except OSError:
            return False

    monkeypatch.setattr(os.path, "isfile", _isfile)
    monkeypatch.setattr(host_platform.os.path, "isfile", _isfile)


def test_is_flatpak_false_outside_sandbox(monkeypatch):
    from core.host_platform import is_flatpak

    _simulate_flatpak(monkeypatch, enabled=False)
    assert is_flatpak() is False


def test_is_flatpak_true_inside_sandbox(monkeypatch):
    from core.host_platform import is_flatpak

    _simulate_flatpak(monkeypatch, enabled=True)
    assert is_flatpak() is True


def test_host_command_identity_outside_flatpak(monkeypatch):
    from core.host_platform import host_command

    _simulate_flatpak(monkeypatch, enabled=False)
    monkeypatch.setenv("WINEPREFIX", "/prefix")
    cmd = ["wine", "/games/game.exe"]
    assert host_command(cmd, env={"WINEPREFIX": "/prefix"}, cwd="/games") == cmd
    assert host_command(cmd, supervise=True) == cmd


def test_host_command_flatpak_argv_shape(monkeypatch):
    from core.host_platform import host_command

    _simulate_flatpak(monkeypatch, enabled=True)
    monkeypatch.delenv("WINEPREFIX", raising=False)
    argv = host_command(
        ["wine", "/games/game.exe"],
        env={"WINEPREFIX": "/prefix"},
        cwd="/games",
    )
    assert argv[:4] == ["flatpak-spawn", "--host", "--watch-bus", "--directory=/games"]
    assert "--env=WINEPREFIX=/prefix" in argv
    assert argv[-2:] == ["wine", "/games/game.exe"]
    assert argv[argv.index("--") + 1] == "wine"


def test_host_command_forwards_env_diff_only(monkeypatch):
    from core.host_platform import host_command

    _simulate_flatpak(monkeypatch, enabled=True)
    monkeypatch.setenv("KEEP", "same")
    monkeypatch.setenv("CHANGED", "old")
    argv = host_command(
        ["winetricks", "-q", "corefonts"],
        env={
            "KEEP": "same",
            "CHANGED": "new",
            "WINEPREFIX": "/prefix",
            "LD_LIBRARY_PATH": "/sandbox/lib",
            "QT_QPA_PLATFORM": "xcb",
            "PYTHONHOME": "/sandbox/python",
            "FLATPAK_ID": "io.github.kirin_3.wLib",
            "QTWEBENGINE_DISABLE_SANDBOX": "1",
        },
    )
    assert "--env=KEEP=same" not in argv
    assert "--env=CHANGED=new" in argv
    assert "--env=WINEPREFIX=/prefix" in argv
    for blocked in (
        "LD_LIBRARY_PATH",
        "QT_QPA_PLATFORM",
        "PYTHONHOME",
        "FLATPAK_ID",
        "QTWEBENGINE_DISABLE_SANDBOX",
    ):
        assert not any(part.startswith(f"--env={blocked}=") for part in argv), blocked


def test_host_command_supervisor_wrapping(monkeypatch):
    from core.host_platform import host_command

    _simulate_flatpak(monkeypatch, enabled=True)
    monkeypatch.delenv("WINEPREFIX", raising=False)
    argv = host_command(
        ["wine", "/games/game.exe"],
        env={"WINEPREFIX": "/prefix"},
        supervise=True,
    )
    assert argv[:3] == ["flatpak-spawn", "--host", "--watch-bus"]
    sep = argv.index("--")
    inner = argv[sep + 1 :]
    assert inner[0:4] == ["sh", "-c", inner[2], "sh"]
    assert "setsid" in inner[2]
    assert "kill -TERM" in inner[2]
    assert inner[4:] == ["wine", "/games/game.exe"]


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("setsid") is None,
    reason="needs a POSIX sh and util-linux setsid",
)
def test_host_supervisor_script_tracks_group_and_stops_it():
    import signal
    import subprocess
    import time

    from core.host_platform import _HOST_SUPERVISOR_SCRIPT

    def supervise(*cmd: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(["sh", "-c", _HOST_SUPERVISOR_SCRIPT, "sh", *cmd])

    # Launcher stub exits at once; its child keeps the group alive.
    start = time.monotonic()
    proc = supervise("sh", "-c", "sleep 2 & exit 0")
    assert proc.wait(timeout=10) == 0
    assert time.monotonic() - start >= 1.5

    # SIGTERM stops the group, escalating to SIGKILL for children ignoring TERM.
    proc = supervise("sh", "-c", "trap '' TERM; sleep 60")
    time.sleep(1.5)
    proc.send_signal(signal.SIGTERM)
    _ = proc.wait(timeout=10)


def test_host_which_outside_flatpak_uses_path(monkeypatch):
    import core.host_platform as host_platform

    _simulate_flatpak(monkeypatch, enabled=False)
    monkeypatch.setattr(host_platform.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert host_platform.host_which("wine") == "/usr/bin/wine"


def test_host_which_inside_flatpak_hit_and_miss(monkeypatch):
    import core.host_platform as host_platform

    _simulate_flatpak(monkeypatch, enabled=True)

    hit = MagicMock(returncode=0, stdout="/usr/bin/wine\n")
    with patch("subprocess.run", return_value=hit) as run:
        assert host_platform.host_which("wine") == "/usr/bin/wine"
        assert run.call_args.args[0][:3] == ["flatpak-spawn", "--host", "sh"]

    miss = MagicMock(returncode=1, stdout="")
    with patch("subprocess.run", return_value=miss):
        assert host_platform.host_which("missing-tool") is None

    with patch("subprocess.run", side_effect=OSError("no spawn")):
        assert host_platform.host_which("wine") is None
