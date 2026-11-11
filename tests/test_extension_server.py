# SPDX-License-Identifier: GPL-3.0-or-later
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from io import BytesIO
from email.message import Message
import os
import socket
import json
import threading
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock
from urllib.request import Request, urlopen

import pytest

from conftest import PAIRED_EXTENSION_TOKEN
from core import extension_server as server_module
from core.api import Api


def _make_extension_handler(path, matching_game=None, headers=None):
    handler = server_module.ExtensionRequestHandler.__new__(server_module.ExtensionRequestHandler)
    handler.path = path
    message_headers = Message()
    # Paired by default (see conftest); a test of the gate sends its own header.
    for key, value in {
        "X-wLib-Extension": PAIRED_EXTENSION_TOKEN,
        **(headers or {}),
    }.items():
        message_headers[key] = value
    handler.headers = message_headers
    handler.__dict__["wfile"] = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler._find_matching_game = MagicMock(return_value=matching_game)
    return handler


@pytest.mark.parametrize("extension", ["png", "avif"])
def test_cover_route_is_read_only_without_cors(tmp_path, monkeypatch, extension):
    from core import database, game_covers

    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    folder = tmp_path / "covers"
    source = tmp_path / "source.png"
    data = (
        (Path(__file__).parent / "fixtures/playnite/avif/cover.png").read_bytes()
        if extension == "avif" else b"\x89PNG\r\n\x1a\nimage"
    )
    source.write_bytes(data)
    name = game_covers.copy_cover_file(source)
    assert name.endswith("." + extension)
    handler = _make_extension_handler(
        "/covers/" + name, headers={"Origin": "https://untrusted.example"}
    )
    handler.do_GET()
    handler.send_response.assert_called_once_with(200)
    headers = dict(call.args for call in handler.send_header.call_args_list)
    assert headers["Content-Type"] == "image/" + extension
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "immutable" in headers["Cache-Control"]
    assert "Access-Control-Allow-Origin" not in headers
    assert handler.wfile.getvalue() == (folder / name).read_bytes()
    assert handler.wfile.getvalue() == data


def test_cover_route_serves_card_copies(tmp_path, monkeypatch):
    from core import database

    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    folder = tmp_path / "covers" / "cards"
    folder.mkdir(parents=True)
    name = "c" * 32 + ".anim.webp"
    (folder / name).write_bytes(b"RIFFdata")
    handler = _make_extension_handler("/covers/cards/" + name)
    handler.do_GET()
    handler.send_response.assert_called_once_with(200)
    headers = dict(call.args for call in handler.send_header.call_args_list)
    assert headers["Content-Type"] == "image/webp"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert handler.wfile.getvalue() == b"RIFFdata"


@pytest.mark.parametrize(
    "path",
    [
        "/covers/../wlib.db",
        "/covers/",
        "/covers/" + "b" * 32 + ".jpg",
        "/covers/%2e%2e/wlib.db",
        "/covers/cards/../../wlib.db",
        "/covers/cards/",
        "/covers/cards/" + "b" * 32 + ".still.webp",
        "/covers/cards/" + "b" * 32 + ".still.png",
        "/covers/cards/" + "b" * 32 + ".gif",
    ],
)
def test_cover_route_rejects_invalid_names(path):
    handler = _make_extension_handler(path)
    handler.send_error = MagicMock()
    handler.do_GET()
    handler.send_error.assert_called_once_with(404)


def test_dropbox_callback_without_origin(monkeypatch):
    handler = _make_extension_handler("/dropbox/callback?code=one&state=two")
    handler.server = MagicMock()
    callback = handler.server.sync_api.complete_dropbox_callback
    callback.return_value = {"success": True}
    handler.do_GET()
    callback.assert_called_once_with("one", "two")
    handler.send_response.assert_called_once_with(200)
    assert b"You can close this tab" in handler.wfile.getvalue()


@pytest.mark.parametrize(
    "path,method,label",
    [
        ("/dropbox/callback", "complete_dropbox_callback", "Dropbox"),
        ("/gdrive/callback", "complete_gdrive_callback", "Google Drive"),
    ],
)
@pytest.mark.parametrize("success,status", [(True, 200), (False, 400)])
def test_sync_oauth_callbacks_over_http(path, method, label, success, status):
    server = server_module.ExtensionServer(("127.0.0.1", 0), server_module.ExtensionRequestHandler)
    server.sync_api = MagicMock()
    getattr(server.sync_api, method).return_value = {"success": success}
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    connection = HTTPConnection(*server.server_address, timeout=3)
    try:
        connection.request(
            "GET", f"{path}?code=one&state=two", headers={"Host": "localhost:8183"}
        )
        response = connection.getresponse()
        page = response.read().decode()
        assert response.status == status
        assert response.getheader("Content-Security-Policy") == "default-src 'none'"
        assert (f"{label} connected" in page) is success
        assert (f"{label} sign-in failed" in page) is not success
        getattr(server.sync_api, method).assert_called_once_with("one", "two")
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dropbox_callback_origin_rules_unchanged():
    handler = _make_extension_handler(
        "/dropbox/callback?code=one&state=two",
        headers={"Origin": "https://untrusted.example"},
    )
    handler.server = MagicMock()
    handler.do_GET()
    handler.send_response.assert_called_once_with(403)
    handler.server.sync_api.complete_dropbox_callback.assert_not_called()


