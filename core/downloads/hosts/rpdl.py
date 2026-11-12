# SPDX-License-Identifier: GPL-3.0-or-later
"""RPDL (dl.rpdl.net) client: sign-in, search, details and `.torrent` fetch.

Every request goes to `https://dl.rpdl.net` only, one at a time and at least a
second apart. Nothing here prints the token, the password or a torrent URL.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, UTC
from http.client import HTTPResponse
from typing import cast
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from core.downloads import credentials
from core.downloads.network import open_host
from core.downloads.transfer import DownloadError

BASE_URL = "https://dl.rpdl.net"
DOMAINS = ("dl.rpdl.net",)
MIN_INTERVAL = 1.0
TORRENT_MAX_BYTES = 14 * 1024 * 1024
JSON_MAX_BYTES = 4 * 1024 * 1024
SEARCH_LIMIT = 50
CACHE_TTL = 24 * 60 * 60

SIGN_IN_MESSAGE = "Sign in to RPDL in Settings"

_lock = threading.Lock()
_last_request = 0.0


class RpdlError(DownloadError):
    """An RPDL failure; `kind` is one of the KIND_* values."""

    kind: str

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


KIND_SIGN_IN = "sign_in"
KIND_NOT_FOUND = "not_found"
KIND_RATE_LIMIT = "rate_limit"
KIND_UNAVAILABLE = "unavailable"
KIND_INVALID = "invalid_response"
KIND_BAD_LOGIN = "bad_login"

_MESSAGES = {
    KIND_SIGN_IN: SIGN_IN_MESSAGE,
    KIND_NOT_FOUND: "RPDL torrent not found",
    KIND_RATE_LIMIT: "RPDL rate limit reached",
    KIND_UNAVAILABLE: "RPDL is unavailable",
    KIND_INVALID: "RPDL sent an invalid response",
}


def _error(kind: str) -> RpdlError:
    return RpdlError(kind, _MESSAGES[kind])


def classify_status(status: int) -> RpdlError:
    if status == 401:
        return _error(KIND_SIGN_IN)
    if status == 404:
        return _error(KIND_NOT_FOUND)
    if status == 429:
        return _error(KIND_RATE_LIMIT)
    return _error(KIND_UNAVAILABLE)


def urlopen(request: Request, timeout: int = 30) -> object:
    return open_host(request, DOMAINS, timeout)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _request(
    path: str,
    *,
    token: str = "",
    body: dict[str, object] | None = None,
    limit: int = JSON_MAX_BYTES,
    on_http_error: Callable[[HTTPError], RpdlError] | None = None,
) -> tuple[bytes, str]:
    """One serialized RPDL call; returns the body and its content type."""
    global _last_request
    headers = {"User-Agent": "wLib", "Accept": "application/json"}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(BASE_URL + path, data=data, headers=headers)
    with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            _sleep(wait)
        try:
            with cast(HTTPResponse, urlopen(request, timeout=30)) as response:
                content_type = str(response.headers.get("Content-Type", "")).lower()
                payload = response.read(limit + 1)
        except HTTPError as exc:
            raise (on_http_error or _status_error)(exc) from exc
        except (URLError, OSError) as exc:
            raise _error(KIND_UNAVAILABLE) from exc
        finally:
            _last_request = time.monotonic()
    if len(payload) > limit:
        raise _error(KIND_INVALID)
    return payload, content_type


def _status_error(exc: HTTPError) -> RpdlError:
    return classify_status(exc.code)


def _json_body(payload: bytes, content_type: str) -> dict[str, object]:
    if "html" in content_type or payload.lstrip()[:1] == b"<":
        raise _error(KIND_UNAVAILABLE)
    try:
        value = cast(object, json.loads(payload))
    except ValueError as exc:
        raise _error(KIND_INVALID) from exc
    if not isinstance(value, dict):
        raise _error(KIND_INVALID)
    return cast(dict[str, object], value)


def _data(body: dict[str, object]) -> dict[str, object]:
    inner = body.get("data", body)
    return cast(dict[str, object], inner) if isinstance(inner, dict) else body


# --- sign-in ---------------------------------------------------------------


def _login_failed(exc: HTTPError) -> RpdlError:
    if exc.code in (400, 401, 403, 422):
        return RpdlError(KIND_BAD_LOGIN, "RPDL username or password is wrong")
    return classify_status(exc.code)


def login(username: str, password: str) -> tuple[str, str]:
    """Sign in; returns (token, username). Rejected credentials raise kind 'bad_login'."""
    username = username.strip()
    if not username or not password:
        raise RpdlError(KIND_BAD_LOGIN, "Enter your RPDL username and password")
    payload, content_type = _request(
        "/api/user/login",
        body={"login": username, "password": password},
        on_http_error=_login_failed,
    )
    body = _json_body(payload, content_type)
    data = _data(body)
    token = ""
    for key in ("token", "access_token", "jwt"):
        value = data.get(key) or body.get(key)
        if isinstance(value, str) and value:
            token = value
            break
    if not token:
        raise _error(KIND_INVALID)
    name = data.get("username")
    return token, name if isinstance(name, str) and name else username


def authorized(call: Callable[[str], bytes]) -> bytes:
    """Run `call(token)`; on a 401 sign in again once with the stored password and retry."""
    token = credentials.get(credentials.RPDL_TOKEN)
    username = credentials.get(credentials.RPDL_USERNAME)
    password = credentials.get(credentials.RPDL_PASSWORD)
    if not token and not (username and password):
        raise _error(KIND_SIGN_IN)
    if token:
        try:
            return call(token)
        except RpdlError as exc:
            if exc.kind != KIND_SIGN_IN:
                raise
    if not (username and password):
        raise _error(KIND_SIGN_IN)
    try:
        fresh, _name = login(username, password)
    except RpdlError as exc:
        if exc.kind == KIND_BAD_LOGIN:
            credentials.put({credentials.RPDL_TOKEN: ""})
            raise _error(KIND_SIGN_IN) from exc
        raise
    credentials.put({credentials.RPDL_TOKEN: fresh})
    return call(fresh)


# --- field normalisation ---------------------------------------------------

_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4,
          "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}


def parse_size(value: object) -> int:
    """Bytes from a number (bytes) or text such as '1.5 GB'."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        match = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)", text)
        if match:
            unit = match.group(2).lower() or "b"
            if unit in _UNITS:
                return int(float(match.group(1)) * _UNITS[unit])
    return 0


