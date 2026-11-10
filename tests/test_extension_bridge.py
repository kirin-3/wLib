# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import threading
import time

import pytest

from core import extension_bridge as bridge
from core import extension_ws
from core.extension_bridge import (
    BridgeConnection,
    BridgeFailed,
    BridgeUnavailable,
)


@pytest.fixture(autouse=True)
def _clean_bridge():
    bridge.reset_for_tests()
    yield
    bridge.reset_for_tests()


def test_rfc6455_sample_handshake_key():
    # RFC 6455 section 1.3 sample vector.
    assert (
        extension_ws.websocket_accept_key("dGhlIHNhbXBsZSBub25jZQ==")
        == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    )


def test_server_frame_roundtrip_short():
    payload = b"hi"
    frame = extension_ws.encode_server_text("hi")
    assert frame[0] == 0x81
    assert frame[1] == 2
    assert frame[2:] == payload


def test_client_masked_frame_16bit_length():
    body = b"x" * 200
    raw = extension_ws.encode_client_frame(extension_ws.OP_TEXT, body)
    assert raw[1] == 0x80 | 126
    fin, opcode, payload = extension_ws.read_frame(io.BytesIO(raw))
    assert fin is True
    assert opcode == extension_ws.OP_TEXT
    assert payload == body


def test_client_masked_frame_64bit_length():
    body = b"y" * 70000
    raw = extension_ws.encode_client_frame(extension_ws.OP_TEXT, body)
    assert raw[1] == 0x80 | 127
    fin, opcode, payload = extension_ws.read_frame(io.BytesIO(raw))
    assert payload == body


def test_continuation_reassembly():
    first = extension_ws.encode_client_frame(
        extension_ws.OP_TEXT, b"hel", fin=False
    )
    second = extension_ws.encode_client_frame(
        extension_ws.OP_CONT, b"lo", fin=True
    )
    stream = io.BytesIO(first + second)
    kind, payload = extension_ws.recv_message(stream)
    assert kind == "text"
    assert payload == b"hello"


def test_control_frame_between_fragments_keeps_message():
    # RFC 6455 lets a control frame sit between the fragments of a data message.
    state = extension_ws.MessageState()
    stream = io.BytesIO(
        extension_ws.encode_client_frame(extension_ws.OP_TEXT, b"hel", fin=False)
        + extension_ws.encode_client_frame(extension_ws.OP_PING, b"p")
        + extension_ws.encode_client_frame(extension_ws.OP_CONT, b"lo", fin=True)
    )
    assert extension_ws.recv_message(stream, state) == ("ping", b"p")
    assert extension_ws.recv_message(stream, state) == ("text", b"hello")


def test_bridge_loop_keeps_fragments_across_control_frame(monkeypatch):
    messages: list[object] = []
    monkeypatch.setattr(
        bridge,
        "_handle_message",
        lambda _connection, message: messages.append(message),
    )
    first = extension_ws.encode_client_frame(
        extension_ws.OP_TEXT, b'{"type":"he', fin=False
    )
    ping = extension_ws.encode_client_frame(extension_ws.OP_PING, b"p")
    last = extension_ws.encode_client_frame(
        extension_ws.OP_CONT, b'llo","protocol":1,"can_fetch":true}', fin=True
    )
    out = io.BytesIO()

    bridge.serve_bridge_connection(object(), io.BytesIO(first + ping + last), out)

    assert messages == [{"type": "hello", "protocol": 1, "can_fetch": True}]
    assert out.getvalue() == extension_ws.encode_server_pong(b"p")


def test_oversize_frame_rejected():
    header = bytes([0x81, 0x80 | 127]) + (9 * 1024 * 1024).to_bytes(8, "big")
    stream = io.BytesIO(header + b"\x01\x02\x03\x04")
    with pytest.raises(extension_ws.MessageTooLarge):
        extension_ws.read_frame(stream)


def test_oversize_reassembled_message_rejected():
    chunk = b"z" * (5 * 1024 * 1024)
    first = extension_ws.encode_client_frame(
        extension_ws.OP_TEXT, chunk, fin=False
    )
    second = extension_ws.encode_client_frame(
        extension_ws.OP_CONT, chunk, fin=True
    )
    with pytest.raises(extension_ws.MessageTooLarge):
        extension_ws.recv_message(io.BytesIO(first + second))


def test_close_frame_roundtrip():
    raw = extension_ws.encode_client_frame(extension_ws.OP_CLOSE, b"\x03\xe8")
    kind, payload = extension_ws.recv_message(io.BytesIO(raw))
    assert kind == "close"
    assert payload == b"\x03\xe8"


def test_unmasked_client_frame_rejected():
    raw = extension_ws.encode_server_text("hi")
    with pytest.raises(extension_ws.WebSocketError):
        extension_ws.read_frame(io.BytesIO(raw))


def _fake_conn(sent: list[str]) -> BridgeConnection:
    return BridgeConnection(send_text=lambda text: sent.append(text))


