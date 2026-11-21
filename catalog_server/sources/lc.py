# SPDX-License-Identifier: GPL-3.0-or-later
"""LewdCorner's login-gated latest-updates feed (90 games per page, about 105 pages).

`cat=Games` returns the Games and AI Games forums (and, for a Plus account,
their Plus forums), sorted strictly by each item's `timestamp`, the time of
its last update. Only items in the game forums are kept; other forums are
skipped and counted in the log. The feed answers only a logged-in account: an
answer without a login stores nothing and ends this step. The guest steps
(`lc_plus`, `lc_threads`) still run; a rate limit or a challenge on any LC
step stops all of them for the run (`lc_pause`).
"""

import logging
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from ..net import Pacer, Response
from .base import SourceContext

log = logging.getLogger(__name__)

PER_PAGE = 90
FEED_URL = (
    "https://lewdcorner.com/latest-updates/api"
    + f"?page={{page}}&per_page={PER_PAGE}&cat=Games&sort=date"
)
THREAD_URL = "https://lewdcorner.com/threads/{thread_id}/"
# Games, AI Games, Games+ and AI Games+ (the last two are LC Plus forums).
GAME_NODES = frozenset({6, 212, 119, 213})
# Answers that can mean "not logged in"; checked here instead of failing as HTTP errors.
_LOGIN_STATUSES = (401, 403)
NO_LOGIN = "no LC login; check the cookie with check-lc-login"


# Pacer keys of every LC step; a pause on one stops the guest steps.
LC_STEPS = ("lc", "lc_plus", "lc_threads")


class LcLoginError(ValueError):
    """The feed answered without a logged-in session."""


def lc_pause(pacer: Pacer) -> str | None:
    """Why LC is paused for this run (a rate limit or a challenge on any LC
    step), or None. A lost feed login does not pause LC."""
    for step in LC_STEPS:
        if step in pacer.paused:
            return f"{step} is paused: {pacer.paused[step]}"
    return None


def _iso(ts: object) -> str | None:
    if not isinstance(ts, int) or isinstance(ts, bool):
        return None
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item for item in cast(list[object], value) if isinstance(item, str) and item
    ]


def _payload(response: Response) -> Mapping[str, object]:
    """The feed's JSON object; raises LcLoginError when LC shows no login."""
    if response.status in _LOGIN_STATUSES:
        raise LcLoginError(f"{NO_LOGIN} (HTTP {response.status})")
    text = response.text().lstrip()
    if text.startswith("<"):
        # A login form or a Cloudflare challenge instead of the JSON feed.
        raise LcLoginError(f"{NO_LOGIN} (HTML answer)")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("LC feed answer is not JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("LC feed answer is not an object")
    body = cast(dict[str, object], payload)
    error = body.get("error")
    if isinstance(error, str) and error:
        # Logged out, LC answers status "ok" with no items and this error.
        raise LcLoginError(f"{NO_LOGIN} ({error})")
    return body


def parse_page(
    payload: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[int, str], int, int | None, bool]:
    """Rows, prefix names, the item count before filtering, total pages and `hasMore`."""
    if payload.get("status") != "ok" or not isinstance(payload.get("result"), dict):
        raise ValueError(f"LC feed status: {payload.get('status')!r}")
    result = cast(dict[str, object], payload["result"])
    items = result.get("items")
    if not isinstance(items, list):
        raise ValueError("LC feed answer has no items")
    total_items = _int(result.get("total"))
    pages = -(-total_items // PER_PAGE) if total_items else None
    has_more = result.get("hasMore") is True
    rows: list[dict[str, object]] = []
    names: dict[int, str] = {}
    skipped: Counter[object] = Counter()
    for item in cast(list[object], items):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, object], item)
        thread_id = _int(entry.get("id"))
        title = entry.get("title")
        if thread_id is None or not isinstance(title, str) or not title.strip():
            continue
        node_id = _int(entry.get("node_id"))
        if node_id not in GAME_NODES:
            skipped[node_id] += 1
            continue
        prefixes: list[int] = []
        for badge in cast(list[object], entry.get("badges") or []):
            if not isinstance(badge, dict):
                continue
            badge_id = _int(cast(dict[str, object], badge).get("id"))
            badge_name = cast(dict[str, object], badge).get("name")
            if badge_id is None:
                continue
            if badge_id not in prefixes:
                prefixes.append(badge_id)
            if isinstance(badge_name, str) and badge_name:
                names[badge_id] = badge_name
        cover = entry.get("image") if isinstance(entry.get("image"), str) else None
        ts = _int(entry.get("timestamp"))
        rating = entry.get("rating")
        rows.append(
            {
                "thread_id": thread_id,
                "title": title.strip(),
                "creator": str(entry.get("developer") or "").strip() or None,
                "version": str(entry.get("version") or "").strip() or None,
                "updated_at": _iso(ts),
                "ts": ts,
                "node_id": node_id,
                "tier": "Premium" if entry.get("is_premium") is True else "Free",
                "prefixes": prefixes,
                "tags": _strings(entry.get("tags")),
                "platforms": _strings(entry.get("platforms")),
                "cover_url": cover or None,
                "screens": [
                    url
                    for url in _strings(entry.get("images"))
                    if url != cover and url.startswith("https://")
                ],
                "views": _int(entry.get("view_count")),
                "likes": _int(entry.get("like_score")),
                "rating": rating
                if isinstance(rating, (int, float)) and not isinstance(rating, bool)
                else None,
                "url": THREAD_URL.format(thread_id=thread_id),
            }
        )
    if skipped:
        log.info(
            "lc: skipped %d items outside the game forums: %s",
            sum(skipped.values()),
            ", ".join(
                f"node {node}: {count}"
                for node, count in sorted(
                    skipped.items(), key=lambda pair: str(pair[0])
                )
            ),
        )
    return rows, names, len(cast(list[object], items)), pages, has_more


