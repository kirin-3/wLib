"""Regression tests for extension bridge bugs from the second bug hunt."""

import json
from email.message import Message
from io import BytesIO
from typing import cast
from unittest.mock import MagicMock

import pytest

from conftest import PAIRED_EXTENSION_TOKEN
from core import database
from core import extension_server as server_module


def _check(path: str) -> dict[str, object]:
    handler = server_module.ExtensionRequestHandler.__new__(
        server_module.ExtensionRequestHandler
    )
    handler.path = path
    headers = Message()
    headers["X-wLib-Extension"] = PAIRED_EXTENSION_TOKEN
    handler.headers = headers
    handler.__dict__["wfile"] = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.do_GET()
    handler.send_response.assert_called_once_with(200)
    return json.loads(cast(BytesIO, handler.wfile).getvalue().decode("utf-8"))


@pytest.fixture
def startup_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    sync_api = MagicMock()
    sync_api.get_startup_extension_sync_status.return_value = {
        "success": True,
        "installed_version": "1.0.11",
    }
    monkeypatch.setattr(server_module, "api_ref", sync_api)


def test_failed_lc_batch_lookup_is_an_error_not_an_empty_match(
    monkeypatch: pytest.MonkeyPatch, startup_sync: None
) -> None:
    def broken(_url: str) -> None:
        raise database.sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(database, "find_game_by_lc_url", broken)

    payload = _check("/api/check?batch=1&url=https://lewdcorner.com/threads/25455/")

    assert "matches" not in payload
    assert payload["error_code"] == "lookup_failed"
    assert isinstance(payload["error"], str) and payload["error"]
    # The version fields stay, so an old extension can still update itself.
    assert payload["app_version"] == server_module.APP_VERSION
    assert payload["extension_version"] == "1.0.11"


def test_single_check_lookup_error_keeps_its_old_answer(
    monkeypatch: pytest.MonkeyPatch, startup_sync: None
) -> None:
    monkeypatch.setattr(
        server_module.ExtensionRequestHandler,
        "_build_check_payload",
        MagicMock(side_effect=RuntimeError("db down")),
    )

    payload = _check("/api/check?url=https://f95zone.to/threads/demo.123/")

    assert payload == {
        "exists": False,
        "app_version": server_module.APP_VERSION,
        "extension_version": "1.0.11",
    }
