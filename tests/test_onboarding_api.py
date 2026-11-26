# pyright: reportMissingImports=false, reportPrivateUsage=false
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlite3

import pytest

from core import database
from core.api import Api
from core.catalog import CatalogClient


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()


def test_get_onboarding_state_and_step_validation(monkeypatch):
    monkeypatch.setattr(
        CatalogClient, "start_background", lambda self, *, force=False: True
    )
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: "")

    api = Api()
    state = api.get_onboarding_state()
    assert state == {
        "success": True,
        "status": "",
        "step": "",
        "catalog_held": True,
    }

    for step in ("welcome", "catalog", "lc", "import", "extension", "done"):
        res = api.set_onboarding_step(step)
        assert res == {"success": True, "step": step}
        assert api.get_onboarding_state()["step"] == step

    for bad in ("", "unknown", "WELCOME", None, 123, ["welcome"]):
        res = api.set_onboarding_step(bad)
        assert res["success"] is False
        assert isinstance(res.get("error"), str) and res["error"]


@pytest.mark.parametrize("status", ["done", "skipped"])
def test_finish_onboarding_saves_status_clears_step_and_releases_hold(
    monkeypatch, status
):
    calls: list[bool] = []
    monkeypatch.setattr(
        CatalogClient,
        "start_background",
        lambda self, *, force=False: calls.append(force) or True,
    )
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: "")

    api = Api()
    assert api.set_onboarding_step("extension")["success"] is True
    res = api.finish_onboarding(status)
    assert res == {"success": True, "status": status, "catalog_started": True}
    assert calls == [True]
    assert api.get_onboarding_state() == {
        "success": True,
        "status": status,
        "step": "",
        "catalog_held": False,
    }
    assert database.get_setting("onboarding") == status
    assert database.get_setting("onboarding_step") == ""


def test_finish_onboarding_rejects_invalid_status(monkeypatch):
    monkeypatch.setattr(
        CatalogClient, "start_background", lambda self, *, force=False: True
    )
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: "")

    api = Api()
    for bad in ("", "finished", "DONE", None, True, 1):
        res = api.finish_onboarding(bad)
        assert res["success"] is False
        assert isinstance(res.get("error"), str) and res["error"]
    assert api._catalog_held is True


def test_release_catalog_hold_is_idempotent_and_respects_catalog_enabled(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(
        CatalogClient,
        "start_background",
        lambda self, *, force=False: calls.append(force) or True,
    )
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: "")

    # Catalog on: first release starts catalog, second release is a no-op.
    api = Api()
    first = api.release_catalog_hold()
    assert first == {"success": True, "released": True, "started": True}
    assert calls == [True]
    second = api.release_catalog_hold()
    assert second == {"success": True, "released": False, "started": False}
    assert calls == [True]

    # Catalog turned off before release: release clears hold without starting.
    calls.clear()
    api_off = Api()
    assert api_off.save_settings({"catalog_enabled": False})["success"] is True
    released = api_off.release_catalog_hold()
    assert released == {"success": True, "released": True, "started": False}
    assert calls == []
    assert api_off._catalog_held is False


def test_onboarding_methods_return_error_payload_on_db_failure(monkeypatch):
    monkeypatch.setattr(
        CatalogClient, "start_background", lambda self, *, force=False: True
    )
    monkeypatch.setattr(CatalogClient, "installed_sha256", lambda self: "")
    api = Api()

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("core.api_mixins.onboarding.get_setting", boom)
    monkeypatch.setattr("core.api_mixins.onboarding.update_setting", boom)
    monkeypatch.setattr("core.api_mixins.onboarding.update_settings", boom)

    for res in (
        api.get_onboarding_state(),
        api.set_onboarding_step("catalog"),
        api.finish_onboarding("done"),
    ):
        assert res["success"] is False
        assert "locked" in str(res.get("error", ""))

    monkeypatch.setattr(api, "_release_catalog_hold", boom)
    rel = api.release_catalog_hold()
    assert rel["success"] is False
    assert "locked" in str(rel.get("error", ""))
