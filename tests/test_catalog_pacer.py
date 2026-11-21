# SPDX-License-Identifier: GPL-3.0-or-later
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from catalog_fakes import FakeClock, FakeTransport, json_response
from catalog_server.config import USER_AGENT, site_proxies_from_env
from catalog_server.net import (
    BudgetExhausted,
    Pacer,
    RateLimited,
    Request,
    urllib_transport,
)
from catalog_server.store import StateStore


def _ok_transport():
    transport = FakeTransport()
    transport.route(lambda request: True, json_response({"ok": True}))
    return transport


def test_f95_budget_holds_across_two_runs(tmp_path):
    clock = FakeClock()
    transport = _ok_transport()
    state = StateStore(str(tmp_path / "state.sqlite"))
    first = Pacer(state, transport, clock=clock, sleep=clock.sleep)
    for _ in range(60):
        first.request("f95", "https://f95zone.to/a")
    # A second run in the same hour only gets what is left of the 90.
    state.close()
    state = StateStore(str(tmp_path / "state.sqlite"))
    second = Pacer(state, transport, clock=clock, sleep=clock.sleep)
    assert second.remaining("f95") == 30
    for _ in range(30):
        second.request("f95", "https://f95zone.to/a")
    with pytest.raises(BudgetExhausted):
        second.request("f95", "https://f95zone.to/a")
    assert len(transport.requests) == 90
    # Every 3 s apart, and never more than 90 in any rolling hour.
    times = state.request_times("f95", 0)
    assert all(b - a >= 3.0 for a, b in zip(times, times[1:], strict=False))


def test_waiting_for_budget_sleeps_until_the_window_frees(tmp_path):
    clock = FakeClock()
    state = StateStore(str(tmp_path / "state.sqlite"))
    pacer = Pacer(
        state, _ok_transport(), clock=clock, sleep=clock.sleep, wait_for_budget={"f95"}
    )
    for _ in range(91):
        pacer.request("f95", "https://f95zone.to/a")
    times = state.request_times("f95", 0)
    assert times[-1] - times[0] >= 3600
    for index in range(len(times)):
        window = [t for t in times if times[index] - 3600 < t <= times[index]]
        assert len(window) <= 90


def test_vndb_waits_out_its_150_per_five_minutes(tmp_path):
    clock = FakeClock()
    state = StateStore(str(tmp_path / "state.sqlite"))
    pacer = Pacer(state, _ok_transport(), clock=clock, sleep=clock.sleep)
    for _ in range(400):
        pacer.request("vndb", "https://api.vndb.org/kana/vn")
    times = state.request_times("vndb", 0)
    assert len(times) == 400
    for index in range(len(times)):
        assert len([t for t in times if times[index] - 300 < t <= times[index]]) <= 150


def test_429_pauses_only_that_source(tmp_path):
    clock = FakeClock()
    transport = FakeTransport()
    transport.route("f95zone", json_response({"status": "error"}, status=429))
    transport.route(lambda request: True, json_response([]))
    state = StateStore(str(tmp_path / "state.sqlite"))
    pacer = Pacer(state, transport, clock=clock, sleep=clock.sleep)
    with pytest.raises(RateLimited):
        pacer.request("f95", "https://f95zone.to/a")
    with pytest.raises(RateLimited):
        pacer.request("f95", "https://f95zone.to/b")
    assert len(transport.requests) == 1
    pacer.request("ryuugames", "https://www.ryuugames.com/wp-json/")
    assert "f95" in pacer.paused and "ryuugames" not in pacer.paused


def test_rate_limit_body_pauses_the_source(tmp_path):
    clock = FakeClock()
    transport = _ok_transport()
    state = StateStore(str(tmp_path / "state.sqlite"))
    pacer = Pacer(state, transport, clock=clock, sleep=clock.sleep)
    with pytest.raises(RateLimited):
        pacer.request(
            "f95", "https://f95zone.to/a", is_rate_limited=lambda response: True
        )


