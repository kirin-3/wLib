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

from core import database, f95zone, lewdcorner
from core.catalog import CATALOG_FILE
from core.catalog_format import META_BUILT_AT
from core.store_links import StoreLink, link_from_id, parse_store_link


class PageResolution(TypedDict):
    ids: list[StoreLink]
    catalog_entry: dict[str, object]
    matches: list[int]
    attach_lc_available: bool


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


# Normalize link orientation, deduplicate rows, and reject ambiguity at either end.
_LC_PARTNERS_SQL = """
WITH pairs(f95_id, lc_id) AS (
    SELECT id_b, id_a FROM links WHERE src_a = 'lc' AND src_b = 'f95' AND origin = 'title'
    UNION
    SELECT id_a, id_b FROM links WHERE src_a = 'f95' AND src_b = 'lc' AND origin = 'title'
), counted AS (
    SELECT *, count(*) OVER (PARTITION BY f95_id) AS f95_count,
              count(*) OVER (PARTITION BY lc_id) AS lc_count
    FROM pairs
)
SELECT counted.f95_id, counted.lc_id, lc.tier
FROM counted
JOIN f95_threads f95 ON f95.thread_id = counted.f95_id AND f95.removed_at IS NULL
JOIN lc_threads lc ON lc.thread_id = counted.lc_id AND lc.removed_at IS NULL
WHERE f95_count = 1 AND lc_count = 1
"""


def lc_partners() -> list[tuple[str, str, str]]:
    """Read all strict (F95 ID, LC ID, tier) partners in one catalog pass."""
    if database.get_setting("catalog_enabled") == "0":
        return []
    path = Path(database.DB_PATH).parent / CATALOG_FILE
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            return cast(list[tuple[str, str, str]], conn.execute(_LC_PARTNERS_SQL).fetchall())
    except (sqlite3.Error, OSError, ValueError):
        return []


def lc_for_f95(thread_id: str) -> tuple[str, str] | None:
    return next(((lc, tier) for f95, lc, tier in lc_partners() if f95 == thread_id), None)


def f95_for_lc(thread_id: str) -> str | None:
    return next((f95 for f95, lc, _tier in lc_partners() if lc == thread_id), None)


def lookup_lc_partner(url: object) -> dict[str, str]:
    """Prefill only an unclaimed catalog partner."""
    lc_id = lewdcorner.extract_thread_id(url)
    if lc_id:
        f95 = f95_for_lc(lc_id)
        if f95:
            f95_url = f"https://f95zone.to/threads/{f95}/"
            if not database.find_game_by_f95_url(f95_url):
                return {"f95_url": f95_url}
        return {}
    f95_id = f95zone.extract_thread_id(url)
    if f95_id and lewdcorner.setting_enabled(database.get_setting("lc_enabled")):
        partner = lc_for_f95(f95_id)
        if partner:
            lc, tier = partner
            lc_url = f"https://lewdcorner.com/threads/{lc}/"
            if (
                (tier.casefold() != "premium" or lewdcorner.setting_enabled(database.get_setting("lc_plus")))
                and not database.find_game_by_lc_url(lc_url)
            ):
                return {"lc_url": lc_url}
    return {}