class LcSource:
    name: str = "lc"

    def _fetch(
        self, ctx: SourceContext, page: int
    ) -> tuple[list[dict[str, object]], int, int | None, bool]:
        response = ctx.pacer.request(
            self.name, FEED_URL.format(page=page), ok_statuses=_LOGIN_STATUSES
        )
        # A lost login raises LcLoginError: this step stores nothing and ends,
        # and the guest steps still run.
        payload = _payload(response)
        rows, names, count, pages, has_more = parse_page(payload)
        ctx.catalog.upsert_lc_prefix_names(names)
        ctx.upsert("lc_threads", "thread_id", rows)
        return rows, count, pages, has_more

    def incremental(self, ctx: SourceContext) -> None:
        high_water = ctx.state.get(self.name, "hwm_ts")
        if not isinstance(high_water, int):
            return
        newest = high_water
        page = 1
        while True:
            rows, _count, pages, has_more = self._fetch(ctx, page)
            stamps = [row["ts"] for row in rows if isinstance(row["ts"], int)]
            newest = max([newest, *stamps])
            if (
                not stamps
                or all(ts <= high_water for ts in stamps)
                or not has_more
                or (pages is not None and page >= pages)
            ):
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
        rows, count, fetched_pages, _has_more = self._fetch(ctx, page)
        if fetched_pages is None:
            # A reply without its total must not end the sweep: every later
            # page would be marked removed. Use the count from earlier pages.
            stored = ctx.state.get(self.name, "sweep_total_pages")
            if not isinstance(stored, int) or stored <= 0 or page == 1:
                raise ValueError(f"LC returned page {page} without a total")
            fetched_pages = stored
        total = fetched_pages
        if page == 1 and ctx.state.get(self.name, "hwm_ts") is None:
            stamps = [row["ts"] for row in rows if isinstance(row["ts"], int)]
            if stamps:
                ctx.state.put(self.name, "hwm_ts", max(stamps))
        ctx.state.put(self.name, "sweep_total_pages", total)
        if not count and page <= total:
            # An empty page inside the feed is a failed request, not the end.
            raise ValueError(f"LC returned an empty page {page} of {total}")
        if page >= total:
            return True
        ctx.state.put(self.name, "sweep_page", page + 1)
        return False

    def reset_sweep(self, ctx: SourceContext) -> None:
        ctx.state.put(self.name, "sweep_page", None)
