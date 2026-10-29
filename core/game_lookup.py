# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve exact page identities using the installed, read-only catalog."""

import json
import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import TypedDict, cast

from core import database
from core.catalog import CATALOG_FILE
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
    if database.get_setting("catalog_enabled") == "0" or not thread_id.isdigit():
        return ""
    path = Path(database.DB_PATH).parent / CATALOG_FILE
    try:
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
