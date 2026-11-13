# SPDX-License-Identifier: GPL-3.0-or-later
import json
from contextlib import closing

import pytest

from core.api import Api
from core.database import get_connection, init_db, update_setting
from core.downloads import torrent_settings as ts
from core.library_backup import (
    SECTION_SETTINGS_GENERAL,
    SECTION_SETTINGS_PATHS,
    apply_library_backup,
    build_library_backup,
    build_sync_backup,
)


@pytest.fixture(autouse=True)
def clean_torrent_settings():
    def wipe():
        init_db()
        with closing(get_connection()) as conn:
            _ = conn.execute("DELETE FROM settings WHERE key LIKE 'torrent_%'")
            conn.commit()

    wipe()
    yield
    wipe()


def test_defaults_when_missing_or_blank():
    init_db()
    assert ts.seed_mode() == "ask" and ts.seed_ratio() == 1.0 and ts.seed_hours() is None
    assert ts.limits_kib() == (0, 0) and ts.port_mapping() is False
    update_setting("torrent_seed_mode", "")
    update_setting("torrent_seed_ratio", "")
    update_setting("torrent_seed_mode", "bogus")
    assert ts.seed_mode() == "ask" and ts.seed_ratio() == 1.0


def test_values_are_read():
    init_db()
    update_setting("torrent_seed_mode", "never")
    update_setting("torrent_seed_ratio", "2.5")
    update_setting("torrent_seed_hours", "12")
    update_setting("torrent_download_limit_kib", "300")
    update_setting("torrent_port_mapping", "true")
    assert (ts.seed_mode(), ts.seed_ratio(), ts.seed_hours()) == ("never", 2.5, 12.0)
    assert ts.limits_kib() == (300, 0) and ts.port_mapping() is True
    update_setting("torrent_seed_ratio", "0")
    assert ts.seed_ratio() is None


def test_seed_keys_round_trip_and_device_local_keys_stay_out(tmp_path):
    init_db()
    for key, value in (
        ("torrent_seed_mode", "always"), ("torrent_seed_ratio", "1.5"), ("torrent_seed_hours", "6"),
        ("torrent_download_limit_kib", "111"), ("torrent_upload_limit_kib", "222"), ("torrent_port_mapping", "true"),
    ):
        update_setting(key, value)
    backup = build_library_backup({"sections": [SECTION_SETTINGS_GENERAL, SECTION_SETTINGS_PATHS]})
    general = backup["settings"]["general"]
    assert (general["torrent_seed_mode"], general["torrent_seed_ratio"], general["torrent_seed_hours"]) == ("always", "1.5", "6")
    text = json.dumps(backup) + json.dumps(build_sync_backup("t", "d"))
    for key in ts.DEVICE_LOCAL_KEYS:
        assert key not in text
    update_setting("torrent_seed_mode", "never")
    update_setting("torrent_seed_ratio", "9")
    update_setting("torrent_seed_hours", "")
    apply_library_backup(backup, {"sections": [SECTION_SETTINGS_GENERAL]})
    assert (ts.seed_mode(), ts.seed_ratio(), ts.seed_hours()) == ("always", 1.5, 6.0)
    update_setting("torrent_download_limit_kib", "5")
    apply_library_backup(backup, {"sections": [SECTION_SETTINGS_GENERAL]})
    assert ts.limits_kib()[0] == 5


def test_sync_payload_carries_seed_keys():
    init_db()
    update_setting("torrent_seed_mode", "always")
    sync = build_sync_backup("t", "d")
    assert sync["settings"]["general"]["torrent_seed_mode"] == "always"


def test_settings_api_round_trip_and_validation(monkeypatch):
    init_db()
    pushed: list[bool] = []
    from core.downloads import torrents

    monkeypatch.setattr(torrents.supervisor, "apply_limits", lambda: pushed.append(True))
    api = Api()
    assert api.save_settings({"torrent_seed_mode": "always", "torrent_seed_ratio": 2, "torrent_seed_hours": "",
                              "torrent_upload_limit_kib": 100, "torrent_port_mapping": True})["success"]
    settings = api.get_settings()
    assert settings["torrent_seed_mode"] == "always" and settings["torrent_seed_ratio"] == 2.0
    assert settings["torrent_seed_hours"] == ""
    # The download limit is the shared download limit now (download_limit_kib).
    assert "torrent_download_limit_kib" not in settings
    assert settings["torrent_upload_limit_kib"] == 100 and settings["torrent_port_mapping"] is True
    assert pushed
    assert api.save_settings({"torrent_seed_mode": "sometimes"})["success"] is False
    assert api.save_settings({"torrent_upload_limit_kib": "-5"})["success"] is False
    assert Api().get_settings()["torrent_seed_mode"] == "always"


