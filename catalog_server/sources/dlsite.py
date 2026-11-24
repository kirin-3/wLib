# SPDX-License-Identifier: GPL-3.0-or-later
"""DLsite adult games: doujin (/maniax/) and commercial PC (/pro/).

Listing pages give IDs, circle names and genres; `product/info/ajax` gives
the rest for up to 100 IDs per request, including `age_category`. Each page
also reads the English listing and English details, and a server-side genre
map in `state.sqlite` translates genre IDs into English.
"""

import logging
import re
from collections.abc import Mapping
from datetime import timedelta
from html import unescape
from typing import cast

from ..net import HttpStatusError
from .base import SourceContext, periodic_sweep_due

_log = logging.getLogger(__name__)

PER_PAGE = 100
# A full re-crawl is about 2,800 requests; incremental runs cover new works.
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
LISTING_EN_URL = LISTING_URL + "?locale=en_US"
INFO_URL = (
    "https://www.dlsite.com/{site}/product/info/ajax?product_id={ids}&cdn_cache_min=1"
)
INFO_EN_URL = INFO_URL + "&locale=en_US"
GENRE_LIST_URL = "https://www.dlsite.com/{site}/genre/list?locale=en_US"
PRODUCT_JSON_URL = (
    "https://www.dlsite.com/{site}/api/=/product.json?workno={product_id}&locale=en_US"
)
WORK_URL = "https://www.dlsite.com/{site}/work/=/product_id/{product_id}.html"
# Incremental runs stop at the first page with nothing new; this caps a runaway.
MAX_INCREMENTAL_PAGES = 30

_ITEM_SPLIT_RE = re.compile(r'<tr\s+data-list_item_product_id="(\w+)"')
# The English /maniax/ listing uses <li> rows; the English /pro/ listing keeps <tr> rows.
_EN_ITEM_SPLIT_RE = re.compile(r'<(?:li|tr)\s+data-list_item_product_id="(\w+)"')
_MAKER_RE = re.compile(
    r'<dd class="maker_name">.*?maker_id/(\w+)\.html">([^<]*)</a>', re.S
)
_TAGS_BLOCK_RE = re.compile(r'<dd class="search_tag">(.*?)</dd>', re.S)
_TAG_LINK_RE = re.compile(r'<a\b[^>]*href="([^"]*)"[^>]*>([^<]+)</a>', re.S)
_GENRE_ID_RE = re.compile(r"/genre/(\d+)(?:/|$|\?)")
_GENRE_LIST_LINK_RE = re.compile(
    r'<a\b[^>]*href="[^"]*/genre/(\d+)(?:/[^"]*)?"[^>]*>(.*?)</a>', re.S
)
_SPAN_RE = re.compile(r"<span\b[^>]*>.*?</span>", re.S)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Kana and CJK ideographs: the English genre list shows some newer genres
# with their Japanese name, which product.json does translate.
_JAPANESE_RE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")


def _normalize_genre_id(raw: object) -> str:
    text = str(raw or "").strip()
    if not text or not text.isdigit():
        return ""
    return str(int(text)).zfill(3)


def _english_or_none(english: object, japanese: object) -> str | None:
    en = str(english or "").strip()
    jp = str(japanese or "").strip()
    return en if en and en != jp else None


def parse_listing(payload: object) -> tuple[list[dict[str, object]], int]:
    """Return the listed works (id, circle, genres, genre_ids) and total work count."""
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
    for product_id, chunk in zip(parts[1::2], parts[2::2], strict=True):
        maker = _MAKER_RE.search(chunk)
        tags_block = _TAGS_BLOCK_RE.search(chunk)
        genres: list[str] = []
        genre_ids: list[str] = []
        if tags_block:
            for href, raw_tag in cast(
                list[tuple[str, str]], _TAG_LINK_RE.findall(tags_block.group(1))
            ):
                name = unescape(raw_tag).strip()
                if not name:
                    continue
                genres.append(name)
                gid_match = _GENRE_ID_RE.search(href)
                genre_ids.append(
                    _normalize_genre_id(gid_match.group(1)) if gid_match else ""
                )
        items.append(
            {
                "product_id": product_id,
                "circle_id": maker.group(1) if maker else None,
                "circle": unescape(maker.group(2)).strip() if maker else None,
                "genres": genres,
                "genre_ids": genre_ids,
            }
        )
    return items, int(count) if isinstance(count, int) else 0


def parse_english_listing(payload: object) -> dict[str, str]:
    """Return product_id -> English circle name from an English listing page."""
    if not isinstance(payload, dict):
        raise ValueError("DLsite English listing is not an object")
    html = cast(dict[str, object], payload).get("search_result")
    if not isinstance(html, str):
        raise ValueError("DLsite English listing is missing search_result")
    parts = _EN_ITEM_SPLIT_RE.split(html)
    circles: dict[str, str] = {}
    for product_id, chunk in zip(parts[1::2], parts[2::2], strict=True):
        maker = _MAKER_RE.search(chunk)
        circles[product_id] = unescape(maker.group(2)).strip() if maker else ""
    return circles


