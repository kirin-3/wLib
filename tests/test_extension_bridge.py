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
    first = extension_ws.encode_client_frame(extension_ws.OP_TEXT, b"hel", fin=False)
    second = extension_ws.encode_client_frame(extension_ws.OP_CONT, b"lo", fin=True)
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
    first = extension_ws.encode_client_frame(extension_ws.OP_TEXT, chunk, fin=False)
    second = extension_ws.encode_client_frame(extension_ws.OP_CONT, chunk, fin=True)
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
    bridge._handle_message(conn, {"type": "hello", "protocol": 1, "can_fetch": True})
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
    bridge._handle_message(conn, {"type": "hello", "protocol": 1, "can_fetch": True})
    with pytest.raises(BridgeFailed):
        bridge.fetch("https://f95zone.to/threads/1/", timeout=0.05)


def test_bridge_error_result():
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 1, "can_fetch": True})

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
        {
            "type": "fetch_result",
            "id": request["id"],
            "ok": False,
            "error": "too_large",
        },
    )
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_bridge_disconnect_fails_pending_at_once():
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 1, "can_fetch": True})
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
    bridge._handle_message(conn, {"type": "hello", "protocol": 1, "can_fetch": False})
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
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_resolve": True}
    )
    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            bridge.resolve("https://f95zone.to/dl/1/?fake", "mega", timeout=1)
        )
    )
    thread.start()
    deadline = time.monotonic() + 1
    while not sent and time.monotonic() < deadline:
        time.sleep(0.005)
    request = json.loads(sent[0])
    bridge._handle_message(
        conn,
        {
            "type": "resolve_result",
            "id": request["id"],
            "ok": True,
            "url": "https://mega.nz/file/a#key",
        },
    )
    thread.join(1)
    assert results[0]["url"].endswith("#key")


def test_resolve_disconnect_wakes_waiter():
    sent = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_resolve": True}
    )
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
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_resolve": True}
    )
    results = []
    stages = []

    def run():
        results.append(
            bridge.resolve(
                "https://f95zone.to/dl/1/?fake",
                "mega",
                timeout=0.1,
                on_progress=stages.append,
            )
        )

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    deadline = time.monotonic() + 1
    while not sent and time.monotonic() < deadline:
        time.sleep(0.005)
    second.start()
    request = json.loads(sent[0])
    bridge._handle_message(
        conn, {"type": "resolve_progress", "id": request["id"], "stage": "needs_user"}
    )
    time.sleep(0.15)
    assert len(sent) == 1 and stages == ["needs_user"]
    bridge._handle_message(
        conn, {"type": "resolve_result", "id": request["id"], "ok": True}
    )
    deadline = time.monotonic() + 1
    while len(sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    request = json.loads(sent[1])
    bridge._handle_message(
        conn, {"type": "resolve_result", "id": request["id"], "ok": True}
    )
    first.join(1)
    second.join(1)
    assert len(results) == 2


def test_resolve_cancel_sends_cancellation():
    sent = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_resolve": True}
    )
    cancelled = threading.Event()
    errors = []

    def run():
        try:
            bridge.resolve("https://f95zone.to/dl/1/?u=fake", "mega", cancel=cancelled)
        except BridgeFailed as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 1
    while not sent and time.monotonic() < deadline:
        time.sleep(0.005)
    cancelled.set()
    thread.join(1)
    assert errors == ["cancelled"]
    assert json.loads(sent[-1]) == {
        "type": "resolve_cancel",
        "id": json.loads(sent[0])["id"],
    }


def test_resolve_timeout_asks_extension_to_close_its_window():
    sent = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_resolve": True}
    )
    with pytest.raises(BridgeFailed, match="in time"):
        bridge.resolve("https://f95zone.to/dl/1/?u=fake", "mega", timeout=0.05)
    assert json.loads(sent[-1])["type"] == "resolve_cancel"


def test_fetch_on_connection_closed_before_register_fails_fast(monkeypatch):
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 1, "can_fetch": True})
    # The disconnect lands after the connection is picked but before the
    # request is registered, so its cleanup never sees this request.
    monkeypatch.setattr(bridge, "_newest_eligible", lambda _site: conn)
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


