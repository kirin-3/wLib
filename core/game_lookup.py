# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve exact page identities using the installed, read-only catalog."""

import json
import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import TypedDict, cast
from urllib.parse import urlsplit, urlunsplit

from core import database
from core.catalog import CATALOG_FILE
from core.catalog_format import META_BUILT_AT
from core.store_links import StoreLink, link_from_id, parse_store_link


class PageResolution(TypedDict):
    ids: list[StoreLink]
    catalog_entry: dict[str, object]
    matches: list[int]


def _strings(value: object) -> list[str]:
    try:
        items = cast(object, json.loads(str(value or "[]")))
    except ValueError:
        return []
    return (
        [item for item in cast(list[object], items) if isinstance(item, str)]
        if isinstance(items, list)
        else []
    )


def _version(value: object) -> str:
    # Some Ryuugames titles end in "(VJ01002857)", which the catalog builder
    # stores as a "V…" version. A DLsite product code is never a version.
    text = str(value or "").strip()
    return "" if re.fullmatch(r"(?:RJ|VJ|BJ)\d+", text, re.I) else text


def _catalog_page(
    url: str, page: StoreLink | None, supplied: list[StoreLink]
) -> tuple[dict[str, object], list[StoreLink]]:
    if database.get_setting("catalog_enabled") == "0":
        return {}, []
    path = Path(database.DB_PATH).parent / CATALOG_FILE
    entry: dict[str, object] = {}
    extra: list[StoreLink] = []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            # WordPress permalinks do not contain the post ID. Resolve them by
            # their exact stored URL, or use the ID the page read from its DOM.
            if page is None and "ryuugames.com" in url:
                row = cast(
                    sqlite3.Row | None,
                    conn.execute(
                        "SELECT post_id FROM ryuu_posts WHERE rtrim(url, '/') = ? AND removed_at IS NULL",
                        (url.split("#")[0].split("?")[0].rstrip("/"),),
                    ).fetchone(),
                )
                if row is not None:
                    page = link_from_id("ryuugames", str(cast(object, row[0])))
                else:
                    page = next(
                        (link for link in supplied if link["source"] == "ryuugames"),
                        None,
                    )
            if page is None:
                return {}, []
            extra.append(page)
            source, identity = page["source"], page["id"]
            tables = {
                "dlsite": ("dlsite_works", "product_id"),
                "vndb": ("vndb_vns", "vn_id"),
                "ryuugames": ("ryuu_posts", "post_id"),
            }
            # Removed catalog rows keep their identity for a live page, but
            # their stale links must not resolve games any more.
            expand_links = True
            if source in tables:
                table, key = tables[source]
                row = cast(
                    sqlite3.Row | None,
                    conn.execute(
                        f"SELECT * FROM {table} WHERE {key} = ? AND removed_at IS NULL",
                        (identity,),
                    ).fetchone(),
                )
                expand_links = row is not None
                if row is not None:
                    data = {str(key): cast(object, row[key]) for key in row.keys()}
                    entry = {
                        "title": data.get("title") or "",
                        "developer": data.get("circle")
                        or data.get("developer")
                        or ", ".join(_strings(data.get("developers"))),
                        "coverImage": data.get("cover_url") or "",
                        "tags": ", ".join(
                            _strings(data.get("genres") or data.get("tags"))
                        ),
                        "version": _version(data.get("version")),
                    }
                    if source == "ryuugames":
                        for field, target in (
                            ("rj_codes", "dlsite"),
                            ("steam_ids", "steam"),
                            ("vndb_ids", "vndb"),
                        ):
                            for value in _strings(data.get(field)):
                                if link := link_from_id(target, value):
                                    extra.append(link)
            # Only expand the page's own ID. IDs discovered here are never
            # recursively expanded, even when several releases share a VN.
            if expand_links:
                rows = cast(
                    list[tuple[str, str]],
                    conn.execute(
                        "SELECT src_b, id_b FROM links WHERE src_a = ? AND id_a = ? UNION SELECT src_a, id_a FROM links WHERE src_b = ? AND id_b = ?",
                        (source, identity, source, identity),
                    ).fetchall(),
                )
                for target, value in rows:
                    if link := link_from_id(target, value):
                        extra.append(link)
    except (sqlite3.Error, OSError, ValueError):
        return {}, []
    return entry, extra