def test_extension_check_payload_includes_play_status_for_matches():
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game={
            "id": 1,
            "title": "Demo",
            "f95_url": "https://f95zone.to/threads/demo.123/",
            "play_status": "Playing",
            "exe_path": "/tmp/demo.sh",
        },
    )

    handler.do_GET()

    response_body = cast(BytesIO, handler.wfile).getvalue().decode("utf-8")

    assert json.loads(response_body) == {
        "exists": True,
        "playStatus": "Playing",
        "wishlisted": False,
        "gameId": 1,
        "app_version": server_module.APP_VERSION,
    }


def test_extension_check_reports_wishlisted_for_empty_executable():
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game={"exe_path": "  "},
    )
    handler.do_GET()
    assert json.loads(handler.wfile.getvalue()) == {
        "exists": True,
        "wishlisted": True,
        "app_version": server_module.APP_VERSION,
    }


def test_extension_check_payload_omits_play_status_for_missing_matches():
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game=None,
    )

    handler.do_GET()

    response_body = cast(BytesIO, handler.wfile).getvalue().decode("utf-8")

    assert json.loads(response_body) == {
        "exists": False,
        "app_version": server_module.APP_VERSION,
    }


def _mock_startup_sync(monkeypatch, status):
    sync_api = MagicMock()
    sync_api.get_startup_extension_sync_status.return_value = status
    monkeypatch.setattr(server_module, "api_ref", sync_api)
    return sync_api


def test_extension_check_includes_app_and_extension_versions(monkeypatch):
    _mock_startup_sync(monkeypatch, {"success": True, "installed_version": "1.0.11"})
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game={
            "id": 1,
            "title": "Demo",
            "f95_url": "https://f95zone.to/threads/demo.123/",
            "play_status": "Playing",
            "exe_path": "/tmp/demo.sh",
        },
    )

    handler.do_GET()

    assert json.loads(cast(BytesIO, handler.wfile).getvalue().decode("utf-8")) == {
        "exists": True,
        "playStatus": "Playing",
        "wishlisted": False,
        "gameId": 1,
        "app_version": server_module.APP_VERSION,
        "extension_version": "1.0.11",
    }


def test_extension_check_without_url_still_reports_versions(monkeypatch):
    _mock_startup_sync(monkeypatch, {"success": True, "installed_version": "1.0.11"})
    handler = _make_extension_handler("/api/check")

    handler.do_GET()

    assert json.loads(cast(BytesIO, handler.wfile).getvalue().decode("utf-8")) == {
        "exists": False,
        "app_version": server_module.APP_VERSION,
        "extension_version": "1.0.11",
    }


def test_extension_check_reports_versions_on_lookup_error(monkeypatch):
    _mock_startup_sync(monkeypatch, {"success": True, "installed_version": "1.0.11"})
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/"
    )
    handler._find_matching_game = MagicMock(side_effect=RuntimeError("db down"))

    handler.do_GET()

    assert json.loads(cast(BytesIO, handler.wfile).getvalue().decode("utf-8")) == {
        "exists": False,
        "app_version": server_module.APP_VERSION,
        "extension_version": "1.0.11",
    }


@pytest.mark.parametrize(
    "status",
    [
        {"success": False, "error": "sync failed"},
        {"success": True, "installed_version": ""},
        {"success": True},
    ],
)
def test_extension_check_omits_extension_version_when_sync_failed(monkeypatch, status):
    _mock_startup_sync(monkeypatch, status)
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/demo.123/",
        matching_game={"exe_path": "  "},
    )

    handler.do_GET()

    payload = json.loads(cast(BytesIO, handler.wfile).getvalue().decode("utf-8"))
    assert payload["app_version"] == server_module.APP_VERSION
    assert "extension_version" not in payload