def parse_genre_list(html: str) -> dict[str, str]:
    """Return genre_id -> English name from a DLsite genre/list page.

    Names still in Japanese are left out, so the genre stays unknown and is
    looked up through product.json.
    """
    genres: dict[str, str] = {}
    for raw_id, inner in cast(list[tuple[str, str]], _GENRE_LIST_LINK_RE.findall(html)):
        genre_id = _normalize_genre_id(raw_id)
        cleaned = _HTML_TAG_RE.sub("", _SPAN_RE.sub("", inner))
        name = unescape(cleaned).strip()
        if genre_id and name and not _JAPANESE_RE.search(name):
            genres[genre_id] = name
    return genres


def parse_product_genres(payload: object) -> dict[str, str]:
    """Return genre_id -> English site name from product.json `genres_replaced`."""
    if isinstance(payload, list):
        items = cast(list[object], payload)
        work: object = items[0] if items else None
    else:
        work = payload
    if not isinstance(work, dict):
        return {}
    raw_genres = cast(dict[str, object], work).get("genres_replaced")
    if not isinstance(raw_genres, list):
        return {}
    genres: dict[str, str] = {}
    for entry in cast(list[object], raw_genres):
        if not isinstance(entry, dict):
            continue
        entry_dict = cast(dict[str, object], entry)
        genre_id = _normalize_genre_id(
            entry_dict.get("search_val") or entry_dict.get("id")
        )
        name = unescape(str(entry_dict.get("name") or "")).strip()
        if genre_id and name:
            genres[genre_id] = name
    return genres


