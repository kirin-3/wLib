# SPDX-License-Identifier: GPL-3.0-or-later
"""Outgoing requests: one at a time per site, paced, with budgets kept in state.sqlite."""

import json
import socket
import ssl
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from http.client import HTTPResponse
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrllibRequest
from urllib.request import urlopen

from .config import SITE_LIMITS, USER_AGENT, SiteLimits
from .store import StateStore


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict[str, str])
    body: bytes | None = None


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict[str, str])

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        return cast(object, json.loads(self.body))

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


Transport = Callable[[Request], Response]


class RateLimited(Exception):
    def __init__(self, site: str, reason: str) -> None:
        super().__init__(f"{site}: {reason}")
        self.site: str = site
        self.reason: str = reason


class BudgetExhausted(Exception):
    def __init__(self, site: str) -> None:
        super().__init__(f"{site}: request budget used up for this window")
        self.site: str = site


class HttpStatusError(Exception):
    def __init__(self, site: str, status: int, url: str) -> None:
        super().__init__(f"{site}: HTTP {status} for {url}")
        self.status: int = status


def urllib_transport(request: Request) -> Response:
    raw = UrllibRequest(
        request.url,
        data=request.body,
        headers=dict(request.headers),
        method=request.method,
    )
    try:
        with cast(
            HTTPResponse,
            urlopen(raw, timeout=60, context=ssl.create_default_context()),
        ) as response:
            return Response(response.status, response.read(), dict(response.headers))
    except HTTPError as exc:
        with exc:
            return Response(exc.code, exc.read(), dict(exc.headers))
    except (TimeoutError, socket.timeout, URLError) as exc:
        raise ConnectionError(f"{request.url}: {exc}") from exc


class Pacer:
    """Sends every builder request; enforces delay and budget per site.

    After a 429 or a rate-limit body the site stays paused until this Pacer
    (one run) ends, so the next run tries again.
    """

    def __init__(
        self,
        state: StateStore,
        transport: Transport = urllib_transport,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        wait_for_budget: Collection[str] = (),
        limits: Mapping[str, SiteLimits] = SITE_LIMITS,
    ) -> None:
        self._state: StateStore = state
        self._transport: Transport = transport
        self._clock: Callable[[], float] = clock
        self._sleep: Callable[[float], None] = sleep
        # Sites whose budget is waited out instead of ending the source's run.
        self.wait_for_budget: frozenset[str] = frozenset(wait_for_budget)
        self._limits: Mapping[str, SiteLimits] = limits
        self.paused: dict[str, str] = {}
        self.sent: dict[str, int] = {}

    def remaining(self, site: str) -> int | None:
        limits = self._limits[site]
        if limits.budget is None:
            return None
        used = len(self._state.request_times(site, self._clock() - limits.window))
        return max(0, limits.budget - used)

    def _wait_turn(self, site: str) -> None:
        limits = self._limits[site]
        if limits.budget is not None:
            while True:
                now = self._clock()
                times = self._state.request_times(site, now - limits.window)
                if len(times) < limits.budget:
                    break
                if site not in self.wait_for_budget:
                    raise BudgetExhausted(site)
                # The oldest request in the window drops out first.
                self._sleep(max(1.0, times[0] + limits.window - now + 0.5))
        last = self._state.last_request_at(site)
        if last is not None:
            remaining = limits.min_delay - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)

    def request(
        self,
        site: str,
        url: str,
        *,
        method: str = "GET",
        json_body: object = None,
        is_rate_limited: Callable[[Response], bool] | None = None,
        ok_statuses: Collection[int] = (),
    ) -> Response:
        if site in self.paused:
            raise RateLimited(site, self.paused[site])
        self._wait_turn(site)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json, */*"}
        body = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode()
        status: int | None = None
        try:
            response = self._transport(Request(method, url, headers, body))
            status = response.status
        finally:
            self._state.log_request(site, self._clock(), status)
            self.sent[site] = self.sent.get(site, 0) + 1
        if response.status == 429 or (
            is_rate_limited is not None and is_rate_limited(response)
        ):
            reason = f"rate limited (HTTP {response.status})"
            self.paused[site] = reason
            raise RateLimited(site, reason)
        if response.status >= 400 and response.status not in ok_statuses:
            raise HttpStatusError(site, response.status, url)
        return response