@pytest.fixture
def extension_server(tmp_path, monkeypatch):
    from core import database

    # Requests resolve links against the library, so never touch the real one.
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "extension.db"))
    database.init_db()
    server = server_module.ThreadingHTTPServer(("127.0.0.1", 0), server_module.ExtensionRequestHandler)
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


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/check?url=https://f95zone.to/threads/demo.123/"),
        ("POST", "/api/add"),
        ("OPTIONS", "/api/add"),
    ],
)
@pytest.mark.parametrize(
    "hosts,origin,dev_mode,status",
    [
        (("localhost:8183",), None, False, 200),
        (("127.0.0.1:8183",), None, False, 200),
        (("LOCALHOST:8183",), "chrome-extension://wlib", False, 200),
        (("localhost:8183",), "moz-extension://wlib", False, 200),
        (("localhost:8183",), "http://localhost:5173", True, 200),
        (("localhost:8183",), "http://localhost:5173", False, 403),
        (("localhost:8183",), "https://example.com", True, 403),
        (("example.com:8183",), None, False, 403),
        (("example.com:8183",), "chrome-extension://wlib", False, 403),
        (("localhost",), None, False, 403),
        (("localhost:8184",), None, True, 403),
        ((), None, False, 403),
        (("localhost:8183", "example.com:8183"), None, False, 403),
    ],
)
def test_extension_request_validates_host_and_origin(
    monkeypatch, extension_server, method, path, hosts, origin, dev_mode, status
):
    lookup = MagicMock(return_value={"play_status": "Playing"})
    window = MagicMock()
    monkeypatch.setattr(server_module.ExtensionRequestHandler, "_find_matching_game", lookup)
    monkeypatch.setattr(server_module, "window_ref", window)
    monkeypatch.setattr(server_module, "DEV_MODE", dev_mode)
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.putrequest(method, path, skip_host=True)
        for host in hosts:
            connection.putheader("Host", host)
        if origin is not None:
            connection.putheader("Origin", origin)
        connection.putheader("X-wLib-Extension", PAIRED_EXTENSION_TOKEN)
        request_body = json.dumps(
            {"title": "Game", "url": "https://f95zone.to/threads/123/"}
        ).encode()
        connection.putheader("Content-Length", str(len(request_body)))
        connection.endheaders(request_body)
        response = connection.getresponse()
        body = response.read()
        assert response.status == status
        if status == 403:
            assert json.loads(body)["success"] is False
            assert response.getheader("Access-Control-Allow-Origin") is None
            lookup.assert_not_called()
            window.evaluate_js.assert_not_called()
        else:
            assert response.getheader("Access-Control-Allow-Origin") == origin
    finally:
        connection.close()


@pytest.mark.parametrize(
    "headers,status",
    [
        ({}, 403),  # what <img src="http://127.0.0.1:8183/api/open?..."> sends
        # An extension Origin or the bare header is not enough to open a game.
        ({"X-wLib-Extension": "1"}, 403),
        ({"Origin": "chrome-extension://wlib"}, 403),
        ({"X-wLib-Extension": "b" * 64}, 403),
        ({"X-wLib-Extension": PAIRED_EXTENSION_TOKEN}, 200),
        (
            {
                "Origin": "chrome-extension://wlib",
                "X-wLib-Extension": PAIRED_EXTENSION_TOKEN,
            },
            200,
        ),
    ],
)
def test_extension_open_requires_extension_origin_or_header(
    monkeypatch, extension_server, headers, status
):
    window = MagicMock()
    monkeypatch.setattr(server_module, "window_ref", window)
    monkeypatch.setattr(
        server_module.ExtensionRequestHandler, "_find_matching_game", lambda _self, _url: None
    )
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request(
            "GET",
            "/api/open?url=https://f95zone.to/threads/demo.123/",
            headers={"Host": "localhost:8183", **headers},
        )
        response = connection.getresponse()
        _ = response.read()
        assert response.status == status
        opened = any(
            "wlib-extension-open" in call.args[0]
            for call in window.evaluate_js.call_args_list
        )
        assert opened is (status == 200)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "window,content_length,status",
    [
        (MagicMock(), "abc", 400),
        (MagicMock(), "-1", 400),
        (None, "2", 503),
    ],
)
def test_extension_add_always_replies_with_failure(
    monkeypatch, extension_server, window, content_length, status
):
    monkeypatch.setattr(server_module, "window_ref", window)
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.putrequest("POST", "/api/add", skip_host=True)
        connection.putheader("Host", "localhost:8183")
        connection.putheader("X-wLib-Extension", PAIRED_EXTENSION_TOKEN)
        connection.putheader("Content-Length", content_length)
        # No body when the length is invalid: the server can't read it, and
        # unread data makes Windows reset the connection before the reply.
        connection.endheaders(b"{}" if content_length == "2" else None)
        response = connection.getresponse()
        assert response.status == status
        assert json.loads(response.read())["success"] is False
    finally:
        connection.close()