def merge_details(
    site: str,
    listed: list[dict[str, object]],
    payload: object,
    *,
    en_payload: object = None,
    en_circles: Mapping[str, str] | None = None,
    genre_names: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    """Combine listing items with their details; returns (adult rows, dropped IDs)."""
    details = cast(dict[str, object], payload) if isinstance(payload, dict) else {}
    en_details = (
        cast(dict[str, object], en_payload) if isinstance(en_payload, dict) else None
    )
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
        title = str(info.get("work_name") or "")
        jp_genres = cast(list[str], item.get("genres") or [])
        row: dict[str, object] = {
            "product_id": product_id,
            "site": str(info.get("site_id") or site),
            "title": title,
            "circle_id": item["circle_id"] or info.get("maker_id"),
            "circle": item["circle"],
            "work_type": info.get("work_type"),
            "age_category": ADULT_AGE_CATEGORY,
            "genres": jp_genres,
            "released_at": info.get("regist_date"),
            "cover_url": f"https:{image}" if image.startswith("//") else image or None,
            "url": WORK_URL.format(site=site, product_id=product_id),
        }
        if en_details is not None:
            en_raw = en_details.get(product_id)
            if isinstance(en_raw, dict):
                row["title_en"] = _english_or_none(
                    cast(dict[str, object], en_raw).get("work_name"), title
                )
        if en_circles is not None and product_id in en_circles:
            row["circle_en"] = _english_or_none(en_circles[product_id], item["circle"])
        if genre_names is not None:
            genre_ids = cast(list[str], item.get("genre_ids") or [])
            row["genres_en"] = [
                genre_names.get(gid) or jp_name
                for jp_name, gid in zip(
                    jp_genres,
                    genre_ids
                    if len(genre_ids) == len(jp_genres)
                    else [""] * len(jp_genres),
                    strict=True,
                )
            ]
        rows.append(row)
    return rows, dropped


class DlsiteSource:
    name: str = "dlsite"

    def _refresh_genre_lists(self, ctx: SourceContext) -> None:
        """Read both English genre list pages into `state.dlsite_genres`."""
        merged: dict[str, str] = {}
        for site, _filter in LISTINGS:
            try:
                response = ctx.pacer.request(
                    self.name, GENRE_LIST_URL.format(site=site)
                )
                merged.update(parse_genre_list(response.text()))
            except (ConnectionError, HttpStatusError, ValueError) as exc:
                _log.warning("DLsite %s genre list failed: %s", site, exc)
        if merged:
            ctx.state.upsert_dlsite_genres(merged, ctx.now())

    def _resolve_genre_names(
        self,
        ctx: SourceContext,
        site: str,
        adult_items: list[dict[str, object]],
        *,
        refresh_lists: bool,
    ) -> dict[str, str]:
        if refresh_lists or not ctx.state.has_dlsite_genres():
            self._refresh_genre_lists(ctx)
        sample_work_by_gid: dict[str, str] = {}
        jp_name_by_gid: dict[str, str] = {}
        for item in adult_items:
            product_id = str(item["product_id"])
            genre_ids = cast(list[str], item.get("genre_ids") or [])
            jp_names = cast(list[str], item.get("genres") or [])
            for index, gid in enumerate(genre_ids):
                if gid and gid not in sample_work_by_gid:
                    sample_work_by_gid[gid] = product_id
                    if index < len(jp_names):
                        jp_name_by_gid[gid] = jp_names[index]
        if not sample_work_by_gid:
            return {}
        unknown = ctx.state.unknown_dlsite_genres(sample_work_by_gid)
        remaining = set(unknown)
        for gid in unknown:
            if gid not in remaining:
                continue
            workno = sample_work_by_gid[gid]
            try:
                response = ctx.pacer.request(
                    self.name,
                    PRODUCT_JSON_URL.format(site=site, product_id=workno),
                )
                fetched = parse_product_genres(response.json())
            except (ConnectionError, HttpStatusError, ValueError) as exc:
                _log.warning(
                    "DLsite product.json failed for %s (genre %s): %s",
                    workno,
                    gid,
                    exc,
                )
                remaining.discard(gid)
                continue
            if gid not in fetched and gid in jp_name_by_gid:
                # The English site has no name for this genre; keep the
                # Japanese one so the ID is not asked for again.
                fetched[gid] = jp_name_by_gid[gid]
            if fetched:
                ctx.state.upsert_dlsite_genres(fetched, ctx.now())
                remaining.difference_update(fetched)
            remaining.discard(gid)
        return ctx.state.dlsite_genre_names(sample_work_by_gid)

    def _page(
        self,
        ctx: SourceContext,
        listing: int,
        page: int,
        *,
        only_new: bool,
        refresh_genres: bool = False,
    ) -> tuple[int, int]:
        """Fetch one listing page and its details; returns (new IDs, total works)."""
        site, filter_path = LISTINGS[listing]
        response = ctx.pacer.request(
            self.name,
            LISTING_URL.format(
                site=site, filter=filter_path, order="release_d", page=page
            ),
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
            en_response = ctx.pacer.request(
                self.name,
                LISTING_EN_URL.format(
                    site=site, filter=filter_path, order="release_d", page=page
                ),
            )
            en_circles = parse_english_listing(en_response.json())
            if not en_circles:
                raise ValueError(
                    f"DLsite returned an empty English listing page {page}"
                )
            item_ids = ",".join(str(item["product_id"]) for item in items)
            info = ctx.pacer.request(
                self.name,
                INFO_URL.format(site=site, ids=item_ids),
            )
            jp_payload = info.json()
            jp_rows, dropped = merge_details(site, items, jp_payload)
            if not jp_rows and not dropped and stored and not only_new:
                # An empty details payload is a failed request, not a page of
                # works to drop. Advancing would hide the stored works from
                # the sweep and let the removal marking delete them. New works
                # without details are simply retried, so they never stall the
                # source.
                raise ValueError(
                    f"DLsite returned no details for {len(items)} listed works"
                )
            rows: list[dict[str, object]] = []
            if jp_rows:
                en_info = ctx.pacer.request(
                    self.name,
                    INFO_EN_URL.format(site=site, ids=item_ids),
                )
                en_payload = en_info.json()
                if not isinstance(en_payload, dict) or not en_payload:
                    raise ValueError(
                        f"DLsite returned no English details for {len(jp_rows)} listed works"
                    )
                en_details = cast(dict[str, object], en_payload)
                adult_ids = {str(row["product_id"]) for row in jp_rows}
                adult_items = [
                    item for item in items if str(item["product_id"]) in adult_ids
                ]
                genre_names = self._resolve_genre_names(
                    ctx, site, adult_items, refresh_lists=refresh_genres
                )
                rows, dropped = merge_details(
                    site,
                    items,
                    jp_payload,
                    en_payload=en_details,
                    en_circles=en_circles,
                    genre_names=genre_names,
                )
            by_columns: dict[tuple[str, ...], list[dict[str, object]]] = {}
            for row in rows:
                by_columns.setdefault(tuple(row), []).append(row)
            for group in by_columns.values():
                ctx.upsert("dlsite_works", "product_id", group)
            ctx.state.add_skipped_dlsite(dropped)
            # A stored work the details omit is still listed. Keep it seen so
            # the removal marking does not retire it; an all-ages work is in
            # `dropped` and is not kept.
            answered = {str(row["product_id"]) for row in rows} | set(dropped)
            ctx.catalog.touch(
                "dlsite_works", "product_id", stored - answered, ctx.now()
            )
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
        _, total = self._page(
            ctx,
            listing,
            page,
            only_new=False,
            refresh_genres=(listing == 0 and page == 1),
        )
        if page * PER_PAGE < total:
            ctx.state.put(self.name, "sweep_cursor", [listing, page + 1])
            return False
        if listing + 1 < len(LISTINGS):
            ctx.state.put(self.name, "sweep_cursor", [listing + 1, 1])
            return False
        return True

    def reset_sweep(self, ctx: SourceContext) -> None:
        ctx.state.put(self.name, "sweep_cursor", None)