def test_lc_bridge_capability_is_independent_and_paced(monkeypatch):
    now = 100.0
    requests = []

    def sleep(delay):
        nonlocal now
        now += delay

    monkeypatch.setattr(bridge.time, "monotonic", lambda: now)
    monkeypatch.setattr(bridge.time, "sleep", sleep)

    def connection(**hello):
        def send(raw):
            message = json.loads(raw)
            requests.append((now, conn, message.get("site", "f95")))
            bridge._handle_message(
                conn,
                {
                    "type": "fetch_result",
                    "id": message["id"],
                    "ok": True,
                    "body": "ok",
                },
            )

        conn = BridgeConnection(send)
        bridge._register_connection(conn)
        bridge._handle_message(conn, {"type": "hello", "protocol": 2, **hello})
        return conn

    old = connection(can_fetch=True)
    url = "https://lewdcorner.com/threads/123/"
    assert not old.can_fetch_lc
    with pytest.raises(BridgeUnavailable):
        bridge.fetch(url, site="lc")
    lc = connection(can_fetch=False, can_fetch_lc=True)
    newest = connection(can_fetch=True, can_fetch_lc=False)
    assert bridge.fetch(url, site="lc").body == "ok"
    assert bridge.fetch("https://f95zone.to/threads/123/").body == "ok"
    assert bridge.fetch(url, site="lc").body == "ok"
    assert requests == [(100, lc, "lc"), (100, newest, "f95"), (102, lc, "lc")]
    bridge._handle_message(newest, {"type": "status", "can_fetch_lc": True})
    assert bridge.fetch(url, site="lc").body == "ok"
    assert requests[-1] == (104, newest, "lc")
    for conn in (lc, newest):
        bridge._handle_message(conn, {"type": "status", "can_fetch_lc": False})
    assert not bridge.is_available(site="lc")
    assert bridge.is_available()


def test_lc_resolve_requires_its_capability_and_shares_lc_pacing(monkeypatch):
    starts = []
    now = [10.0]
    conn: BridgeConnection

    def send(message):
        request = json.loads(message)
        if request["type"] == "fetch":
            response = {
                "type": "fetch_result",
                "ok": True,
                "status": 200,
                "body": "<html>",
            }
        else:
            response = {
                "type": "resolve_result",
                "ok": True,
                "url": "https://mega.nz/file/abc#key",
            }
        assert bridge._lc_request_lock.locked()
        starts.append((request["type"], now[0]))
        bridge._handle_message(conn, {**response, "id": request["id"]})

    def sleep(delay):
        now[0] += delay

    conn = BridgeConnection(send)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn,
        {"type": "hello", "protocol": 2, "can_fetch_lc": True, "can_resolve": True},
    )
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(time, "sleep", sleep)
    url = "https://lewdcorner.com/masked/out?t=fake"
    with pytest.raises(BridgeUnavailable):
        bridge.resolve(url, "mega", site="lc")
    assert starts == []
    assert bridge.fetch("https://lewdcorner.com/threads/1/", site="lc").status == 200
    bridge._handle_message(conn, {"type": "status", "can_resolve_lc": True})
    assert bridge.status_snapshot()["can_resolve_lc"]
    assert (
        bridge.resolve(url, "mega", site="lc")["url"] == "https://mega.nz/file/abc#key"
    )
    assert starts == [("fetch", 10.0), ("resolve", 12.0)]
    assert not bridge._lc_request_lock.locked()
    bridge._handle_message(conn, {"type": "status", "can_resolve_lc": False})
    assert not bridge.status_snapshot()["can_resolve_lc"]


LC_IMAGE = "https://lewdcorner.com/attachments/cover-webp.891912/"


def _image_connection(answer=None, **hello):
    """A connection whose extension answers fetch_image with ``answer(message)`` chunks."""
    import base64

    sent = []

    def send(raw):
        message = json.loads(raw)
        sent.append(message)
        if answer is None:
            return
        for chunk in answer(message):
            if isinstance(chunk.get("data"), bytes):
                chunk = {**chunk, "data": base64.b64encode(chunk["data"]).decode()}
            bridge._handle_message(
                conn, {"type": "fetch_image_chunk", "id": message["id"], **chunk}
            )

    conn = BridgeConnection(send)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 2, **hello})
    return conn, sent