def _catalog_thread_entries(
    source: str, thread_ids: Sequence[str]
) -> dict[str, dict[str, object]]:
    ids = sorted({value for value in thread_ids if value.isascii() and value.isdigit()})
    if not ids or database.get_setting("catalog_enabled") == "0":
        return {}
    path = Path(database.DB_PATH).parent / CATALOG_FILE
    results: dict[str, dict[str, object]] = {}
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            tables = {name for (name,) in cast(
                list[tuple[str]], conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            )}
            prefix_table = f"{source}_prefix_names"
            names = dict(cast(
                list[tuple[int, str]], conn.execute(f"SELECT id, name FROM {prefix_table}").fetchall()
            )) if prefix_table in tables else {}
            tag_names = dict(cast(
                list[tuple[int, str]], conn.execute("SELECT id, name FROM f95_tag_names").fetchall()
            )) if source == "f95" and "f95_tag_names" in tables else {}
            detail_table = f"{source}_thread_details"
            details = detail_table in tables
            join = f"LEFT JOIN {detail_table} d ON d.thread_id = t.thread_id AND d.status = 'ok'" if details else ""
            fields = ", d.overview, d.language, d.external_ids" if details else ""
            if details and source == "lc":
                fields += ", d.developer"
            for offset in range(0, len(ids), 500):
                chunk = ids[offset:offset + 500]
                marks = ",".join("?" for _ in chunk)
                rows = cast(list[sqlite3.Row], conn.execute(
                    f"SELECT t.*{fields} FROM {source}_threads t {join} "
                    + f"WHERE t.removed_at IS NULL AND t.thread_id IN ({marks})", chunk
                ).fetchall())
                for row in rows:
                    data = {str(key): cast(object, row[key]) for key in row.keys()}
                    prefixes = _named_values(data.get("prefixes"), names)
                    if source == "lc":
                        engine, completed = lewdcorner.prefix_details(prefixes)
                    else:
                        labels = [f95zone.normalize_prefix_label(prefix) for prefix in prefixes]
                        engine = next((prefix for prefix, label in zip(prefixes, labels, strict=True)
                                       if label not in f95zone.NON_ENGINE_PREFIXES), "")
                        completed = "completed" in labels
                    rating = data.get("rating")
                    entry = {
                        "title": data.get("title") or "",
                        "developer": data.get("developer") or data.get("creator") or "",
                        "version": data.get("version") or "",
                        "ts": data.get("ts"),
                        "coverImage": data.get("cover_url") or "",
                        "tags": ", ".join(_named_values(data.get("tags"), tag_names)),
                        "rating": rating if isinstance(rating, (int, float)) and 0 < rating <= 5 else "",
                        "engine": engine,
                        "thread_completed": completed if prefixes else None,
                        "overview": data.get("overview") or "",
                        "language": data.get("language") or "",
                        "external_links": _detail_links(data.get("external_ids")),
                        "links": _detail_store_links(data.get("external_ids")),
                    }
                    if source == "lc":
                        entry.update(tier=data.get("tier"), origin=data.get("origin"))
                    results[str(data["thread_id"])] = entry
    except (sqlite3.Error, OSError, ValueError):
        return {}
    return results


def _named_values(raw: object, names: dict[int, str]) -> list[str]:
    try:
        items = cast(object, json.loads(str(raw or "[]")))
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    values: list[str] = []
    for item in cast(list[object], items):
        if isinstance(item, int) or (isinstance(item, str) and item.isascii() and item.isdigit()):
            name = names.get(int(item), "")
        else:
            name = item if isinstance(item, str) else ""
        if name:
            values.append(name)
    return values


def lc_thread_entries(thread_ids: Sequence[str]) -> dict[str, dict[str, object]]:
    return _catalog_thread_entries("lc", thread_ids)


def f95_thread_entries(thread_ids: Sequence[str]) -> dict[str, dict[str, object]]:
    return _catalog_thread_entries("f95", thread_ids)


def combined_thread_entry(f95: dict[str, object], lc: dict[str, object]) -> dict[str, object]:
    entry = {**lc, **{key: value for key, value in f95.items() if value}}
    if f95.get("thread_completed") is not None:
        entry["thread_completed"] = f95["thread_completed"]
    return entry


def _detail_store_links(raw: object) -> list[StoreLink]:
    links = [link for url in _detail_links(raw) if (link := parse_store_link(url))]
    try:
        ids = cast(object, json.loads(str(raw or "{}")))
    except ValueError:
        return links
    if isinstance(ids, dict):
        for source, entries in cast(dict[str, object], ids).items():
            if not isinstance(entries, list):
                continue
            for entry in cast(list[object], entries):
                identity = cast(dict[str, object], entry).get("id") if isinstance(entry, dict) else entry
                if identity and (link := link_from_id(source, str(identity))):
                    if not any((link["source"], link["id"]) == (old["source"], old["id"]) for old in links):
                        links.append(link)
    return links


