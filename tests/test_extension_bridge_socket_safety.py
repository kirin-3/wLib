# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge socket safety: pairing removal, handshake checks, fragment limits."""

import io
import socket
import threading

import pytest

from conftest import PAIRED_EXTENSION_TOKEN
from core import extension_bridge as bridge
from core import extension_pairing as pairing
from core import extension_server as server_module
from core import extension_ws

PAIRED_ID = pairing.pairing_code(pairing.token_hash(PAIRED_EXTENSION_TOKEN))


@pytest.fixture(autouse=True)
def _clean_bridge():
    bridge.reset_for_tests()
    yield
    bridge.reset_for_tests()


# --- fragment limits -------------------------------------------------------


def _fragmented(count: int, part: bytes = b"") -> bytes:
    frames = [extension_ws.encode_client_frame(extension_ws.OP_TEXT, b"{", fin=False)]
    frames += [
        extension_ws.encode_client_frame(extension_ws.OP_CONT, part, fin=False)
        for _ in range(count - 2)
    ]
    frames.append(
        extension_ws.encode_client_frame(extension_ws.OP_CONT, b"}", fin=True)
    )
    return b"".join(frames)


def test_message_at_the_fragment_limit_is_accepted():
    stream = io.BytesIO(_fragmented(extension_ws.MAX_MESSAGE_FRAGMENTS))
    assert extension_ws.recv_message(stream) == ("text", b"{}")


def test_many_empty_fragments_are_rejected():
    stream = io.BytesIO(_fragmented(extension_ws.MAX_MESSAGE_FRAGMENTS + 1))
    state = extension_ws.MessageState()
    with pytest.raises(extension_ws.MessageTooLarge):
        _ = extension_ws.recv_message(stream, state)
    assert not state.active
    assert state.frames == 0
    assert len(state.buffer) == 0