def _ok(data=b"img", **extra):
    return {
        "index": 0,
        "ok": True,
        "status": 200,
        "content_type": "image/webp",
        "final_url": LC_IMAGE,
        "data": data,
        "last": True,
        **extra,
    }


def test_lc_image_fetch_needs_its_flag():
    older, older_sent = _image_connection(can_fetch_lc=True)
    assert not bridge.can_fetch_lc_images()
    with pytest.raises(BridgeUnavailable):
        bridge.fetch_image(LC_IMAGE, background=False)
    assert older_sent == []
    assert bridge.status_snapshot()["can_fetch_lc_images"] is False
    _newer, newer_sent = _image_connection(
        lambda _m: [_ok()], can_fetch_lc=True, can_fetch_lc_images=True
    )
    answer = bridge.fetch_image(LC_IMAGE, background=False)
    assert (answer.status, answer.content_type, answer.body) == (
        200,
        "image/webp",
        b"img",
    )
    assert newer_sent[0] == {
        "type": "fetch_image",
        "id": newer_sent[0]["id"],
        "url": LC_IMAGE,
    }
    assert older_sent == []
    assert bridge.status_snapshot()["can_fetch_lc_images"] is True


def test_lc_image_listener_runs_when_the_flag_turns_true():
    calls = []
    bridge.add_lc_images_listener(lambda: calls.append(1))
    conn, _sent = _image_connection(can_fetch_lc=True)
    assert calls == []
    bridge._handle_message(conn, {"type": "status", "can_fetch_lc_images": True})
    bridge._handle_message(conn, {"type": "status", "can_fetch_lc_images": True})
    assert calls == [1]
    _image_connection(can_fetch_lc_images=True)
    assert calls == [1, 1]


def test_lc_image_chunks_are_joined_and_checked(monkeypatch):
    parts = [b"a" * 10, b"b" * 10, b"c" * 3]
    _image_connection(
        lambda _m: [
            _ok(parts[0], last=False),
            {"index": 1, "data": parts[1], "last": False},
            {"index": 2, "data": parts[2], "last": True},
        ],
        can_fetch_lc_images=True,
    )
    assert bridge.fetch_image(LC_IMAGE, background=False).body == b"".join(parts)

    bridge.reset_for_tests()
    _image_connection(
        lambda _m: [_ok(last=False), {"index": 2, "data": b"x", "last": True}],
        can_fetch_lc_images=True,
    )
    with pytest.raises(BridgeFailed, match="chunk missing"):
        bridge.fetch_image(LC_IMAGE, background=False)

    bridge.reset_for_tests()
    monkeypatch.setattr(bridge, "MAX_IMAGE_BYTES", 15)
    _image_connection(
        lambda _m: [
            _ok(b"a" * 10, last=False),
            {"index": 1, "data": b"b" * 10, "last": True},
        ],
        can_fetch_lc_images=True,
    )
    with pytest.raises(BridgeFailed, match="too_large"):
        bridge.fetch_image(LC_IMAGE, background=False)

    bridge.reset_for_tests()
    _image_connection(
        lambda _m: [{"index": 0, "ok": False, "error": "refused", "last": True}],
        can_fetch_lc_images=True,
    )
    with pytest.raises(BridgeFailed, match="refused"):
        bridge.fetch_image(LC_IMAGE, background=False)

    bridge.reset_for_tests()
    _image_connection(
        lambda _m: [_ok(b"", status=500, content_type="text/html")],
        can_fetch_lc_images=True,
    )
    answer = bridge.fetch_image(LC_IMAGE, background=False)
    assert (answer.status, answer.content_type, answer.body) == (500, "text/html", b"")

    bridge.reset_for_tests()
    _image_connection(None, can_fetch_lc_images=True)
    with pytest.raises(BridgeFailed, match="in time"):
        bridge.fetch_image(LC_IMAGE, background=False, timeout=0.01)
    assert bridge._pending_images == {}


