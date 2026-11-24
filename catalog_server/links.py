# SPDX-License-Identifier: GPL-3.0-or-later
"""Rebuild `links` from links the sources state, then strict title matches
(`origin = 'title'`, see `title_match`), then imported pairs
(`origin = 'import'`). A weaker link is dropped when either of its ends
already has a stronger link to the other end's site. No other title matching."""

from collections.abc import Iterable, Sequence
from typing import cast

from .store import IMPORT_ORIGIN, CatalogStore
from .title_match import match_f95_dlsite, match_lc_f95

TITLE_ORIGIN = "title"
# Catalog tables by link site, with their key column, for imported pairs.
_SOURCE_KEYS = {
    "f95": ("f95_threads", "thread_id"),
    "lc": ("lc_threads", "thread_id"),
    "dlsite": ("dlsite_works", "product_id"),
    "ryuugames": ("ryuu_posts", "post_id"),
    "vndb": ("vndb_vns", "vn_id"),
}
Pair = tuple[str, str, str, str]

# Each statement inserts (src_a, id_a, src_b, id_b, origin). Only current
# rows (`removed_at IS NULL`) state links, and targets that are catalog
# sources must be current catalog rows; Steam is not a source, so its IDs are
# kept as stated.
_LINK_QUERIES = (
    """
    SELECT 'ryuugames', CAST(p.post_id AS TEXT), 'dlsite', j.value, 'ryuugames'
    FROM ryuu_posts p, json_each(p.rj_codes) j
    WHERE p.removed_at IS NULL AND j.value IN (SELECT product_id FROM dlsite_works WHERE removed_at IS NULL)
    """,
    """
    SELECT 'ryuugames', CAST(p.post_id AS TEXT), 'vndb', j.value, 'ryuugames'
    FROM ryuu_posts p, json_each(p.vndb_ids) j
    WHERE p.removed_at IS NULL AND j.value IN (SELECT vn_id FROM vndb_vns WHERE removed_at IS NULL)
    """,
    """
    SELECT 'ryuugames', CAST(p.post_id AS TEXT), 'steam', j.value, 'ryuugames'
    FROM ryuu_posts p, json_each(p.steam_ids) j
    WHERE p.removed_at IS NULL
    """,
    """
    SELECT 'vndb', v.value, 'dlsite', d.value, 'vndb'
    FROM vndb_releases r, json_each(r.vn_ids) v, json_each(r.dlsite_ids) d
    WHERE r.removed_at IS NULL AND v.value IN (SELECT vn_id FROM vndb_vns WHERE removed_at IS NULL)
      AND d.value IN (SELECT product_id FROM dlsite_works WHERE removed_at IS NULL)
    """,
    """
    SELECT 'vndb', v.value, 'steam', s.value, 'vndb'
    FROM vndb_releases r, json_each(r.vn_ids) v, json_each(r.steam_ids) s
    WHERE r.removed_at IS NULL AND v.value IN (SELECT vn_id FROM vndb_vns WHERE removed_at IS NULL)
    """,
    # DLsite work IDs on the Developer or Store lines of a first post. Circle
    # IDs (`dlsite_circle`) name no work; smartphone editions are never linked.
    """
    SELECT 'f95', CAST(t.thread_id AS TEXT), 'dlsite', json_extract(j.value, '$.id'), 'f95'
    FROM f95_threads t
    JOIN f95_thread_details d ON d.thread_id = t.thread_id AND d.status = 'ok',
    json_each(d.external_ids, '$.dlsite') j
    WHERE t.removed_at IS NULL AND json_extract(j.value, '$.id') IN
      (SELECT product_id FROM dlsite_works WHERE removed_at IS NULL AND COALESCE(site, '') != 'appx')
    """,
    """
    SELECT 'lc', CAST(t.thread_id AS TEXT), 'dlsite', json_extract(j.value, '$.id'), 'lc'
    FROM lc_threads t
    JOIN lc_thread_details d ON d.thread_id = t.thread_id AND d.status = 'ok'
      -- Details imported from Atlas are not LC's own statement.
      AND d.origin IS NULL,
    json_each(d.external_ids, '$.dlsite') j
    WHERE t.removed_at IS NULL AND json_extract(j.value, '$.id') IN
      (SELECT product_id FROM dlsite_works WHERE removed_at IS NULL AND COALESCE(site, '') != 'appx')
    """,
)


def _linked_sites(
    conn_rows: Iterable[tuple[str, str, str, str]],
) -> set[tuple[str, str, str]]:
    """`(site, id, other site)` for both ends of each link."""
    found: set[tuple[str, str, str]] = set()
    for src_a, id_a, src_b, id_b in conn_rows:
        found.add((src_a, id_a, src_b))
        found.add((src_b, id_b, src_a))
    return found


def _current(catalog: CatalogStore, site: str, identity: str) -> bool:
    table, key = _SOURCE_KEYS[site]
    row = cast(
        tuple[int] | None,
        catalog.conn.execute(
            f"SELECT 1 FROM {table} WHERE {key} = ? AND removed_at IS NULL"
            + (" AND COALESCE(site, '') != 'appx'" if site == "dlsite" else ""),
            (int(identity) if key in ("thread_id", "post_id") else identity,),
        ).fetchone(),
    )
    return row is not None


def _insert_weaker(catalog: CatalogStore, pairs: Iterable[Pair], origin: str) -> None:
    taken = _linked_sites(
        cast(
            list[tuple[str, str, str, str]],
            catalog.conn.execute(
                "SELECT src_a, id_a, src_b, id_b FROM links"
            ).fetchall(),
        )
    )
    rows: list[tuple[str, str, str, str, str]] = []
    for src_a, id_a, src_b, id_b in pairs:
        if (src_a, id_a, src_b) in taken or (src_b, id_b, src_a) in taken:
            continue
        rows.append((src_a, id_a, src_b, id_b, origin))
        # A later pair of the same batch must not give either end a second link.
        taken.add((src_a, id_a, src_b))
        taken.add((src_b, id_b, src_a))
    _ = catalog.conn.executemany(
        "INSERT OR IGNORE INTO links (src_a, id_a, src_b, id_b, origin) VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def rebuild_links(catalog: CatalogStore, imported: Sequence[Pair] = ()) -> int:
    with catalog.conn:
        _ = catalog.conn.execute("DELETE FROM links")
        for query in _LINK_QUERIES:
            _ = catalog.conn.execute(
                "INSERT OR IGNORE INTO links (src_a, id_a, src_b, id_b, origin) "
                + query
            )
        title_pairs: list[Pair] = [
            ("lc", str(lc_id), "f95", str(f95_id))
            for lc_id, f95_id in match_lc_f95(catalog)
        ]
        title_pairs += [
            ("f95", str(thread_id), "dlsite", product_id)
            for thread_id, product_id in match_f95_dlsite(catalog)
        ]
        _insert_weaker(catalog, title_pairs, TITLE_ORIGIN)
        _insert_weaker(
            catalog,
            (
                pair
                for pair in imported
                if _current(catalog, pair[0], pair[1])
                and _current(catalog, pair[2], pair[3])
            ),
            IMPORT_ORIGIN,
        )
    row = cast(
        tuple[int], catalog.conn.execute("SELECT COUNT(*) FROM links").fetchone()
    )
    return row[0]
