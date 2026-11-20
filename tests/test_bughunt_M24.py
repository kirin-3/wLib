# SPDX-License-Identifier: GPL-3.0-or-later
"""M24: a sequel number in the game title is not the listing's version."""
import pytest

from core.downloads import rpdl_match as m


def test_sequel_number_is_not_the_version():
    assert m.versions_in_title("Game-2-1.4-pc", "Game 2") == ["1.4"]
    assert not m.listing_has_version("Game-2-1.4-pc", "2", "Game 2")
    assert m.listing_has_version("Game-2-1.4-pc", "1.4", "Game 2")


def test_sequel_number_listing_not_preselected_for_that_version():
    out = m.rank([{"id": 1, "title": "Game-2-1.4-pc", "seeders": 5, "date": 1}], "Game 2", "2")
    assert out["preselected_id"] is None
    assert out["results"][0]["version_match"] is False


@pytest.mark.parametrize("title", ["Game-2-2-pc", "Game2-2-pc", "GameII-2-pc"])
def test_real_version_equal_to_sequel_number_still_matches(title):
    game = "Game II" if title.startswith("GameII") else "Game 2"
    assert m.listing_has_version(title, "2", game)


def test_without_game_title_behavior_is_unchanged():
    assert m.versions_in_title("Game-2-1.4-pc") == ["2", "1.4"]
    assert m.listing_has_version("Game-0.8-pc", "0.8", "Game")