def test_extension_wishlist_saves_without_executable_and_rejects_duplicate(
    monkeypatch, tmp_path, extension_server
):
    from core.database import get_all_games

    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wishlist.db"))
    api = Api()
    monkeypatch.setattr(
        api.scraper, "get_thread_metadata", lambda *_args: {"success": False}
    )
    monkeypatch.setattr(server_module, "api_ref", api)
    window = MagicMock()
    monkeypatch.setattr(server_module, "window_ref", window)
    body = json.dumps(
        {
            "title": "Wishlist demo",
            "url": "https://f95zone.to/threads/wishlist.555/",
            "version": "1.0",
            "coverImage": "https://example.com/cover.jpg",
            "tags": "sandbox",
            "rating": "4",
            "developer": "Studio",
            "engine": "Ren'Py",
        }
    )

    def send():
        connection = HTTPConnection(*extension_server.server_address, timeout=3)
        try:
            connection.request(
                "POST",
                "/api/wishlist",
                body,
                headers={"Host": "localhost:8183", "Origin": "chrome-extension://wlib", "X-wLib-Extension": PAIRED_EXTENSION_TOKEN},
            )
            reply = connection.getresponse()
            return reply.status, json.loads(reply.read())
        finally:
            connection.close()

    status, payload = send()
    assert status == 200 and payload["success"] is True
    game = get_all_games()[0]
    assert game["title"] == "Wishlist demo"
    assert game["exe_path"] == ""
    assert game["engine"] == "Ren'Py"
    assert game["tags"] == "sandbox"
    assert window.evaluate_js.called

    status, payload = send()
    assert status == 409 and payload["error_code"] == "duplicate_url"
    assert len(get_all_games()) == 1


@pytest.mark.parametrize(
    ("origin", "oversized", "expected_status"),
    [
        ("https://example.com", False, 403),
        ("chrome-extension://wlib", True, 413),
    ],
)
def test_extension_wishlist_rejects_web_origin_and_oversized_body(
    monkeypatch, extension_server, origin, oversized, expected_status
):
    api = MagicMock()
    monkeypatch.setattr(server_module, "api_ref", api)
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.putrequest("POST", "/api/wishlist", skip_host=True)
        connection.putheader("Host", "localhost:8183")
        connection.putheader("Origin", origin)
        connection.putheader("X-wLib-Extension", PAIRED_EXTENSION_TOKEN)
        connection.putheader(
            "Content-Length",
            str(server_module.MAX_EXTENSION_BODY_BYTES + 1) if oversized else "2",
        )
        # The oversized body is sent in full, as a browser would, so the reply
        # must arrive as a 413 rather than a connection reset.
        connection.endheaders(
            b"{}" if not oversized else b" " * (server_module.MAX_EXTENSION_BODY_BYTES + 1)
        )
        reply = connection.getresponse()
        assert reply.status == expected_status
        body = json.loads(reply.read())
        assert body["success"] is False
        if oversized:
            assert body["error"] == "Request body is too large"
            assert reply.getheader("Access-Control-Allow-Origin") == origin
        api.add_game.assert_not_called()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "", "url": "https://f95zone.to/threads/demo.1/"},
        {"title": "Demo", "url": ""},
        {"title": "Demo", "url": "https://example.com/threads/demo.1/"},
    ],
)
def test_extension_wishlist_requires_title_and_thread_url(
    monkeypatch, extension_server, payload
):
    api = MagicMock()
    monkeypatch.setattr(server_module, "api_ref", api)
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request(
            "POST",
            "/api/wishlist",
            json.dumps(payload),
            headers={"Host": "localhost:8183", "Origin": "chrome-extension://wlib", "X-wLib-Extension": PAIRED_EXTENSION_TOKEN},
        )
        reply = connection.getresponse()
        assert reply.status == 400
        assert json.loads(reply.read())["success"] is False
        api.add_game.assert_not_called()
    finally:
        connection.close()


def test_extension_check_remains_responsive_while_open_waits(
    monkeypatch, extension_server
):
    evaluating = threading.Event()
    release = threading.Event()

    def evaluate_js(_script):
        evaluating.set()
        assert release.wait(5)

    window = MagicMock()
    window.evaluate_js.side_effect = evaluate_js
    monkeypatch.setattr(server_module, "window_ref", window)
    monkeypatch.setattr(
        server_module.ExtensionRequestHandler,
        "_find_matching_game",
        lambda _self, _url: None,
    )

    def request(path):
        address, port = extension_server.server_address
        with urlopen(
            Request(
                f"http://{address}:{port}{path}",
                headers={
                    "Host": "localhost:8183",
                    "X-wLib-Extension": PAIRED_EXTENSION_TOKEN,
                },
            ),
            timeout=3,
        ) as response:
            return json.loads(response.read())

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending_open = executor.submit(
            request, "/api/open?url=https://f95zone.to/threads/demo.123/"
        )
        try:
            assert evaluating.wait(3)
            assert request("/api/check?url=__ping__") == {
                "exists": False,
                "app_version": server_module.APP_VERSION,
            }
        finally:
            release.set()
        assert pending_open.result(timeout=3) == {"success": True}