def resolve_page(url: str, ids: Sequence[str] = ()) -> PageResolution:
    supplied: list[StoreLink] = []
    for value in ids:
        source, separator, identity = value.partition(":")
        if separator and (link := link_from_id(source, identity)):
            supplied.append(link)
    lc_id = lewdcorner.extract_thread_id(url)
    attach_lc = False
    if lc_id:
        partner = f95_for_lc(lc_id)
        entry = lc_thread_entries([lc_id]).get(lc_id, {})
        expanded = cast(list[StoreLink], entry.get("links", []))
        thread_game = database.find_game_by_lc_url(url)
        if thread_game is None and partner:
            thread_game = database.find_game_by_f95_url(f"https://f95zone.to/threads/{partner}/")
            if thread_game:
                matched = database.get_game(int(str(thread_game["id"])))
                attach_lc = matched is not None and not matched.get("lc_url")
    else:
        page = parse_store_link(url)
        if page is not None:
            supplied.insert(0, page)
        entry, expanded = _catalog_page(url, page, supplied)
        thread_game = database.find_game_by_f95_url(url)

    unique = {(link["source"], link["id"]): link for link in supplied + expanded}
    links = list(unique.values())
    matches = (
        [int(str(thread_game["id"]))] if thread_game else database.find_games_by_links(links)
    )
    return {"ids": links, "catalog_entry": entry, "matches": matches, "attach_lc_available": attach_lc}


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


# Order of the developer links a game keeps.
_LINK_KINDS = ("patreon", "subscribestar", "itch", "kofi", "steam", "discord")


def _detail_links(raw: object) -> list[str]:
    try:
        ids = cast(object, json.loads(str(raw or "{}")))
    except ValueError:
        return []
    if not isinstance(ids, dict):
        return []
    by_kind = cast(dict[str, object], ids)
    urls: list[str] = []
    for kind in (*_LINK_KINDS, *sorted(set(by_kind) - set(_LINK_KINDS))):
        entries = by_kind.get(kind)
        if not isinstance(entries, list):
            continue
        for entry in cast(list[object], entries):
            url = cast(dict[str, object], entry).get("url") if isinstance(entry, dict) else None
            if isinstance(url, str) and url not in urls:
                urls.append(url)
    return urls


def f95_thread_details(thread_ids: Sequence[int]) -> dict[int, dict[str, object]]:
    """First-post details per F95 thread, in the games-table field names.

    Only threads the catalog read successfully are included. Returns {} when
    the catalog is off, missing, unreadable or older than the details table.
    """
    ids = sorted({int(thread_id) for thread_id in thread_ids})
    path = Path(database.DB_PATH).parent / CATALOG_FILE
    details: dict[int, dict[str, object]] = {}
    try:
        if database.get_setting("catalog_enabled") == "0" or not path.is_file():
            return {}
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            has_table = cast(
                tuple[int] | None,
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'f95_thread_details'"
                ).fetchone(),
            )
            if has_table is None:
                return {}
            for offset in range(0, len(ids), 500):
                chunk = ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = cast(
                    list[tuple[object, ...]],
                    conn.execute(
                        "SELECT thread_id, overview, original_title, aliases, language, external_ids "
                        + f"FROM f95_thread_details WHERE status = 'ok' AND thread_id IN ({placeholders})",
                        chunk,
                    ).fetchall(),
                )
                for thread_id, overview, original, aliases, language, external in rows:
                    if not isinstance(thread_id, int):
                        continue
                    details[thread_id] = {
                        "overview": overview or "",
                        "original_title": original or "",
                        "aliases": _strings(aliases),
                        "language": language or "",
                        "external_links": _detail_links(external),
                    }
    except (sqlite3.Error, OSError, ValueError):
        return {}
    return details


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
