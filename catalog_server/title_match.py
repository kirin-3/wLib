# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict title matching: LewdCorner↔F95 threads, and F95 original titles↔DLsite works (`match_f95_dlsite`).

A pair is linked only when it is certain: both threads are current, their
normalized title appears exactly once among current LC threads and exactly
once among current F95 threads, and either their normalized developers are
equal or their first posts share a developer ID (Patreon, SubscribeStar,
itch, Ko-fi or Steam). Discord invites are not stable IDs, so they never
count. A missed match is accepted; a wrong one is not.

LC rows imported from Atlas (`origin = 'import'`) carry Atlas's merged title
and creator, often copied from F95, so matching them would publish Atlas's
own links. They still make a title non-unique but are never linked; once the
feed lists such a thread, LC's own values replace Atlas's and it can match.
For the same reason, developer IDs in details imported from Atlas never count.
Details kept after a failed re-read also supply no matching evidence.
"""

import json
import re
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Iterable
from typing import cast

from .store import CatalogStore

# Developer ID kinds that identify a developer across sites.
MATCH_ID_KINDS = frozenset({"patreon", "subscribestar", "itch", "kofi", "steam"})

_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
# Folded to their ASCII form, so "Summoner’s" and "Summoner's" stay the same.
_ASCII_PUNCTUATION = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"}
)


def norm(text: str | None) -> str:
    """NFKD, ASCII fold, casefold, `&` as "and", other runs of symbols as one space."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text).translate(_ASCII_PUNCTUATION)
    folded = folded.encode("ascii", "ignore").decode("ascii")
    folded = folded.casefold().replace("&", " and ")
    return _NON_ALNUM_RE.sub(" ", folded).strip()


def devkey(creator: str | None) -> str:
    return norm(creator).replace(" ", "")


def _ids(raw: object) -> set[tuple[str, str]]:
    if not isinstance(raw, str):
        return set()
    try:
        data = cast(object, json.loads(raw))
    except ValueError:
        return set()
    if not isinstance(data, dict):
        return set()
    found: set[tuple[str, str]] = set()
    for kind, entries in cast(dict[str, object], data).items():
        if kind not in MATCH_ID_KINDS or not isinstance(entries, list):
            continue
        for entry in cast(list[object], entries):
            if isinstance(entry, dict):
                value = cast(dict[str, object], entry).get("id")
                if isinstance(value, str) and value:
                    found.add((kind, value.casefold()))
    return found


# (thread id, developer key, developer IDs, imported from Atlas)
_Row = tuple[int, str, set[tuple[str, str]], bool]


def _by_title(rows: Iterable[tuple[object, ...]]) -> dict[str, list[_Row]]:
    found: dict[str, list[_Row]] = {}
    for thread_id, title, creator, raw_ids, origin in rows:
        key = norm(cast(str | None, title))
        if key:
            found.setdefault(key, []).append(
                (
                    cast(int, thread_id),
                    devkey(cast(str | None, creator)),
                    _ids(raw_ids),
                    origin is not None,
                )
            )
    return found


def _kept_details_filter(conn: sqlite3.Connection, table: str) -> str:
    # The comparison tool also opens older snapshots read-only.
    has_flag = (
        conn.execute(
            "SELECT 1 FROM pragma_table_info(?) WHERE name = 'kept_after_failure'",
            (table,),
        ).fetchone()
        is not None
    )
    return " AND d.kept_after_failure = 0" if has_flag else ""


def match_lc_f95(catalog: CatalogStore | sqlite3.Connection) -> list[tuple[int, int]]:
    """`(lc thread id, f95 thread id)` pairs that pass the strict match."""
    conn = catalog if isinstance(catalog, sqlite3.Connection) else catalog.conn
    lc = _by_title(
        conn.execute(
            "SELECT t.thread_id, t.title, t.creator, d.external_ids, t.origin FROM lc_threads t "
            # Developer IDs imported from Atlas are not LC's own; never match on them.
            + "LEFT JOIN lc_thread_details d ON d.thread_id = t.thread_id AND d.origin IS NULL"
            + _kept_details_filter(conn, "lc_thread_details")
            + " WHERE t.removed_at IS NULL"
        )
    )
    f95 = _by_title(
        conn.execute(
            "SELECT t.thread_id, t.title, t.creator, d.external_ids, NULL FROM f95_threads t "
            + "LEFT JOIN f95_thread_details d ON d.thread_id = t.thread_id"
            + _kept_details_filter(conn, "f95_thread_details")
            + " WHERE t.removed_at IS NULL"
        )
    )
    pairs: list[tuple[int, int]] = []
    for key, lc_rows in lc.items():
        f95_rows = f95.get(key)
        if len(lc_rows) != 1 or f95_rows is None or len(f95_rows) != 1:
            continue
        lc_id, lc_dev, lc_ids, lc_imported = lc_rows[0]
        f95_id, f95_dev, f95_ids, _ = f95_rows[0]
        if lc_imported:
            continue
        if (lc_dev and lc_dev == f95_dev) or lc_ids & f95_ids:
            pairs.append((lc_id, f95_id))
    return sorted(pairs)


