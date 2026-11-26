# pyright: reportMissingImports=false, reportPrivateUsage=false
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest

from core import database
from core.api import Api
from core.catalog import CatalogClient


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()


def test_new_install_holds_catalog_at_startup_and_blocks_all_start_paths(monkeypatch):
    calls: list[bool] = []

    def fake_start(self, *, force: bool = False) -> bool:
        calls.append(force)
        return True

    monkeypatch.setattr(CatalogClient, "start_background", fake_start)
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: "")

    api = Api()
    assert api._catalog_held is True
    assert calls == []

    # Toggling catalog off -> on during the hold must not start a download.
    assert api.save_settings({"catalog_enabled": False})["success"] is True
    assert api.save_settings({"catalog_enabled": True})["success"] is True
    assert calls == []

    # Manual check_catalog_now during the hold must not start a download.
    check_res = api.check_catalog_now()
    assert check_res["success"] is True
    assert check_res["started"] is False
    assert calls == []


def test_not_held_when_catalog_copy_is_already_installed(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(
        CatalogClient,
        "start_background",
        lambda self, *, force=False: calls.append(force) or True,
    )
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: "abc123")

    api = Api()
    assert api._catalog_held is False
    assert calls == [False]


@pytest.mark.parametrize("status", ["done", "skipped"])
def test_not_held_when_onboarding_is_finished(monkeypatch, status):
    database.update_setting("onboarding", status)
    calls: list[bool] = []
    monkeypatch.setattr(
        CatalogClient,
        "start_background",
        lambda self, *, force=False: calls.append(force) or True,
    )
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: "")

    api = Api()
    assert api._catalog_held is False
    assert calls == [False]


def test_not_held_when_catalog_is_off(monkeypatch):
    database.update_setting("catalog_enabled", "0")
    calls: list[bool] = []
    monkeypatch.setattr(
        CatalogClient,
        "start_background",
        lambda self, *, force=False: calls.append(force) or True,
    )
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: "")

    api = Api()
    assert api._catalog_held is False
    assert calls == []

    # Turning the catalog on starts immediately because it was not held.
    assert api.save_settings({"catalog_enabled": True})["success"] is True
    assert calls == [True]
