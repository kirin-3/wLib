# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict LewdCorner↔F95 title matching.

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
"""

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from typing import cast

from .store import CatalogStore

# Developer ID kinds that identify a developer across sites.
MATCH_ID_KINDS = frozenset({"patreon", "subscribestar", "itch", "kofi", "steam"})

_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
# Folded to their ASCII form, so "Summoner’s" and "Summoner's" stay the same.
_ASCII_PUNCTUATION = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


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


def match_lc_f95(catalog: CatalogStore | sqlite3.Connection) -> list[tuple[int, int]]:
    """`(lc thread id, f95 thread id)` pairs that pass the strict match."""
    conn = catalog if isinstance(catalog, sqlite3.Connection) else catalog.conn
    lc = _by_title(
        conn.execute(
            "SELECT t.thread_id, t.title, t.creator, d.external_ids, t.origin FROM lc_threads t "
            # Developer IDs imported from Atlas are not LC's own; never match on them.
            + "LEFT JOIN lc_thread_details d ON d.thread_id = t.thread_id AND d.origin IS NULL "
            + "WHERE t.removed_at IS NULL"
        )
    )
    f95 = _by_title(
        conn.execute(
            "SELECT t.thread_id, t.title, t.creator, d.external_ids, NULL FROM f95_threads t "
            + "LEFT JOIN f95_thread_details d ON d.thread_id = t.thread_id "
            + "WHERE t.removed_at IS NULL"
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