def test_bridge_success():
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 1, "can_fetch": True}
    )
    assert bridge.is_available()

    result_holder: dict[str, object] = {}

    def _fetch():
        try:
            result_holder["response"] = bridge.fetch(
                "https://f95zone.to/threads/1/", timeout=5
            )
        except Exception as exc:  # pragma: no cover - test fails loudly
            result_holder["error"] = exc

    thread = threading.Thread(target=_fetch, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not sent and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sent
    request = json.loads(sent[0])
    assert request["type"] == "fetch"
    bridge._handle_message(
        conn,
        {
            "type": "fetch_result",
            "id": request["id"],
            "ok": True,
            "status": 200,
            "final_url": "https://f95zone.to/threads/slug.1/",
            "body": "<html>ok</html>",
        },
    )
    thread.join(timeout=5)
    assert not thread.is_alive()
    response = result_holder["response"]
    assert isinstance(response, bridge.BridgeResponse)
    assert response.body == "<html>ok</html>"
    assert response.status == 200


def test_bridge_timeout():
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 1, "can_fetch": True}
    )
    with pytest.raises(BridgeFailed):
        bridge.fetch("https://f95zone.to/threads/1/", timeout=0.05)


def test_bridge_error_result():
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 1, "can_fetch": True}
    )

    def _fetch():
        with pytest.raises(BridgeFailed):
            bridge.fetch("https://f95zone.to/threads/1/", timeout=5)

    thread = threading.Thread(target=_fetch, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not sent and time.monotonic() < deadline:
        time.sleep(0.01)
    request = json.loads(sent[0])
    bridge._handle_message(
        conn,
        {"type": "fetch_result", "id": request["id"], "ok": False, "error": "too_large"},
    )
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_bridge_disconnect_fails_pending_at_once():
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 1, "can_fetch": True}
    )
    errors: list[str] = []

    def _fetch():
        try:
            bridge.fetch("https://f95zone.to/threads/1/", timeout=5)
        except BridgeFailed as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=_fetch, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while len(sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    bridge._remove_connection(conn)
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len(errors) == 2
    assert bridge.is_available() is False


def test_bridge_newest_connection_selection():
    first_sent: list[str] = []
    second_sent: list[str] = []
    first = _fake_conn(first_sent)
    second = _fake_conn(second_sent)
    bridge._register_connection(first)
    bridge._handle_message(first, {"type": "hello", "protocol": 1, "can_fetch": True})
    bridge._register_connection(second)
    bridge._handle_message(second, {"type": "hello", "protocol": 1, "can_fetch": True})

    def _fetch():
        try:
            bridge.fetch("https://f95zone.to/threads/1/", timeout=0.2)
        except BridgeFailed:
            pass

    thread = threading.Thread(target=_fetch, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert second_sent and not first_sent


def test_bridge_can_fetch_false_ignored():
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 1, "can_fetch": False}
    )
    assert bridge.is_available() is False
    with pytest.raises(BridgeUnavailable):
        bridge.fetch("https://f95zone.to/threads/1/", timeout=0.05)
    # Status update enables the same connection.
    bridge._handle_message(conn, {"type": "status", "can_fetch": True})
    assert bridge.is_available() is True


def test_bridge_ping_answered_with_pong():
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._handle_message(conn, {"type": "ping"})
    assert sent
    assert json.loads(sent[0]) == {"type": "pong"}


def test_bridge_status_snapshot_tracks_connections():
    conn = _fake_conn([])
    bridge._register_connection(conn)
    bridge._handle_message(
        conn,
        {
            "type": "hello",
            "protocol": 1,
            "can_fetch": True,
            "extension_version": "1.2.0",
        },
    )
    snapshot = bridge.status_snapshot()
    assert snapshot["connected"] is True
    assert snapshot["eligible"] is True
    assert snapshot["extension_version"] == "1.2.0"
    assert snapshot["can_fetch"] is True
    assert snapshot["disconnects"] == 0
    assert snapshot["last_fetch"] is None
    json.dumps(snapshot)

    bridge._remove_connection(conn)
    bridge._remove_connection(conn)
    snapshot = bridge.status_snapshot()
    assert snapshot["connected"] is False
    assert snapshot["can_fetch"] is None
    # A repeated removal must not count a second disconnect.
    assert snapshot["disconnects"] == 1
    assert snapshot["last_connection_seconds"] == 0
    assert snapshot["last_disconnect_seconds_ago"] == 0


def test_bridge_records_fetch_outcome_and_logs_state_changes(caplog):
    caplog.set_level("INFO", logger="wlib.bridge")
    bridge.record_fetch_outcome(200, False)
    bridge.record_fetch_outcome(200, False)
    guest_logs = [r for r in caplog.records if "without a login" in r.getMessage()]
    assert len(guest_logs) == 1
    bridge.record_fetch_outcome(200, True)
    assert any("while logged in" in r.getMessage() for r in caplog.records)

    last_fetch = bridge.status_snapshot()["last_fetch"]
    assert last_fetch == {
        "status": 200,
        "signed_in": True,
        "error": "",
        "seconds_ago": 0,
    }

    bridge.record_fetch_outcome(None, None, "extension did not answer in time")
    last_fetch = bridge.status_snapshot()["last_fetch"]
    assert isinstance(last_fetch, dict)
    assert last_fetch["error"] == "extension did not answer in time"


