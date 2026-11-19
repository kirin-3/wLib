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


# The F95 account import adds "Watched on F95"; "Refresh F95 info" must keep it.

def _import_watched(thread_id: int) -> None:
    from core import f95_lists

    entry = f95_lists.ListEntry(str(thread_id), "G [v1] [D]", "G", "1", "D", "", [], ["watched"])
    _ = f95_lists.apply_import([entry], catalog_lookup=lambda _ids: {})


def _refresh(thread_id: int, f95_tags: str) -> str:
    from core.api import Api
    from core.database import find_game_by_f95_url

    url = f"https://f95zone.to/threads/{thread_id}/"
    _ = Api()._refresh_f95_info_for_url(url, {"tags": f95_tags})  # pyright: ignore[reportPrivateUsage]
    game = find_game_by_f95_url(url)
    assert game is not None
    from core.database import get_game

    full = get_game(int(str(game["id"])))
    assert full is not None
    return str(full["tags"])


def test_refresh_keeps_list_tag_on_new_imported_game():
    from core.database import init_db

    init_db()
    _import_watched(10)
    assert _refresh(10, "3dcg, sandbox") == "3dcg, sandbox, Watched on F95"


def test_refresh_keeps_list_tag_on_existing_game_without_baseline():
    from core.database import add_game, get_game, init_db

    init_db()
    game_id = add_game("Old", "", "https://f95zone.to/threads/11/", tags="3dcg, rpg")
    assert game_id is not None
    assert (get_game(game_id) or {}).get("f95_tags") is None
    _import_watched(11)
    assert (get_game(game_id) or {}).get("f95_tags") == "3dcg, rpg"
    assert _refresh(11, "3dcg, rpg, sandbox") == "3dcg, rpg, sandbox, Watched on F95"


def test_refresh_keeps_list_tag_on_user_edited_game():
    from core.database import add_game, get_game, init_db, update_game

    init_db()
    game_id = add_game("Edited", "", "https://f95zone.to/threads/12/", tags="mine")
    assert game_id is not None
    _ = update_game(game_id, {"tags_user_edited": 1})
    _import_watched(12)
    assert (get_game(game_id) or {}).get("f95_tags") is None
    assert _refresh(12, "3dcg") == "mine, Watched on F95"
    assert _refresh(12, "3dcg, rpg") == "rpg, mine, Watched on F95"


def test_update_check_backfill_adds_thread_tags_to_imported_game():
    from core.api import Api
    from core.database import find_game_by_f95_url, get_game, init_db

    init_db()
    _import_watched(13)
    url = "https://f95zone.to/threads/13/"
    game = find_game_by_f95_url(url)
    assert game is not None
    game_id = int(str(game["id"]))
    assert (get_game(game_id) or {}).get("f95_tags") == ""
    assert Api()._backfill_missing_metadata_for_url(url, {"tags": ["3dcg", "sandbox"]}) == 1  # pyright: ignore[reportPrivateUsage]
    full = get_game(game_id) or {}
    assert (full["tags"], full["f95_tags"]) == ("3dcg, sandbox, Watched on F95", "3dcg, sandbox")
    # A later refresh keeps the list tag.
    assert _refresh(13, "3dcg, sandbox, rpg") == "3dcg, sandbox, rpg, Watched on F95"


def test_backfill_keeps_tags_of_games_without_a_baseline():
    from core.api import Api
    from core.database import add_game, get_game, init_db

    init_db()
    game_id = add_game("Old", "", "https://f95zone.to/threads/14/", tags="mine")
    assert game_id is not None
    assert Api()._backfill_missing_metadata_for_url("https://f95zone.to/threads/14/", {"tags": ["3dcg"]}) == 0  # pyright: ignore[reportPrivateUsage]
    assert (get_game(game_id) or {})["tags"] == "mine"
