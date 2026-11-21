# SPDX-License-Identifier: GPL-3.0-or-later
import json
import urllib.request
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError

import pytest

from core import extension_server as server_module
from core import steam_runner

PAYLOAD: dict[str, object] = {
    "gameId": 4,
    "prefix": "/p",
    "sandboxed": False,
    "umu": False,
    "blocked": False,
    "title": "G",
}


class _Answer:
    def __init__(self, data: object) -> None:
        self._body = json.dumps(data).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.fixture(autouse=True)
def fixed_token(monkeypatch):
    monkeypatch.setattr(steam_runner, "runner_token", lambda: "t" * 64)


def _urlopen(monkeypatch, outcome):
    def fake(_request, timeout):
        if isinstance(outcome, BaseException):
            raise outcome
        return _Answer(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", fake)


def test_no_wlib_listening_allows_the_launch(monkeypatch):
    _urlopen(monkeypatch, URLError(ConnectionRefusedError(111, "refused")))
    assert steam_runner.launch_check(PAYLOAD) is None
    _urlopen(monkeypatch, ConnectionRefusedError(111, "refused"))
    assert steam_runner.launch_check(PAYLOAD) is None


@pytest.mark.parametrize(
    "outcome",
    [
        TimeoutError("timed out"),
        URLError(TimeoutError("timed out")),
        ["not", "a", "dict"],
    ],
)
def test_an_unanswered_check_refuses_the_launch(monkeypatch, outcome):
    _urlopen(monkeypatch, outcome)
    assert steam_runner.launch_check(PAYLOAD) == steam_runner.LAUNCH_CHECK_UNANSWERED


def test_window_answers_are_followed(monkeypatch):
    _urlopen(monkeypatch, {"games": []})
    assert steam_runner.launch_check(PAYLOAD) is not None
    _urlopen(monkeypatch, {"success": True})
    assert steam_runner.launch_check(PAYLOAD) is None
    _urlopen(monkeypatch, {"success": False, "error": "Busy installing"})
    assert steam_runner.launch_check(PAYLOAD) == "Busy installing"
    body = BytesIO(b'{"success": false, "error": "wLib is still starting."}')
    _urlopen(
        monkeypatch,
        HTTPError("http://127.0.0.1:8183/api/launch-check", 503, "x", Message(), body),
    )
    assert steam_runner.launch_check(PAYLOAD) == "wLib is still starting."


def test_check_before_the_api_exists_is_a_503(monkeypatch):
    monkeypatch.setattr(server_module, "api_ref", None)
    body = json.dumps(PAYLOAD).encode()
    handler = server_module.ExtensionRequestHandler.__new__(
        server_module.ExtensionRequestHandler
    )
    handler.path = "/api/launch-check"
    headers = Message()
    headers["X-wLib-Extension"] = "1"
    headers[steam_runner.RUNNER_HEADER] = steam_runner.runner_token()
    headers["Content-Length"] = str(len(body))
    handler.headers = headers
    handler.__dict__["rfile"] = BytesIO(body)
    handler.__dict__["wfile"] = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    handler.do_POST()

    handler.send_response.assert_called_once_with(503)
    assert json.loads(handler.wfile.getvalue())["success"] is False