def test_resolve_requires_capable_extension_and_preserves_fragment():
    sent = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 1, "can_fetch": True})
    with pytest.raises(BridgeUnavailable):
        bridge.resolve("https://f95zone.to/dl/1/?fake", "mega")
    bridge._handle_message(conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_resolve": True})
    results = []
    thread = threading.Thread(target=lambda: results.append(bridge.resolve("https://f95zone.to/dl/1/?fake", "mega", timeout=1)))
    thread.start()
    deadline = time.monotonic() + 1
    while not sent and time.monotonic() < deadline:
        time.sleep(0.005)
    request = json.loads(sent[0])
    bridge._handle_message(conn, {"type": "resolve_result", "id": request["id"], "ok": True, "url": "https://mega.nz/file/a#key"})
    thread.join(1)
    assert results[0]["url"].endswith("#key")


def test_resolve_disconnect_wakes_waiter():
    sent = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_resolve": True})
    errors = []
    def run():
        try:
            bridge.resolve("https://f95zone.to/dl/1/?fake", "mega")
        except BridgeFailed as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 1
    while not sent and time.monotonic() < deadline:
        time.sleep(0.005)
    bridge._remove_connection(conn)
    thread.join(1)
    assert errors == ["disconnected"]


def test_resolves_serialize_and_needs_user_extends_deadline():
    sent = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_resolve": True})
    results = []
    stages = []
    def run():
        results.append(bridge.resolve("https://f95zone.to/dl/1/?fake", "mega", timeout=0.1, on_progress=stages.append))
    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    deadline = time.monotonic() + 1
    while not sent and time.monotonic() < deadline:
        time.sleep(0.005)
    second.start()
    request = json.loads(sent[0])
    bridge._handle_message(conn, {"type": "resolve_progress", "id": request["id"], "stage": "needs_user"})
    time.sleep(0.15)
    assert len(sent) == 1 and stages == ["needs_user"]
    bridge._handle_message(conn, {"type": "resolve_result", "id": request["id"], "ok": True})
    deadline = time.monotonic() + 1
    while len(sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    request = json.loads(sent[1])
    bridge._handle_message(conn, {"type": "resolve_result", "id": request["id"], "ok": True})
    first.join(1)
    second.join(1)
    assert len(results) == 2


def test_resolve_cancel_sends_cancellation():
    sent = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {'type':'hello','protocol':2,'can_fetch':True,'can_resolve':True})
    cancelled = threading.Event()
    errors = []
    def run():
        try:
            bridge.resolve('https://f95zone.to/dl/1/?u=fake', 'mega', cancel=cancelled)
        except BridgeFailed as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 1
    while not sent and time.monotonic() < deadline:
        time.sleep(.005)
    cancelled.set()
    thread.join(1)
    assert errors == ['cancelled']
    assert json.loads(sent[-1]) == {'type':'resolve_cancel','id':json.loads(sent[0])['id']}


def test_resolve_timeout_asks_extension_to_close_its_window():
    sent = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {'type':'hello','protocol':2,'can_fetch':True,'can_resolve':True})
    with pytest.raises(BridgeFailed, match='in time'):
        bridge.resolve('https://f95zone.to/dl/1/?u=fake', 'mega', timeout=0.05)
    assert json.loads(sent[-1])['type'] == 'resolve_cancel'


def test_fetch_on_connection_closed_before_register_fails_fast(monkeypatch):
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 1, "can_fetch": True})
    # The disconnect lands after the connection is picked but before the
    # request is registered, so its cleanup never sees this request.
    monkeypatch.setattr(bridge, "_newest_eligible", lambda: conn)
    bridge._remove_connection(conn)
    started = time.monotonic()
    with pytest.raises(bridge.BridgeUnavailable):
        _ = bridge.fetch("https://f95zone.to/threads/1/", timeout=5)
    assert time.monotonic() - started < 1
    assert sent == []
    assert not bridge._pending


def test_removed_pairing_cuts_only_its_own_sockets():
    from core import extension_bridge

    cut: list[str] = []
    for pairing_id in ("AAAA-1111", "BBBB-2222"):
        conn = extension_bridge.BridgeConnection()
        conn.pairing_id = pairing_id
        conn.shutdown = lambda pairing_id=pairing_id: cut.append(pairing_id)
        extension_bridge._register_connection(conn)
    try:
        extension_bridge.close_paired_connections("AAAA-1111")
        assert cut == ["AAAA-1111"]
    finally:
        extension_bridge.reset_for_tests()
