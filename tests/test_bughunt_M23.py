# SPDX-License-Identifier: GPL-3.0-or-later
"""M23: a short marker word after the version (DLC, OST) must still count as a marker."""
import pytest

from core.downloads import rpdl_match as m


@pytest.mark.parametrize("title", ["Game-2.0-DLC", "Game-2.0-OST", "Game-v2.0-pc-dlc"])
def test_short_marker_after_version_is_a_marker(title):
    tokens, markers = m.name_tokens(title)
    assert tokens == ["game"]
    assert markers & {"dlc", "ost"}
    assert m.title_score("Game", title) < m.PRESELECT_AT


def test_dlc_after_version_is_not_preselected():
    out = m.rank([{"id": 1, "title": "Game-2.0-DLC", "seeders": 5, "date": 1}], "Game", "2.0")
    assert out["preselected_id"] is None


def test_short_release_suffix_after_version_still_dropped():
    assert m.title_score("Game", "Game-2.0-pc") == 100
    assert m.title_score("Game", "Game-2.0-a") == 100
