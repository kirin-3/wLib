# SPDX-License-Identifier: GPL-3.0-or-later
"""The shared speed limit: the token bucket and the host/torrent split (design D8)."""
from __future__ import annotations

import io
import threading
import time
from threading import Event

import pytest

from core.downloads.pacing import DEFAULT_CHUNK, MIN_CHUNK, SpeedLimiter, split_limit


class FakeTime:
    """A clock that a sleep moves forward; no real waiting."""

    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float, interrupt: Event) -> bool:
        if interrupt.is_set():
            return True
        self.slept.append(seconds)
        self.now += seconds
        return False


def _limiter(rate: float) -> tuple[SpeedLimiter, FakeTime]:
    fake = FakeTime()
    limiter = SpeedLimiter(clock=fake.clock, sleep=fake.sleep)
    limiter.set_rate(rate)
    return limiter, fake


def test_unlimited_never_waits():
    limiter, fake = _limiter(0)
    assert limiter.chunk_size() == DEFAULT_CHUNK
    assert limiter.acquire(10 * DEFAULT_CHUNK, Event())
    assert fake.slept == []


def test_rate_stays_at_or_below_the_limit():
    rate = 100 * 1024
    limiter, fake = _limiter(rate)
    start = fake.now
    total = 0
    for _ in range(200):
        chunk = limiter.chunk_size()
        assert limiter.acquire(chunk, Event())
        total += chunk
    elapsed = fake.now - start
    # The bucket holds at most a quarter-second burst.
    assert total <= rate * (elapsed + 0.25)
    assert total / elapsed == pytest.approx(rate, rel=0.05)


def test_smaller_read_size_with_a_limit():
    limiter, _ = _limiter(32 * 1024)
    assert limiter.chunk_size() == MIN_CHUNK
    limiter.set_rate(8 * 1024 * 1024)
    # A quarter-second at 8 MiB/s is 2 MiB, more than the 1 MiB top.
    assert limiter.chunk_size() == DEFAULT_CHUNK
    limiter.set_rate(1024 * 1024)
    assert limiter.chunk_size() == 256 * 1024


def test_changed_limit_applies_during_a_transfer():
    limiter, fake = _limiter(50 * 1024)
    for _ in range(20):
        assert limiter.acquire(16 * 1024, Event())
    slow = fake.now
    limiter.set_rate(500 * 1024)
    for _ in range(20):
        assert limiter.acquire(16 * 1024, Event())
    fast = fake.now - slow
    # 320 KiB at 500 KiB/s takes well under a second; at 50 KiB/s it took over six.
    assert fast < 1.0 < slow - 100.0
    # Removing the limit lets the rest through at once.
    limiter.set_rate(0)
    before = fake.now
    assert limiter.acquire(10 * 1024 * 1024, Event())
    assert fake.now == before


def test_each_wait_is_short_so_a_new_limit_applies_soon():
    limiter, fake = _limiter(1024)
    assert limiter.acquire(64 * 1024, Event())
    assert fake.slept
    assert max(fake.slept) <= 0.25


def test_cancel_returns_at_once_during_a_wait():
    limiter = SpeedLimiter()
    limiter.set_rate(1024)
    stop = Event()
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(limiter.acquire(1024 * 1024, stop)))
    started = time.monotonic()
    worker.start()
    time.sleep(0.1)
    stop.set()
    worker.join(2)
    assert not worker.is_alive()
    assert result == [False]
    # 1 MiB at 1 KiB/s would wait about 17 minutes.
    assert time.monotonic() - started < 1.5


def test_transfer_loop_uses_the_limiter(tmp_path):
    from core.downloads.transfer import FileInfo, transfer

    data = b"PK\x03\x04" + b"x" * (256 * 1024)
    limiter, fake = _limiter(128 * 1024)
    path = transfer(
        FileInfo("game.zip", len(data), lambda: io.BytesIO(data)), "paced", Event(), lambda *_: None,
        directory=tmp_path, limiter=limiter,
    )
    assert path.read_bytes() == data
    # 256 KiB at 128 KiB/s with a quarter-second burst: at least 1.75 s.
    assert fake.now - 100.0 >= 1.7


@pytest.mark.parametrize(
    ("limit", "host_n", "torrent_n", "host_used", "torrent_used"),
    [
        (1000, 1, 1, None, None),
        (1000, 2, 1, None, None),
        (1000, 1, 3, 100.0, 750.0),
        (1001, 1, 1, 500.0, 10.0),
        (7, 3, 2, 0.0, 0.0),
        (1000, 0, 0, None, None),
        (5, 1, 1, 0.0, 5.0),
    ],
)
def test_shares_always_add_up_to_the_limit(limit, host_n, torrent_n, host_used, torrent_used):
    host, torrent = split_limit(limit, host_n, torrent_n, host_used, torrent_used)
    assert host + torrent == limit
    assert host >= 0 and torrent >= 0


def test_one_kind_alone_gets_all_of_it():
    assert split_limit(800, 2, 0) == (800, 0)
    assert split_limit(800, 0, 1) == (0, 800)


def test_zero_limit_is_unlimited_for_both():
    assert split_limit(0, 1, 1) == (0, 0)


def test_unused_share_moves_to_the_other_side():
    # Even split first: 500 / 500.
    assert split_limit(1000, 1, 1) == (500, 500)
    # The torrent uses only 100 of its 500: the host gets the rest.
    host, torrent = split_limit(1000, 1, 1, host_used_kib=500.0, torrent_used_kib=100.0)
    assert (host, torrent) == (900, 100)
    # The host is idle: the torrent gets all but the 10% floor.
    host, torrent = split_limit(1000, 1, 1, host_used_kib=0.0, torrent_used_kib=500.0)
    assert (host, torrent) == (100, 900)
    # Both under-use: the split by job count stays.
    assert split_limit(1000, 1, 1, host_used_kib=10.0, torrent_used_kib=10.0) == (500, 500)