def test_background_lc_images_keep_their_gap_and_hourly_limit(monkeypatch):
    now = [1000.0]
    sends = []
    monkeypatch.setattr(bridge.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        bridge.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    bridge._lc_request_lock.timed_wait = lambda _cond, seconds: now.__setitem__(
        0, now[0] + seconds
    )
    _image_connection(
        lambda _m: sends.append(now[0]) or [_ok()], can_fetch_lc_images=True
    )
    for _ in range(3):
        _ = bridge.fetch_image(LC_IMAGE, background=True)
    assert sends == [1000.0, 1005.0, 1010.0]
    # A foreground fetch keeps the normal 2 s gap.
    _ = bridge.fetch_image(LC_IMAGE, background=False)
    assert sends[-1] == 1012.0
    now[0] = 2000.0
    for _ in range(bridge.LC_BACKGROUND_PER_HOUR - 3):
        _ = bridge.fetch_image(LC_IMAGE, background=True)
    with pytest.raises(bridge.LcImageLimitReached):
        bridge.fetch_image(LC_IMAGE, background=True)
    count = len(sends)
    now[0] = 1000.0 + 3600.5
    _ = bridge.fetch_image(LC_IMAGE, background=True)
    assert len(sends) == count + 1


def test_foreground_lc_requests_go_before_waiting_background_images(monkeypatch):
    monkeypatch.setattr(bridge, "LC_BACKGROUND_GAP_SECONDS", 0)
    monkeypatch.setattr(bridge, "LC_GAP_SECONDS", 0)
    order = []
    _image_connection(
        lambda message: order.append(message["url"]) or [_ok()],
        can_fetch_lc_images=True,
    )
    gate = bridge._lc_request_lock
    gate.acquire_foreground()  # another LC request in flight
    background = threading.Thread(
        target=lambda: bridge.fetch_image(
            "https://lewdcorner.com/attachments/bg.1/", background=True
        )
    )
    background.start()
    time.sleep(0.05)
    foreground = threading.Thread(
        target=lambda: bridge.fetch_image(
            "https://lewdcorner.com/attachments/fg.2/", background=False
        )
    )
    foreground.start()
    while gate._waiting_foreground == 0:
        time.sleep(0.01)
    gate.release()
    foreground.join(5)
    background.join(5)
    assert order == [
        "https://lewdcorner.com/attachments/fg.2/",
        "https://lewdcorner.com/attachments/bg.1/",
    ]
    assert not gate.locked()


LIST_URL = "https://f95zone.to/watched/threads?tab=games&unread=0&sort=title&page=1"


def _first_fetch_sent(fn, sent: list[str]) -> dict[str, object]:
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert sent
    return json.loads(sent[0])


def test_old_extension_fetches_threads_but_not_lists():
    sent: list[str] = []
    conn = _fake_conn(sent)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 2, "can_fetch": True})
    assert bridge.is_available()
    assert bridge.list_fetch_unavailable_reason() == "extension_outdated"
    with pytest.raises(BridgeUnavailable) as excinfo:
        bridge.fetch_list_page(LIST_URL, timeout=0.05)
    assert excinfo.value.reason == "extension_outdated"
    assert not sent

    def _thread_fetch():
        try:
            bridge.fetch("https://f95zone.to/threads/1/", timeout=0.2)
        except BridgeFailed:
            pass

    request = _first_fetch_sent(_thread_fetch, sent)
    assert request["url"] == "https://f95zone.to/threads/1/"


def test_no_extension_reason_for_list_fetch():
    assert bridge.list_fetch_unavailable_reason() == "no_extension"
    with pytest.raises(BridgeUnavailable) as excinfo:
        bridge.fetch_list_page(LIST_URL, timeout=0.05)
    assert excinfo.value.reason == "no_extension"
    # A list-capable extension without host access is not eligible either.
    conn = _fake_conn([])
    bridge._register_connection(conn)
    bridge._handle_message(
        conn,
        {"type": "hello", "protocol": 2, "can_fetch": False, "can_fetch_lists": True},
    )
    assert bridge.list_fetch_unavailable_reason() == "no_extension"