def test_bind_extension_server_uses_threaded_loopback_server(monkeypatch):
    server_factory = MagicMock()
    monkeypatch.setattr(server_module, "ExtensionServer", server_factory)

    assert server_module.bind_extension_server() is server_factory.return_value
    server_factory.assert_called_once_with(
        ("127.0.0.1", 8183), server_module.ExtensionRequestHandler
    )


def test_second_instance_cannot_share_the_extension_port():
    first = server_module.ExtensionServer(("127.0.0.1", 0), server_module.ExtensionRequestHandler)
    try:
        with pytest.raises(OSError):
            server_module.ExtensionServer(first.server_address, server_module.ExtensionRequestHandler)
    finally:
        first.server_close()


@pytest.mark.parametrize("path", ["/api/checkpoint", "/api/check-evil", "/api/openx"])
def test_extension_get_rejects_prefix_squat_paths(monkeypatch, extension_server, path):
    window = MagicMock()
    monkeypatch.setattr(server_module, "window_ref", window)
    monkeypatch.setattr(
        server_module.ExtensionRequestHandler, "_find_matching_game", lambda _s, _u: None
    )
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request(
            "GET",
            f"{path}?url=https://f95zone.to/threads/demo.123/",
            headers={"Host": "localhost:8183", "Origin": "chrome-extension://wlib", "X-wLib-Extension": PAIRED_EXTENSION_TOKEN},
        )
        response = connection.getresponse()
        _ = response.read()
        assert response.status == 404
        window.evaluate_js.assert_not_called()
    finally:
        connection.close()


def test_extension_handler_times_out_slow_clients():
    assert server_module.ExtensionRequestHandler.timeout == 10


def _bridge_request(
    server,
    *,
    origin=None,
    host="localhost:8183",
    upgrade=True,
    token=PAIRED_EXTENSION_TOKEN,
):
    connection = HTTPConnection(*server.server_address, timeout=3)
    try:
        connection.putrequest("GET", f"/api/bridge?token={token}", skip_host=True)
        connection.putheader("Host", host)
        if origin is not None:
            connection.putheader("Origin", origin)
        if upgrade:
            connection.putheader("Upgrade", "websocket")
            connection.putheader("Connection", "Upgrade")
            connection.putheader(
                "Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ=="
            )
            connection.putheader("Sec-WebSocket-Version", "13")
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        accept = response.getheader("Sec-WebSocket-Accept")
        return response.status, body, accept
    finally:
        connection.close()


def test_bridge_upgrades_for_extension_origin(extension_server, monkeypatch):
    served = {}
    served_event = threading.Event()

    def fake_serve(sock, rfile, wfile, pairing_id=""):
        served["timeout"] = sock.gettimeout()
        served_event.set()
        # Close immediately so the handler thread exits; the 101 was sent.
        try:
            sock.shutdown(2)
        except OSError:
            pass

    monkeypatch.setattr("core.extension_bridge.serve_bridge_connection", fake_serve)
    status, _body, accept = _bridge_request(
        extension_server, origin="chrome-extension://wlib"
    )
    assert status == 101
    assert accept == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    # The 101 reaches the client before the handler thread calls the bridge.
    assert served_event.wait(5)
    assert served.get("timeout") == 60


def test_bridge_upgrades_for_firefox_origin(extension_server, monkeypatch):
    monkeypatch.setattr(
        "core.extension_bridge.serve_bridge_connection",
        lambda *_args, **_kwargs: None,
    )
    status, _body, _accept = _bridge_request(
        extension_server, origin="moz-extension://wlib"
    )
    assert status == 101


