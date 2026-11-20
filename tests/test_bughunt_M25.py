# SPDX-License-Identifier: GPL-3.0-or-later
"""M25: the requested build must not be pushed out of the shown list by older builds with more seeders."""
from __future__ import annotations

from core.downloads import rpdl_match


def _older(count: int) -> list[dict[str, object]]:
    return [
        {"id": i, "title": f"Eternum-0.{i}-pc", "seeders": 20, "date": 100 + i}
        for i in range(1, count + 1)
    ]


def test_exact_version_survives_cutoff_against_capped_older_builds() -> None:
    wanted: dict[str, object] = {"id": 99, "title": "Eternum-0.9-pc", "seeders": 1, "date": 1}
    results = [*_older(rpdl_match.MAX_SHOWN), wanted]
    ranked = rpdl_match.rank(results, "Eternum", "0.9")
    shown = ranked["results"]
    assert isinstance(shown, list)
    ids = [r["id"] for r in shown]
    assert 99 in ids
    assert ids[0] == 99
    assert ranked["preselected_id"] == 99


def test_without_version_seeders_still_break_ties() -> None:
    results: list[dict[str, object]] = [
        {"id": 1, "title": "Eternum-0.8-pc", "seeders": 1, "date": 5},
        {"id": 2, "title": "Eternum-0.7-pc", "seeders": 20, "date": 1},
    ]
    shown = rpdl_match.rank(results, "Eternum")["results"]
    assert isinstance(shown, list)
    assert [r["id"] for r in shown] == [2, 1]