# F95 original title ↔ DLsite title. Japanese titles keep their characters;
# only width, case, bracket tags, censor marks and punctuation are evened out.
_BRACKET_TAG_RE = re.compile(r"【[^】]*】|\[[^\]]*\]")
_CENSOR_MARKS = str.maketrans(dict.fromkeys("○〇●◯＊*", "\0"))
# Spaces, punctuation and `_`; the wildcard stays.
_NON_WORD_RE = re.compile(r"[^\w\x00]+|_+")
# A key shorter than this ("Mia", "ミク") is too common to be certain.
MIN_ORIGINAL_TITLE_KEY = 4
_WILDCARD = "\0"


def original_title_key(text: str | None) -> str:
    """NFKC, casefold, bracket tags dropped, censor marks as a wildcard
    (`\\0`), spaces and punctuation dropped."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _BRACKET_TAG_RE.sub("", folded).translate(_CENSOR_MARKS)
    return _NON_WORD_RE.sub("", folded)


def _keys_equal(a: str, b: str) -> bool:
    return len(a) == len(b) and all(
        x == y or _WILDCARD in (x, y) for x, y in zip(a, b, strict=True)
    )


def match_f95_dlsite(
    catalog: CatalogStore | sqlite3.Connection,
) -> list[tuple[int, str]]:
    """`(f95 thread id, DLsite product id)` pairs whose first-post original
    title equals the work's title. The title must be unique among current F95
    threads and name exactly one current DLsite work outside the smartphone
    (`appx`) site."""
    conn = catalog if isinstance(catalog, sqlite3.Connection) else catalog.conn
    f95: dict[str, list[int]] = {}
    for thread_id, title in cast(
        list[tuple[int, str | None]],
        conn.execute(
            "SELECT t.thread_id, d.original_title FROM f95_threads t "
            + "JOIN f95_thread_details d ON d.thread_id = t.thread_id AND d.status = 'ok'"
            + _kept_details_filter(conn, "f95_thread_details")
            + " WHERE t.removed_at IS NULL AND d.original_title IS NOT NULL",
        ).fetchall(),
    ):
        key = original_title_key(title)
        if len(key) >= MIN_ORIGINAL_TITLE_KEY:
            f95.setdefault(key, []).append(thread_id)
    works: dict[str, list[str]] = {}
    by_length: dict[int, list[str]] = {}
    for product_id, title in cast(
        list[tuple[str, str | None]],
        conn.execute(
            "SELECT product_id, title FROM dlsite_works "
            + "WHERE removed_at IS NULL AND COALESCE(site, '') != 'appx'"
        ).fetchall(),
    ):
        key = original_title_key(title)
        if len(key) < MIN_ORIGINAL_TITLE_KEY:
            continue
        if key not in works:
            by_length.setdefault(len(key), []).append(key)
        works.setdefault(key, []).append(product_id)
    pairs: list[tuple[int, str]] = []
    for key, thread_ids in f95.items():
        if len(thread_ids) != 1:
            continue
        if _WILDCARD not in key and key in works:
            candidates = works[key]
            # A censored DLsite title of the same length can match too.
            candidates = candidates + [
                product
                for other in by_length.get(len(key), [])
                if _WILDCARD in other and _keys_equal(key, other)
                for product in works[other]
            ]
        else:
            candidates = [
                product
                for other in by_length.get(len(key), [])
                if _keys_equal(key, other)
                for product in works[other]
            ]
        if len(candidates) == 1:
            pairs.append((thread_ids[0], candidates[0]))
    # Keys are unique only as exact text; with the wildcard, two F95 titles
    # ("abcd", "abxd") can both name one censored work ("ab○d"). A work that
    # more than one thread names is not certain.
    named = Counter(product for _thread, product in pairs)
    return sorted(pair for pair in pairs if named[pair[1]] == 1)