def test_bridge_handshake_uses_http_1_1_status_line(extension_server, monkeypatch):
    monkeypatch.setattr(
        "core.extension_bridge.serve_bridge_connection",
        lambda *_args, **_kwargs: None,
    )
    with socket.create_connection(extension_server.server_address, timeout=3) as sock:
        sock.sendall(
            b"GET /api/bridge?token=" + PAIRED_EXTENSION_TOKEN.encode() + b" HTTP/1.1\r\n"
            b"Host: localhost:8183\r\n"
            b"Origin: moz-extension://wlib\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: keep-alive, Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        status_line = sock.makefile("rb").readline()
    # Firefox rejects a WebSocket handshake answered with HTTP/1.0.
    assert status_line == b"HTTP/1.1 101 Switching Protocols\r\n"


def test_bridge_rejects_web_origin(extension_server):
    status, body, _accept = _bridge_request(
        extension_server, origin="https://example.com"
    )
    assert status == 403
    assert json.loads(body)["success"] is False


def test_bridge_rejects_missing_origin(extension_server):
    status, body, _accept = _bridge_request(extension_server, origin=None)
    assert status == 403
    assert json.loads(body)["success"] is False


def test_bridge_rejects_missing_upgrade(extension_server):
    status, _body, _accept = _bridge_request(
        extension_server, origin="chrome-extension://wlib", upgrade=False
    )
    assert status == 400


def test_bridge_rejects_bad_host(extension_server):
    status, body, _accept = _bridge_request(
        extension_server,
        origin="chrome-extension://wlib",
        host="attacker.test:8183",
    )
    assert status == 403
    assert json.loads(body)["success"] is False


def test_store_page_wishlist_check_open_and_thread_links(
    monkeypatch, tmp_path, extension_server
):
    from urllib.parse import urlencode
    from core.api import Api
    from core import database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "links.db"))
    database.init_db()
    api = Api()
    monkeypatch.setattr(api, "_fetch_added_game_metadata", lambda *_: None)
    monkeypatch.setattr(server_module, "api_ref", api)
    window = MagicMock()
    monkeypatch.setattr(server_module, "window_ref", window)
    headers = {"Host": "localhost:8183", "Origin": "chrome-extension://wlib", "X-wLib-Extension": PAIRED_EXTENSION_TOKEN}
    page = "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html"
    payload = {"title": "Work", "pageUrl": page, "links": [{"url": page}]}
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request("POST", "/api/wishlist", json.dumps(payload), headers)
        response = connection.getresponse()
        assert response.status == 200
        game_id = json.loads(response.read())["id"]
        connection.request(
            "GET", "/api/check?" + urlencode({"url": page}), headers=headers
        )
        response = connection.getresponse()
        checked = json.loads(response.read())
        assert (
            checked["exists"] and checked["gameId"] == game_id and checked["wishlisted"]
        )
        connection.request(
            "GET", "/api/open?" + urlencode({"url": page}), headers=headers
        )
        response = connection.getresponse()
        assert json.loads(response.read())["gameId"] == game_id
        assert "gameId:" in window.evaluate_js.call_args[0][0]
        connection.request("POST", "/api/wishlist", json.dumps(payload), headers)
        response = connection.getresponse()
        assert response.status == 409
        response.read()
        connection.request(
            "GET",
            "/api/check?"
            + urlencode(
                {"url": "https://f95zone.to/threads/123/", "id": "dlsite:RJ123"}
            ),
            headers=headers,
        )
        response = connection.getresponse()
        assert json.loads(response.read())["canAttachThread"]
        assert not database.get_game(game_id)["f95_url"]
        payload["url"] = "https://f95zone.to/threads/123/"
        payload["pageUrl"] = payload["url"]
        connection.request("POST", "/api/wishlist", json.dumps(payload), headers)
        response = connection.getresponse()
        assert response.status == 409
        assert json.loads(response.read())["attached"]
        assert len(database.get_all_games()) == 1
        connection.request(
            "POST",
            "/api/thread-links",
            json.dumps(
                {"url": payload["url"], "links": [{"url": "https://vndb.org/v1"}]}
            ),
            headers,
        )
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        assert {link["source"] for link in database.get_game_links(game_id)} == {
            "dlsite",
            "vndb",
        }
        connection.request(
            "POST",
            "/api/thread-links",
            "{}",
            {"Host": "localhost:8183", "Origin": "https://foreign.example"},
        )
        response = connection.getresponse()
        assert response.status == 403
        response.read()
    finally:
        connection.close()


def test_wishlist_waits_for_startup_database(
    monkeypatch, extension_server, tmp_path
):
    from core import database

    # The app serves this port before Api() has run init_db(). A wishlist call
    # in that window must get the documented 503, not a raw SQLite error from
    # resolving the payload's store links against a schema-less database.
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "not-initialized.db"))
    monkeypatch.setattr(server_module, "api_ref", None)
    monkeypatch.setattr(server_module, "DATABASE_READY_TIMEOUT", 0.05)
    extension_server.database_ready = threading.Event()
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        connection.request(
            "POST",
            "/api/wishlist",
            json.dumps(
                {
                    "title": "Wishlist demo",
                    "url": "https://f95zone.to/threads/wishlist.555/",
                }
            ),
            headers={"Host": "localhost:8183", "Origin": "chrome-extension://wlib", "X-wLib-Extension": PAIRED_EXTENSION_TOKEN},
        )
        response = connection.getresponse()
        assert response.status == 503
        assert json.loads(response.read()) == {
            "success": False,
            "error": "Application is not ready",
        }
    finally:
        connection.close()


