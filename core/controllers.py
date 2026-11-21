# SPDX-License-Identifier: GPL-3.0-or-later
"""Detect controller presence without starting Chromium's gamepad polling."""

from __future__ import annotations

import ctypes
import os
import select
import struct
import sys
import threading
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import cast, final

from core import steam_env

_SYS_INPUT = Path("/sys/class/input")
_UDEV_DATA = Path("/run/udev/data")
_DEV_INPUT = Path("/dev/input")
_IN_CREATE_DELETE = 0x100 | 0x200
# If the watched directory disappears, switch to the focus-driven fallback.
_IN_WATCH_LOST = 0x400 | 0x800 | 0x2000 | 0x8000
_INOTIFY_EVENT = struct.Struct("iIII")
_DEVICE_SETTLE_SECONDS = 0.5
_LINUX_RECHECK_SECONDS = 30
_WINDOWS_RECHECK_SECONDS = 15


_BTN_LEFT = 0x110
_BTN_JOYSTICK_FIRST = 0x120  # BTN_JOYSTICK and BTN_GAMEPAD fill 0x120-0x13f.


def _udev_joystick(entry: Path, udev_root: Path) -> bool | None:
    """udev's joystick mark for one event device, or None when udev has no record of it."""
    try:
        device = (entry / "dev").read_text().strip()
        lines = (udev_root / f"c{device}").read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return None
    return "E:ID_INPUT_JOYSTICK=1" in lines


def _capabilities_joystick(entry: Path) -> bool:
    try:
        words = (entry / "device/capabilities/key").read_text().split()
    except FileNotFoundError:
        # The key capability is optional and devices can disappear mid-scan.
        return False
    word_bits = struct.calcsize("l") * 8
    bitmap = 0
    for word in words:
        bitmap = (bitmap << word_bits) | int(word, 16)
    # Gaming mice and headset receivers often advertise joystick buttons too;
    # a left mouse button marks them as pointers, as udev does.
    return (
        bool((bitmap >> _BTN_JOYSTICK_FIRST) & 0xFFFFFFFF)
        and not (bitmap >> _BTN_LEFT) & 1
    )


def linux_controllers_present(
    sys_root: Path = _SYS_INPUT, udev_root: Path = _UDEV_DATA
) -> bool | None:
    """True when an input device is a controller, False when none is, None when unreadable.

    udev's ID_INPUT_JOYSTICK mark decides when udev has a record of the device:
    Chromium only treats devices with that mark as gamepads, so a `js*` node or
    joystick buttons on a device udev calls a mouse must not count. Without a
    record (no udev, or a sandbox without /run/udev/data), the key capability
    bits decide.
    """
    try:
        names = os.listdir(sys_root)
    except OSError:
        return None

    uncertain = False
    for name in names:
        if not name.startswith("event"):
            continue
        entry = sys_root / name
        try:
            marked = _udev_joystick(entry, udev_root)
            if marked is None:
                marked = _capabilities_joystick(entry)
        except (OSError, ValueError):
            uncertain = True
            continue
        if marked:
            return True
    return None if uncertain else False


class _RawDevice(ctypes.Structure):
    _fields_ = [("handle", ctypes.c_void_p), ("type", ctypes.c_uint32)]  # pyright: ignore[reportUnannotatedClassAttribute]