def parse_date(value: object) -> int:
    """Unix time from a number or from text (digits or an ISO date)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp())
    return 0


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def normalize_result(raw: dict[str, object]) -> dict[str, object] | None:
    torrent_id = _int(raw.get("torrent_id", raw.get("id")))
    title = raw.get("title")
    if not torrent_id or not isinstance(title, str) or not title.strip():
        return None
    return {
        "id": torrent_id,
        "title": title.strip(),
        "size": parse_size(raw.get("file_size", raw.get("size"))),
        "seeders": _int(raw.get("seeders")),
        "leechers": _int(raw.get("leechers")),
        "date": parse_date(raw.get("upload_date", raw.get("date_uploaded"))),
        "info_hash": str(raw.get("info_hash") or ""),
    }


# --- search ----------------------------------------------------------------


def clean_query(title: str) -> str:
    """Letters and digits only, as RPDL's search expects."""
    return "".join(ch for ch in title if ch.isalnum())


def _cache_path() -> str:
    from core.host_platform import get_cache_root

    return os.path.join(get_cache_root(), "wLib", "rpdl-search.json")


def _cache_load() -> dict[str, dict[str, object]]:
    try:
        with open(_cache_path(), encoding="utf-8") as handle:
            value = cast(object, json.load(handle))
    except (OSError, ValueError):
        return {}
    return cast(dict[str, dict[str, object]], value) if isinstance(value, dict) else {}


