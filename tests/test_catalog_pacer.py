# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from catalog_fakes import FakeClock, FakeTransport, json_response
from catalog_server.config import USER_AGENT
from catalog_server.net import BudgetExhausted, Pacer, RateLimited
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
    assert all(b - a >= 3.0 for a, b in zip(times, times[1:]))


def test_waiting_for_budget_sleeps_until_the_window_frees(tmp_path):
    clock = FakeClock()
    state = StateStore(str(tmp_path / "state.sqlite"))
    pacer = Pacer(state, _ok_transport(), clock=clock, sleep=clock.sleep, wait_for_budget={"f95"})
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
        pacer.request("f95", "https://f95zone.to/a", is_rate_limited=lambda response: True)


def test_user_agent_is_on_every_request(tmp_path):
    clock = FakeClock()
    transport = _ok_transport()
    pacer = Pacer(StateStore(str(tmp_path / "state.sqlite")), transport, clock=clock, sleep=clock.sleep)
    for site in ("f95", "dlsite", "ryuugames"):
        pacer.request(site, f"https://example.invalid/{site}")
    pacer.request("vndb", "https://api.vndb.org/kana/vn", method="POST", json_body={"a": 1})
    assert transport.requests
    for request in transport.requests:
        assert request.headers["User-Agent"] == USER_AGENT
    assert "wLib" in USER_AGENT and "github.com/kirin-3/wLib" in USER_AGENT


def test_timeouts_and_5xx_are_retried_within_the_budget(tmp_path):
    clock = FakeClock()
    answers = [ConnectionError("timed out"), json_response({}, status=502), json_response({"ok": True})]

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

    pacer = Pacer(StateStore(str(tmp_path / "state.sqlite")), transport, clock=clock, sleep=clock.sleep)
    with pytest.raises(ConnectionError):
        pacer.request("dlsite", "https://www.dlsite.com/x")
    assert pacer.sent["dlsite"] == 3
