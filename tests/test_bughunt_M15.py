# SPDX-License-Identifier: GPL-3.0-or-later
"""The torrent helper sends "finished" only after a confirmed disk cache flush."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("libtorrent")

from core.downloads import torrent_helper as th  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class FakeHandle:
    def __init__(self) -> None:
        self.flushes = 0

    def flush_cache(self) -> None:
        self.flushes += 1

    def is_valid(self) -> bool:
        return False


class FakeSession:
    def __init__(self) -> None:
        self.removed: list[object] = []

    def pop_alerts(self) -> list[object]:
        return []

    def remove_torrent(self, handle: object) -> None:
        self.removed.append(handle)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_engine(
    monkeypatch: pytest.MonkeyPatch, state: str = "seeding"
) -> tuple[Any, FakeHandle, list[dict[str, object]], Clock]:
    events: list[dict[str, object]] = []
    clock = Clock()
    monkeypatch.setattr(th.time, "monotonic", clock)
    engine: Any = th.Engine(events.append, {})
    engine._session = FakeSession()
    handle = FakeHandle()
    engine._entries["j"] = th._Entry("j", "/nowhere", handle)  # pyright: ignore[reportArgumentType]
    engine._status = lambda entry: {
        "id": entry.job_id,
        "state": state,
        "finished": True,
    }
    return engine, handle, events, clock


def names(events: list[dict[str, object]]) -> list[object]:
    return [e["event"] for e in events if e["event"] != "status"]


def test_no_finished_without_flush_alert_and_flush_is_asked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, handle, events, clock = make_engine(monkeypatch)
    engine.tick()
    assert handle.flushes == 1
    for _ in range(5):
        clock.now += th.FLUSH_WAIT + 1
        engine.tick()
    assert "finished" not in names(events)
    assert handle.flushes == 6


def test_flush_alert_sends_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _handle, events, clock = make_engine(monkeypatch)
    engine.tick()
    clock.now += 30
    engine._entries["j"].flushed = True
    engine.tick()
    assert names(events) == ["finished"]


def test_flush_timeout_drops_torrent_and_reports_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, handle, events, clock = make_engine(monkeypatch)
    engine.tick()
    clock.now += th.FLUSH_GIVE_UP + 1
    engine.tick()
    errors = [e for e in events if e["event"] == "error"]
    assert errors == [
        {
            "event": "error",
            "message": th.MSG_FLUSH_TIMEOUT,
            "id": "j",
            "code": "flush_timeout",
        }
    ]
    assert "finished" not in names(events)
    assert "j" not in engine._entries and engine._session.removed == [handle]
    assert all(t["id"] != "j" for t in events[-1]["torrents"])  # pyright: ignore[reportGeneralTypeIssues]


def test_storage_error_never_sends_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, handle, events, clock = make_engine(monkeypatch, state="error")
    engine._entries["j"].flushed = True
    engine.tick()
    clock.now += th.FLUSH_GIVE_UP + 1
    engine.tick()
    assert names(events) == [] and handle.flushes == 0
    assert events[-1]["torrents"][0]["state"] == "error"  # pyright: ignore[reportIndexIssue]