def _cache_store(cache: dict[str, dict[str, object]]) -> None:
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(cache, handle)
        os.replace(tmp, path)
    except OSError:
        pass


def search(title: str, *, refresh: bool = False) -> list[dict[str, object]]:
    """Public torrent list for a title, newest first, cached for 24 hours."""
    query = clean_query(title)
    if len(query) < 2:
        return []
    key = query.lower()
    cache = _cache_load()
    entry = cache.get(key)
    now = time.time()
    if not refresh and isinstance(entry, dict):
        stamp = entry.get("at")
        results = entry.get("results")
        if isinstance(stamp, (int, float)) and now - stamp < CACHE_TTL and isinstance(results, list):
            return cast(list[dict[str, object]], results)
    # The parameter set RPDL's own site and other clients send; `uploaded_DESC` is newest first.
    params = urlencode({
        "page_size": SEARCH_LIMIT, "page": 0, "sort": "uploaded_DESC", "categories": "", "search": query,
    })
    payload, content_type = _request("/api/torrents?" + params)
    data = _data(_json_body(payload, content_type))
    rows = data.get("results")
    if not isinstance(rows, list):
        raise _error(KIND_INVALID)
    results = [
        item
        for row in cast(list[object], rows)
        if isinstance(row, dict)
        for item in [normalize_result(cast(dict[str, object], row))]
        if item is not None
    ]
    cache = {k: v for k, v in cache.items() if now - _num(v.get("at")) < CACHE_TTL}
    cache[key] = {"at": now, "results": results}
    _cache_store(cache)
    return results


def _num(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


# --- details and .torrent --------------------------------------------------


def details(torrent_id: int) -> dict[str, object]:
    """Torrent details (info hash, trackers, files); public, so no token is sent."""
    payload, content_type = _request(f"/api/torrent/{quote(str(int(torrent_id)))}")
    data = _data(_json_body(payload, content_type))
    result = normalize_result(data) or {}
    trackers = data.get("trackers")
    result["trackers"] = [t for t in cast(list[object], trackers) if isinstance(t, str)] if isinstance(trackers, list) else []
    return result


def _bdecode(data: bytes, pos: int = 0, depth: int = 0) -> int:
    """Validate one bencoded value starting at `pos`; returns the position after it."""
    if depth > 64 or pos >= len(data):
        raise ValueError("bad bencode")
    ch = data[pos : pos + 1]
    if ch == b"i":
        end = data.index(b"e", pos)
        _ = int(data[pos + 1 : end])
        return end + 1
    if ch in (b"l", b"d"):
        pos += 1
        while data[pos : pos + 1] != b"e":
            pos = _bdecode(data, pos, depth + 1)
        return pos + 1
    if ch.isdigit():
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        end = colon + 1 + length
        if end > len(data):
            raise ValueError("bad bencode")
        return end
    raise ValueError("bad bencode")


def is_torrent_file(data: bytes) -> bool:
    """True for a non-empty bencoded dictionary that fills the whole answer."""
    if not data or len(data) > TORRENT_MAX_BYTES or data[:1] != b"d":
        return False
    try:
        return _bdecode(data) == len(data) and len(data) > 2
    except (ValueError, IndexError):
        return False


def fetch_torrent(torrent_id: int) -> bytes:
    """The `.torrent` bytes, fetched with the stored token (renewed once on a 401)."""
    path = f"/api/torrent/download/{quote(str(int(torrent_id)))}"

    def call(token: str) -> bytes:
        payload, content_type = _request(path, token=token, limit=TORRENT_MAX_BYTES)
        if "html" in content_type or payload.lstrip()[:1] == b"<":
            raise _error(KIND_UNAVAILABLE)
        if not is_torrent_file(payload):
            raise _error(KIND_INVALID)
        return payload

    return authorized(call)
