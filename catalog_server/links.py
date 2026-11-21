# SPDX-License-Identifier: GPL-3.0-or-later
"""Rebuild `links` from links the sources state, plus strict LewdCorner↔F95
title matches (`origin = 'title'`, see `title_match`). No other title matching."""

from typing import cast

from .store import CatalogStore
from .title_match import match_lc_f95

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
)


def rebuild_links(catalog: CatalogStore) -> int:
    with catalog.conn:
        _ = catalog.conn.execute("DELETE FROM links")
        for query in _LINK_QUERIES:
            _ = catalog.conn.execute(
                "INSERT OR IGNORE INTO links (src_a, id_a, src_b, id_b, origin) "
                + query
            )
        _ = catalog.conn.executemany(
            "INSERT OR IGNORE INTO links (src_a, id_a, src_b, id_b, origin) "
            + "VALUES ('lc', ?, 'f95', ?, 'title')",
            [(str(lc_id), str(f95_id)) for lc_id, f95_id in match_lc_f95(catalog)],
        )
    row = cast(
        tuple[int], catalog.conn.execute("SELECT COUNT(*) FROM links").fetchone()
    )
    return row[0]
