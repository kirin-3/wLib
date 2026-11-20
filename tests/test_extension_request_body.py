# SPDX-License-Identifier: GPL-3.0-or-later
import json
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock

from core import extension_server as server_module


class _OneBody:
    """A socket stream that holds one body; reading past it would block."""

    def __init__(self, body: bytes) -> None:
        self._data = BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        chunk = self._data.read(size)
        if not chunk:
            raise AssertionError("read past the request body (a real socket would wait)")
        return chunk


def test_rejected_open_reads_its_body_once(monkeypatch):
    monkeypatch.setattr(server_module, "window_ref", None)
    body = b"abcd"
    handler = server_module.ExtensionRequestHandler.__new__(server_module.ExtensionRequestHandler)
    handler.path = "/api/open?url=https%3A%2F%2Ff95zone.to%2Fthreads%2F1%2F"
    headers = Message()
    headers["X-wLib-Extension"] = "b" * 64  # not a paired token
    headers["Content-Length"] = str(len(body))
    handler.headers = headers
    handler.__dict__["rfile"] = _OneBody(body)
    handler.__dict__["wfile"] = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    handler.do_GET()

    handler.send_response.assert_called_once_with(403)
    assert json.loads(handler.wfile.getvalue())["success"] is False