def test_database_endpoints_wait_until_startup_has_run_init_db(
    monkeypatch, extension_server
):
    window = MagicMock()
    monkeypatch.setattr(server_module, "window_ref", None)
    monkeypatch.setattr(server_module, "DATABASE_READY_TIMEOUT", 0.05)
    ready = threading.Event()
    extension_server.database_ready = ready
    find_calls: list[object] = []
    monkeypatch.setattr(
        server_module.ExtensionRequestHandler,
        "_build_check_payload",
        lambda _s, url, _ids: find_calls.append(url) or {"exists": False},
    )
    headers = {"Host": "localhost:8183", "Origin": "chrome-extension://wlib", "X-wLib-Extension": PAIRED_EXTENSION_TOKEN}
    links = json.dumps({"url": "https://f95zone.to/threads/123/", "links": []})
    connection = HTTPConnection(*extension_server.server_address, timeout=3)
    try:
        # The instance lock still answers while the database is not ready.
        connection.request("GET", "/api/open", headers=headers)
        response = connection.getresponse()
        assert json.loads(response.read())["success"] is False
        connection.request(
            "GET", "/api/check?url=https://f95zone.to/threads/123/", headers=headers
        )
        response = connection.getresponse()
        assert response.status == 503
        not_ready = json.loads(response.read())
        assert not_ready["error"] == "Application is not ready"
        assert not_ready["app_version"] == server_module.APP_VERSION
        connection.request("POST", "/api/thread-links", links, headers)
        response = connection.getresponse()
        assert response.status == 503
        response.read()
        assert find_calls == []

        ready.set()
        monkeypatch.setattr(server_module, "window_ref", window)
        connection.request(
            "GET", "/api/check?url=https://f95zone.to/threads/123/", headers=headers
        )
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        connection.request("POST", "/api/thread-links", links, headers)
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        assert find_calls == ["https://f95zone.to/threads/123/"]
    finally:
        connection.close()


def test_cached_cover_route(tmp_path, monkeypatch):
    from core import database, game_covers

    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    data = b"\x89PNG\r\n\x1a\nimage"
    name = game_covers.store_cached_cover(data)
    handler = _make_extension_handler("/covers/cache/" + name)
    handler.do_GET()
    handler.send_response.assert_called_once_with(200)
    headers = dict(call.args for call in handler.send_header.call_args_list)
    assert headers["Content-Type"] == "image/png"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "immutable" in headers["Cache-Control"]
    assert handler.wfile.getvalue() == data


@pytest.mark.parametrize("name", ["", "../wlib.db", "%2e%2e/wlib.db", "a" * 64 + ".png", "a" * 32 + ".png"])
def test_cached_cover_route_rejects_invalid_names(name):
    handler = _make_extension_handler("/covers/cache/" + name)
    handler.send_error = MagicMock()
    handler.do_GET()
    handler.send_error.assert_called_once_with(404)


def test_runner_endpoints_require_token_and_notify_window(monkeypatch, tmp_path):
    from core import steam_runner

    monkeypatch.setattr(steam_runner, 'get_data_dir', lambda: str(tmp_path))
    token = steam_runner.runner_token()
    assert steam_runner.runner_token() == token
    if os.name != 'nt':  # Windows has no POSIX mode bits; the profile folder guards it.
        assert (tmp_path / 'runner-token').stat().st_mode & 0o077 == 0
    api = MagicMock()
    api.get_running_games.return_value = [12]
    api.reserve_runner_launch.return_value = 'Busy installing'
    monkeypatch.setattr(server_module, 'api_ref', api)
    window = MagicMock()
    monkeypatch.setattr(server_module, 'window_ref', window)
    denied = _make_extension_handler('/api/running')
    denied.do_GET()
    denied.send_response.assert_called_once_with(403)
    # The extension header alone is not enough: a sandboxed game can send it.
    denied = _make_extension_handler('/api/running', headers={'X-wLib-Extension': '1'})
    denied.do_GET()
    denied.send_response.assert_called_once_with(403)
    runner = {'X-wLib-Extension': '1', 'X-wLib-Runner': token}
    handler = _make_extension_handler('/api/running', headers=runner)
    handler.do_GET()
    assert json.loads(handler.wfile.getvalue())['games'] == [12]
    payload = b'{"gameId": 12, "prefix": "/p", "sandboxed": true}'
    handler = _make_extension_handler('/api/launch-check', headers={**runner, 'Content-Length': str(len(payload))})
    handler.__dict__['rfile'] = BytesIO(payload)
    handler.do_POST()
    assert json.loads(handler.wfile.getvalue()) == {'success': False, 'error': 'Busy installing'}
    assert api.reserve_runner_launch.call_args.args == (12, '/p')
    assert api.reserve_runner_launch.call_args.kwargs['sandboxed'] is True
    payload = b'{"gameId": 12, "state": "ended"}'
    handler = _make_extension_handler('/api/runner-event', headers={**runner, 'Content-Length': str(len(payload))})
    handler.__dict__['rfile'] = BytesIO(payload)
    handler.do_POST()
    handler.send_response.assert_called_once_with(200)
    api.release_runner_launch.assert_called_once_with(12)
    assert 'wlib-refresh-library' in window.evaluate_js.call_args.args[0]


