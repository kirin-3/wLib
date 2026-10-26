# SPDX-License-Identifier: GPL-3.0-or-later
"""Rebuild `links` from links the sources state. No title matching."""

from typing import cast

from .store import CatalogStore

# Each statement inserts (src_a, id_a, src_b, id_b, origin). Targets that are
# catalog sources must exist in the catalog; Steam is not a source, so its
# IDs are kept as stated.
_LINK_QUERIES = (
    """
    SELECT 'ryuugames', CAST(p.post_id AS TEXT), 'dlsite', j.value, 'ryuugames'
    FROM ryuu_posts p, json_each(p.rj_codes) j
    WHERE j.value IN (SELECT product_id FROM dlsite_works)
    """,
    """
    SELECT 'ryuugames', CAST(p.post_id AS TEXT), 'vndb', j.value, 'ryuugames'
    FROM ryuu_posts p, json_each(p.vndb_ids) j
    WHERE j.value IN (SELECT vn_id FROM vndb_vns)
    """,
    """
    SELECT 'ryuugames', CAST(p.post_id AS TEXT), 'steam', j.value, 'ryuugames'
    FROM ryuu_posts p, json_each(p.steam_ids) j
    """,
    """
    SELECT 'vndb', v.value, 'dlsite', d.value, 'vndb'
    FROM vndb_releases r, json_each(r.vn_ids) v, json_each(r.dlsite_ids) d
    WHERE v.value IN (SELECT vn_id FROM vndb_vns)
      AND d.value IN (SELECT product_id FROM dlsite_works)
    """,
    """
    SELECT 'vndb', v.value, 'steam', s.value, 'vndb'
    FROM vndb_releases r, json_each(r.vn_ids) v, json_each(r.steam_ids) s
    WHERE v.value IN (SELECT vn_id FROM vndb_vns)
    """,
)


def rebuild_links(catalog: CatalogStore) -> int:
    with catalog.conn:
        _ = catalog.conn.execute("DELETE FROM links")
        for query in _LINK_QUERIES:
            _ = catalog.conn.execute(
                "INSERT OR IGNORE INTO links (src_a, id_a, src_b, id_b, origin) " + query
            )
    row = cast(tuple[int], catalog.conn.execute("SELECT COUNT(*) FROM links").fetchone())
    return row[0]
