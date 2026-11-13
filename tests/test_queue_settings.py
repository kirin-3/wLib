# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.database import get_setting, init_db, update_setting
from core.downloads import queue_settings as qs


def test_defaults():
    init_db()
    assert qs.values() == {
        "download_limit_kib": 0,
        "download_max_active": 2,
        "download_pause_while_playing": True,
        "download_pause_on_battery": False,
    }


def test_values_are_read():
    init_db()
    update_setting(qs.LIMIT_KEY, "2048")
    update_setting(qs.MAX_ACTIVE_KEY, "4")
    update_setting(qs.PAUSE_WHILE_PLAYING_KEY, "false")
    update_setting(qs.PAUSE_ON_BATTERY_KEY, "true")
    assert qs.limit_kib() == 2048 and qs.max_active() == 4
    assert qs.pause_while_playing() is False and qs.pause_on_battery() is True


@pytest.mark.parametrize("stored", ["", "abc", "-1", "9", "0", "2.5", "nan"])
def test_bad_stored_slot_number_means_default(stored):
    init_db()
    update_setting(qs.MAX_ACTIVE_KEY, stored)
    assert qs.max_active() == 2


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        (qs.LIMIT_KEY, 0, "0"),
        (qs.LIMIT_KEY, "1500", "1500"),
        (qs.LIMIT_KEY, -5, None),
        (qs.LIMIT_KEY, "fast", None),
        (qs.LIMIT_KEY, 1.5, None),
        (qs.LIMIT_KEY, True, None),
        (qs.LIMIT_KEY, None, None),
        (qs.LIMIT_KEY, qs.MAX_LIMIT_KIB + 1, None),
        (qs.MAX_ACTIVE_KEY, 1, "1"),
        (qs.MAX_ACTIVE_KEY, "5", "5"),
        (qs.MAX_ACTIVE_KEY, 0, None),
        (qs.MAX_ACTIVE_KEY, 6, None),
        (qs.PAUSE_WHILE_PLAYING_KEY, False, "false"),
        (qs.PAUSE_ON_BATTERY_KEY, "TRUE", "true"),
        (qs.PAUSE_ON_BATTERY_KEY, "yes", None),
        (qs.PAUSE_ON_BATTERY_KEY, 1, None),
        ("unknown_key", 1, None),
    ],
)
def test_normalize_for_save(key, raw, expected):
    assert qs.normalize_for_save(key, raw) == expected


def test_legacy_torrent_limit_is_copied_once():
    init_db()
    update_setting(qs.LEGACY_TORRENT_LIMIT_KEY, "1000")
    assert qs.migrate_legacy_limit() is True
    assert qs.limit_kib() == 1000
    # The old key stays for an older version.
    assert get_setting(qs.LEGACY_TORRENT_LIMIT_KEY) == "1000"
    # A later change of the shared limit is not overwritten again.
    update_setting(qs.LIMIT_KEY, "0")
    assert qs.migrate_legacy_limit() is False
    assert qs.limit_kib() == 0


def test_no_legacy_limit_saves_unlimited():
    init_db()
    assert qs.migrate_legacy_limit() is False
    assert get_setting(qs.LIMIT_KEY) == "0"


def test_bad_legacy_limit_is_not_copied():
    init_db()
    update_setting(qs.LEGACY_TORRENT_LIMIT_KEY, "lots")
    assert qs.migrate_legacy_limit() is False
    assert qs.limit_kib() == 0
