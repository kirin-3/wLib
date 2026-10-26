# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from core.api import Api
from core.catalog import CatalogClient
from core.database import get_setting, init_db, update_setting


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    init_db()


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(
        CatalogClient, "start_background", lambda self, force=False: calls.append(force) or True
    )
    return calls


def test_startup_check_runs_when_enabled(started, tmp_path):
    api = Api()
    assert started == [False]
    assert api.catalog.data_dir == str(tmp_path / "data")


def test_startup_check_skipped_when_disabled(started):
    update_setting("catalog_enabled", "0")
    Api()
    assert started == []


def test_status_and_check_now(started):
    api = Api()
    status = api.get_catalog_status()
    assert status["success"] is True and status["enabled"] is True
    assert status["installed"] is False
    assert set(status) >= {"built_at", "sources", "last_check", "last_error", "checking"}
    result = api.check_catalog_now()
    assert result["success"] is True and result["started"] is True
    assert started[-1] is True


def test_check_now_refused_when_disabled(started):
    update_setting("catalog_enabled", "0")
    result = Api().check_catalog_now()
    assert result == {"success": False, "error": "The game catalog is turned off"}


def test_setting_round_trips_and_turning_off_deletes_files(started, tmp_path):
    api = Api()
    assert api.get_settings()["catalog_enabled"] is True
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "catalog.db").write_bytes(b"x")
    (data / "catalog.json").write_text("{}")
    assert api.save_settings({"catalog_enabled": False})["success"] is True
    assert get_setting("catalog_enabled") == "0"
    assert api.get_settings()["catalog_enabled"] is False
    assert not os.path.exists(data / "catalog.db")
    assert not os.path.exists(data / "catalog.json")
    started.clear()
    assert api.save_settings({"catalog_enabled": True})["success"] is True
    assert get_setting("catalog_enabled") == "1"
    assert started == [True]
    # Saving other settings leaves it alone.
    api.save_settings({"proton_path": ""})
    assert get_setting("catalog_enabled") == "1"


def test_startup_with_unreachable_catalog_does_not_block(monkeypatch, tmp_path):
    import time

    monkeypatch.undo()
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WLIB_CATALOG_MANIFEST_URL", "http://127.0.0.1:9/v1/manifest.json")
    began = time.monotonic()
    api = Api()
    assert time.monotonic() - began < 5
    for _ in range(250):
        if not api.get_catalog_status()["checking"]:
            break
        time.sleep(0.02)
    status = api.get_catalog_status()
    assert status["success"] is True and status["last_error"]