def resolve_page(url: str, ids: Sequence[str] = ()) -> PageResolution:
    supplied: list[StoreLink] = []
    for value in ids:
        source, separator, identity = value.partition(":")
        if separator and (link := link_from_id(source, identity)):
            supplied.append(link)
    page = parse_store_link(url)
    if page is not None:
        supplied.insert(0, page)
    entry, expanded = _catalog_page(url, page, supplied)
    unique = {(link["source"], link["id"]): link for link in supplied + expanded}
    links = list(unique.values())
    f95_game = database.find_game_by_f95_url(url)
    matches = (
        [int(str(f95_game["id"]))] if f95_game else database.find_games_by_links(links)
    )
    return {"ids": links, "catalog_entry": entry, "matches": matches}


def f95_thread_cover(thread_id: str) -> str:
    """The catalog's cover for an F95 thread, or "".

    F95 hides every starter-post attachment, the cover included, from guests,
    so a scrape without the signed-in extension has no cover of its own.
    """
    if not thread_id.isdigit():
        return ""
    path = Path(database.DB_PATH).parent / CATALOG_FILE
    try:
        if database.get_setting("catalog_enabled") == "0":
            return ""
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            row = cast(
                tuple[object] | None,
                conn.execute(
                    "SELECT cover_url FROM f95_threads WHERE thread_id = ?",
                    (int(thread_id),),
                ).fetchone(),
            )
    except (sqlite3.Error, OSError, ValueError):
        return ""
    cover = str(row[0] or "").strip() if row else ""
    return cover if cover.startswith("https://") else ""


def f95_thread_screenshots(thread_id: str) -> list[str] | None:
    """Return the catalog list, or None when no readable catalog row exists."""
    if not thread_id.isdigit():
        return None
    path = Path(database.DB_PATH).parent / CATALOG_FILE
    try:
        if database.get_setting("catalog_enabled") == "0":
            return None
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            row = cast(tuple[object] | None, conn.execute(
                "SELECT screens FROM f95_threads WHERE thread_id = ?", (int(thread_id),)
            ).fetchone())
    except (sqlite3.Error, OSError, ValueError):
        return None
    if row is None:
        return None
    return [url for url in _strings(row[0]) if url.startswith("https://")]


def f95_thread_ratings(thread_ids: Sequence[int]) -> tuple[str, dict[int, float]]:
    """The catalog's ``built_at`` and its F95 rating for each known thread.

    Reads the catalog read-only, in chunks of 500 IDs. Returns ``("", {})``
    when the catalog is off, missing or unreadable. Rows with no rating are
    left out; the caller decides which values to use.
    """
    ids = sorted({int(thread_id) for thread_id in thread_ids})
    path = Path(database.DB_PATH).parent / CATALOG_FILE
    ratings: dict[int, float] = {}
    try:
        if database.get_setting("catalog_enabled") == "0" or not path.is_file():
            return "", {}
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            row = cast(
                tuple[object] | None,
                conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (META_BUILT_AT,)
                ).fetchone(),
            )
            built_at = str(row[0] or "").strip() if row else ""
            for offset in range(0, len(ids), 500):
                chunk = ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = cast(
                    list[tuple[object, object]],
                    conn.execute(
                        f"SELECT thread_id, rating FROM f95_threads WHERE thread_id IN ({placeholders}) AND rating IS NOT NULL",
                        chunk,
                    ).fetchall(),
                )
                for thread_id, rating in rows:
                    if isinstance(thread_id, int) and isinstance(rating, int | float):
                        ratings[thread_id] = float(rating)
    except (sqlite3.Error, OSError, ValueError):
        return "", {}
    return built_at, ratings


def screenshot_urls(url: str) -> dict[str, str]:
    """Map F95 media hosts to preview and full-size versions."""
    try:
        parts = urlsplit(url)
        if parts.netloc in ("preview.f95zone.to", "attachments.f95zone.to"):
            return {host: urlunsplit(parts._replace(netloc=domain)) for host, domain in (
                ("thumb", "preview.f95zone.to"), ("full", "attachments.f95zone.to")
            )}
    except ValueError:
        pass
    return {"thumb": url, "full": url}
