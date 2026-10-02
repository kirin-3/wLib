# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PACKAGING = REPOSITORY_ROOT / "packaging" / "windows"


def test_windows_resource_and_build_inputs_exist():
    required = (
        REPOSITORY_ROOT / "requirements-windows.txt",
        WINDOWS_PACKAGING / "wlib.spec",
        WINDOWS_PACKAGING / "wLib.wxs",
        WINDOWS_PACKAGING / "wlib.ico",
        REPOSITORY_ROOT / "scripts" / "build-windows.ps1",
    )

    assert all(path.is_file() for path in required)
    assert (WINDOWS_PACKAGING / "wlib.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_pyinstaller_spec_contains_required_windows_payload():
    spec = (WINDOWS_PACKAGING / "wlib.spec").read_text(encoding="utf-8")

    for required_text in (
        'name="wLib"',
        'console=False',
        '"ui/dist"',
        '"extension"',
        'collect_data_files("certifi")',
        'collect_data_files("playwright")',
        '"PyQt6.QtWebEngineWidgets"',
        '"gi"',
        '"PySide6"',
    ):
        assert required_text in spec


def test_msi_metadata_is_per_user_x64_and_data_safe():
    wxs = (WINDOWS_PACKAGING / "wLib.wxs").read_text(encoding="utf-8")
    build_script = (
        REPOSITORY_ROOT / "scripts" / "build-windows.ps1"
    ).read_text(encoding="utf-8")

    assert 'Scope="perUser"' in wxs
    assert 'Id="PerUserProgramFilesFolder"' in wxs
    assert 'UpgradeCode="ECB1D2B7-1B92-470A-BF78-6E18825C99B8"' in wxs
    assert 'INSTALLDESKTOPSHORTCUT = 1' in wxs
    assert 'Property="INSTALLDESKTOPSHORTCUT" CheckBoxValue="1"' in wxs
    assert '<Property Id="INSTALLDESKTOPSHORTCUT" Secure="yes" />' in wxs
    assert 'Value="LaunchWLib"' in wxs
    assert "LOCALAPPDATA\\wLib" not in wxs
    assert "-ext WixToolset.UI.wixext" in build_script
    assert '-arch x64' in build_script
    assert 'windows-x64-portable.zip' in build_script
    assert 'windows-x64.msi' in build_script
    assert 'SHA256SUMS.txt' in build_script
    assert "Compress-ArchiveWithRetry" in build_script


def test_windows_build_rejects_four_part_msi_versions():
    build_script = (
        REPOSITORY_ROOT / "scripts" / "build-windows.ps1"
    ).read_text(encoding="utf-8")

    assert "Version must contain exactly three numeric parts" in build_script
    assert "(?:\\.[0-9]+)?$" not in build_script


def test_source_smoke_mode_uses_isolated_data_directory(tmp_path):
    smoke_data = tmp_path / "data"
    env = os.environ.copy()
    env["WLIB_DATA_DIR"] = str(smoke_data)
    process = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "main.py"), "--smoke-test"],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout.strip().splitlines()[-1])
    assert payload["success"] is True
    assert payload["data_dir"] == str(smoke_data)
    assert (smoke_data / "wlib.db").is_file()
