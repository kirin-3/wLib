# SPDX-License-Identifier: GPL-3.0-or-later
"""F95Zone's public `latest_data` feed (90 games per page, about 311 pages)."""

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from ..net import Response
from ..store import CatalogStore
from .base import SourceContext

LATEST_DATA_URL = (
    "https://f95zone.to/sam/latest_alpha/latest_data.php"
    + "?cmd=list&cat=games&page={page}&sort=date&rows=90"
)
THREAD_URL = "https://f95zone.to/threads/{thread_id}/"
TAGS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "f95_tags.json")

# Even a 200 can carry the anonymous-limit message.
_LIMIT_MARKER = "limited amount of requests"


def _is_rate_limited(response: Response) -> bool:
    return response.status == 429 or _LIMIT_MARKER in response.text()


def _iso(ts: object) -> str | None:
    if not isinstance(ts, int):
        return None
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in cast(list[object], value) if isinstance(item, int)]


def parse_page(payload: object) -> tuple[list[dict[str, object]], int]:
    """Return the page's rows and the total page count."""
    if not isinstance(payload, dict):
        raise ValueError("F95 response is not an object")
    body = cast(dict[str, object], payload)
    if body.get("status") != "ok" or not isinstance(body.get("msg"), dict):
        raise ValueError(f"F95 response status: {body.get('status')!r}")
    msg = cast(dict[str, object], body["msg"])
    pagination = msg.get("pagination")
    total = 0
    if isinstance(pagination, dict):
        raw_total = cast(dict[str, object], pagination).get("total")
        total = raw_total if isinstance(raw_total, int) else 0
    rows: list[dict[str, object]] = []
    for item in cast(list[object], msg.get("data") or []):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, object], item)
        thread_id = entry.get("thread_id")
        if not isinstance(thread_id, int):
            continue
        ts = entry.get("ts")
        rows.append(
            {
                "thread_id": thread_id,
                "title": str(entry.get("title") or ""),
                "creator": str(entry.get("creator") or "") or None,
                "version": str(entry.get("version") or "") or None,
                "updated_at": _iso(ts),
                "ts": ts if isinstance(ts, int) else None,
                "views": entry.get("views") if isinstance(entry.get("views"), int) else None,
                "likes": entry.get("likes") if isinstance(entry.get("likes"), int) else None,
                "rating": entry.get("rating")
                if isinstance(entry.get("rating"), (int, float))
                else None,
                "prefixes": _int_list(entry.get("prefixes")),
                "tags": _int_list(entry.get("tags")),
                "cover_url": str(entry.get("cover") or "") or None,
                "url": THREAD_URL.format(thread_id=thread_id),
            }
        )
    return rows, total


def load_names(catalog: CatalogStore, path: str = TAGS_FILE) -> None:
    """Load the checked-in ID→name map; IDs missing from it simply have no name."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = cast(object, json.load(handle))
    except FileNotFoundError:
        data = {}
    tags: dict[int, str] = {}
    prefixes: dict[int, tuple[str, str | None]] = {}
    if isinstance(data, dict):
        body = cast(dict[str, object], data)
        raw_tags = body.get("tags")
        if isinstance(raw_tags, dict):
            for key, name in cast(dict[str, object], raw_tags).items():
                if key.isdigit() and isinstance(name, str):
                    tags[int(key)] = name
        raw_prefixes = body.get("prefixes")
        if isinstance(raw_prefixes, dict):
            for key, value in cast(dict[str, object], raw_prefixes).items():
                if not key.isdigit() or not isinstance(value, Mapping):
                    continue
                entry = cast(Mapping[str, object], value)
                name = entry.get("name")
                category = entry.get("category")
                if isinstance(name, str):
                    prefixes[int(key)] = (
                        name,
                        category if isinstance(category, str) else None,
                    )
    catalog.replace_names(tags, prefixes)


class F95Source:
    name: str = "f95"

    def _fetch(self, ctx: SourceContext, page: int) -> tuple[list[dict[str, object]], int]:
        response = ctx.pacer.request(
            self.name,
            LATEST_DATA_URL.format(page=page),
            is_rate_limited=_is_rate_limited,
        )
        rows, total = parse_page(response.json())
        ctx.upsert("f95_threads", "thread_id", rows)
        return rows, total

    def incremental(self, ctx: SourceContext) -> None:
        high_water = ctx.state.get(self.name, "hwm_ts")
        if not isinstance(high_water, int):
            return
        newest = high_water
        page = 1
        while True:
            rows, total = self._fetch(ctx, page)
            stamps = [row["ts"] for row in rows if isinstance(row["ts"], int)]
            newest = max([newest, *stamps])
            if not stamps or all(ts <= high_water for ts in stamps) or page >= total:
                break
            page += 1
        # Only advance once every page above the old mark was stored.
        ctx.state.put(self.name, "hwm_ts", newest)

    def sweep_due(self, ctx: SourceContext) -> bool:
        del ctx
        return True

    def sweep_step(self, ctx: SourceContext) -> bool:
        page = ctx.state.get(self.name, "sweep_page", 1)
        page = page if isinstance(page, int) and page > 0 else 1
        rows, total = self._fetch(ctx, page)
        if page == 1 and ctx.state.get(self.name, "hwm_ts") is None:
            stamps = [row["ts"] for row in rows if isinstance(row["ts"], int)]
            if stamps:
                ctx.state.put(self.name, "hwm_ts", max(stamps))
        ctx.state.put(self.name, "sweep_total_pages", total)
        if not rows or page >= total:
            return True
        ctx.state.put(self.name, "sweep_page", page + 1)
        return False

    def reset_sweep(self, ctx: SourceContext) -> None:
        ctx.state.put(self.name, "sweep_page", None)
