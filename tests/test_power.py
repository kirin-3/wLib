# SPDX-License-Identifier: GPL-3.0-or-later
import os

from core import power


def supply(root, name, **files):
    folder = root / name
    folder.mkdir(parents=True)
    for key, value in files.items():
        (folder / key).write_text(value + "\n")


def test_mains_online(tmp_path):
    supply(tmp_path, "AC", type="Mains", online="1")
    supply(tmp_path, "BAT0", type="Battery", status="Charging")
    assert power.linux_on_battery(str(tmp_path)) is False


def test_discharging_battery(tmp_path):
    supply(tmp_path, "AC", type="Mains", online="0")
    supply(tmp_path, "BAT0", type="Battery", status="Discharging")
    assert power.linux_on_battery(str(tmp_path)) is True


def test_usb_c_power_counts_as_mains(tmp_path):
    supply(tmp_path, "ucsi-source-psy-USBC000:001", type="USB", online="1")
    supply(tmp_path, "BAT0", type="Battery", status="Discharging")
    assert power.linux_on_battery(str(tmp_path)) is False


def test_no_battery(tmp_path):
    supply(tmp_path, "AC", type="Mains", online="1")
    assert power.linux_on_battery(str(tmp_path)) is False
    empty = tmp_path / "empty"
    empty.mkdir()
    assert power.linux_on_battery(str(empty)) is None


def test_peripheral_battery_is_ignored(tmp_path):
    supply(tmp_path, "hidpp_battery_0", type="Battery", status="Discharging", scope="Device")
    assert power.linux_on_battery(str(tmp_path)) is None


def test_unreadable(tmp_path):
    assert power.linux_on_battery(str(tmp_path / "missing")) is None
    supply(tmp_path, "BAT0", type="Battery")
    status = tmp_path / "BAT0" / "status"
    status.mkdir()  # A folder where a file is expected cannot be read.
    assert power.linux_on_battery(str(tmp_path)) is None
    assert os.path.isdir(status)
