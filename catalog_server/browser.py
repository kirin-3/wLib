# SPDX-License-Identifier: GPL-3.0-or-later
"""GET requests through Scrapling's StealthySession (a patched, headless Chromium).

Scrapling is a server-only dependency, imported only when the first request
needs a browser. Playwright's sync API allows one live browser per thread, so
one browser runs at a time: it stays open while requests use the same proxy
(None = direct) and restarts when the proxy changes. Sources run one after
another, so that happens about once per run. `close()` shuts it.
"""

import importlib
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, cast

from .net import Request, Response

log = logging.getLogger(__name__)

# Milliseconds for each page load.
PAGE_TIMEOUT_MS = 60_000


class BrowserPage(Protocol):
    status: int
    body: bytes
    headers: Mapping[str, str]


class BrowserSession(Protocol):
    def start(self) -> None: ...

    def fetch(self, url: str, **kwargs: object) -> BrowserPage: ...

    def close(self) -> None: ...


SessionFactory = Callable[..., BrowserSession]


def _scrapling_session(**kwargs: object) -> BrowserSession:
    fetchers = importlib.import_module("scrapling.fetchers")
    make = cast(SessionFactory, getattr(fetchers, "StealthySession"))
    return make(**kwargs)


def _xf_user_cookies(value: str, domain: str) -> list[dict[str, object]]:
    return [
        {
            "name": "xf_user",
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "Lax",
        }
    ]


def f95_login_cookies(value: str) -> list[dict[str, object]]:
    """The browser cookie for F95's `xf_user` value; it is sent only to f95zone.to."""
    return _xf_user_cookies(value, ".f95zone.to")


def lc_login_cookies(value: str) -> list[dict[str, object]]:
    """The browser cookie for LewdCorner's `xf_user` value; it is sent only to lewdcorner.com."""
    return _xf_user_cookies(value, ".lewdcorner.com")


class BrowserTransport:
    def __init__(
        self,
        *,
        cookies: Sequence[Mapping[str, object]] = (),
        session_factory: SessionFactory = _scrapling_session,
    ) -> None:
        self._cookies: list[dict[str, object]] = [dict(cookie) for cookie in cookies]
        self._session_factory: SessionFactory = session_factory
        self._live: tuple[str | None, BrowserSession] | None = None

    def _session(self, proxy: str | None) -> BrowserSession:
        if self._live is not None and self._live[0] != proxy:
            self.close()
        if self._live is None:
            options: dict[str, object] = {
                "headless": True,
                "disable_resources": True,
                "google_search": False,
                "solve_cloudflare": True,
                "timeout": PAGE_TIMEOUT_MS,
                # The Pacer retries and counts every attempt against the budget.
                "retries": 1,
                "cookies": self._cookies,
            }
            if proxy is not None:
                options["proxy"] = proxy
                options["block_webrtc"] = True
            session = self._session_factory(**options)
            session.start()
            self._live = (proxy, session)
        return self._live[1]

    def close(self) -> None:
        if self._live is None:
            return
        _, session = self._live
        self._live = None
        try:
            session.close()
        except Exception:  # noqa: BLE001 - a dead browser may fail to close
            log.warning("closing a failed browser session failed", exc_info=True)

    def __call__(self, request: Request) -> Response:
        if request.method != "GET" or request.body is not None:
            raise ValueError(f"the browser only sends GET requests, not {request.method}")
        try:
            page = self._session(request.proxy).fetch(request.url)
        except Exception as exc:  # noqa: BLE001 - every browser failure is retried
            # A new browser starts for the next attempt.
            self.close()
            raise ConnectionError(f"{request.url}: {exc}") from exc
        if page.status == 0:
            self.close()
            raise ConnectionError(f"{request.url}: the browser got no response")
        return Response(page.status, bytes(page.body), dict(page.headers))
