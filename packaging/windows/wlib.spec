# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH).resolve().parents[1]
extension_root = Path(
    os.environ.get("WLIB_EXTENSION_STAGE", project_root / "extension")
).resolve()
version_file = Path(os.environ["WLIB_VERSION_FILE"]).resolve()
icon_file = project_root / "packaging" / "windows" / "wlib.ico"

datas = [
    (str(project_root / "ui" / "dist"), "ui/dist"),
    (str(extension_root), "extension"),
    (str(project_root / "wlib.png"), "."),
]
datas += collect_data_files("certifi")
datas += collect_data_files("playwright")

hiddenimports = collect_submodules("playwright")
hiddenimports += collect_submodules("webview")
hiddenimports += [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
]

analysis = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "gi",
        "PyQt5",
        "PySide2",
        "PySide6",
        "tkinter",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="wLib",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon_file),
    version=str(version_file),
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="wLib",
)