class _HidInfo(ctypes.Structure):
    _fields_ = [  # pyright: ignore[reportUnannotatedClassAttribute]
        ("vendor", ctypes.c_uint32),
        ("product", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("usage_page", ctypes.c_uint16),
        ("usage", ctypes.c_uint16),
    ]


class _RawDeviceInfo(ctypes.Structure):
    _fields_ = [  # pyright: ignore[reportUnannotatedClassAttribute]
        ("size", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("hid", _HidInfo),
        # The largest member of the Win32 union is RID_DEVICE_INFO_KEYBOARD.
        ("padding", ctypes.c_uint32 * 2),
    ]


def _raw_input_present(user32: ctypes.CDLL) -> bool | None:
    get_list = user32.GetRawInputDeviceList
    get_list.argtypes = [
        ctypes.POINTER(_RawDevice),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.c_uint,
    ]
    get_list.restype = ctypes.c_int
    count = ctypes.c_uint()
    entry_size = ctypes.sizeof(_RawDevice)
    if cast(int, get_list(None, ctypes.byref(count), entry_size)) < 0:
        return None
    if not count.value:
        return False
    devices = (_RawDevice * count.value)()
    listed = cast(int, get_list(devices, ctypes.byref(count), entry_size))
    if listed < 0:
        return None

    get_info = user32.GetRawInputDeviceInfoW
    get_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
    ]
    get_info.restype = ctypes.c_int
    uncertain = False
    for index in range(listed):
        device = cast(_RawDevice, devices[index])
        if cast(int, device.type) != 2:  # RIM_TYPEHID
            continue
        info = _RawDeviceInfo()
        size = ctypes.c_uint(ctypes.sizeof(info))
        info.size = size.value
        if (
            cast(
                int,
                get_info(
                    cast(int | None, device.handle),
                    0x2000000B,
                    ctypes.byref(info),
                    ctypes.byref(size),
                ),
            )
            < 0
        ):
            # A device removed mid-scan must not hide a controller listed after it.
            uncertain = True
            continue
        hid = cast(_HidInfo, info.hid)
        if cast(int, hid.usage_page) == 1 and cast(int, hid.usage) in (4, 5, 8):
            return True
    return None if uncertain else False


def windows_controllers_present() -> bool | None:
    try:
        # Windows-only ctypes symbols are absent from the Linux stubs.
        load_dll = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
        raw_present = _raw_input_present(load_dll("user32", winmode=0x800))
        if raw_present is not False:
            return raw_present

        try:
            xinput = load_dll("xinput1_4.dll", winmode=0x800)
        except OSError:
            xinput = load_dll("xinput9_1_0.dll", winmode=0x800)
        get_state = xinput.XInputGetState
        get_state.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        get_state.restype = ctypes.c_uint32
        state = (ctypes.c_uint32 * 4)()  # XINPUT_STATE is 16 bytes.
        for slot in range(4):
            status = cast(int, get_state(slot, ctypes.byref(state)))
            if status == 0:
                return True
            if status != 1167:  # ERROR_DEVICE_NOT_CONNECTED
                return None
        return False
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        return None


def controllers_present() -> bool | None:
    if steam_env.gaming_mode():
        return None
    platform = sys.platform
    if platform.startswith("linux"):
        return linux_controllers_present()
    if platform == "win32":
        return windows_controllers_present()
    return None


def _open_input_watch(path: Path) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    fd = cast(int, libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    try:
        mask = _IN_CREATE_DELETE | _IN_WATCH_LOST
        if cast(int, libc.inotify_add_watch(fd, os.fsencode(path), mask)) < 0:
            raise OSError(ctypes.get_errno(), "inotify_add_watch", path)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _input_watch_lost(events: bytes) -> bool:
    offset = 0
    while offset < len(events):
        _, mask, _, name_length = cast(
            tuple[int, int, int, int], _INOTIFY_EVENT.unpack_from(events, offset)
        )
        if mask & _IN_WATCH_LOST:
            return True
        offset += _INOTIFY_EVENT.size + name_length
    return False


@final
class ControllerWatcher:
    """Run one background presence check at startup and monitor for changes."""

    def __init__(
        self,
        on_change: Callable[[bool | None], None],
        *,
        check: Callable[[], bool | None] = controllers_present,
        dev_root: Path = _DEV_INPUT,
    ) -> None:
        self._on_change = on_change
        self._check = check
        self._dev_root = dev_root
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stop_write: int | None = None
        self._stopped = False
        self._focused = False
        self._focus_generation = 0
        self._present: bool | None = None

    @property
    def present(self) -> bool | None:
        with self._condition:
            return self._present

    def start(self) -> None:
        with self._condition:
            if self._thread is not None or self._stopped:
                return
            self._thread = threading.Thread(
                target=self._run, name="Controller presence", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._condition:
            if not self._stopped:
                self._stopped = True
                if self._stop_write is not None:
                    _ = os.write(self._stop_write, b"\0")
                self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            # An in-flight evaluate_js may still be waiting on a closing renderer.
            thread.join(timeout=0.5)

    def on_focus(self, focused: bool) -> None:
        with self._condition:
            if self._focused == focused:
                return
            self._focused = focused
            if focused:
                self._focus_generation += 1
            self._condition.notify_all()

    def _refresh(self) -> None:
        present = self._check()
        with self._condition:
            if self._stopped or present == self._present:
                return
            self._present = present
        self._on_change(present)

    def _run(self) -> None:
        if steam_env.gaming_mode():
            return
        platform = sys.platform
        if platform.startswith("linux"):
            if self._watch_linux():
                return
            self._watch_focused(_LINUX_RECHECK_SECONDS)
        elif platform == "win32":
            self._watch_focused(_WINDOWS_RECHECK_SECONDS)

    def _watch_linux(self) -> bool:
        try:
            with ExitStack() as files:
                fd = _open_input_watch(self._dev_root)
                _ = files.callback(os.close, fd)
                stop_read, stop_write = os.pipe()
                _ = files.callback(os.close, stop_read)
                _ = files.callback(os.close, stop_write)
                with self._condition:
                    if self._stopped:
                        return True
                    self._stop_write = stop_write
                try:
                    self._refresh()
                    while True:
                        ready, _, _ = select.select([fd, stop_read], [], [])
                        if stop_read in ready:
                            return True
                        if _input_watch_lost(os.read(fd, 65536)):
                            return False
                        ready, _, _ = select.select(
                            [stop_read], [], [], _DEVICE_SETTLE_SECONDS
                        )
                        if ready:
                            return True
                        self._refresh()
                finally:
                    with self._condition:
                        self._stop_write = None
        # ctypes.CDLL(None) raises TypeError where no C library loads by default (Windows).
        except (OSError, AttributeError, ValueError, TypeError):
            return False

    def _watch_focused(self, interval: float) -> None:
        with self._condition:
            generation = self._focus_generation
        while True:
            self._refresh()
            with self._condition:
                while not self._stopped:
                    if self._focused:
                        if (
                            generation != self._focus_generation
                            or not self._condition.wait(interval)
                        ):
                            generation = self._focus_generation
                            break
                    else:
                        _ = self._condition.wait()
                if self._stopped:
                    return
