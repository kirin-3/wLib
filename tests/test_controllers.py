# SPDX-License-Identifier: GPL-3.0-or-later
import ctypes
import struct
import threading
from queue import Empty, Queue
from unittest.mock import Mock

import pytest

from core import controllers


def write_input(root, name, *, buttons=(), minor=64):
    entry = root / name
    capabilities = entry / "device/capabilities"
    capabilities.mkdir(parents=True)
    bitmap = sum(1 << bit for bit in buttons)
    word_bits = struct.calcsize("l") * 8
    words = []
    while bitmap:
        words.append(format(bitmap & ((1 << word_bits) - 1), "x"))
        bitmap >>= word_bits
    (capabilities / "key").write_text(" ".join(reversed(words)) or "0")
    (entry / "dev").write_text(f"13:{minor}\n")


@pytest.mark.parametrize(
    ("buttons", "udev"),
    [
        pytest.param((0x130, 0x13E), None, id="Xbox-without-udev"),
        pytest.param((0x130, 0x13E), "E:ID_INPUT_JOYSTICK=1", id="DualSense-udev"),
        pytest.param((0x120, 0x13F), None, id="Switch-Pro-without-udev"),
        pytest.param((), "E:ID_INPUT_JOYSTICK=1", id="Steam-virtual-pad-udev"),
    ],
)
def test_linux_controller_signals(tmp_path, buttons, udev):
    write_input(tmp_path / "sys", "event0", buttons=buttons)
    udev_dir = tmp_path / "udev"
    if udev is not None:
        udev_dir.mkdir()
        (udev_dir / "c13:64").write_text(f"I:1\n{udev}\n")
    assert controllers.linux_controllers_present(tmp_path / "sys", udev_dir) is True


def test_linux_udev_mouse_with_joystick_buttons_is_not_a_controller(tmp_path):
    # A headset receiver with a joydev node and joystick buttons that udev calls a mouse.
    root = tmp_path / "sys"
    (root / "js0").mkdir(parents=True)
    write_input(root, "event0", buttons=(0x110, 0x120, 0x12F))
    udev_dir = tmp_path / "udev"
    udev_dir.mkdir()
    (udev_dir / "c13:64").write_text("E:ID_INPUT=1\nE:ID_INPUT_MOUSE=1\n")
    assert controllers.linux_controllers_present(root, udev_dir) is False


def test_linux_mouse_with_joystick_buttons_without_udev_is_not_a_controller(tmp_path):
    write_input(tmp_path / "sys", "event0", buttons=(0x110, 0x120, 0x12F))
    assert (
        controllers.linux_controllers_present(tmp_path / "sys", tmp_path / "udev")
        is False
    )


def test_linux_unreadable_device_is_unknown(tmp_path):
    entry = tmp_path / "sys/event0/device/capabilities"
    entry.mkdir(parents=True)
    (entry / "key").write_text("not-hex")
    assert (
        controllers.linux_controllers_present(tmp_path / "sys", tmp_path / "udev")
        is None
    )


def test_linux_keyboard_mouse_and_tablet_are_not_controllers(tmp_path):
    root = tmp_path / "sys"
    write_input(root, "event0", buttons=(1, 0x11F))
    write_input(root, "event1", buttons=(0x110, 0x117), minor=65)
    write_input(root, "event2", buttons=(0x140,), minor=66)
    assert controllers.linux_controllers_present(root, tmp_path / "udev") is False


def test_linux_unknown_when_sysfs_is_unavailable(tmp_path):
    assert controllers.linux_controllers_present(tmp_path / "missing", tmp_path) is None


@pytest.mark.parametrize(
    ("usage_page", "usage", "xinput_slot", "expected"),
    [
        (1, 4, None, True),
        (1, 5, None, True),
        (1, 8, None, True),
        (1, 2, 3, True),
        (2, 5, None, False),
    ],
)
def test_windows_presence(monkeypatch, usage_page, usage, xinput_slot, expected):
    def get_list(devices, count, size):
        assert size == ctypes.sizeof(controllers._RawDevice)
        if devices is None:
            count._obj.value = 1
            return 0
        devices[0].handle = 123
        devices[0].type = 2
        return 1

    def get_info(handle, command, info, size):
        assert handle == 123
        assert command == 0x2000000B
        assert size._obj.value == info._obj.size == 32
        info._obj.hid.usage_page = usage_page
        info._obj.hid.usage = usage
        return size._obj.value

    raw = Mock(
        GetRawInputDeviceList=Mock(side_effect=get_list),
        GetRawInputDeviceInfoW=Mock(side_effect=get_info),
    )
    xinput = Mock(
        XInputGetState=Mock(
            side_effect=lambda slot, _: 0 if slot == xinput_slot else 1167
        )
    )

    def load_dll(name, *, winmode):
        assert winmode == 0x800
        if name == "user32":
            return raw
        if name == "xinput1_4.dll":
            raise OSError
        assert name == "xinput9_1_0.dll"
        return xinput

    monkeypatch.setattr(ctypes, "WinDLL", load_dll, raising=False)
    assert controllers.windows_controllers_present() is expected
    if xinput_slot == 3:
        assert [call.args[0] for call in xinput.XInputGetState.call_args_list] == [
            0,
            1,
            2,
            3,
        ]


