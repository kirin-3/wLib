# SPDX-License-Identifier: GPL-3.0-or-later
"""DLsite adult games: doujin (/maniax/) and commercial PC (/pro/).

Listing pages give IDs, circle names and genres; `product/info/ajax` gives
the rest for up to 100 IDs per request, including `age_category`.
"""

import re
from datetime import timedelta
from html import unescape
from typing import cast

from .base import SourceContext, periodic_sweep_due

PER_PAGE = 100
# A full re-crawl is about 1,400 requests; incremental runs cover new works.
SWEEP_INTERVAL = timedelta(days=7)
ADULT_AGE_CATEGORY = 3
# (site, listing filter). /pro/ lists all-ages works too; age_category drops them.
LISTINGS: tuple[tuple[str, str], ...] = (
    ("maniax", "work_category%5B0%5D/doujin/"),
    ("pro", ""),
)
LISTING_URL = (
    "https://www.dlsite.com/{site}/fsr/ajax/=/{filter}"
    + "order%5B0%5D/{order}/work_type_category%5B0%5D/game/"
    + "per_page/100/page/{page}/show_type/1"
)
INFO_URL = "https://www.dlsite.com/{site}/product/info/ajax?product_id={ids}&cdn_cache_min=1"
WORK_URL = "https://www.dlsite.com/{site}/work/=/product_id/{product_id}.html"
# Incremental runs stop at the first page with nothing new; this caps a runaway.
MAX_INCREMENTAL_PAGES = 30

_ITEM_SPLIT_RE = re.compile(r'<tr\s+data-list_item_product_id="(\w+)"')
_MAKER_RE = re.compile(
    r'<dd class="maker_name">.*?maker_id/(\w+)\.html">([^<]*)</a>', re.S
)
_TAGS_BLOCK_RE = re.compile(r'<dd class="search_tag">(.*?)</dd>', re.S)
_TAG_RE = re.compile(r">([^<]+)</a>")


def parse_listing(payload: object) -> tuple[list[dict[str, object]], int]:
    """Return the listed works (id, circle, genres) and the total work count."""
    if not isinstance(payload, dict):
        raise ValueError("DLsite listing is not an object")
    body = cast(dict[str, object], payload)
    html = body.get("search_result")
    info = body.get("page_info")
    if not isinstance(html, str) or not isinstance(info, dict):
        raise ValueError("DLsite listing is missing search_result or page_info")
    count = cast(dict[str, object], info).get("count")
    parts = _ITEM_SPLIT_RE.split(html)
    items: list[dict[str, object]] = []
    # split() yields [head, id1, chunk1, id2, chunk2, ...]
    for product_id, chunk in zip(parts[1::2], parts[2::2]):
        maker = _MAKER_RE.search(chunk)
        tags_block = _TAGS_BLOCK_RE.search(chunk)
        items.append(
            {
                "product_id": product_id,
                "circle_id": maker.group(1) if maker else None,
                "circle": unescape(maker.group(2)).strip() if maker else None,
                "genres": [
                    unescape(tag).strip()
                    for tag in cast(
                        list[str],
                        _TAG_RE.findall(tags_block.group(1) if tags_block else ""),
                    )
                ],
            }
        )
    return items, int(count) if isinstance(count, int) else 0


def merge_details(
    site: str, listed: list[dict[str, object]], payload: object
) -> tuple[list[dict[str, object]], list[str]]:
    """Combine listing items with their details; returns (adult rows, dropped IDs)."""
    details = cast(dict[str, object], payload) if isinstance(payload, dict) else {}
    rows: list[dict[str, object]] = []
    dropped: list[str] = []
    for item in listed:
        product_id = str(item["product_id"])
        raw = details.get(product_id)
        if not isinstance(raw, dict):
            continue
        info = cast(dict[str, object], raw)
        if info.get("age_category") != ADULT_AGE_CATEGORY:
            dropped.append(product_id)
            continue
        image = str(info.get("work_image") or "")
        rows.append(
            {
                "product_id": product_id,
                "site": str(info.get("site_id") or site),
                "title": str(info.get("work_name") or ""),
                "circle_id": item["circle_id"] or info.get("maker_id"),
                "circle": item["circle"],
                "work_type": info.get("work_type"),
                "age_category": ADULT_AGE_CATEGORY,
                "genres": item["genres"],
                "released_at": info.get("regist_date"),
                "cover_url": f"https:{image}" if image.startswith("//") else image or None,
                "url": WORK_URL.format(site=site, product_id=product_id),
            }
        )
    return rows, dropped


class DlsiteSource:
    name: str = "dlsite"

    def _page(
        self, ctx: SourceContext, listing: int, page: int, *, only_new: bool
    ) -> tuple[int, int]:
        """Fetch one listing page and its details; returns (new IDs, total works)."""
        site, filter_path = LISTINGS[listing]
        response = ctx.pacer.request(
            self.name,
            LISTING_URL.format(site=site, filter=filter_path, order="release_d", page=page),
        )
        items, total = parse_listing(response.json())
        if not items and total > (page - 1) * PER_PAGE:
            # A page the listing says exists must have works on it. An empty
            # one is a failed request; advancing past it would leave its IDs
            # unseen until the removal marking treats them as gone.
            raise ValueError(f"DLsite returned an empty page {page} of {total}")
        ids = [str(item["product_id"]) for item in items]
        stored = ctx.catalog.existing_keys("dlsite_works", "product_id", ids)
        known = stored | ctx.state.skipped_dlsite(ids)
        if only_new:
            ctx.catalog.touch("dlsite_works", "product_id", known, ctx.now())
            items = [item for item in items if str(item["product_id"]) not in known]
        if items:
            info = ctx.pacer.request(
                self.name,
                INFO_URL.format(
                    site=site, ids=",".join(str(item["product_id"]) for item in items)
                ),
            )
            rows, dropped = merge_details(site, items, info.json())
            if not rows and not dropped and stored and not only_new:
                # An empty details payload is a failed request, not a page of
                # works to drop. Advancing would hide the stored works from
                # the sweep and let the removal marking delete them. New works
                # without details are simply retried, so they never stall the
                # source.
                raise ValueError(
                    f"DLsite returned no details for {len(items)} listed works"
                )
            ctx.upsert("dlsite_works", "product_id", rows)
            ctx.state.add_skipped_dlsite(dropped)
        return len([value for value in ids if value not in known]), total

    def incremental(self, ctx: SourceContext) -> None:
        if ctx.state.source_status(self.name)["sweep_completed_at"] is None:
            return
        for listing in range(len(LISTINGS)):
            for page in range(1, MAX_INCREMENTAL_PAGES + 1):
                new, total = self._page(ctx, listing, page, only_new=True)
                if new == 0 or page * PER_PAGE >= total:
                    break

    def sweep_due(self, ctx: SourceContext) -> bool:
        return periodic_sweep_due(ctx, self.name, SWEEP_INTERVAL)

    def sweep_step(self, ctx: SourceContext) -> bool:
        cursor = ctx.state.get(self.name, "sweep_cursor", [0, 1])
        listing, page = cast(list[int], cursor) if isinstance(cursor, list) else [0, 1]
        _, total = self._page(ctx, listing, page, only_new=False)
        if page * PER_PAGE < total:
            ctx.state.put(self.name, "sweep_cursor", [listing, page + 1])
            return False
        if listing + 1 < len(LISTINGS):
            ctx.state.put(self.name, "sweep_cursor", [listing + 1, 1])
            return False
        return True

    def reset_sweep(self, ctx: SourceContext) -> None:
        ctx.state.put(self.name, "sweep_cursor", None)
