# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os

import pytest

from core.api import Api
from core.database import init_db


@pytest.fixture(autouse=True)
def _windows_api(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "windows-api.db"))
    monkeypatch.setattr("core.api.is_linux", lambda: False)
    monkeypatch.setattr("core.api.is_windows", lambda: True)
    monkeypatch.setattr("core.host_platform.sys.platform", "win32")
    init_db()


def test_platform_capabilities_report_native_windows_support():
    capabilities = Api().get_platform_capabilities()

    assert capabilities["platform"] == "windows"
    assert capabilities["native_windows_launch"] is True
    assert capabilities["wine_proton"] is False
    assert capabilities["runtime_installers"] is False
    assert capabilities["launch_modes"] == ["auto"]


def test_linux_only_runtime_operations_are_rejected(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail("network must not be used"),
    )

    api = Api()
    for result in (
        api.download_proton_ge(),
        api.download_cheat_engine(),
        api.install_rpgmaker_dependencies(),
        api.install_rpgmaker_rtp(),
    ):
        assert result["success"] is False
        assert result["error_code"] == "unsupported_platform"


def test_windows_system_handler_is_used(monkeypatch, tmp_path):
    target = tmp_path / "Game.exe"
    target.write_bytes(b"MZ")
    opened: list[str] = []
    monkeypatch.setattr(
        "core.api.open_windows_system_target",
        lambda path: (opened.append(path) is None, ""),
    )

    result = Api().open_folder(str(target))

    assert result["success"] is True
    assert opened == [str(target)]


def test_windows_game_picker_includes_every_supported_target(monkeypatch):
    captured_file_types: tuple[str, ...] = ()

    def fake_dialog(
        dialog_kind: str, directory: str = "", file_types: tuple[str, ...] = ()
    ) -> str:
        nonlocal captured_file_types
        assert dialog_kind == "file"
        captured_file_types = file_types
        return ""

    api = Api()
    monkeypatch.setattr(api, "_browse_qt_dialog", fake_dialog)

    assert api.browse_file() == ""
    game_filter = captured_file_types[0]
    for suffix in ("*.exe", "*.bat", "*.cmd", "*.jar", "*.html", "*.htm"):
        assert suffix in game_filter


def test_windows_renpy_save_discovery_uses_appdata(monkeypatch, tmp_path):
    appdata = tmp_path / "Roaming"
    save_dir = appdata / "RenPy" / "MyGame-12345"
    save_dir.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    results = Api().find_save_files(
        exe_path=str(tmp_path / "MyGame.exe"),
        title="My Game",
    )

    assert any(entry["path"] == str(save_dir) for entry in results)


@pytest.mark.parametrize("windows", [True, False])
def test_save_discovery_ignores_short_title_words(monkeypatch, tmp_path, windows):
    prefix = tmp_path / "prefix"
    appdata = (
        tmp_path / "Roaming"
        if windows
        else prefix / "drive_c" / "users" / "player" / "AppData" / "Roaming"
    )
    for name in ("TheTool", "Candy", "CatCache", "MOON-Saves", "run-data"):
        (appdata / name).mkdir(parents=True)
    monkeypatch.setattr("core.api.is_windows", lambda: windows)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    results = Api().find_save_files(
        exe_path=str(tmp_path / "run.exe"),
        title="The Cat and Moon",
        custom_prefix=str(prefix),
    )

    assert {os.path.normpath(entry["path"]) for entry in results} == {
        str(appdata / "MOON-Saves"),
        str(appdata / "run-data"),
    }
