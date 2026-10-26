# SPDX-License-Identifier: GPL-3.0-or-later
"""Fakes shared by the catalog builder tests."""

import json
import os
from collections.abc import Callable

from catalog_server.net import Pacer, Request, Response
from catalog_server.sources import SourceContext
from catalog_server.store import CatalogStore, StateStore

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "catalog")


def fixture_bytes(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        return handle.read()


def fixture_json(name: str) -> object:
    return json.loads(fixture_bytes(name))


def json_response(payload: object, status: int = 200, headers=None) -> Response:
    return Response(status, json.dumps(payload).encode(), headers or {})


class FakeClock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def iso(self) -> str:
        from datetime import UTC, datetime

        return datetime.fromtimestamp(self.now, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeTransport:
    """Answers each request with the first route whose predicate matches."""

    def __init__(self) -> None:
        self.routes: list[tuple[Callable[[Request], bool], Callable[[Request], Response]]] = []
        self.requests: list[Request] = []

    def route(self, match, respond) -> None:
        predicate = match if callable(match) else (lambda request, text=match: text in request.url)
        handler = respond if callable(respond) else (lambda request, value=respond: value)
        self.routes.append((predicate, handler))

    def __call__(self, request: Request) -> Response:
        self.requests.append(request)
        for predicate, handler in self.routes:
            if predicate(request):
                return handler(request)
        raise AssertionError(f"unexpected request: {request.url}")


def make_context(tmp_path, transport, clock=None, wait_for_budget=()):
    clock = clock or FakeClock()
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    state = StateStore(str(tmp_path / "state.sqlite"))
    pacer = Pacer(state, transport, clock=clock, sleep=clock.sleep, wait_for_budget=wait_for_budget)
    return SourceContext(catalog, state, pacer, clock.iso), clock


def rows(catalog: CatalogStore, sql: str, params=()):
    return catalog.conn.execute(sql, params).fetchall()
