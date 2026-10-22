# SPDX-License-Identifier: GPL-3.0-or-later
import json
import sys
from types import SimpleNamespace

import pytest

from core import database, diagnostics


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()
    monkeypatch.setattr(diagnostics, "_stages", {})


@pytest.mark.parametrize(
    "flatpak,appimage,frozen,windows,root,expected",
    [
        (True, True, True, False, "/opt/wlib", "Flatpak"),
        (False, True, True, False, "/tmp", "AppImage"),
        (False, False, False, False, "/tmp", "Source"),
        (False, False, True, True, "/tmp", "Windows build"),
        (False, False, True, False, "/opt/wlib/_internal", "Native package"),
        (False, False, True, False, "/tmp", "tar.gz"),
    ],
)
def test_packaging(monkeypatch, flatpak, appimage, frozen, windows, root, expected):
    monkeypatch.setattr(diagnostics, "is_flatpak", lambda: flatpak)
    monkeypatch.setattr(diagnostics, "is_windows", lambda: windows)
    monkeypatch.setenv("APPIMAGE", "app" if appimage else "")
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", root, raising=False)
    assert diagnostics.packaging_label() == expected


def test_basic_counts_sizes_and_stage_copies(monkeypatch):
    database.add_game("Private title sentinel", "/secret.exe")
    details = {"gpu": {"vendor": "test"}}
    diagnostics.record_renderer_stage("startup", details)
    details["gpu"]["vendor"] = "changed"
    snapshot = diagnostics.get_renderer_stages()
    assert snapshot["startup"]["gpu"]["vendor"] == "test"
    assert "runtime" not in snapshot
    monkeypatch.setattr(diagnostics.os.path, "getsize", lambda path: 123)
    monkeypatch.setattr(
        diagnostics.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(ImportError()),
    )
    basic = diagnostics.collect_basic("1.2.3", "4.5.6")
    assert basic["game_count"] == 1
    assert basic["database_bytes"] == 246
    assert basic["app_version"] == "1.2.3"
    assert basic["qt"] is None
    assert "Private title sentinel" not in diagnostics.build_report(basic)
    json.dumps(basic)


@pytest.mark.parametrize("windows", [True, False])
def test_basic_os_and_qt_versions(monkeypatch, windows):
    monkeypatch.setattr(diagnostics, "is_windows", lambda: windows)
    monkeypatch.setattr(diagnostics, "is_linux", lambda: not windows)
    monkeypatch.setattr(
        diagnostics.platform, "win32_ver", lambda: ("11", "10.0", "", "")
    )
    monkeypatch.setattr(
        diagnostics.platform,
        "freedesktop_os_release",
        lambda: {"PRETTY_NAME": "Test Linux"},
    )
    monkeypatch.setattr(
        diagnostics.importlib,
        "import_module",
        lambda name: SimpleNamespace(
            qVersion=lambda: "6.9", qWebEngineVersion=lambda: "6.8"
        ),
    )
    basic = diagnostics.collect_basic("1", "2")
    assert basic["os"] == ("Windows 11 10.0" if windows else "Test Linux")
    assert basic["qt"] == "6.9"
    assert basic["webengine"] == "6.8"


def test_os_release_falls_back_and_missing_database_sizes(monkeypatch):
    monkeypatch.setattr(diagnostics, "is_linux", lambda: True)
    monkeypatch.setattr(
        diagnostics.platform,
        "freedesktop_os_release",
        lambda: (_ for _ in ()).throw(OSError()),
    )
    monkeypatch.setattr(diagnostics.platform, "platform", lambda: "fallback OS")
    monkeypatch.setattr(
        diagnostics.os.path,
        "getsize",
        lambda path: (_ for _ in ()).throw(FileNotFoundError()),
    )
    basic = diagnostics.collect_basic("1", "2")
    assert basic["os"] == "fallback OS"
    assert basic["database_bytes"] == 0
    assert basic["game_count"] == 0


@pytest.mark.parametrize("linux", [True, False])
def test_tools(monkeypatch, linux):
    monkeypatch.setattr(diagnostics, "is_linux", lambda: linux)
    monkeypatch.setattr(diagnostics, "host_which", lambda name: None)
    calls = []

    def runners():
        calls.append(True)
        return {"runners": []}

    tools = diagnostics.collect_tools(runners, lambda: {"reachable": False})
    assert tools["7zz"] is None
    assert ("wine" in tools) == linux
    assert bool(calls) == linux
    assert tools["extension"] == {"reachable": False}


@pytest.mark.parametrize(
    "windows,home,user,text,expected",
    [
        (
            False,
            "/home/kirin",
            "kirin",
            "/home/kirin/.local/share/wLib",
            "~/.local/share/wLib",
        ),
        (
            False,
            "/home/kirin",
            "kirin",
            "/run/media/kirin/tools/7zz",
            "/run/media/<user>/tools/7zz",
        ),
        (
            True,
            r"C:\Users\kirin",
            "kirin",
            r"c:\USERS\KIRIN\AppData\Local\wLib",
            r"~\AppData\Local\wLib",
        ),
        (True, r"C:\Users\kirin", "kirin", "C:/Users/Kirin/AppData", "~/AppData"),
        (
            False,
            "/home/ab",
            "ab",
            "/opt/ab/tool /home/abc/tool /home/ab/data",
            "/opt/ab/tool /home/abc/tool ~/data",
        ),
    ],
)
def test_redaction(monkeypatch, windows, home, user, text, expected):
    monkeypatch.setattr(diagnostics, "is_windows", lambda: windows)
    monkeypatch.setattr(diagnostics.os.path, "expanduser", lambda path: home)
    monkeypatch.setattr(diagnostics.getpass, "getuser", lambda: user)
    assert diagnostics.redact_paths(text) == expected


def test_report_marks_missing_tools_and_empty_stages():
    report = diagnostics.build_report(
        {"renderer_stages": {}, "game_count": 3},
        {"7zz": None, "bsdtar": "/usr/bin/bsdtar", "runners": []},
    )
    assert "renderer_stages: Not available" in report
    assert "  7zz: Not found" in report
    assert "  runners: Not found" in report
    assert "  bsdtar: /usr/bin/bsdtar" in report
