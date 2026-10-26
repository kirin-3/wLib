# SPDX-License-Identifier: GPL-3.0-or-later
"""Ryuugames through the WordPress REST API.

Post bodies are read only to pull out store IDs and the developer; the body
itself, and with it every download link, is never stored.
"""

import re
from datetime import timedelta
from html import unescape
from typing import cast
from urllib.parse import quote

from ..net import Response
from .base import SourceContext, periodic_sweep_due

# A full re-crawl reads every post body; `modified_after` covers the rest.
SWEEP_INTERVAL = timedelta(days=7)

POSTS_URL = (
    "https://www.ryuugames.com/wp-json/wp/v2/posts?per_page=100&page={page}"
    + "&orderby={orderby}&order={order}"
    + "&_fields=id,date_gmt,modified_gmt,link,title,content,categories"
)
CATEGORY_NAMES: dict[int, str] = {
    903: "English",
    123: "Japanese RAW",
    22377: "MTL",
}
TUTORIAL_CATEGORY = 9041

_RJ_RE = re.compile(r"\b((?:RJ|VJ)\d{6,8})\b")
_STEAM_RE = re.compile(r"store\.steampowered\.com/app/(\d+)")
_VNDB_RE = re.compile(r"vndb\.org/(v\d+)\b")
_DEVELOPER_RE = re.compile(r"Developer\s*:\s*([^\n]+)")
_IMG_RE = re.compile(r'<img [^>]*src="(https://www\.ryuugames\.com/wp-content/uploads/[^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
_VERSION_RE = re.compile(r"\((V[^)]*)\)", re.I)
_LANG_PREFIX_RE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")
_TITLE_NOISE_RE = re.compile(
    r"\((?:V[^)]*|RJ\d+|VJ\d+|Official|Chapter[^)]*)\)|\bUncensored\b|\bFree Download\b",
    re.I,
)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def clean_title(raw: str) -> tuple[str, str | None]:
    """Split a post title into (game title, first version)."""
    version_match = _VERSION_RE.search(raw)
    title = _TITLE_NOISE_RE.sub("", _LANG_PREFIX_RE.sub("", raw))
    title = re.sub(r"\s{2,}", " ", title).strip(" -")
    return title or raw.strip(), version_match.group(1) if version_match else None


def parse_post(post: dict[str, object]) -> dict[str, object] | None:
    post_id = post.get("id")
    if not isinstance(post_id, int):
        return None
    categories = [
        item for item in cast(list[object], post.get("categories") or []) if isinstance(item, int)
    ]
    if TUTORIAL_CATEGORY in categories:
        return None
    title_obj = post.get("title")
    content_obj = post.get("content")
    raw_title = unescape(
        str(cast(dict[str, object], title_obj).get("rendered") or "")
        if isinstance(title_obj, dict)
        else ""
    )
    html = (
        str(cast(dict[str, object], content_obj).get("rendered") or "")
        if isinstance(content_obj, dict)
        else ""
    )
    text = unescape(_TAG_RE.sub("", html.replace("<br />", "\n")))
    developer = _DEVELOPER_RE.search(text)
    cover = _IMG_RE.search(html)
    title, version = clean_title(raw_title)
    return {
        "post_id": post_id,
        "title": title,
        "raw_title": raw_title,
        "developer": developer.group(1).strip() if developer else None,
        "version": version,
        "categories": [CATEGORY_NAMES[item] for item in categories if item in CATEGORY_NAMES],
        "rj_codes": _unique(_RJ_RE.findall(raw_title + "\n" + html)),
        "steam_ids": _unique(_STEAM_RE.findall(html)),
        "vndb_ids": _unique(_VNDB_RE.findall(html)),
        "published_at": _gmt(post.get("date_gmt")),
        "updated_at": _gmt(post.get("modified_gmt")),
        "cover_url": cover.group(1) if cover else None,
        "url": str(post.get("link") or f"https://www.ryuugames.com/?p={post_id}"),
    }


def _gmt(value: object) -> str | None:
    return f"{value}Z" if isinstance(value, str) and value else None


def parse_posts(response: Response) -> tuple[list[dict[str, object]], int]:
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Ryuugames response is not a list")
    rows = [
        row
        for post in cast(list[object], payload)
        if isinstance(post, dict)
        and (row := parse_post(cast(dict[str, object], post))) is not None
    ]
    total = response.header("X-WP-TotalPages") or "0"
    return rows, int(total) if total.isdigit() else 0


class RyuugamesSource:
    name: str = "ryuugames"

    def _fetch(self, ctx: SourceContext, url: str) -> int:
        # WordPress answers 400 for a page past the end.
        response = ctx.pacer.request(self.name, url, ok_statuses=frozenset({400}))
        if response.status == 400:
            return 0
        rows, total = parse_posts(response)
        ctx.upsert("ryuu_posts", "post_id", rows)
        return total

    def incremental(self, ctx: SourceContext) -> None:
        since = ctx.state.get(self.name, "modified_since")
        if not isinstance(since, str):
            return
        started = ctx.now()
        page = 1
        while True:
            url = POSTS_URL.format(page=page, orderby="modified", order="asc")
            url += "&modified_after=" + quote(since)
            total = self._fetch(ctx, url)
            if page >= total:
                break
            page += 1
        ctx.state.put(self.name, "modified_since", started)

    def sweep_due(self, ctx: SourceContext) -> bool:
        return periodic_sweep_due(ctx, self.name, SWEEP_INTERVAL)

    def sweep_step(self, ctx: SourceContext) -> bool:
        page = ctx.state.get(self.name, "sweep_page", 1)
        page = page if isinstance(page, int) and page > 0 else 1
        if page == 1:
            ctx.state.put(self.name, "sweep_modified_since", ctx.now())
        # Newest first: posts added mid-sweep shift entries later, never skip them.
        total = self._fetch(ctx, POSTS_URL.format(page=page, orderby="id", order="desc"))
        if page >= total:
            return True
        ctx.state.put(self.name, "sweep_page", page + 1)
        return False

    def reset_sweep(self, ctx: SourceContext) -> None:
        ctx.state.put(self.name, "sweep_page", None)
        since = ctx.state.get(self.name, "sweep_modified_since")
        if isinstance(since, str) and ctx.state.get(self.name, "modified_since") is None:
            ctx.state.put(self.name, "modified_since", since)