@pytest.mark.parametrize(("second_usage", "expected"), [(5, True), (2, None)])
def test_windows_device_that_fails_is_skipped(monkeypatch, second_usage, expected):
    def get_list(devices, count, size):
        if devices is None:
            count._obj.value = 2
            return 0
        for index, handle in enumerate((1, 2)):
            devices[index].handle = handle
            devices[index].type = 2
        return 2

    def get_info(handle, command, info, size):
        if handle == 1:
            return -1
        info._obj.hid.usage_page = 1
        info._obj.hid.usage = second_usage
        return size._obj.value

    user32 = Mock(
        GetRawInputDeviceList=Mock(side_effect=get_list),
        GetRawInputDeviceInfoW=Mock(side_effect=get_info),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=user32), raising=False)
    assert controllers.windows_controllers_present() is expected


def test_windows_failed_raw_input_query(monkeypatch):
    user32 = Mock(GetRawInputDeviceList=Mock(return_value=-1))
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=user32), raising=False)
    assert controllers.windows_controllers_present() is None


@pytest.mark.parametrize(
    ("platform", "gaming_mode", "expected"),
    [
        ("linux", False, True),
        ("win32", False, False),
        ("darwin", False, None),
        ("linux", True, None),
    ],
)
def test_platform_dispatch(monkeypatch, platform, gaming_mode, expected):
    linux = Mock(return_value=True)
    windows = Mock(return_value=False)
    monkeypatch.setattr(controllers.sys, "platform", platform)
    monkeypatch.setattr(controllers.steam_env, "gaming_mode", lambda: gaming_mode)
    monkeypatch.setattr(controllers, "linux_controllers_present", linux)
    monkeypatch.setattr(controllers, "windows_controllers_present", windows)
    assert controllers.controllers_present() is expected
    assert linux.call_count == int(platform == "linux" and not gaming_mode)
    assert windows.call_count == int(platform == "win32")


@pytest.mark.skipif(
    not controllers.sys.platform.startswith("linux"), reason="Linux inotify"
)
def test_inotify_detects_arrival_and_removal_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(controllers.steam_env, "gaming_mode", lambda: False)
    device = tmp_path / "event1"
    changes = Queue()
    watcher = controllers.ControllerWatcher(
        changes.put, check=device.exists, dev_root=tmp_path
    )
    watcher.start()
    try:
        assert changes.get(timeout=1) is False
        device.touch()
        assert changes.get(timeout=2) is True
        device.unlink()
        assert changes.get(timeout=2) is False
    finally:
        watcher.stop()
    assert changes.empty()
    assert not watcher._thread.is_alive()


@pytest.mark.parametrize(
    ("platform", "interval_name"),
    [
        ("linux", "_LINUX_RECHECK_SECONDS"),
        ("win32", "_WINDOWS_RECHECK_SECONDS"),
    ],
)
def test_fallback_only_rechecks_while_focused(
    tmp_path, monkeypatch, platform, interval_name
):
    monkeypatch.setattr(controllers.sys, "platform", platform)
    monkeypatch.setattr(controllers.steam_env, "gaming_mode", lambda: False)
    monkeypatch.setattr(controllers, interval_name, 0.05)
    checks = Queue()
    changes = Queue()

    def check():
        checks.put(threading.current_thread())
        return False

    watcher = controllers.ControllerWatcher(
        changes.put, check=check, dev_root=tmp_path / "missing"
    )
    watcher.start()
    try:
        checks.get(timeout=1)
        with pytest.raises(Empty):
            checks.get(timeout=0.2)
        watcher.on_focus(True)
        checks.get(timeout=1)
        checks.get(timeout=1)
        watcher.on_focus(False)
        with pytest.raises(Empty):
            checks.get(timeout=0.2)
        assert changes.get_nowait() is False
        assert changes.empty()
    finally:
        watcher.stop()
    assert not watcher._thread.is_alive()