def test_fragmented_bytes_over_the_limit_are_rejected():
    chunk = b"x" * (extension_ws.MAX_MESSAGE_BYTES // 2 + 1)
    stream = io.BytesIO(
        extension_ws.encode_client_frame(extension_ws.OP_TEXT, chunk, fin=False)
        + extension_ws.encode_client_frame(extension_ws.OP_CONT, chunk, fin=True)
    )
    with pytest.raises(extension_ws.MessageTooLarge):
        _ = extension_ws.recv_message(stream)


def test_bridge_closes_with_1009_on_too_many_fragments():
    out = io.BytesIO()
    bridge.serve_bridge_connection(
        object(), io.BytesIO(_fragmented(extension_ws.MAX_MESSAGE_FRAGMENTS + 1)), out
    )
    assert out.getvalue() == extension_ws.encode_server_close(1009)


# --- pairing removal -------------------------------------------------------


def _serve_in_thread(pairing_id: str) -> tuple[threading.Thread, socket.socket]:
    server_side, client_side = socket.socketpair()
    client_side.settimeout(5)
    thread = threading.Thread(
        target=bridge.serve_bridge_connection,
        args=(
            server_side,
            server_side.makefile("rb"),
            server_side.makefile("wb", buffering=0),
            pairing_id,
        ),
        daemon=True,
    )
    thread.start()
    return thread, client_side


def _wait_registered() -> None:
    for _ in range(500):
        with bridge._lock:
            if bridge._connections:
                return
        threading.Event().wait(0.01)
    raise AssertionError("bridge connection was not registered")


def test_revoke_in_the_store_closes_the_socket():
    thread, client = _serve_in_thread(PAIRED_ID)
    try:
        _wait_registered()
        assert pairing.store().revoke(PAIRED_ID)
        thread.join(5)
        assert not thread.is_alive()
        assert bridge._connections == []
    finally:
        client.close()


def test_revoke_before_registration_closes_the_socket(monkeypatch):
    # The removal lands after the HTTP pairing check but before the socket
    # is in the registry, so close_paired_connections cannot see it.
    register = bridge._register_connection

    def revoke_then_register(conn: bridge.BridgeConnection) -> None:
        assert pairing.store().revoke(PAIRED_ID)
        register(conn)

    monkeypatch.setattr(bridge, "_register_connection", revoke_then_register)
    thread, client = _serve_in_thread(PAIRED_ID)
    try:
        thread.join(5)
        assert not thread.is_alive()
        assert bridge._connections == []
    finally:
        client.close()


def test_eviction_at_the_pairing_limit_closes_the_socket():
    thread, client = _serve_in_thread(PAIRED_ID)
    try:
        _wait_registered()
        store = pairing.store()
        for index in range(pairing.MAX_PAIRED):
            token = f"{index + 1:064x}"
            _ = store.check(token, "chrome-extension://other")
            request = next(
                entry
                for entry in store.snapshot()["pending"]
                if entry["id"] == pairing.pairing_code(pairing.token_hash(token))
            )
            assert store.resolve(request["id"], True)
        assert not store.is_paired_id(PAIRED_ID)
        thread.join(5)
        assert not thread.is_alive()
        assert bridge._connections == []
    finally:
        client.close()


def test_paired_socket_stays_open():
    thread, client = _serve_in_thread(PAIRED_ID)
    try:
        _wait_registered()
        thread.join(0.2)
        assert thread.is_alive()
    finally:
        client.close()
        thread.join(5)


# --- handshake -------------------------------------------------------------


@pytest.fixture
def bridge_server(monkeypatch):
    monkeypatch.setattr(
        "core.extension_bridge.serve_bridge_connection",
        lambda *_args, **_kwargs: None,
    )
    server = server_module.ThreadingHTTPServer(
        ("127.0.0.1", 0), server_module.ExtensionRequestHandler
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


GOOD_HEADERS = {
    "Host": "localhost:8183",
    "Origin": "chrome-extension://wlib",
    "Upgrade": "websocket",
    "Connection": "keep-alive, Upgrade",
    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
    "Sec-WebSocket-Version": "13",
}


def _handshake(server, *, version="HTTP/1.1", **changes: str | None) -> bytes:
    headers = dict(GOOD_HEADERS)
    for name, value in changes.items():
        name = name.replace("_", "-")
        if value is None:
            _ = headers.pop(name, None)
        else:
            headers[name] = value
    request = f"GET /api/bridge?token={PAIRED_EXTENSION_TOKEN} {version}\r\n"
    request += "".join(f"{name}: {value}\r\n" for name, value in headers.items())
    with socket.create_connection(server.server_address, timeout=3) as sock:
        sock.sendall((request + "\r\n").encode())
        return sock.makefile("rb").readline()


def test_valid_handshake_switches_protocols(bridge_server):
    assert _handshake(bridge_server).startswith(b"HTTP/1.1 101 ")


@pytest.mark.parametrize(
    "changes",
    [
        {"Connection": None},
        {"Connection": "keep-alive"},
        {"Upgrade": "not-websocket"},
        {"Sec-WebSocket-Key": None},
        {"Sec-WebSocket-Key": "abc"},
        {"Sec-WebSocket-Key": "dGhpcyBpcyBub3Qgc2l4dGVlbiBieXRlcw=="},
        {"Sec-WebSocket-Key": "not base64!!"},
    ],
)
def test_incomplete_handshake_is_refused(bridge_server, changes):
    assert b" 400 " in _handshake(bridge_server, **changes)


def test_http_1_0_handshake_is_refused(bridge_server):
    assert b" 400 " in _handshake(bridge_server, version="HTTP/1.0")


@pytest.mark.parametrize("version", [None, "8", "14"])
def test_unsupported_websocket_version_gets_426(bridge_server, version):
    assert b" 426 " in _handshake(bridge_server, **{"Sec-WebSocket-Version": version})


def test_unpaired_handshake_is_still_403(bridge_server):
    line = _handshake(bridge_server)
    assert line.startswith(b"HTTP/1.1 101 ")
    assert pairing.store().revoke(PAIRED_ID)
    assert b" 403 " in _handshake(bridge_server)
