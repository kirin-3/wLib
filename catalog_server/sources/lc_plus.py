# SPDX-License-Identifier: GPL-3.0-or-later
"""The guest listings of LewdCorner's Plus forums, Games+ (node 119) and AI
Games+ (node 213), newest update first, 20 threads per page.

The builder's LC account is Standard, so its feed never lists Plus threads,
but a guest (no cookie) can open these listings and the Plus thread pages.
Each card gives the thread ID, the title with its bracket tags, the prefix
labels, the cover and the day of the last update. The version is never taken
from the card title: it comes from the thread page's Version field
(`lc_threads`), and the stored version is cleared whenever the card shows a
new date, so a version always belongs to its date.

A page must be LC's own guest forum page; anything else (a Cloudflare
challenge, a login page, a page with a login) stores nothing and stops LC for
the run.
"""

import html
import logging
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from ..net import RateLimited
from .base import SourceContext
from .lc import THREAD_URL, lc_pause

log = logging.getLogger(__name__)

# Games+ and AI Games+, in sweep order.
FORUMS: tuple[int, ...] = (119, 213)
LISTING_URL = (
    "https://lewdcorner.com/forums/{node}/{page}?order=latest_update_date&direction=desc"
)
TIER = "Premium"
# The card label for the prefix the feed names "Completed".
_LABEL_NAMES = {"complete": "completed"}

