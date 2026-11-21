# SPDX-License-Identifier: GPL-3.0-or-later
"""RPDL replies with NaN or Infinity (which `json.loads` accepts) must not crash."""

import io
import json
from email.message import Message

import pytest

from core.downloads.hosts import rpdl

NONFINITE = ["NaN", "Infinity", "-Infinity"]


class Response(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture(autouse=True)
def fast(monkeypatch, tmp_path):
    monkeypatch.setattr(rpdl, "_sleep", lambda seconds: None)
    monkeypatch.setenv("WLIB_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(rpdl, "_last_request", 0.0)


@pytest.fixture
def replies(monkeypatch):
    queue: list[bytes] = []
    monkeypatch.setattr(
        rpdl, "urlopen", lambda request, timeout=30: Response(queue.pop(0))
    )
    return queue


def _row(token: str, torrent_id: str = "7") -> str:
    return (
        f'{{"torrent_id": {torrent_id}, "title": "Game", "info_hash": "ab", "file_size": {token},'
        f' "seeders": {token}, "leechers": {token}, "upload_date": {token}}}'
    )


@pytest.mark.parametrize("token", NONFINITE)
def test_search_treats_nonfinite_numbers_as_unknown(replies, token):
    replies.append(f'{{"data": {{"results": [{_row(token)}]}}}}'.encode())
    (result,) = rpdl.search("Game")
    assert result["id"] == 7
    assert result["size"] == 0 and result["seeders"] == 0
    assert result["leechers"] == 0 and result["date"] == 0


@pytest.mark.parametrize("token", NONFINITE)
def test_search_drops_row_with_nonfinite_id(replies, token):
    replies.append(
        f'{{"data": {{"results": [{_row("1", torrent_id=token)}, {_row("1", torrent_id="2")}]}}}}'.encode()
    )
    results = rpdl.search("Game")
    assert [item["id"] for item in results] == [2]


@pytest.mark.parametrize("token", NONFINITE)
def test_details_treats_nonfinite_numbers_as_unknown(replies, token):
    replies.append(f'{{"data": {_row(token)}}}'.encode())
    result = rpdl.details(7)
    assert result["id"] == 7 and result["info_hash"] == "ab"
    assert result["size"] == 0 and result["seeders"] == 0 and result["date"] == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_helpers_fall_back_on_nonfinite(value):
    assert rpdl._int(value) == 0
    assert rpdl.parse_size(value) == 0
    assert rpdl.parse_date(value) == 0
    assert rpdl._num(value) == 0.0


def test_parse_size_overflowing_text_is_unknown():
    assert rpdl.parse_size("9" * 400 + " GB") == 0


def test_valid_values_are_unchanged():
    assert rpdl._int(3.9) == 3 and rpdl._int(-2) == 0 and rpdl._int("12") == 12
    assert rpdl.parse_size(2048.5) == 2048 and rpdl.parse_size("1.5 GB") == int(
        1.5 * 1024**3
    )
    assert rpdl.parse_size("1 XB") == 0
    assert rpdl.parse_date(1700000000.7) == 1700000000
    assert rpdl._num(5) == 5.0


@pytest.mark.parametrize("token", NONFINITE)
def test_cache_with_nonfinite_stamp_is_refetched(replies, token, tmp_path):
    rpdl._cache_store({"game": {"at": json.loads(token), "results": [{"id": 1}]}})
    replies.append(f'{{"data": {{"results": [{_row("1", "3")}]}}}}'.encode())
    assert [item["id"] for item in rpdl.search("Game")] == [3]
