# SPDX-License-Identifier: GPL-3.0-or-later
"""Local HTTP server for the wLib browser extension on 127.0.0.1:8183.

main() binds it, then sets api_ref and window_ref once the API and the window exist.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Protocol, cast, override
from collections.abc import Callable

from core.api import APP_VERSION
from core.f95zone import extract_thread_id, normalize_thread_url

if TYPE_CHECKING:
    from webview import Window

    from core.api import Api


DEV_MODE = os.environ.get("DEV_MODE", "0") == "1"
VITE_DEV_SERVER = "http://localhost:5173"
EXTENSION_REQUEST_HEADER = "X-wLib-Extension"
# Answers for an extension the user has not allowed (core/extension_pairing.py).
PAIRING_ERRORS: dict[str, dict[str, str]] = {
    "pending": {
        "error": "Allow this browser extension in the wLib app, then try again.",
        "error_code": "pairing_required",
    },
    "denied": {
        "error": "wLib refused this browser extension. Restart wLib to ask again.",
        "error_code": "pairing_denied",
    },
    "invalid": {
        "error": "Update the wLib browser extension to connect.",
        "error_code": "extension_outdated",
    },
}
# Sync sign-in redirects: path -> (Api method, provider name for the result page).
OAUTH_CALLBACKS = {
    "/dropbox/callback": ("complete_dropbox_callback", "Dropbox"),
    "/gdrive/callback": ("complete_gdrive_callback", "Google Drive"),
}
MAX_EXTENSION_BODY_BYTES = 1024 * 1024
# An oversized body is read and dropped up to this size so the client gets the
# 413 reply; anything larger is cut off with a connection reset.
MAX_DISCARDED_BODY_BYTES = 64 * 1024 * 1024
# Stays under the extension's 10 s request timeout.
DATABASE_READY_TIMEOUT = 8.0


window_ref: Window | None = None
api_ref: Api | None = None
window_minimized = threading.Event()


class ExtensionWindow(Protocol):
    on_top: bool

    def evaluate_js(self, script: str) -> object: ...


class _ShowableWindow(Protocol):
    def show(self) -> None: ...

    def restore(self) -> None: ...


class ExtensionRequestHandler(BaseHTTPRequestHandler):
    # Idle or stalled clients would otherwise hold a server thread forever.
    timeout = 10  # pyright: ignore[reportUnannotatedClassAttribute]
    close_connection: bool

    @override
    def parse_request(self) -> bool:
        if not super().parse_request():
            return False

        hosts = self.headers.get_all("Host", [])
        if len(hosts) == 1 and hosts[0].lower() in {
            "localhost:8183",
            "127.0.0.1:8183",
        }:
            return True

        self._discard_request_body()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        _ = self.wfile.write(b'{"success": false, "error": "Host not allowed"}')
        return False

    def _discard_request_body(self) -> None:
        # Closing with unread request data makes the OS reset the connection
        # (always on Windows), so the client sees an abort instead of our reply.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if not 0 < length <= MAX_DISCARDED_BODY_BYTES:
            return
        try:
            while length > 0:
                chunk = self.rfile.read(min(length, 64 * 1024))
                if not chunk:
                    break
                length -= len(chunk)
        except OSError:
            pass

    def _runner_request(self) -> bool:
        """True for a request from wLib's own Steam runner (see steam_runner.runner_token)."""
        import secrets

        from core.steam_runner import RUNNER_HEADER, runner_token

        sent = self.headers.get(RUNNER_HEADER) or ""
        try:
            expected = runner_token()
        except OSError:
            return False
        return bool(sent) and secrets.compare_digest(sent, expected)

    def _pairing_state(self, token: object) -> str:
        """"paired", or why this caller may not use the extension endpoints.

        An extension Origin alone proves nothing: every installed extension
        has one, and local programs send none. The caller must also hold a
        token the user allowed in the app (core/extension_pairing.py)."""
        origin = (self.headers.get("Origin") or "").strip()
        if DEV_MODE and origin == VITE_DEV_SERVER:
            return "paired"
        from core.extension_pairing import store

        state, new_request = store().check(token, origin)
        if new_request and window_ref:
            try:
                _ = cast(ExtensionWindow, window_ref).evaluate_js(
                    "window.dispatchEvent(new Event('wlib-extension-pairing'))"
                )
            except Exception as exc:
                print(f"[wLib] Failed to announce extension pairing request: {exc}")
        return state

    def _require_pairing(self, allowed_origin: str, token: object = None) -> bool:
        """True for a paired caller; otherwise answers 403 and returns False."""
        state = self._pairing_state(
            self.headers.get(EXTENSION_REQUEST_HEADER) if token is None else token
        )
        if state == "paired":
            return True
        self._discard_request_body()
        self.send_response(403)
        self._send_cors_headers(allowed_origin)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        _ = self.wfile.write(
            json.dumps({"success": False, **PAIRING_ERRORS[state]}).encode()
        )
        return False

    def _wait_for_database(self) -> bool:
        # main() serves this port before Api() has run init_db(), so database
        # endpoints wait for the schema instead of reading it mid-migration.
        ready = getattr(getattr(self, "server", None), "database_ready", None)
        return not isinstance(ready, threading.Event) or ready.wait(
            DATABASE_READY_TIMEOUT
        )

    def _find_matching_game(
        self, url: object, ids: Sequence[str] = ()
    ) -> dict[str, object] | None:
        if not isinstance(url, str) or not url.strip():
            return None

        from core.database import find_game_by_f95_url, get_game
        from core.game_lookup import resolve_page

        thread_game = find_game_by_f95_url(url)
        if thread_game:
            return thread_game
        matches = resolve_page(url, ids)["matches"]
        return get_game(matches[0]) if matches else None

    def _build_check_payload(
        self, url: object, ids: Sequence[str] = ()
    ) -> dict[str, object]:
        matching_game = (
            self._find_matching_game(url, ids) if ids else self._find_matching_game(url)
        )
        payload: dict[str, object] = {"exists": matching_game is not None}

        if matching_game is not None:
            if isinstance(matching_game.get("id"), int):
                payload["gameId"] = matching_game["id"]
            if (
                extract_thread_id(url)
                and not matching_game.get("f95_url")
                and isinstance(matching_game.get("id"), int)
            ):
                from core.game_lookup import resolve_page

                if len(resolve_page(str(url), ids)["matches"]) == 1:
                    payload["canAttachThread"] = True
            payload["wishlisted"] = not str(matching_game.get("exe_path") or "").strip()
            play_status = matching_game.get("play_status")
            if isinstance(play_status, str) and play_status.strip():
                payload["playStatus"] = play_status.strip()

        return payload

    def _prepare_game_payload(self, raw: object) -> dict[str, object]:
        from core.game_lookup import resolve_page
        from core.store_links import coerce_store_links

        if not isinstance(raw, dict):
            raise ValueError("Request body must be a JSON object")
        payload = dict(cast(dict[str, object], raw))
        page_url = str(payload.get("pageUrl") or payload.get("url") or "")
        links = coerce_store_links(payload.get("links"))
        if not extract_thread_id(payload.get("url")) and not links:
            raise ValueError("An F95 thread URL or store link is required")
        resolved = resolve_page(
            page_url, [f"{link['source']}:{link['id']}" for link in links]
        )
        links = coerce_store_links([*links, *resolved["ids"]])
        for key, value in resolved["catalog_entry"].items():
            if value:
                payload[key] = value
        payload["url"] = (
            normalize_thread_url(payload.get("url"))
            if extract_thread_id(payload.get("url"))
            else ""
        )
        payload["pageUrl"] = page_url
        payload["links"] = links
        if not str(payload.get("title") or "").strip() or not (payload["url"] or links):
            raise ValueError("A title and an F95 thread URL or store link are required")
        return payload

    def _get_allowed_origin(self) -> str | None:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return ""

        if origin.startswith(("chrome-extension://", "moz-extension://")):
            return origin

        if DEV_MODE and origin == VITE_DEV_SERVER:
            return origin

        return None

    def _send_cors_headers(self, allowed_origin: str) -> None:
        self.send_header("Vary", "Origin")
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", f"Content-Type, {EXTENSION_REQUEST_HEADER}"
        )

    def _reject_origin(self) -> None:
        self._discard_request_body()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        _ = self.wfile.write(b'{"success": false, "error": "Origin not allowed"}')

    @override
    def log_message(self, format: str, *args: object) -> None:
        # Suppress default HTTP server logs to keep the console clean
        pass

    def do_OPTIONS(self) -> None:
        allowed_origin = self._get_allowed_origin()
        if allowed_origin is None:
            self._reject_origin()
            return

        self._discard_request_body()
        self.send_response(200, "ok")
        self._send_cors_headers(allowed_origin)
        self.end_headers()

    def _is_bridge_origin_allowed(self, origin: str) -> bool:
        return origin.startswith(
            ("chrome-extension://", "moz-extension://")
        )

    def _handle_bridge_upgrade(self) -> None:
        upgrade = (self.headers.get("Upgrade") or "").lower()
        if "websocket" not in upgrade:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(
                b'{"success": false, "error": "WebSocket upgrade required"}'
            )
            return

        origin = (self.headers.get("Origin") or "").strip()
        if not self._is_bridge_origin_allowed(origin):
            self._reject_origin()
            return
        # Browsers cannot set headers on a WebSocket, so the token is a query value.
        from urllib.parse import parse_qs, urlparse

        token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        if not self._require_pairing("", token):
            return

        key = (self.headers.get("Sec-WebSocket-Key") or "").strip()
        if not key:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(
                b'{"success": false, "error": "Missing Sec-WebSocket-Key"}'
            )
            return

        try:
            from core.extension_ws import websocket_accept_key

            accept = websocket_accept_key(key)
        except Exception:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(
                b'{"success": false, "error": "Invalid Sec-WebSocket-Key"}'
            )
            return

        self._discard_request_body()
        # RFC 6455 requires an HTTP/1.1 status line; Firefox drops the socket
        # on BaseHTTPRequestHandler's default "HTTP/1.0 101" while Chromium
        # accepts it.
        self.protocol_version: str = "HTTP/1.1"
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        sock = cast(socket.socket, self.request)
        try:
            sock.settimeout(60)
        except OSError:
            pass
        try:
            from core import extension_bridge

            from core.extension_pairing import pairing_code, token_hash

            extension_bridge.serve_bridge_connection(
                sock, self.rfile, self.wfile, pairing_code(token_hash(token))
            )
        except Exception as exc:
            print(f"[wLib] Bridge connection failed: {exc}")
        finally:
            self.close_connection = True

    def do_GET(self) -> None:
        from urllib.parse import urlparse as _urlparse

        if _urlparse(self.path).path.startswith("/covers/"):
            from core.cover_cards import card_path
            from core.game_covers import COVER_TYPES, cover_path, cached_cover_path

            name = _urlparse(self.path).path.removeprefix("/covers/")
            if name.startswith("cards/"):
                path = card_path(name.removeprefix("cards/"))
            elif name.startswith("cache/"):
                path = cached_cover_path(name.removeprefix("cache/"))
            else:
                path = cover_path(name)
            try:
                if path is None:
                    raise FileNotFoundError
                data = path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", COVER_TYPES[path.suffix[1:]])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.end_headers()
            _ = self.wfile.write(data)
            return

        if _urlparse(self.path).path == "/api/bridge":
            self._handle_bridge_upgrade()
            return

        allowed_origin = self._get_allowed_origin()
        if allowed_origin is None:
            self._reject_origin()
            return

        self._discard_request_body()
        from urllib.parse import urlparse, parse_qs

        sign_in = OAUTH_CALLBACKS.get(urlparse(self.path).path)
        if sign_in is not None:
            method, label = sign_in
            query = parse_qs(urlparse(self.path).query)
            callback_api = cast(ExtensionServer, self.server).sync_api
            result = (
                cast(
                    Callable[[str, str], dict[str, object]],
                    getattr(callback_api, method),
                )(query.get("code", [""])[0], query.get("state", [""])[0])
                if callback_api is not None
                else {"success": False}
            )
            self.send_response(200 if result.get("success") else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.end_headers()
            message = (
                f"{label} connected. You can close this tab."
                if result.get("success")
                else f"{label} sign-in failed or expired. Return to wLib and connect again."
            )
            _ = self.wfile.write(
                f"<!doctype html><html lang='en'><meta charset='utf-8'><title>wLib {label}</title><p>{message}</p></html>".encode()
            )
        elif urlparse(self.path).path == "/api/check":
            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)
            check_url = query.get("url", [""])[0]

            check_payload: dict[str, object] = {"exists": False}
            # An unpaired caller still gets 200 with the versions, so an old
            # extension can see the new version and reload itself, but it
            # learns nothing about the library.
            pairing = self._pairing_state(self.headers.get(EXTENSION_REQUEST_HEADER))
            if pairing != "paired":
                check_payload.update(PAIRING_ERRORS[pairing])
                check_url = ""
            if check_url and not self._wait_for_database():
                self.send_response(503)
                self._send_cors_headers(allowed_origin)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                not_ready = {
                    "exists": False,
                    "error": "Application is not ready",
                    "app_version": APP_VERSION,
                }
                _ = self.wfile.write(json.dumps(not_ready).encode())
                return
            if check_url:
                try:
                    check_payload = self._build_check_payload(
                        check_url, query.get("id", [])
                    )
                except Exception as e:
                    print(f"[wLib] Database error during extension check: {e}")
                    check_payload = {"exists": False}

            check_payload["app_version"] = APP_VERSION
            sync_api = getattr(getattr(self, "server", None), "sync_api", None)
            if sync_api is None:
                sync_api = api_ref
            if sync_api is not None:
                try:
                    sync_status = sync_api.get_startup_extension_sync_status()
                    installed_version = sync_status.get("installed_version")
                    if sync_status.get("success") and installed_version:
                        check_payload["extension_version"] = installed_version
                except Exception:
                    pass

            self.send_response(200)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(check_payload).encode())
        elif urlparse(self.path).path == "/api/running":
            if not self._runner_request():
                self._reject_origin()
                return
            ready = api_ref is not None and window_ref is not None and self._wait_for_database()
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            games = api_ref.get_running_games() if ready and api_ref is not None else []
            _ = self.wfile.write(json.dumps({"success": ready, "games": games}).encode())
        elif urlparse(self.path).path == "/api/open":
            from urllib.parse import urlparse, parse_qs

            # Any web page can fire this GET without an Origin (e.g. <img src>);
            # require an allowed Origin or the header only the extension sends.
            if not allowed_origin and not self.headers.get(EXTENSION_REQUEST_HEADER):
                self._reject_origin()
                return

            query = parse_qs(urlparse(self.path).query)
            game_url = query.get("url", [""])[0]
            # A bare call only shows the window (a second wLib instance does
            # this); opening a game needs a paired extension.
            if (game_url or query.get("id")) and not self._require_pairing(
                allowed_origin
            ):
                return
            resolved_url = normalize_thread_url(game_url) or game_url
            resolved_game_id: int | None = None

            success = True
            error_message = ""
            if window_ref:
                try:
                    active_window = cast(ExtensionWindow, window_ref)
                    if game_url:
                        matching_game = (
                            self._find_matching_game(game_url, query.get("id", []))
                            if query.get("id")
                            else self._find_matching_game(game_url)
                        )
                        if matching_game:
                            candidate_id = matching_game.get("id")
                            resolved_game_id = (
                                candidate_id if isinstance(candidate_id, int) else None
                            )
                            resolved_f95_url = matching_game.get("f95_url")
                            if isinstance(resolved_f95_url, str) and resolved_f95_url:
                                resolved_url = resolved_f95_url

                    # Raising alone leaves a minimized or hidden window where
                    # it is; bring it back first.
                    try:
                        cast(_ShowableWindow, cast(object, active_window)).show()
                        if window_minimized.is_set():
                            cast(_ShowableWindow, cast(object, active_window)).restore()
                    except Exception:
                        pass
                    active_window.on_top = True
                    active_window.on_top = False
                    # Tell the frontend to open the edit modal for this game
                    if resolved_url:
                        import base64

                        resolved_url_text = str(resolved_url)
                        b64_url = base64.b64encode(
                            resolved_url_text.encode("utf-8")
                        ).decode("utf-8")
                        id_field = (
                            f", gameId: {resolved_game_id}"
                            if resolved_game_id is not None
                            else ""
                        )
                        js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-open', {{ detail: {{ url: decodeURIComponent(escape(atob('{b64_url}'))){id_field} }} }}));"
                        _ = active_window.evaluate_js(js_code)
                except Exception as e:
                    print(f"[wLib] Failed to process extension open event: {e}")
                    success = False
                    error_message = str(e)
            else:
                success = False
                error_message = "Application window is not ready"

            self.send_response(200)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload: dict[str, object] = {"success": success}
            if resolved_game_id is not None:
                payload["gameId"] = resolved_game_id
            if error_message:
                payload["error"] = error_message
            _ = self.wfile.write(json.dumps(payload).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        allowed_origin = self._get_allowed_origin()
        if allowed_origin is None:
            self._reject_origin()
            return

        try:
            body_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            body_length = 0
        if body_length > MAX_EXTENSION_BODY_BYTES:
            # Reply first, then drop the body: closing with it unread would
            # reset the connection before the client reads the 413.
            self.close_connection = True
            self.send_response(413)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            _ = self.wfile.write(
                b'{"success": false, "error": "Request body is too large"}'
            )
            self.wfile.flush()
            self._discard_request_body()
            return

        if self.path == "/api/launch-check":
            if not self._runner_request():
                self._reject_origin()
                return
            check_status, check_result = 200, {"success": True}
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                raw_check = cast(object, json.loads(self.rfile.read(length)))
                if not isinstance(raw_check, dict):
                    raise ValueError("Invalid launch check")
                check = cast(dict[str, object], raw_check)
                launch_id = check.get("gameId")
                if not isinstance(launch_id, int) or isinstance(launch_id, bool) or launch_id <= 0:
                    raise ValueError("Invalid game ID")
                if api_ref is not None:
                    reason = api_ref.reserve_runner_launch(
                        launch_id,
                        str(check.get("prefix") or ""),
                        sandboxed=check.get("sandboxed") is True,
                        umu=check.get("umu") is True,
                        blocked=check.get("blocked") is True,
                        title=str(check.get("title") or ""),
                    )
                    if reason:
                        check_result = {"success": False, "error": reason}
            except (ValueError, TypeError, OSError):
                check_status, check_result = 400, {"success": False, "error": "Invalid launch check"}
            self.send_response(check_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(check_result).encode())
            return
        if self.path in (
            "/api/wishlist",
            "/api/add",
            "/api/thread-links",
        ) and not self._require_pairing(allowed_origin):
            return
        if self.path == "/api/runner-event":
            if not self._runner_request():
                self._reject_origin()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                raw = cast(object, json.loads(self.rfile.read(length)))
                if not isinstance(raw, dict):
                    raise ValueError("Invalid runner event")
                payload = cast(dict[str, object], raw)
                if payload.get("state") not in ("started", "ended", "error"):
                    raise ValueError("Invalid runner event")
                if not window_ref:
                    raise ValueError("Application window is not ready")
                ended_id = payload.get("gameId")
                if (
                    payload.get("state") in ("ended", "error")
                    and api_ref is not None
                    and isinstance(ended_id, int)
                ):
                    api_ref.release_runner_launch(ended_id)
                active_window = cast(ExtensionWindow, window_ref)
                _ = active_window.evaluate_js("window.dispatchEvent(new Event('wlib-refresh-library'))")
                if payload.get("state") == "error":
                    detail = json.dumps({"error": str(payload.get("error") or "Launch failed")})
                    _ = active_window.evaluate_js(f"window.dispatchEvent(new CustomEvent('wlib-launch-error', {{detail: {detail}}}))")
                runner_ok = True
                runner_status = 200
            except (ValueError, TypeError, OSError):
                runner_ok = False
                runner_status = 400
            except Exception:
                logging.getLogger(__name__).exception("Runner event failed")
                runner_ok = False
                runner_status = 500
            self.send_response(runner_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps({"success": runner_ok}).encode())
        elif self.path == "/api/wishlist":
            status = 200
            wishlist_response: dict[str, object]
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= content_length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                raw_payload = cast(object, json.loads(self.rfile.read(content_length).decode("utf-8")))
                if not isinstance(raw_payload, dict):
                    raise ValueError("Request body must be a JSON object")
                # Preparing the payload resolves store links, which reads the
                # database; wait for startup init_db() so an early wishlist
                # gets the documented 503 instead of a raw SQLite error.
                if not self._wait_for_database() or api_ref is None:
                    status = 503
                    wishlist_response = {"success": False, "error": "Application is not ready"}
                else:
                    payload = self._prepare_game_payload(
                        cast(dict[str, object], raw_payload)
                    )
                    wishlist_response = api_ref.add_game(
                        title=str(payload.get("title") or ""),
                        exe_path="",
                        f95_url=str(payload.get("url") or ""),
                        version=str(payload.get("version") or ""),
                        cover_image=str(payload.get("coverImage") or ""),
                        tags=", ".join(
                            str(item) for item in cast(list[object], payload["tags"])
                        )
                        if isinstance(payload.get("tags"), list)
                        else str(payload.get("tags") or ""),
                        rating=str(payload.get("rating") or ""),
                        developer=str(payload.get("developer") or ""),
                        engine=str(payload.get("engine") or ""),
                        links=payload.get("links"),
                    )
                    if wishlist_response.get("attached") is True:
                        wishlist_response = {
                            **wishlist_response,
                            "success": False,
                            "error": "Game is already in wLib; F95 thread attached",
                            "error_code": "duplicate_url",
                        }
                    if wishlist_response.get("error_code") == "duplicate_url":
                        status = 409
                    elif not wishlist_response.get("success"):
                        status = 400
                    elif window_ref:
                        try:
                            active_window = cast(ExtensionWindow, window_ref)
                            _ = active_window.evaluate_js(
                                "window.dispatchEvent(new Event('wlib-refresh-library'))"
                            )
                        except Exception as e:
                            print(f"[wLib] Failed to refresh library after wishlist: {e}")
            except Exception as e:
                status = 400
                wishlist_response = {"success": False, "error": str(e)}

            self.send_response(status)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(wishlist_response).encode())
        elif self.path == "/api/add":
            status = 200
            response: dict[str, object] = {"success": True}
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= content_length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                post_data = self.rfile.read(content_length)
                payload = cast(object, json.loads(post_data.decode("utf-8")))

                # We have the payload. Send it to the Vue frontend via PyWebView javascript evaluation
                if window_ref:
                    payload = self._prepare_game_payload(payload)
                    import base64

                    active_window = cast(ExtensionWindow, window_ref)

                    b64_json = base64.b64encode(
                        json.dumps(payload).encode("utf-8")
                    ).decode("utf-8")
                    js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-add', {{ detail: JSON.parse(decodeURIComponent(escape(atob('{b64_json}')))) }}));"
                    _ = active_window.evaluate_js(js_code)
                else:
                    status = 503
                    response = {"success": False, "error": "Application window is not ready"}
            except Exception as e:
                status = 400
                response = {"success": False, "error": str(e)}

            self.send_response(status)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(response).encode())
        elif self.path == "/api/thread-links":
            status = 200
            response = {"success": True}
            try:
                from core.database import find_game_by_f95_url, replace_game_links
                from core.store_links import coerce_store_links

                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= MAX_EXTENSION_BODY_BYTES:
                    raise ValueError("Invalid Content-Length")
                raw = cast(object, json.loads(self.rfile.read(length).decode("utf-8")))
                if not isinstance(raw, dict):
                    raise ValueError("Request body must be a JSON object")
                body = cast(dict[str, object], raw)
                if not extract_thread_id(body.get("url")) or not isinstance(
                    body.get("links"), list
                ):
                    raise ValueError("A thread URL and links list are required")
                if not self._wait_for_database():
                    status = 503
                    response = {"success": False, "error": "Application is not ready"}
                elif (game := find_game_by_f95_url(body.get("url"))) is None:
                    status = 404
                    response = {"success": False, "error": "Game not found"}
                else:
                    replace_game_links(
                        int(str(game["id"])),
                        "f95_post",
                        coerce_store_links(body["links"]),
                    )
            except Exception as exc:
                status = 400
                response = {"success": False, "error": str(exc)}
            self.send_response(status)
            self._send_cors_headers(allowed_origin)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _ = self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()


class ExtensionServer(ThreadingHTTPServer):
    # The port doubles as the single-instance lock. Windows SO_REUSEADDR would
    # let a second wLib bind it while the first is still listening.
    allow_reuse_address: bool = sys.platform != "win32"
    sync_api: Api | None = None
    # Set by main() while Api() has not run init_db() yet.
    database_ready: threading.Event | None = None


def bind_extension_server() -> ThreadingHTTPServer | None:
    try:
        return ExtensionServer(("127.0.0.1", 8183), ExtensionRequestHandler)
    except OSError as e:
        print(f"Failed to start extension HTTP server: {e}")
        return None
