# SPDX-License-Identifier: GPL-3.0-or-later
"""Log lines that explain extension pairing and fetch problems in wlib.log."""

import json
import logging
import threading
import time
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock

import pytest

from conftest import PAIRED_EXTENSION_TOKEN
from core import extension_bridge as bridge
from core import extension_pairing as pairing
from core import extension_server as server_module
from core.app_log import RepeatLimiter
from core.extension_bridge import BridgeConnection, BridgeUnavailable

GUEST_PAGE = '<html id="XF" data-logged-in="false"><title>Log in</title></html>'
MEMBER_PAGE = '<html id="XF" data-logged-in="true"><h1>Game</h1></html>'
LC_THREAD = "https://lewdcorner.com/threads/4321/"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    bridge.reset_for_tests()
    monkeypatch.setattr(bridge, "LC_GAP_SECONDS", 0.0)
    monkeypatch.setattr(server_module, "_repeats", RepeatLimiter())
    monkeypatch.setattr(pairing, "_repeats", RepeatLimiter())
    yield
    bridge.reset_for_tests()


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records]


# ---- RepeatLimiter ----


def test_repeat_limiter_logs_once_per_window_and_counts_repeats(caplog):
    now = [100.0]
    limiter = RepeatLimiter(window=60, now=lambda: now[0])
    logger = logging.getLogger("wlib.test")
    caplog.set_level(logging.INFO, logger="wlib.test")
    for _ in range(4):
        limiter.log(logger, logging.WARNING, "key", "Refused %s", "x")
    limiter.log(logger, logging.WARNING, "other", "Other")
    assert _messages(caplog) == ["Refused x", "Other"]
    now[0] += 61
    limiter.log(logger, logging.WARNING, "key", "Refused %s", "x")
    assert _messages(caplog)[-1] == "Refused x (3 more like this since the last report)"


def test_repeat_limiter_keeps_a_bounded_number_of_keys():
    now = [0.0]
    limiter = RepeatLimiter(window=60, now=lambda: now[0], max_keys=2)
    for key in ("a", "b", "c"):
        now[0] += 1
        assert limiter.allow(key) == 0
    # "a" was the oldest and was dropped, so it is new again.
    assert limiter.allow("a") == 0
    assert limiter.allow("c") is None


# ---- Bridge: LC fetch outcomes ----


def _lc_conn(sent: list[str], pairing_id: str = "ABCD-1234") -> BridgeConnection:
    conn = BridgeConnection(send_text=sent.append)
    conn.pairing_id = pairing_id
    bridge._register_connection(conn)
    bridge._handle_message(
        conn,
        {"type": "hello", "protocol": 2, "can_fetch": True, "can_fetch_lc": True, "extension_version": "1.2.14"},
    )
    return conn


def _answer_lc_fetch(conn: BridgeConnection, sent: list[str], result: dict[str, object]) -> bridge.BridgeResponse:
    holder: dict[str, object] = {}

    def _run() -> None:
        try:
            holder["response"] = bridge.fetch(LC_THREAD, timeout=5, site="lc")
        except Exception as exc:  # noqa: BLE001 - the test reads it below
            holder["error"] = exc

    before = len(sent)
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while len(sent) == before and time.monotonic() < deadline:
        time.sleep(0.005)
    request = json.loads(sent[-1])
    assert request["site"] == "lc"
    bridge._handle_message(conn, {"type": "fetch_result", "id": request["id"], **result})
    thread.join(timeout=5)
    assert "error" not in holder, holder.get("error")
    response = holder["response"]
    assert isinstance(response, bridge.BridgeResponse)
    return response


def test_lc_signed_out_fetch_is_logged_once_with_how_it_was_made(caplog):
    caplog.set_level(logging.INFO, logger="wlib.bridge")
    sent: list[str] = []
    conn = _lc_conn(sent)
    guest = {"ok": True, "status": 200, "final_url": LC_THREAD, "body": GUEST_PAGE,
             "via": "background", "tab_retry": "no_tab"}
    response = _answer_lc_fetch(conn, sent, guest)
    assert (response.via, response.tab_retry) == ("background", "no_tab")
    _ = _answer_lc_fetch(conn, sent, guest)

    signed_out = [line for line in _messages(caplog) if "LewdCorner without a login" in line]
    assert len(signed_out) == 1
    assert "HTTP 200" in signed_out[0]
    assert "no open tab of the site" in signed_out[0]
    assert "container tab" in signed_out[0]

    _ = _answer_lc_fetch(
        conn, sent, {"ok": True, "status": 200, "final_url": LC_THREAD, "body": MEMBER_PAGE, "via": "tab"}
    )
    assert any(
        "LewdCorner while logged in (request from an open tab)" in line for line in _messages(caplog)
    )


