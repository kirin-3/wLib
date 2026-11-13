# SPDX-License-Identifier: GPL-3.0-or-later
"""Download pacing: the shared speed limit (design D8).

Host transfers run in this process and share one `SpeedLimiter` (a token
bucket). Torrents run in the helper process and get their share through the
helper's `set_limits`. `split_limit` decides the two shares each second tick;
they always add up to the limit, so the combined speed stays under it.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from threading import Event
from typing import final

DEFAULT_CHUNK = 1024 * 1024
MIN_CHUNK = 16 * 1024
# Stored-up allowance; keeps a burst after an idle moment short.
BURST_SECONDS = 0.25
# Longest single wait, so a changed limit applies within this time.
MAX_WAIT_SECONDS = 0.25
# A side that used less than this part of its share gives the rest away...
UNDERUSE = 0.8
# ...but keeps this part of the limit so it can speed up again.
FLOOR = 0.1


def _default_sleep(seconds: float, interrupt: Event) -> bool:
    return interrupt.wait(seconds)


@final
class SpeedLimiter:
    """A token bucket shared by every host transfer; rate 0 means unlimited."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float, Event], bool] = _default_sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._rate: float = 0.0
        self._tokens: float = 0.0
        self._last: float = clock()

    @property
    def rate(self) -> float:
        """Bytes per second; 0 is unlimited."""
        with self._lock:
            return self._rate

    def set_rate(self, bytes_per_second: float) -> None:
        with self._lock:
            self._refill()
            self._rate = max(0.0, float(bytes_per_second))
            if self._rate:
                self._tokens = min(self._tokens, self._rate * BURST_SECONDS)

    def chunk_size(self) -> int:
        """Read size: 1 MiB unlimited, else a quarter-second of the rate (at least 16 KiB)."""
        with self._lock:
            rate = self._rate
        if rate <= 0:
            return DEFAULT_CHUNK
        return int(min(DEFAULT_CHUNK, max(MIN_CHUNK, rate * BURST_SECONDS)))

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        if self._rate > 0:
            self._tokens = min(self._tokens + elapsed * self._rate, self._rate * BURST_SECONDS)

    def acquire(self, amount: int, interrupt: Event) -> bool:
        """Take `amount` bytes of allowance, waiting as needed; False when interrupted."""
        with self._lock:
            self._refill()
            if self._rate <= 0:
                return True
            self._tokens -= amount
        while True:
            with self._lock:
                self._refill()
                # Less than one byte owed counts as paid: a wait that short
                # can round to no time at all and never end.
                if self._rate <= 0 or self._tokens > -1.0:
                    return True
                wait = min(-self._tokens / self._rate, MAX_WAIT_SECONDS)
            if self._sleep(wait, interrupt) or interrupt.is_set():
                return False


def split_limit(
    limit_kib: int,
    host_running: int,
    torrent_running: int,
    host_used_kib: float | None = None,
    torrent_used_kib: float | None = None,
) -> tuple[int, int]:
    """(host share, torrent share) in KiB/s; they always add up to `limit_kib`.

    A side alone gets everything. Otherwise the shares follow the number of
    running jobs, and a side that used under 80% of its share (averaged over
    the last two ticks) gives the surplus to the other, keeping a 10% floor.
    0 means unlimited for both.
    """
    if limit_kib <= 0:
        return 0, 0
    if host_running and not torrent_running:
        return limit_kib, 0
    if torrent_running and not host_running:
        return 0, limit_kib
    if not host_running and not torrent_running:
        half = limit_kib // 2
        return limit_kib - half, half
    host = round(limit_kib * host_running / (host_running + torrent_running))
    torrent = limit_kib - host
    floor = max(1, math.ceil(limit_kib * FLOOR))
    host_under = host_used_kib is not None and host_used_kib < UNDERUSE * host
    torrent_under = torrent_used_kib is not None and torrent_used_kib < UNDERUSE * torrent
    if host_under and not torrent_under and host_used_kib is not None:
        host = min(max(floor, math.ceil(host_used_kib)), limit_kib - floor)
        return host, limit_kib - host
    if torrent_under and not host_under and torrent_used_kib is not None:
        torrent = min(max(floor, math.ceil(torrent_used_kib)), limit_kib - floor)
        return limit_kib - torrent, torrent
    return host, torrent


# Shared by every host transfer in this process.
host_limiter = SpeedLimiter()
