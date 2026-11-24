# SPDX-License-Identifier: GPL-3.0-or-later
"""Group catalog entries that are the same game across sites.

Every current entry (DLsite smartphone editions, `site = 'appx'`, left out)
gets exactly one group. Two entries are joined only on one-to-one evidence:

1. Evidence comes from `links` and from Steam app IDs. A Steam ID joins the
   entries that state it only when each site has at most one of them.
2. Evidence is used in tiers: stated (source links and Steam IDs), then title
   matches, then imported pairs. Within a tier an edge counts only when each
   end has exactly one partner on the other end's site in that tier.
3. A group holds at most one entry per site; a join that would break that is
   skipped, so stronger tiers win.

The primary entry is the first by site order (F95, LC, DLsite, VNDB,
Ryuugames), and the group key is `<site>:<id>` of that entry. A missed join is
accepted; a wrong one is not.
"""

import json
from collections import defaultdict
from collections.abc import Iterable
from typing import cast

from .store import IMPORT_ORIGIN, CatalogStore

SITE_ORDER: tuple[str, ...] = ("f95", "lc", "dlsite", "vndb", "ryuugames")
TITLE_ORIGIN = "title"
STEAM = "steam"

Node = tuple[str, str]
Edge = tuple[Node, Node]

_NODE_QUERIES: dict[str, str] = {
    "f95": "SELECT CAST(thread_id AS TEXT) FROM f95_threads WHERE removed_at IS NULL",
    "lc": "SELECT CAST(thread_id AS TEXT) FROM lc_threads WHERE removed_at IS NULL",
    "dlsite": "SELECT product_id FROM dlsite_works WHERE removed_at IS NULL "
    + "AND COALESCE(site, '') != 'appx'",
    "vndb": "SELECT vn_id FROM vndb_vns WHERE removed_at IS NULL",
    "ryuugames": "SELECT CAST(post_id AS TEXT) FROM ryuu_posts WHERE removed_at IS NULL",
}


def _nodes(catalog: CatalogStore) -> set[Node]:
    found: set[Node] = set()
    for site, query in _NODE_QUERIES.items():
        for (identity,) in cast(
            list[tuple[str]], catalog.conn.execute(query).fetchall()
        ):
            found.add((site, str(identity)))
    return found


def _steam_statements(catalog: CatalogStore, nodes: set[Node]) -> dict[str, set[Node]]:
    """Steam app ID -> the current entries that state it."""
    stated: dict[str, set[Node]] = defaultdict(set)
    for src, identity, steam_id in cast(
        list[tuple[str, str, str]],
        catalog.conn.execute(
            "SELECT src_a, id_a, id_b FROM links WHERE src_b = 'steam'"
        ).fetchall(),
    ):
        stated[str(steam_id)].add((src, str(identity)))
    for site, table in (("f95", "f95_thread_details"), ("lc", "lc_thread_details")):
        for thread_id, raw in cast(
            list[tuple[int, str | None]],
            catalog.conn.execute(
                f"SELECT thread_id, external_ids FROM {table} "
                + "WHERE status = 'ok' AND kept_after_failure = 0"
                + (" AND origin IS NULL" if site == "lc" else "")
            ).fetchall(),
        ):
            try:
                ids = cast(object, json.loads(raw or "{}"))
            except ValueError:
                continue
            if not isinstance(ids, dict):
                continue
            entries = cast(dict[str, object], ids).get(STEAM)
            if not isinstance(entries, list):
                continue
            for entry in cast(list[object], entries):
                if isinstance(entry, dict):
                    value = cast(dict[str, object], entry).get("id")
                    if isinstance(value, str) and value:
                        stated[value].add((site, str(thread_id)))
    return {
        steam_id: {node for node in entries if node in nodes}
        for steam_id, entries in stated.items()
    }


def _tiers(catalog: CatalogStore, nodes: set[Node]) -> list[set[Edge]]:
    stated: set[Edge] = set()
    title: set[Edge] = set()
    imported: set[Edge] = set()
    for src_a, id_a, src_b, id_b, origin in cast(
        list[tuple[str, str, str, str, str]],
        catalog.conn.execute(
            "SELECT src_a, id_a, src_b, id_b, origin FROM links WHERE src_b != 'steam'"
        ).fetchall(),
    ):
        a, b = (src_a, str(id_a)), (src_b, str(id_b))
        if a not in nodes or b not in nodes or src_a == src_b:
            continue
        edge = (min(a, b), max(a, b))
        if origin == TITLE_ORIGIN:
            title.add(edge)
        elif origin == IMPORT_ORIGIN:
            imported.add(edge)
        else:
            stated.add(edge)
    for entries in _steam_statements(catalog, nodes).values():
        sites = [site for site, _ in entries]
        if len(entries) < 2 or len(set(sites)) != len(sites):
            continue
        ordered = sorted(entries)
        for index, a in enumerate(ordered):
            for b in ordered[index + 1 :]:
                stated.add((a, b))
    return [stated, title, imported]


def _one_to_one(edges: Iterable[Edge]) -> list[Edge]:
    """Edges whose ends each have exactly one partner on the other end's site."""
    edges = sorted(set(edges))
    partners: dict[tuple[Node, str], int] = defaultdict(int)
    for a, b in edges:
        partners[(a, b[0])] += 1
        partners[(b, a[0])] += 1
    return [
        (a, b)
        for a, b in edges
        if partners[(a, b[0])] == 1 and partners[(b, a[0])] == 1
    ]


def build_groups(catalog: CatalogStore) -> dict[Node, str]:
    """Each current entry -> its group key."""
    nodes = _nodes(catalog)
    parent: dict[Node, Node] = {node: node for node in nodes}
    sites: dict[Node, set[str]] = {node: {node[0]} for node in nodes}

    def find(node: Node) -> Node:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for tier in _tiers(catalog, nodes):
        for a, b in _one_to_one(tier):
            root_a, root_b = find(a), find(b)
            if root_a == root_b or sites[root_a] & sites[root_b]:
                continue
            parent[root_b] = root_a
            sites[root_a] |= sites.pop(root_b)
    members: dict[Node, list[Node]] = defaultdict(list)
    for node in nodes:
        members[find(node)].append(node)
    keys: dict[Node, str] = {}
    for group in members.values():
        primary = min(group, key=lambda node: SITE_ORDER.index(node[0]))
        key = f"{primary[0]}:{primary[1]}"
        for node in group:
            keys[node] = key
    return keys


def rebuild_groups(catalog: CatalogStore) -> int:
    """Rewrite `game_groups`; returns the number of groups."""
    keys = build_groups(catalog)
    with catalog.conn:
        _ = catalog.conn.execute("DELETE FROM game_groups")
        _ = catalog.conn.executemany(
            "INSERT INTO game_groups (src, id, group_key) VALUES (?, ?, ?)",
            ((src, identity, key) for (src, identity), key in sorted(keys.items())),
        )
    return len(set(keys.values()))