def test_unknown_fetch_details_are_dropped():
    sent: list[str] = []
    conn = _lc_conn(sent)
    response = _answer_lc_fetch(
        conn,
        sent,
        {"ok": True, "status": 200, "final_url": LC_THREAD, "body": "x", "via": "<script>", "tab_retry": 5},
    )
    assert (response.via, response.tab_retry) == ("", "")


def test_old_extension_without_details_says_so(caplog):
    caplog.set_level(logging.INFO, logger="wlib.bridge")
    sent: list[str] = []
    conn = _lc_conn(sent)
    _ = _answer_lc_fetch(conn, sent, {"ok": True, "status": 200, "final_url": LC_THREAD, "body": GUEST_PAGE})
    assert any("1.2.13 or older did not say how" in line for line in _messages(caplog))


def test_lc_fetch_failures_are_logged_without_flooding(caplog):
    caplog.set_level(logging.INFO, logger="wlib.bridge")
    sent: list[str] = []
    conn = _lc_conn(sent)
    for _ in range(3):
        holder: dict[str, object] = {}

        def _run(target: dict[str, object] = holder) -> None:
            try:
                _ = bridge.fetch(LC_THREAD, timeout=5, site="lc")
            except bridge.BridgeFailed as exc:
                target["error"] = str(exc)

        before = len(sent)
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while len(sent) == before and time.monotonic() < deadline:
            time.sleep(0.005)
        request = json.loads(sent[-1])
        bridge._handle_message(conn, {"type": "fetch_result", "id": request["id"], "ok": False, "error": "network"})
        thread.join(timeout=5)
        assert holder["error"] == "network"
    failures = [line for line in _messages(caplog) if "LewdCorner fetch failed: network" in line]
    assert len(failures) == 1


def test_lc_unavailable_names_what_each_connection_can_do(caplog):
    caplog.set_level(logging.INFO, logger="wlib.bridge")
    with pytest.raises(BridgeUnavailable):
        _ = bridge.fetch(LC_THREAD, site="lc")
    assert any("No browser extension can fetch LewdCorner (no extension connected)" in line for line in _messages(caplog))

    conn = BridgeConnection(send_text=lambda _text: None)
    conn.pairing_id = "ABCD-1234"
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 2, "can_fetch": True, "extension_version": "1.2.13"})
    # The same problem within a few minutes is not logged again.
    bridge._repeats = RepeatLimiter()
    with pytest.raises(BridgeUnavailable):
        _ = bridge.fetch(LC_THREAD, site="lc")
    line = _messages(caplog)[-1]
    assert "pairing ABCD-1234 version 1.2.13" in line
    assert "LewdCorner no (LewdCorner switch off)" in line


def test_bridge_open_close_and_hello_name_the_pairing(caplog):
    caplog.set_level(logging.INFO, logger="wlib.bridge")
    sent: list[str] = []
    conn = _lc_conn(sent, "WXYZ-9876")
    bridge._remove_connection(conn, "closed by the extension")
    lines = _messages(caplog)
    assert any("bridge opened (pairing WXYZ-9876, 1 open)" in line for line in lines)
    assert any("connected (pairing WXYZ-9876, version 1.2.14" in line for line in lines)
    assert any(
        "disconnected after" in line and "(pairing WXYZ-9876, closed by the extension)" in line for line in lines
    )


def test_f95_outcome_log_says_how_the_page_was_fetched(caplog):
    caplog.set_level(logging.INFO, logger="wlib.bridge")
    bridge.record_fetch_outcome(200, False, via="background", tab_retry="no_tab")
    line = next(line for line in _messages(caplog) if "F95Zone without a login" in line)
    assert "HTTP 200, background request; no open tab of the site to retry in" in line


# ---- Server: refused requests ----


def _handler(path: str, headers: dict[str, str]) -> server_module.ExtensionRequestHandler:
    handler = server_module.ExtensionRequestHandler.__new__(server_module.ExtensionRequestHandler)
    handler.path = path
    handler.command = "GET"
    message = Message()
    for key, value in headers.items():
        message[key] = value
    handler.headers = message
    handler.__dict__["wfile"] = BytesIO()
    handler.__dict__["rfile"] = BytesIO()
    handler._body_discarded = False
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    return handler


