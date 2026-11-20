# SPDX-License-Identifier: GPL-3.0-or-later
"""A token renewal that finishes after a sign-out or account switch must not save its token."""
import io
import json
from email.message import Message
from urllib.error import HTTPError

import pytest

from core.downloads import accounts, credentials
from core.downloads.hosts import rpdl

TORRENT = b"d4:infod6:lengthi5e4:name1:a12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee"


class Response(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _login(token: str) -> Response:
    return Response(json.dumps({"data": {"token": token, "username": "kim"}}).encode())


def _http(code: int) -> HTTPError:
    return HTTPError("https://dl.rpdl.net/x", code, "err", Message(), None)


@pytest.fixture
def store(monkeypatch):
    values: dict[str, str] = {}
    monkeypatch.setattr(rpdl, "_sleep", lambda seconds: None)
    monkeypatch.setattr(rpdl, "_last_request", 0.0)
    monkeypatch.setattr(credentials, "get", lambda key: values.get(key, ""))
    monkeypatch.setattr(credentials, "put", values.update)
    return values


def _serve(monkeypatch, items: list[object], during_login=None) -> list[str]:
    """Answer requests in order; run `during_login` while the renewal login is in flight."""
    auth: list[str] = []

    def fake(request, timeout=30):
        auth.append(dict(request.header_items()).get("Authorization", ""))
        if request.full_url.endswith("/api/user/login") and during_login is not None:
            during_login()
        item = items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(rpdl, "urlopen", fake)
    return auth


def _old_account(store: dict[str, str]) -> None:
    store.update({
        credentials.RPDL_TOKEN: "old",
        credentials.RPDL_USERNAME: "kim",
        credentials.RPDL_PASSWORD: "pw",
    })


def test_sign_out_during_renewal_keeps_the_account_signed_out(monkeypatch, store):
    _old_account(store)
    auth = _serve(monkeypatch, [_http(401), _login("renewed")], accounts.disconnect_rpdl)
    with pytest.raises(rpdl.RpdlError) as info:
        rpdl.fetch_torrent(3)
    assert info.value.kind == rpdl.KIND_SIGN_IN
    assert store[credentials.RPDL_TOKEN] == ""
    assert accounts.status()["rpdl"]["connected"] is False
    # The renewed token is not used for the download either.
    assert "Bearer renewed" not in auth


def test_account_switch_during_renewal_keeps_the_new_token(monkeypatch, store):
    _old_account(store)

    def switch() -> None:
        store.update({
            credentials.RPDL_TOKEN: "other-token",
            credentials.RPDL_USERNAME: "other",
            credentials.RPDL_PASSWORD: "pw2",
        })

    _serve(monkeypatch, [_http(401), _login("renewed")], switch)
    with pytest.raises(rpdl.RpdlError) as info:
        rpdl.fetch_torrent(3)
    assert info.value.kind == rpdl.KIND_SIGN_IN
    assert store[credentials.RPDL_TOKEN] == "other-token"
    assert store[credentials.RPDL_USERNAME] == "other"


def test_rejected_renewal_does_not_clear_a_new_account_token(monkeypatch, store):
    _old_account(store)

    def switch() -> None:
        store.update({
            credentials.RPDL_TOKEN: "other-token",
            credentials.RPDL_USERNAME: "other",
            credentials.RPDL_PASSWORD: "pw2",
        })

    _serve(monkeypatch, [_http(401), _http(403)], switch)
    with pytest.raises(rpdl.RpdlError) as info:
        rpdl.fetch_torrent(3)
    assert info.value.kind == rpdl.KIND_SIGN_IN
    assert store[credentials.RPDL_TOKEN] == "other-token"


def test_unchanged_account_still_saves_the_renewed_token(monkeypatch, store):
    _old_account(store)
    auth = _serve(monkeypatch, [_http(401), _login("renewed"), Response(TORRENT)])
    assert rpdl.fetch_torrent(3) == TORRENT
    assert store[credentials.RPDL_TOKEN] == "renewed"
    assert auth[-1] == "Bearer renewed"