def test_user_agent_is_on_every_request(tmp_path):
    clock = FakeClock()
    transport = _ok_transport()
    pacer = Pacer(
        StateStore(str(tmp_path / "state.sqlite")),
        transport,
        clock=clock,
        sleep=clock.sleep,
    )
    for site in ("f95", "dlsite", "ryuugames"):
        pacer.request(site, f"https://example.invalid/{site}")
    pacer.request(
        "vndb", "https://api.vndb.org/kana/vn", method="POST", json_body={"a": 1}
    )
    assert transport.requests
    for request in transport.requests:
        assert request.headers["User-Agent"] == USER_AGENT
    assert "wLib" in USER_AGENT and "github.com/kirin-3/wLib" in USER_AGENT


def test_timeouts_and_5xx_are_retried_within_the_budget(tmp_path):
    clock = FakeClock()
    answers = [
        ConnectionError("timed out"),
        json_response({}, status=502),
        json_response({"ok": True}),
    ]

    def transport(request):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    state = StateStore(str(tmp_path / "state.sqlite"))
    pacer = Pacer(state, transport, clock=clock, sleep=clock.sleep)
    assert pacer.request("f95", "https://f95zone.to/a").status == 200
    assert pacer.sent["f95"] == 3
    assert len(state.request_times("f95", 0)) == 3


def test_retries_give_up_after_three_attempts(tmp_path):
    clock = FakeClock()

    def transport(request):
        raise ConnectionError("down")

    pacer = Pacer(
        StateStore(str(tmp_path / "state.sqlite")),
        transport,
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(ConnectionError):
        pacer.request("dlsite", "https://www.dlsite.com/x")
    assert pacer.sent["dlsite"] == 3


def test_urllib_transport_turns_a_truncated_read_into_a_retryable_error(monkeypatch):
    from http.client import IncompleteRead

    from catalog_server import net

    class Truncated:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            raise IncompleteRead(b"partial", 10)

    monkeypatch.setattr(net, "urlopen", lambda *_a, **_k: Truncated())
    with pytest.raises(ConnectionError):
        net.urllib_transport(net.Request("GET", "https://example.com/x"))


def test_only_sites_with_a_proxy_send_through_it(tmp_path):
    clock = FakeClock()
    transport = _ok_transport()
    state = StateStore(str(tmp_path / "state.sqlite"))
    pacer = Pacer(
        state,
        transport,
        clock=clock,
        sleep=clock.sleep,
        proxies={"dlsite": "http://127.0.0.1:8888"},
    )
    pacer.request("dlsite", "https://www.dlsite.com/maniax/")
    pacer.request("f95", "https://f95zone.to/a")
    assert [request.proxy for request in transport.requests] == [
        "http://127.0.0.1:8888",
        None,
    ]


def test_urllib_transport_sends_through_the_proxy():
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.path)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proxy = f"http://127.0.0.1:{server.server_address[1]}"
        response = urllib_transport(
            Request(
                "GET",
                "http://catalog.invalid/page",
                {"User-Agent": USER_AGENT},
                proxy=proxy,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
    assert response.json() == {"ok": True}
    # A proxy gets the absolute URL; a direct request would have failed to resolve.
    assert seen == ["http://catalog.invalid/page"]


def test_site_proxies_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("WLIB_CATALOG_DLSITE_PROXY", " http://127.0.0.1:8888 ")
    monkeypatch.delenv("WLIB_CATALOG_F95_PROXY", raising=False)
    assert site_proxies_from_env() == {"dlsite": "http://127.0.0.1:8888"}


@pytest.mark.parametrize(
    "value",
    [
        "socks5://127.0.0.1:1080",
        "http://user:secret@127.0.0.1:8888",
        "127.0.0.1:8888",
        "http:///x",
    ],
)
def test_site_proxies_refuse_unsupported_urls(monkeypatch, value):
    monkeypatch.setenv("WLIB_CATALOG_DLSITE_PROXY", value)
    with pytest.raises(ValueError, match="WLIB_CATALOG_DLSITE_PROXY"):
        site_proxies_from_env()