def test_unpaired_token_is_logged_by_code_never_by_token(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="wlib.extension")
    monkeypatch.setattr(server_module, "window_ref", MagicMock())
    token = "c" * 64
    code = pairing.pairing_code(pairing.token_hash(token))
    for _ in range(3):
        handler = _handler("/api/check?url=x", {"Origin": "moz-extension://uuid", "X-wLib-Extension": token})
        assert handler._require_pairing("moz-extension://uuid") is False
    lines = _messages(caplog)
    assert any(f"Pairing request from Firefox extension, code {code}" in line for line in lines)
    refused = [line for line in lines if f"Refused Firefox extension code {code}" in line]
    assert len(refused) == 1
    assert "waiting for the user to allow it in wLib" in refused[0]
    assert "/api/check" in refused[0]
    assert all(token not in line and "url=x" not in line for line in lines)


def test_missing_token_is_logged_as_outdated_extension(caplog):
    caplog.set_level(logging.INFO, logger="wlib.extension")
    handler = _handler("/api/check", {"Origin": "chrome-extension://abc"})
    assert handler._require_pairing("chrome-extension://abc") is False
    assert any(
        "Refused Chromium extension request GET /api/check: no valid pairing token" in line
        for line in _messages(caplog)
    )


def test_bridge_token_in_query_is_never_logged(caplog):
    caplog.set_level(logging.INFO, logger="wlib.extension")
    token = "d" * 64
    handler = _handler(f"/api/bridge?token={token}", {"Origin": "https://example.com"})
    handler._reject_origin()
    handler = _handler(f"/api/bridge?token={token}", {"Origin": "moz-extension://u"})
    _ = handler._pairing_state(token)
    lines = _messages(caplog)
    assert any("Refused extension server request GET /api/bridge from Origin https://example.com" in line for line in lines)
    assert all(token not in line for line in lines)


def test_wrong_host_is_logged(caplog, extension_host_request):
    caplog.set_level(logging.INFO, logger="wlib.extension")
    status = extension_host_request("example.com:8183")
    assert status == 403
    assert any(
        "Host example.com:8183 is not localhost:8183 or 127.0.0.1:8183" in line for line in _messages(caplog)
    )


@pytest.fixture
def extension_host_request(tmp_path, monkeypatch):
    from http.client import HTTPConnection

    from core import database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "extension.db"))
    database.init_db()
    server = server_module.ThreadingHTTPServer(("127.0.0.1", 0), server_module.ExtensionRequestHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    def _request(host: str) -> int:
        connection = HTTPConnection(*server.server_address, timeout=3)
        try:
            connection.putrequest("GET", "/api/check", skip_host=True)
            connection.putheader("Host", host)
            connection.putheader("X-wLib-Extension", PAIRED_EXTENSION_TOKEN)
            connection.endheaders()
            response = connection.getresponse()
            _ = response.read()
            return response.status
        finally:
            connection.close()

    try:
        yield _request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


# ---- Pairing store ----


def test_pairing_answers_are_logged(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="wlib.extension")
    store = pairing.PairingStore(str(tmp_path))
    allowed, refused = "e" * 64, "f" * 64
    _ = store.check(allowed, "moz-extension://a")
    _ = store.check(refused, "chrome-extension://b")
    pending = {entry["origin"]: entry["id"] for entry in store.snapshot()["pending"]}
    assert store.resolve(pending["moz-extension://a"], True)
    assert store.resolve(pending["chrome-extension://b"], False)
    assert store.revoke(pending["moz-extension://a"])
    lines = _messages(caplog)
    assert f"Allowed Firefox browser extension {pending['moz-extension://a']}" in lines
    assert f"Refused Chromium browser extension {pending['chrome-extension://b']} until wLib restarts" in lines
    assert f"Removed browser extension {pending['moz-extension://a']}" in lines


def test_full_pending_queue_is_logged(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="wlib.extension")
    store = pairing.PairingStore(str(tmp_path))
    for index in range(pairing.MAX_PENDING + 1):
        _ = store.check(str(index) * 64, "moz-extension://x")
    assert any("requests already wait for an answer in wLib" in line for line in _messages(caplog))


def test_unreadable_pairing_file_is_logged(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="wlib.extension")
    (tmp_path / pairing.PAIRING_FILE).write_text("{not json", encoding="utf-8")
    store = pairing.PairingStore(str(tmp_path))
    assert store.check("1" * 64, "moz-extension://x") == ("pending", True)
    lines = _messages(caplog)
    assert any("every browser extension must be allowed again" in line for line in lines)
    assert "Allowed browser extensions: none" in lines