def test_bare_open_only_shows_the_window(monkeypatch):
    """A second wLib instance sends this without a pairing token."""
    window = MagicMock()
    monkeypatch.setattr(server_module, "window_ref", window)
    handler = _make_extension_handler("/api/open", headers={"X-wLib-Extension": "1"})
    handler.do_GET()
    handler.send_response.assert_called_once_with(200)
    window.show.assert_called_once()
    window.evaluate_js.assert_not_called()


def _handler_reply(handler):
    return json.loads(handler.wfile.getvalue())


@pytest.mark.parametrize(
    "token,code",
    [
        ("1", "extension_outdated"),
        ("", "extension_outdated"),
        ("b" * 64, "pairing_required"),
    ],
)
@pytest.mark.parametrize("path", ["/api/add", "/api/wishlist", "/api/thread-links"])
def test_unpaired_extension_cannot_post(monkeypatch, path, token, code):
    window = MagicMock()
    monkeypatch.setattr(server_module, "window_ref", window)
    monkeypatch.setattr(server_module, "api_ref", MagicMock())
    handler = _make_extension_handler(
        path,
        headers={
            "Origin": "chrome-extension://other",
            "X-wLib-Extension": token,
            "Content-Length": "2",
        },
    )
    handler.rfile = BytesIO(b"{}")
    handler.do_POST()
    handler.send_response.assert_called_once_with(403)
    assert _handler_reply(handler)["error_code"] == code
    server_module.api_ref.add_game.assert_not_called()
    announced = [call.args[0] for call in window.evaluate_js.call_args_list]
    assert announced == (
        ["window.dispatchEvent(new Event('wlib-extension-pairing'))"]
        if code == "pairing_required"
        else []
    )


def test_unpaired_check_reports_versions_but_not_the_library():
    handler = _make_extension_handler(
        "/api/check?url=https://f95zone.to/threads/123/",
        matching_game={"id": 1, "exe_path": "/g"},
        headers={"Origin": "moz-extension://other", "X-wLib-Extension": "1"},
    )
    handler.do_GET()
    handler.send_response.assert_called_once_with(200)
    reply = _handler_reply(handler)
    assert reply["exists"] is False
    assert reply["error_code"] == "extension_outdated"
    assert reply["app_version"] == server_module.APP_VERSION
    assert "gameId" not in reply
    handler._find_matching_game.assert_not_called()


def test_pairing_request_is_allowed_then_removed(tmp_path):
    from core.extension_pairing import PairingStore

    store = PairingStore(str(tmp_path))
    token = "c" * 64
    assert store.check(token, "moz-extension://abc") == ("pending", True)
    assert store.check(token, "moz-extension://abc") == ("pending", False)
    request = store.snapshot()["pending"][0]
    assert request["browser"] == "Firefox"
    assert store.resolve(request["id"], True)
    assert store.check(token) == ("paired", False)
    # Only a hash reaches the disk, and a new process still knows it.
    saved = (tmp_path / "extension-pairing.json").read_text()
    assert token not in saved
    assert PairingStore(str(tmp_path)).check(token) == ("paired", False)
    assert store.revoke(request["id"])
    assert store.check(token) == ("pending", True)


def test_refused_pairing_stays_refused_and_requests_are_limited(tmp_path):
    from core.extension_pairing import MAX_PENDING, PairingStore

    store = PairingStore(str(tmp_path))
    token = "d" * 64
    assert store.check(token)[0] == "pending"
    assert store.resolve(store.snapshot()["pending"][0]["id"], False)
    assert store.check(token) == ("denied", False)
    assert store.check("not-a-token") == ("invalid", False)
    for index in range(MAX_PENDING):
        assert store.check(f"{index:064x}")[0] == "pending"
    assert store.check("e" * 64) == ("denied", False)


def test_unpaired_bridge_is_refused(extension_server):
    status, _headers, _body = _bridge_request(
        extension_server, origin="chrome-extension://other", token="b" * 64
    )
    assert status == 403
