# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
from email.message import Message
from urllib.error import HTTPError

import pytest

from core.downloads import accounts, credentials
from core.downloads.hosts import rpdl
from core.library_backup import (
    SECTION_SETTINGS_GENERAL,
    SECTION_SETTINGS_PATHS,
    build_library_backup,
    build_sync_backup,
)

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


def _json(value, content_type="application/json"):
    return Response(json.dumps(value).encode(), content_type)


def _http(code: int) -> HTTPError:
    return HTTPError("https://dl.rpdl.net/x", code, "err", Message(), None)


@pytest.fixture(autouse=True)
def fast(monkeypatch, tmp_path):
    monkeypatch.setattr(rpdl, "_sleep", lambda seconds: None)
    monkeypatch.setenv("WLIB_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(rpdl, "_last_request", 0.0)


@pytest.fixture
def store(monkeypatch):
    values: dict[str, str] = {}
    monkeypatch.setattr(credentials, "get", lambda key: values.get(key, ""))
    monkeypatch.setattr(credentials, "put", values.update)
    return values


@pytest.fixture
def calls(monkeypatch):
    seen: list[tuple[str, str, dict[str, str]]] = []
    queue: list[object] = []

    def fake(request, timeout=30):
        seen.append((request.full_url, request.get_method(), dict(request.header_items())))
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(rpdl, "urlopen", fake)
    return seen, queue


def test_search_cleans_query_and_reads_numeric_fields(calls):
    seen, queue = calls
    queue.append(_json({"data": {"results": [
        {"torrent_id": 7, "title": "Summers-Gone-1.0", "file_size": 2048, "seeders": 3, "leechers": 1, "upload_date": 1700000000},
    ]}}))
    (result,) = rpdl.search("Summer's Gone")
    assert "search=SummersGone" in seen[0][0] and "page_size=50" in seen[0][0]
    # The sort key RPDL understands for newest first.
    assert "sort=uploaded_DESC" in seen[0][0] and "page=0" in seen[0][0]
    assert seen[0][0].startswith("https://dl.rpdl.net/")
    assert result["size"] == 2048 and result["date"] == 1700000000 and result["seeders"] == 3


def test_search_reads_text_fields(calls):
    _, queue = calls
    queue.append(_json({"data": {"results": [
        {"torrent_id": "9", "title": "Game", "file_size": "1.5 GB", "seeders": "4", "leechers": "0", "upload_date": "2026-01-02T00:00:00Z"},
    ]}}))
    (result,) = rpdl.search("Game")
    assert result["size"] == int(1.5 * 1024**3) and result["id"] == 9
    assert result["date"] == 1767312000


def test_search_too_short_makes_no_request(calls):
    seen, _ = calls
    assert rpdl.search("a!") == []
    assert seen == []


def test_search_cache_and_refresh(calls):
    seen, queue = calls
    body = {"data": {"results": [{"torrent_id": 1, "title": "Game One"}]}}
    queue.extend([_json(body), _json(body)])
    rpdl.search("Game One")
    rpdl.search("game one")
    assert len(seen) == 1
    rpdl.search("Game One", refresh=True)
    assert len(seen) == 2


def test_search_cache_expires(calls, monkeypatch):
    seen, queue = calls
    body = {"data": {"results": [{"torrent_id": 1, "title": "Game One"}]}}
    queue.extend([_json(body), _json(body)])
    rpdl.search("Game One")
    monkeypatch.setattr(rpdl.time, "time", lambda: 10**10)
    rpdl.search("Game One")
    assert len(seen) == 2


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "sign_in"), (404, "not_found"), (429, "rate_limit"), (500, "unavailable"), (503, "unavailable")],
)
def test_failures_are_classified(calls, status, kind):
    _, queue = calls
    queue.append(_http(status))
    with pytest.raises(rpdl.RpdlError) as info:
        rpdl.details(5)
    assert info.value.kind == kind


def test_html_answer_is_unavailable(calls):
    _, queue = calls
    queue.append(Response(b"<html></html>", "text/html"))
    with pytest.raises(rpdl.RpdlError) as info:
        rpdl.details(5)
    assert info.value.kind == "unavailable"


def test_requests_are_spaced_at_least_a_second(calls, monkeypatch):
    _, queue = calls
    slept: list[float] = []
    monkeypatch.setattr(rpdl, "_sleep", slept.append)
    queue.extend([_json({"data": {}}), _json({"data": {}})])
    rpdl.details(1)
    rpdl.details(2)
    assert slept and 0 < slept[-1] <= 1.0


