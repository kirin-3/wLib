# SPDX-License-Identifier: GPL-3.0-or-later
"""Whether the computer runs on battery power (design D10), without a dependency.

`on_battery()` returns True, False, or None when the state cannot be read.
The download queue treats None as mains power.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

POWER_SUPPLY_DIR = "/sys/class/power_supply"
_MAINS_TYPES = ("Mains", "USB")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def linux_on_battery(root: str = POWER_SUPPLY_DIR) -> bool | None:
    """On battery when no mains or USB supply is online and a battery discharges."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    mains_known = False
    battery_known = False
    discharging = False
    for name in names:
        supply = Path(root) / name
        kind = _read(supply / "type")
        if kind in _MAINS_TYPES:
            online = _read(supply / "online")
            if online == "1":
                return False
            if online == "0":
                mains_known = True
        elif kind == "Battery":
            # Peripheral batteries (a mouse, a controller) report scope "Device".
            if _read(supply / "scope") == "Device":
                continue
            status = _read(supply / "status")
            if status:
                battery_known = True
            if status == "Discharging":
                discharging = True
    if discharging:
        return True
    if battery_known or mains_known:
        return False
    return None


def windows_on_battery() -> bool | None:
    """`GetSystemPowerStatus`: ACLineStatus 0 is battery, 1 is mains, 255 unknown."""
    try:
        import ctypes
        from ctypes import wintypes

        class SystemPowerStatus(ctypes.Structure):
            _fields_ = [  # pyright: ignore[reportUnannotatedClassAttribute]
                ("ACLineStatus", wintypes.BYTE),
                ("BatteryFlag", wintypes.BYTE),
                ("BatteryLifePercent", wintypes.BYTE),
                ("SystemStatusFlag", wintypes.BYTE),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD),
            ]

        status = SystemPowerStatus()
        # Windows-only stdlib symbols are absent from Linux type-checking stubs.
        load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
        get_status = load_dll("kernel32").GetSystemPowerStatus
        if not cast(int, get_status(ctypes.byref(status))):
            return None
        line = cast(int, status.ACLineStatus) & 0xFF
    except (OSError, AttributeError, ImportError, ValueError):
        return None
    if line == 0:
        return True
    if line == 1:
        return False
    return None


def on_battery() -> bool | None:
    platform = sys.platform
    if platform.startswith("linux"):
        return linux_on_battery()
    if platform == "win32":
        return windows_on_battery()
    return None