def test_invalid_torrent_value_saves_nothing_else(monkeypatch):
    init_db()
    from core.database import get_setting

    api = Api()
    update_setting("sandboxie_custom_path", "before")
    result = api.save_settings({"sandboxie_custom_path": "after", "torrent_upload_limit_kib": "fast"})
    assert result["success"] is False
    assert get_setting("sandboxie_custom_path") == "before"


def test_watch_folder_validation_and_round_trip(tmp_path):
    init_db()
    api = Api()
    assert ts.watch_folder() == "" and ts.export_warning_accepted() is False
    assert api.get_settings()["torrent_watch_folder"] == ""
    assert api.save_settings({"torrent_watch_folder": str(tmp_path)})["success"]
    assert ts.watch_folder() == str(tmp_path)
    assert Api().get_settings()["torrent_watch_folder"] == str(tmp_path)
    for bad in ("relative/path", str(tmp_path / "missing"), 5):
        result = api.save_settings({"torrent_watch_folder": bad})
        assert result == {"success": False, "error": "Watch folder must be an existing folder"}
    assert ts.watch_folder() == str(tmp_path)
    assert api.save_settings({"torrent_watch_folder": "  "})["success"]
    assert ts.watch_folder() == "" and api.get_settings()["torrent_watch_folder"] == ""


def test_export_warning_flag_round_trip():
    init_db()
    api = Api()
    assert api.save_settings({"torrent_export_warning_accepted": True})["success"]
    assert ts.export_warning_accepted() is True
    assert api.get_settings()["torrent_export_warning_accepted"] is True
    assert ts.WATCH_FOLDER_KEY in ts.DEVICE_LOCAL_KEYS and ts.EXPORT_WARNING_KEY in ts.DEVICE_LOCAL_KEYS


def test_download_queue_settings_save_and_apply(monkeypatch):
    from core.database import get_setting
    from core.downloads import queue_settings

    init_db()
    api = Api()
    applied: list[bool] = []
    monkeypatch.setattr(api._download_queue, "apply_settings", lambda: applied.append(True))
    assert api.get_settings()["download_max_active"] == 2
    assert api.save_settings({"download_limit_kib": 2048, "download_max_active": 3,
                              "download_pause_while_playing": False, "download_pause_on_battery": True})["success"]
    assert applied == [True]
    settings = api.get_settings()
    assert (settings["download_limit_kib"], settings["download_max_active"]) == (2048, 3)
    assert settings["download_pause_while_playing"] is False and settings["download_pause_on_battery"] is True
    # A bad value saves nothing, also not the other keys sent with it.
    update_setting("sandboxie_custom_path", "before")
    result = api.save_settings({"sandboxie_custom_path": "after", "download_limit_kib": 1,
                                "download_max_active": 9})
    assert result["success"] is False and result["field"] == queue_settings.MAX_ACTIVE_KEY
    assert get_setting("sandboxie_custom_path") == "before"
    assert queue_settings.limit_kib() == 2048
    assert applied == [True]


def test_new_slot_number_starts_a_waiting_job():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_download_queue import FakeWorker  # type: ignore[import-not-found]

    from core.downloads.queue import DownloadQueue

    init_db()
    api = Api()
    worker = FakeWorker()
    queue = DownloadQueue([worker], run_thread=False, free_space=lambda: 0)
    api._download_queue = queue
    ids = [queue.add("host", game_id, "G", "1", {}).id for game_id in (1, 2, 3)]
    queue.tick()
    assert queue.get(ids[2]).state == "queued"
    assert api.save_settings({"download_max_active": 3})["success"]
    assert queue.get(ids[2]).state == "running"