def test_torrent_check():
    assert rpdl.is_torrent_file(TORRENT)
    assert not rpdl.is_torrent_file(b"")
    assert not rpdl.is_torrent_file(b"de")
    assert not rpdl.is_torrent_file(b"<html>")
    assert not rpdl.is_torrent_file(b'{"error": "x"}')
    assert not rpdl.is_torrent_file(TORRENT + b"junk")
    assert not rpdl.is_torrent_file(b"d" + b"x" * (rpdl.TORRENT_MAX_BYTES + 1))


def test_fetch_requires_account_before_any_request(calls, store):
    seen, _ = calls
    with pytest.raises(rpdl.RpdlError) as info:
        rpdl.fetch_torrent(3)
    assert info.value.kind == "sign_in" and "Settings" in str(info.value)
    assert seen == []


def test_fetch_sends_token_only_to_rpdl(calls, store):
    seen, queue = calls
    store[credentials.RPDL_TOKEN] = "tok"
    queue.append(Response(TORRENT, "application/x-bittorrent"))
    assert rpdl.fetch_torrent(3) == TORRENT
    url, _, headers = seen[0]
    assert url == "https://dl.rpdl.net/api/torrent/download/3"
    assert headers["Authorization"] == "Bearer tok"
    assert "Cookie" not in headers


def test_fetch_rejects_html_and_json(calls, store):
    _, queue = calls
    store[credentials.RPDL_TOKEN] = "tok"
    queue.append(Response(b"<html>x</html>", "text/html"))
    with pytest.raises(rpdl.RpdlError) as info:
        rpdl.fetch_torrent(3)
    assert info.value.kind == "unavailable"


def test_token_renews_once_on_401(calls, store):
    seen, queue = calls
    store.update({credentials.RPDL_TOKEN: "old", credentials.RPDL_USERNAME: "kim", credentials.RPDL_PASSWORD: "pw"})
    queue.extend([_http(401), _json({"data": {"token": "new", "username": "kim"}}), Response(TORRENT)])
    assert rpdl.fetch_torrent(3) == TORRENT
    assert store[credentials.RPDL_TOKEN] == "new"
    assert seen[-1][2]["Authorization"] == "Bearer new"
    assert len(seen) == 3


def test_renewal_failure_asks_for_sign_in(calls, store):
    _, queue = calls
    store.update({credentials.RPDL_TOKEN: "old", credentials.RPDL_USERNAME: "kim", credentials.RPDL_PASSWORD: "changed"})
    queue.extend([_http(401), _http(403)])
    with pytest.raises(rpdl.RpdlError) as info:
        rpdl.fetch_torrent(3)
    assert info.value.kind == "sign_in" and "Settings" in str(info.value)


def test_connect_stores_all_three_and_status_hides_secrets(calls, store):
    _, queue = calls
    queue.append(_json({"data": {"token": "tok", "username": "Kim"}}))
    assert accounts.connect_rpdl("kim", "pw") == "Kim"
    assert store == {
        credentials.RPDL_USERNAME: "Kim",
        credentials.RPDL_PASSWORD: "pw",
        credentials.RPDL_TOKEN: "tok",
    }
    status = accounts.status()["rpdl"]
    assert status == {"connected": True, "username": "Kim"}


def test_wrong_password_stores_nothing(calls, store):
    _, queue = calls
    queue.append(_http(403))
    with pytest.raises(rpdl.RpdlError) as info:
        accounts.connect_rpdl("kim", "bad")
    assert "wrong" in str(info.value)
    assert not any(store.values())
    assert accounts.status()["rpdl"]["connected"] is False


def test_disconnect_clears_all_three(store):
    store.update({credentials.RPDL_USERNAME: "kim", credentials.RPDL_PASSWORD: "pw", credentials.RPDL_TOKEN: "t"})
    accounts.disconnect_rpdl()
    assert not any(store.get(key) for key in (credentials.RPDL_USERNAME, credentials.RPDL_PASSWORD, credentials.RPDL_TOKEN))


def test_backup_and_sync_exclude_rpdl_keys(tmp_path, monkeypatch):
    from core.database import init_db, update_settings

    monkeypatch.setattr("core.database.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"), raising=False)
    init_db()
    update_settings({
        credentials.RPDL_USERNAME: "secret-user",
        credentials.RPDL_PASSWORD: "secret-password",
        credentials.RPDL_TOKEN: "secret-token",
    })
    backup = build_library_backup({"sections": [SECTION_SETTINGS_GENERAL, SECTION_SETTINGS_PATHS]})
    text = json.dumps(backup) + json.dumps(build_sync_backup("t", "d"))
    for needle in ("rpdl", "secret-user", "secret-password", "secret-token"):
        assert needle not in text