_HTML_TAG_RE = re.compile(r"<html\b[^>]*>", re.S)
_FORUM_VIEW_RE = re.compile(r'\sdata-template="forum_view"')
_GUEST_RE = re.compile(r'\sdata-logged-in="false"')
_CARD_SPLIT = '<div class="lcForumCard">'
_TITLE_RE = re.compile(
    r'<h3 class="lcForumCardTitle"><a href="/threads/[^"]*?\.(\d+)/">(.*?)</a></h3>', re.S
)
_LABEL_RE = re.compile(r'<span class="label\b[^"]*"[^>]*>(.*?)</span>', re.S)
_LABEL_APPEND_RE = re.compile(r'<span class="label-append">.*?</span>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_META_RE = re.compile(r'<div class="lcForumCardMeta">(.*?)</div>', re.S)
_DATE_RE = re.compile(r"<span>([A-Z][a-z]{2} \d{1,2}, \d{4})</span>")
_COVER_RE = re.compile(r'<a class="lcForumCardMedia"[^>]*>.*?<img src="([^"]+)"', re.S)
_PAGE_LINK_RE = re.compile(r'href="/forums/[^"]*?/page-(\d+)')
_TRAILING_GROUP_RE = re.compile(r"\s*\[([^\[\]]*)\]\s*$")


class LcPlusPageError(ValueError):
    """The answer is not LC's guest forum listing."""


@dataclass(frozen=True)
class Card:
    thread_id: int
    title: str
    creator: str | None
    labels: tuple[str, ...]
    ts: int
    cover_url: str | None


@dataclass(frozen=True)
class ListingPage:
    cards: list[Card]
    # Cards that did not parse; counted in the log, not stored.
    skipped: int
    # The highest page number the page links to, or None without page links.
    last_page: int | None


def _label_key(label: str) -> str:
    """A card label as the casefolded prefix name the feed uses. Some engine
    labels are shown in brackets ("[Unity]") where the feed names them
    without ("Unity")."""
    name = label.casefold()
    if len(name) > 2 and name.startswith("[") and name.endswith("]"):
        name = name[1:-1].strip()
    return _LABEL_NAMES.get(name, name)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", fragment))).strip()


def split_title(text: str) -> tuple[str, str | None]:
    """The title without its trailing bracket groups, and the creator from the
    last group when there are two or more ("[v1.2.0] [opopoworld]")."""
    groups: list[str] = []
    rest = text
    while (match := _TRAILING_GROUP_RE.search(rest)) is not None and match.start() > 0:
        groups.insert(0, match.group(1).strip())
        rest = rest[: match.start()]
    title = rest.strip()
    creator = groups[-1] if len(groups) >= 2 and groups[-1] else None
    return title, creator


def _day_ts(text: str) -> int:
    """UTC midnight of a card date such as "Oct 13, 2026"."""
    day = datetime.strptime(text, "%b %d, %Y").replace(tzinfo=UTC)
    return int(day.timestamp())


def _card(block: str) -> Card | None:
    title_match = _TITLE_RE.search(block)
    meta = _META_RE.search(block)
    date = _DATE_RE.findall(meta.group(1)) if meta is not None else []
    if title_match is None or not date:
        return None
    inner = title_match.group(2)
    labels = tuple(_clean(label) for label in cast(list[str], _LABEL_RE.findall(inner)))
    text = _clean(_LABEL_APPEND_RE.sub("", _LABEL_RE.sub("", inner)))
    title, creator = split_title(text)
    if not title:
        return None
    try:
        ts = _day_ts(cast(str, date[-1]))
    except ValueError:
        return None
    cover = _COVER_RE.search(block)
    return Card(
        thread_id=int(title_match.group(1)),
        title=title,
        creator=creator,
        labels=tuple(label for label in labels if label),
        ts=ts,
        cover_url=html.unescape(cover.group(1)) if cover is not None else None,
    )


def check_page(page: str) -> None:
    """Raise LcPlusPageError unless `page` is LC's forum listing seen as a guest."""
    tag = _HTML_TAG_RE.search(page)
    if tag is None or _FORUM_VIEW_RE.search(tag.group(0)) is None:
        raise LcPlusPageError("LC answered without its forum listing")
    if _GUEST_RE.search(tag.group(0)) is None:
        # A login on a guest request means a cookie was sent.
        raise LcPlusPageError("LC answered a guest request with a logged-in page")


def parse_listing(page: str) -> ListingPage:
    check_page(page)
    cards: list[Card] = []
    skipped = 0
    for block in page.split(_CARD_SPLIT)[1:]:
        card = _card(block)
        if card is None:
            skipped += 1
        else:
            cards.append(card)
    numbers = [int(number) for number in cast(list[str], _PAGE_LINK_RE.findall(page))]
    return ListingPage(cards, skipped, max(numbers) if numbers else None)


def listing_url(node: int, page: int) -> str:
    return LISTING_URL.format(node=node, page="" if page <= 1 else f"page-{page}")


class LcPlusSource:
    """Reads the Plus listings with its own pacer budget (`lc_plus`)."""

    name: str = "lc_plus"

    def _fetch(self, ctx: SourceContext, node: int, page: int) -> ListingPage:
        if (paused := lc_pause(ctx.pacer)) is not None:
            raise RateLimited(self.name, paused)
        response = ctx.pacer.request(self.name, listing_url(node, page))
        try:
            listing = parse_listing(response.text())
        except LcPlusPageError as exc:
            # Store nothing and stop the LC steps for this run.
            ctx.pacer.paused[self.name] = str(exc)
            raise
        if listing.skipped:
            log.warning(
                "lc_plus: %d cards on forum %d page %d did not parse", listing.skipped, node, page
            )
        return listing

    def _prefix_ids(self, ctx: SourceContext, cards: Sequence[Card]) -> dict[str, int]:
        """Label name (casefolded) to its one prefix ID; ambiguous or unknown
        names are left out and counted in the log."""
        by_name = ctx.catalog.lc_prefix_ids_by_name()
        found: dict[str, int] = {}
        dropped: Counter[str] = Counter()
        for card in cards:
            for label in card.labels:
                ids = by_name.get(_label_key(label), [])
                if len(ids) == 1:
                    found[_label_key(label)] = ids[0]
                else:
                    dropped[label] += 1
        if dropped:
            log.info(
                "lc_plus: dropped labels without one prefix ID: %s",
                ", ".join(f"{label} ({count})" for label, count in sorted(dropped.items())),
            )
        return found

    def _store(self, ctx: SourceContext, node: int, cards: Sequence[Card]) -> bool:
        """Upsert the cards; True when every card was already stored unchanged."""
        if not cards:
            return False
        stored = ctx.catalog.lc_listing_state(card.thread_id for card in cards)
        prefix_ids = self._prefix_ids(ctx, cards)
        changed: list[dict[str, object]] = []
        same: list[dict[str, object]] = []
        # Stored threads whose version is cleared here. Their page must be read
        # again even when the details step read it for a later `ts` (an Atlas
        # or feed time on the same day is later than the card's midnight), or
        # the version would stay empty.
        cleared: list[int] = []
        for card in cards:
            prefixes: list[int] = []
            for label in card.labels:
                prefix = prefix_ids.get(_label_key(label))
                if prefix is not None and prefix not in prefixes:
                    prefixes.append(prefix)
            row: dict[str, object] = {
                "thread_id": card.thread_id,
                "title": card.title,
                "creator": card.creator,
                "updated_at": datetime.fromtimestamp(card.ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ts": card.ts,
                "node_id": node,
                "tier": TIER,
                "prefixes": prefixes,
                "cover_url": card.cover_url,
                "url": THREAD_URL.format(thread_id=card.thread_id),
            }
            before = stored.get(card.thread_id)
            if before is None:
                changed.append({**row, "version": None})
                continue
            ts, tier, origin = before
            if ts != card.ts or origin is not None or tier != TIER:
                # A new date, an Atlas row or a thread new to Plus has no
                # version until its page is read.
                changed.append({**row, "version": None})
                cleared.append(card.thread_id)
            else:
                same.append(row)
        ctx.upsert("lc_threads", "thread_id", changed)
        ctx.upsert("lc_threads", "thread_id", same)
        _ = ctx.catalog.mark_lc_pages_unread(cleared, any_status=True)
        return not changed

    def incremental(self, ctx: SourceContext) -> None:
        if ctx.state.source_status(self.name)["sweep_completed_at"] is None:
            # The first sweep reads every page; until then this would read them twice.
            return
        for node in FORUMS:
            page = 1
            while True:
                listing = self._fetch(ctx, node, page)
                unchanged = self._store(ctx, node, listing.cards)
                if unchanged or not listing.cards or page >= (listing.last_page or 1):
                    break
                page += 1

    def sweep_due(self, ctx: SourceContext) -> bool:
        del ctx
        return True

    def sweep_step(self, ctx: SourceContext) -> bool:
        index = ctx.state.get(self.name, "sweep_forum", 0)
        index = index if isinstance(index, int) and 0 <= index < len(FORUMS) else 0
        page = ctx.state.get(self.name, "sweep_page", 1)
        page = page if isinstance(page, int) and page > 0 else 1
        node = FORUMS[index]
        listing = self._fetch(ctx, node, page)
        total = listing.last_page
        if total is None:
            # One page has no page links; a later page without them uses the stored total.
            stored = ctx.state.get(self.name, "sweep_total_pages")
            total = stored if page > 1 and isinstance(stored, int) and stored > 0 else page
        total = max(total, page)
        ctx.state.put(self.name, "sweep_total_pages", total)
        if not listing.cards and page < total:
            # An empty page inside the listing is a failed request, not the end.
            raise ValueError(f"LC forum {node} returned an empty page {page} of {total}")
        _ = self._store(ctx, node, listing.cards)
        if page < total:
            ctx.state.put(self.name, "sweep_page", page + 1)
            return False
        if index + 1 < len(FORUMS):
            ctx.state.put(self.name, "sweep_forum", index + 1)
            ctx.state.put(self.name, "sweep_page", 1)
            ctx.state.put(self.name, "sweep_total_pages", None)
            return False
        return True

    def reset_sweep(self, ctx: SourceContext) -> None:
        for key in ("sweep_forum", "sweep_page", "sweep_total_pages"):
            ctx.state.put(self.name, key, None)
