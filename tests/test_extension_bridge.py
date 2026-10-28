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
