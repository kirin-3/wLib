# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalize store URLs into stable identities shared with the extension."""

import re
from collections.abc import Mapping, Sequence
from typing import TypedDict, cast
from urllib.parse import parse_qs, urlsplit, urlunsplit


class StoreLink(TypedDict):
    source: str
    id: str
    url: str


MATCHABLE_SOURCES = frozenset({"dlsite", "steam", "itch", "fanza", "vndb", "ryuugames"})
LINK_ORIGINS = frozenset({"f95_post", "page", "user", "backup"})


def link_from_id(source: str, identity: str) -> StoreLink | None:
    """Build navigable links from exact IDs supplied by pages or the catalog."""
    if source == "itch" and re.fullmatch(r"[a-z0-9_-]+/[^/?#]+", identity):
        creator, game = identity.split("/", 1)
        return parse_store_link(f"https://{creator}.itch.io/{game}")
    templates = {
        "fanza": (r"[a-z_0-9]+", "https://www.dmm.co.jp/dc/doujin/-/detail/=/cid={}/"),
        "dlsite": (
            r"(?:RJ|VJ|BJ)\d+",
            "https://www.dlsite.com/maniax/work/=/product_id/{}.html",
        ),
        "steam": (r"\d+", "https://store.steampowered.com/app/{}/"),
        "vndb": (r"v\d+", "https://vndb.org/{}"),
        "ryuugames": (r"[1-9]\d*", "https://www.ryuugames.com/?p={}"),
    }
    rule = templates.get(source)
    if rule is None or not re.fullmatch(rule[0], identity, re.I):
        return None
    return parse_store_link(rule[1].format(identity))


def parse_store_link(url: str) -> StoreLink | None:
    """Return a URL-derived identity, rejecting non-work pages on known stores.

    Ryuugames numeric shortlinks identify WordPress posts. Slug URLs need a
    page-provided post ID or a catalog URL lookup; a slug alone is not an ID.
    """
    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username or parts.password:
            return None
        host = parts.hostname.lower()
        scheme = parts.scheme.lower()
        port = parts.port
        default_port = (scheme == "http" and port == 80) or (
            scheme == "https" and port == 443
        )
        netloc = host if port is None or default_port else f"{host}:{port}"
    except ValueError:
        return None
    path = parts.path or "/"
    clean_url = urlunsplit((scheme, netloc, path, "", ""))
    source = "other"
    identity = clean_url
    match: re.Match[str] | None
    if host in {"dlsite.com", "www.dlsite.com"}:
        match = re.search(
            r"/product_id/((?:RJ|VJ|BJ)\d+)(?:\.html)?(?:/|$)", path, re.I
        )
        if not match:
            return None
        source, identity = "dlsite", match[1].upper()
    elif host == "store.steampowered.com":
        match = re.match(r"/app/(\d+)(?:/|$)", path)
        if not match:
            return None
        source, identity = "steam", match[1]
    elif host.endswith(".itch.io"):
        creator = host[: -len(".itch.io")]
        match = re.match(r"/([^/]+)(?:/|$)", path)
        if not match or "." in creator:
            return None
        source, identity = "itch", f"{creator}/{match[1]}"
    elif any(
        host == domain or host.endswith("." + domain)
        for domain in ("dmm.co.jp", "dmm.com", "fanza.com")
    ):
        match = re.search(
            r"(?:^|[/?&])cid=([a-z_0-9]+)(?:/|&|$)",
            path + ("?" + parts.query if parts.query else ""),
            re.I,
        )
        if not match:
            match = re.search(r"/detail/([a-z_0-9]+)(?:/|$)", path, re.I)
        if not match:
            return None
        source, identity = "fanza", match[1].lower()
        # DMM query-form product links need their cid to remain navigable.
        if "cid" in parse_qs(parts.query):
            clean_url = urlunsplit(
                (parts.scheme.lower(), netloc, path, f"cid={identity}", "")
            )
    elif host in {"vndb.org", "www.vndb.org"}:
        match = re.fullmatch(r"/(v\d+)/?", path, re.I)
        if not match:
            return None
        source, identity = "vndb", match[1].lower()
    elif host in {"ryuugames.com", "www.ryuugames.com"}:
        post_id = parse_qs(parts.query).get("p", [""])[0]
        if not re.fullmatch(r"[1-9]\d*", post_id):
            return None
        source, identity = "ryuugames", post_id
        clean_url = f"https://www.ryuugames.com/?p={post_id}"
    elif host in {
        "patreon.com",
        "www.patreon.com",
        "subscribestar.com",
        "www.subscribestar.com",
        "subscribestar.adult",
        "www.subscribestar.adult",
        "gumroad.com",
        "www.gumroad.com",
    }:
        match = re.match(r"/(?:c/)?([^/]+)(?:/|$)", path)
        if not match:
            return None
        source = host.removeprefix("www.").split(".")[0]
        identity = match[1].lower()
    elif host.endswith(".gumroad.com"):
        source, identity = "gumroad", host[: -len(".gumroad.com")]
    elif host in {"f95zone.to", "www.f95zone.to"}:
        return None
    return {"source": source, "id": identity, "url": clean_url}


def normalize_store_links(links: Sequence[Mapping[str, object]]) -> list[StoreLink]:
    """Derive identities from URLs, dropping invalid entries and duplicate IDs."""
    result: dict[tuple[str, str], StoreLink] = {}
    for raw in links:
        url = raw.get("url")
        link = parse_store_link(url) if isinstance(url, str) else None
        if link is not None:
            _ = result.setdefault((link["source"], link["id"]), link)
    return list(result.values())


def coerce_store_links(value: object) -> list[StoreLink]:
    """Accept JSON input without trusting caller-supplied site identities."""
    if not isinstance(value, list):
        return []
    rows = [
        cast(Mapping[str, object], row)
        for row in cast(list[object], value)
        if isinstance(row, dict)
    ]
    return normalize_store_links(rows)
