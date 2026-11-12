# SPDX-License-Identifier: GPL-3.0-or-later
from core.f95_tags import merge_f95_tags, split_tags


def _merge(current: str, baseline: str | None, f95: str, user_edited: bool = False):
    tags, new_baseline = merge_f95_tags(
        split_tags(current) or [],
        split_tags(baseline),
        split_tags(f95) or [],
        user_edited,
    )
    return ", ".join(tags), None if new_baseline is None else ", ".join(new_baseline)


def test_user_added_tag_is_kept():
    assert _merge("3dcg, favorite-dev", "3dcg", "3dcg, sandbox", True) == (
        "3dcg, sandbox, favorite-dev",
        "3dcg, sandbox",
    )


def test_user_removed_f95_tag_stays_removed():
    assert _merge(
        "3dcg", "3dcg, male protagonist", "3dcg, male protagonist, sandbox", True
    ) == ("3dcg, sandbox", "3dcg, male protagonist, sandbox")


def test_tag_that_f95_removed_goes_away():
    assert _merge("3dcg, rpg", "3dcg, rpg", "3dcg") == ("3dcg", "3dcg")


def test_different_case_is_one_tag_and_not_user_added():
    assert _merge("3DCG", "3dcg", "3dcg") == ("3dcg", "3dcg")


def test_order_is_thread_order_then_user_tags():
    assert _merge("3dcg, my-pick", "3dcg", "sandbox, 3dcg") == (
        "sandbox, 3dcg, my-pick",
        "sandbox, 3dcg",
    )


def test_no_baseline_with_unedited_tags_uses_current_tags():
    assert _merge("3dcg", None, "3dcg, sandbox") == ("3dcg, sandbox", "3dcg, sandbox")


def test_no_baseline_with_edited_tags_then_second_refresh():
    tags, baseline = _merge("3dcg", None, "3dcg, rpg", True)
    assert (tags, baseline) == ("3dcg", "3dcg, rpg")
    assert _merge(tags, baseline, "3dcg, rpg, sandbox", True) == (
        "3dcg, sandbox",
        "3dcg, rpg, sandbox",
    )


def test_empty_f95_list_changes_nothing():
    assert _merge("3dcg, sandbox", "3dcg", "") == ("3dcg, sandbox", "3dcg")
    assert _merge("3dcg, sandbox", None, "") == ("3dcg, sandbox", None)


def test_split_tags_keeps_null_and_cleans_values():
    assert split_tags(None) is None
    assert split_tags("") == []
    assert split_tags(" a , ,A, b ") == ["a", "b"]