def test_newest_list_capable_connection_wins():
    capable_sent: list[str] = []
    old_sent: list[str] = []
    newer_capable_sent: list[str] = []
    capable = _fake_conn(capable_sent)
    bridge._register_connection(capable)
    bridge._handle_message(
        capable,
        {"type": "hello", "protocol": 2, "can_fetch": True, "can_fetch_lists": True},
    )
    newer_capable = _fake_conn(newer_capable_sent)
    bridge._register_connection(newer_capable)
    bridge._handle_message(
        newer_capable,
        {"type": "hello", "protocol": 2, "can_fetch": True, "can_fetch_lists": True},
    )
    old = _fake_conn(old_sent)
    bridge._register_connection(old)
    bridge._handle_message(old, {"type": "hello", "protocol": 2, "can_fetch": True})
    assert bridge.list_fetch_unavailable_reason() is None
    assert bridge.status_snapshot()["can_fetch_lists"] is True

    def _list_fetch():
        try:
            bridge.fetch_list_page(LIST_URL, timeout=0.2)
        except BridgeFailed:
            pass

    request = _first_fetch_sent(_list_fetch, newer_capable_sent)
    assert request == {"type": "fetch", "id": request["id"], "url": LIST_URL}
    assert not capable_sent and not old_sent


def _lc_page_connection(order):
    def send(raw):
        message = json.loads(raw)
        order.append(message["url"])
        bridge._handle_message(
            conn,
            {
                "type": "fetch_result",
                "id": message["id"],
                "ok": True,
                "status": 200,
                "final_url": message["url"],
                "body": "<html></html>",
            },
        )

    conn = BridgeConnection(send)
    bridge._register_connection(conn)
    bridge._handle_message(
        conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_fetch_lc": True}
    )
    return conn


def test_lc_fetch_listener_runs_once_per_turn_on():
    calls = []
    bridge.add_lc_fetch_listener(lambda: calls.append(1))
    conn = BridgeConnection(lambda _raw: None)
    bridge._register_connection(conn)
    bridge._handle_message(conn, {"type": "hello", "protocol": 2, "can_fetch": True})
    assert calls == []
    bridge._handle_message(
        conn, {"type": "status", "can_fetch": True, "can_fetch_lc": True}
    )
    bridge._handle_message(
        conn, {"type": "status", "can_fetch": True, "can_fetch_lc": True}
    )
    assert calls == [1]
    bridge._handle_message(
        conn, {"type": "status", "can_fetch": True, "can_fetch_lc": False}
    )
    bridge._handle_message(
        conn, {"type": "status", "can_fetch": True, "can_fetch_lc": True}
    )
    assert calls == [1, 1]
    _lc_page_connection([])
    assert calls == [1, 1, 1]


def test_background_lc_page_fetch_waits_behind_foreground(monkeypatch):
    monkeypatch.setattr(bridge, "LC_GAP_SECONDS", 0)
    order = []
    _lc_page_connection(order)
    gate = bridge._lc_request_lock
    gate.acquire_foreground()  # another LC request in flight
    background = threading.Thread(
        target=lambda: bridge.fetch(
            "https://lewdcorner.com/threads/1/", site="lc", background=True
        )
    )
    background.start()
    time.sleep(0.05)
    foreground = threading.Thread(
        target=lambda: bridge.fetch("https://lewdcorner.com/threads/2/", site="lc")
    )
    foreground.start()
    while gate._waiting_foreground == 0:
        time.sleep(0.01)
    gate.release()
    foreground.join(5)
    background.join(5)
    assert order == [
        "https://lewdcorner.com/threads/2/",
        "https://lewdcorner.com/threads/1/",
    ]
    assert not gate.locked()


def test_background_lc_page_fetch_keeps_the_lc_gap(monkeypatch):
    now = [1000.0]
    sends = []
    monkeypatch.setattr(bridge.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        bridge.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    bridge._lc_request_lock.timed_wait = lambda _cond, seconds: now.__setitem__(
        0, now[0] + seconds
    )
    _lc_page_connection(sends)
    _ = bridge.fetch("https://lewdcorner.com/threads/1/", site="lc")
    start = now[0]
    sends.clear()
    _ = bridge.fetch("https://lewdcorner.com/threads/2/", site="lc", background=True)
    assert now[0] - start == pytest.approx(bridge.LC_GAP_SECONDS)
