# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.downloads import rpdl_match as m


def _r(i, title, seeders=5, date=1):
    return {"id": i, "title": title, "seeders": seeders, "leechers": 0, "size": 1, "date": date}


@pytest.mark.parametrize(
    ("game", "title"),
    [
        ("Eternum", "Eternum-0.8-pc"),
        ("Eternum", "Eternum-0.9.5"),
        ("Summer's Gone", "SummersGone-1.0-pc"),
        ("Summer's Gone", "Summers-Gone-Ch2-0.5"),
        ("Being a DIK", "BeingADIK-0.10.0-pc"),
        ("The Genesis Order", "TheGenesisOrder-v1.2.3-fix2-pc"),
        ("Game II", "Game-2-1.0"),
        ("Café Days", "CafeDays-0.3-Linux"),
    ],
)
def test_same_game_scores_high(game, title):
    assert m.title_score(game, title) >= 90, m.name_tokens(title)


def test_sequel_number_differs():
    assert m.title_score("Game 2", "Game-2-1.0") > m.title_score("Game 2", "Game-3-1.0") + 30
    assert m.title_score("Game 2", "Game-3-1.0") < 40


def test_roman_numerals_match_digits():
    assert m.title_score("Game III", "Game-3-0.1") >= 90


def test_soundtrack_scores_lower_than_base_game():
    base = m.title_score("Eternum", "Eternum-0.8")
    ost = m.title_score("Eternum", "Eternum-OST")
    assert ost <= base - 30 and ost < m.PRESELECT_AT


def test_game_with_marker_word_in_its_own_title():
    assert m.title_score("Demo Girl", "DemoGirl-0.2") >= 90


def test_unrelated_with_shared_word_is_hidden():
    assert m.title_score("Eternum", "AeternumTheAwakening-2.2") < m.HIDE_BELOW
    assert m.title_score("Summer Time", "Summer Memories-1.0") < m.HIDE_BELOW


def test_close_but_longer_title_does_not_preselect():
    assert m.title_score("Eternum", "EternumUnofficialRealistic4K-0.1") < m.PRESELECT_AT


def test_versions_compare_with_suffixes():
    assert m.listing_has_version("Game-0.8-fix2-pc", "0.8")
    assert m.listing_has_version("Game-v0.8-pc", "0.8.0")
    assert m.listing_has_version("Game-1.0-Final", "v1")
    assert m.listing_has_version("Game-0.10-pc", "0.10")
    assert not m.listing_has_version("Game-0.7-pc", "0.8")
    assert not m.listing_has_version("Game-10.8-pc", "0.8")
    assert not m.listing_has_version("Game-0.8b-pc", "0.8")
    assert not m.listing_has_version("Game-pc", "0.8")


def test_query_plan_falls_back_in_order():
    assert m.plan_queries("Game Name [v0.5]", "Dev") == ["Game Name [v0.5]", "Game Name", "Game Name Dev"]
    assert m.plan_queries("Game Name", "") == ["Game Name"]
    assert m.plan_queries("A!", "") == []


def test_find_stops_at_first_search_with_results():
    seen = []

    def search(query):
        seen.append(query)
        return [_r(1, "GameName-0.5-pc")] if query == "Game Name" else []

    out = m.find(search, "Game Name [v0.5]", "Dev", "0.5")
    assert seen == ["Game Name [v0.5]", "Game Name"]
    assert out["preselected_id"] == 1 and out["query"] == "Game Name"


def test_clear_match_preselected():
    out = m.rank([_r(1, "Eternum-0.8-pc"), _r(2, "Eternum-OST")], "Eternum", "0.8")
    assert out["preselected_id"] == 1


def test_two_close_matches_need_a_choice():
    out = m.rank([_r(1, "Eternum-0.8-pc"), _r(2, "Eternum-0.8-fix2-linux")], "Eternum", "0.8")
    assert out["preselected_id"] is None and out["needs_choice"] is True
    assert len(out["results"]) == 2


def test_other_version_listed_but_not_preselected():
    out = m.rank([_r(1, "Eternum-0.7-pc")], "Eternum", "0.8")
    assert out["results"][0]["version_match"] is False
    assert out["preselected_id"] is None


def test_older_build_does_not_block_the_right_one():
    out = m.rank([_r(1, "Eternum-0.7-pc"), _r(2, "Eternum-0.8-pc")], "Eternum", "0.8")
    assert out["preselected_id"] == 2


def test_zero_seeders_never_preselected():
    out = m.rank([_r(1, "Eternum-0.8-pc", seeders=0)], "Eternum", "0.8")
    assert out["results"][0]["no_seeders"] is True
    assert out["preselected_id"] is None


def test_at_most_eight_best_first():
    results = [_r(i, f"Eternum-0.{i}-pc", seeders=i) for i in range(1, 15)]
    out = m.rank(results, "Eternum", None)
    assert len(out["results"]) == 8
    scores = [r["score"] for r in out["results"]]
    assert scores == sorted(scores, reverse=True)


def test_joined_season_or_sequel_number_counts():
    assert m.title_score("Game", "GameSeason2-1.0") < m.HIDE_BELOW
    assert m.title_score("Game Season 2", "GameSeason2-1.0") >= 90
    assert m.title_score("Game Season 2", "GameSeason3-1.0") < m.HIDE_BELOW
    assert m.title_score("Game 2", "Game2-1.0") > m.title_score("Game 2", "Game3-1.0") + 30
    assert m.title_score("Area51", "Area 51-0.3") >= 90
    assert m.title_score("Game", "Game-0.5-win10") >= 90
    assert m.title_score("Game", "Game-1.0-pc64") >= 90


def test_find_tries_the_next_query_when_all_results_score_too_low():
    seen = []

    def search(query):
        seen.append(query)
        if query == "Game Name [v0.5]":
            return [_r(9, "OtherThing-1.0")]
        return [_r(1, "GameName-0.5-pc")] if query == "Game Name" else []

    out = m.find(search, "Game Name [v0.5]", "Dev", "0.5")
    assert seen == ["Game Name [v0.5]", "Game Name"]
    assert out["preselected_id"] == 1 and out["query"] == "Game Name"
