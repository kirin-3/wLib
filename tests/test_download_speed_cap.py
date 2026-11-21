# SPDX-License-Identifier: GPL-3.0-or-later
"""The shared download speed limit: interrupted waits and very small limits."""

from __future__ import annotations

from threading import Event
from typing import cast

import pytest

from core.downloads.manager import DownloadManager
from core.downloads.pacing import MIN_CHUNK, SpeedLimiter, split_limit
from core.downloads.torrents import TorrentSupervisor
from core.downloads.workers import HostWorker, TorrentWorker


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.waited = 0.0
        self.interrupt_next = False

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float, interrupt: Event) -> bool:
        del interrupt
        if self.interrupt_next:
            self.interrupt_next = False
            return True
        self.now += seconds
        self.waited += seconds
        return False


def test_an_interrupted_wait_gives_its_allowance_back() -> None:
    fake = _FakeClock()
    limiter = SpeedLimiter(clock=fake.clock, sleep=fake.sleep)
    limiter.set_rate(1024)
    fake.interrupt_next = True
    assert not limiter.acquire(MIN_CHUNK, Event())
    # The resumed read pays for its own 16 KiB only: 16 s at 1 KiB/s, not 32.
    assert limiter.acquire(MIN_CHUNK, Event())
    assert fake.waited == pytest.approx(16.0, abs=0.5)


class _Manager:
    def __init__(self) -> None:
        self.rate: float | None = None

    def set_rate(self, bytes_per_second: float) -> None:
        self.rate = bytes_per_second


class _Supervisor:
    def __init__(self) -> None:
        self.kib: int | None = None

    def set_download_limit(self, kib: int) -> None:
        self.kib = kib


def _applied_bytes(limit: int, host_n: int, torrent_n: int) -> tuple[float, int]:
    """Bytes/s each worker really applies for this split."""
    manager, supervisor = _Manager(), _Supervisor()
    host_worker = HostWorker(cast(DownloadManager, cast(object, manager)))
    torrent_worker = TorrentWorker(
        lambda: cast(TorrentSupervisor, cast(object, supervisor))
    )
    host, torrent = split_limit(limit, host_n, torrent_n)
    host_worker.set_speed_share(host, True)
    torrent_worker.set_speed_share(torrent, True)
    assert manager.rate is not None and supervisor.kib is not None
    # The helper turns its KiB value into bytes/s for libtorrent.
    return manager.rate, supervisor.kib * 1024


@pytest.mark.parametrize(
    ("limit", "host_n", "torrent_n"),
    [(1, 1, 1), (1, 2, 1), (2, 1, 5), (5, 1, 10), (5, 10, 1), (1000, 1, 1)],
)
def test_both_workers_together_stay_under_the_limit(
    limit: int, host_n: int, torrent_n: int
) -> None:
    host_rate, torrent_rate = _applied_bytes(limit, host_n, torrent_n)
    assert host_rate > 0 and torrent_rate > 0  # 0 would be unlimited
    assert host_rate + torrent_rate <= limit * 1024 + 1


def test_each_running_side_keeps_some_share_above_one_kib() -> None:
    assert split_limit(5, 1, 10) == (1, 4)
    assert split_limit(5, 10, 1) == (4, 1)
    assert split_limit(1, 1, 1) == (0, 1)
