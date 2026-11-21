# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.f95_tags import fill_empty_f95_tags, merge_f95_tags, split_tags


def _merge3(
    current: str,
    baseline: str | None,
    f95: str,
    user_edited: bool = False,
    kept: str = "",
) -> tuple[str, str | None, str]:
    tags, new_baseline, new_kept = merge_f95_tags(
        split_tags(current) or [],
        split_tags(baseline),
        split_tags(f95) or [],
        user_edited,
        split_tags(kept) or [],
    )
    return (
        ", ".join(tags),
        None if new_baseline is None else ", ".join(new_baseline),
        ", ".join(new_kept),
    )


def _merge(current: str, baseline: str | None, f95: str, user_edited: bool = False):
    tags, new_baseline, new_kept = _merge3(current, baseline, f95, user_edited)
    assert new_kept == ""
    return tags, new_baseline


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


def test_thread_adds_a_tag_the_user_added():
    assert _merge3("rpg, sandbox", "rpg", "rpg, sandbox", True) == (
        "rpg, sandbox",
        "rpg, sandbox",
        "sandbox",
    )


def test_f95_adds_and_then_removes_a_user_tag():
    first = _merge3("rpg, sandbox", "rpg", "rpg, sandbox", True)
    assert first == ("rpg, sandbox", "rpg, sandbox", "sandbox")
    assert _merge3(first[0], first[1], "rpg", True, first[2]) == (
        "rpg, sandbox",
        "rpg",
        "",
    )


def test_f95_keeps_listing_a_user_tag():
    state = ("rpg, sandbox", "rpg, sandbox", "sandbox")
    for _ in range(2):
        state = _merge3(state[0], state[1], "rpg, sandbox", True, state[2])
        assert state == ("rpg, sandbox", "rpg, sandbox", "sandbox")


def test_user_removes_a_kept_tag_and_thread_keeps_it():
    assert _merge3("rpg", "rpg, sandbox", "rpg, sandbox", True, "sandbox") == (
        "rpg",
        "rpg, sandbox",
        "",
    )


def test_user_removes_a_kept_tag_and_thread_drops_it():
    assert _merge3("rpg", "rpg, sandbox", "rpg", True, "sandbox") == ("rpg", "rpg", "")


def test_empty_thread_list_keeps_kept_tags():
    assert _merge3("rpg, sandbox", "rpg, sandbox", "", True, "sandbox") == (
        "rpg, sandbox",
        "rpg, sandbox",
        "sandbox",
    )


def test_first_refresh_without_baseline_and_kept_tags():
    # Edited: the tags and the kept list stay; only the baseline is recorded.
    assert _merge3("rpg, mine", None, "rpg, sandbox", True, "mine") == (
        "rpg, mine",
        "rpg, sandbox",
        "mine",
    )
    # Unedited: all current tags are the baseline, so nothing is user-added.
    assert _merge3("rpg, sandbox", None, "rpg, sandbox", False, "sandbox") == (
        "rpg, sandbox",
        "rpg, sandbox",
        "",
    )


def test_kept_tags_compare_without_case():
    assert _merge3("rpg, Sandbox", "rpg, sandbox", "rpg", True, " SANDBOX ") == (
        "rpg, Sandbox",
        "rpg",
        "",
    )
    assert _merge3("rpg, Sandbox", "rpg", "RPG, sandbox", True) == (
        "RPG, sandbox",
        "RPG, sandbox",
        "Sandbox",
    )


def test_split_tags_keeps_null_and_cleans_values():
    assert split_tags(None) is None
    assert split_tags("") == []
    assert split_tags(" a , ,A, b ") == ["a", "b"]


def test_fill_empty_f95_tags_keeps_list_tag():
    assert fill_empty_f95_tags(["Watched on F95"], [], ["3dcg", "sandbox"]) == (
        ["3dcg", "sandbox", "Watched on F95"],
        ["3dcg", "sandbox"],
        [],
    )


def test_fill_empty_f95_tags_after_user_removed_list_tags():
    assert fill_empty_f95_tags([], [], ["3dcg", "rpg"]) == (
        ["3dcg", "rpg"],
        ["3dcg", "rpg"],
        [],
    )


def test_fill_empty_f95_tags_records_overlapping_user_tag():
    assert fill_empty_f95_tags(["rpg", "Bookmarked on F95"], [], ["3dcg", "rpg"]) == (
        ["3dcg", "rpg", "Bookmarked on F95"],
        ["3dcg", "rpg"],
        ["rpg"],
    )


@pytest.mark.parametrize(
    ("baseline", "thread_tags"),
    [(None, ["3dcg"]), (["3dcg"], ["3dcg", "sandbox"]), ([], []), ([], [" "])],
)
def test_fill_empty_f95_tags_leaves_other_states_alone(baseline, thread_tags):
    assert fill_empty_f95_tags(["Watched on F95"], baseline, thread_tags) is None


@pytest.mark.parametrize("baseline", ["", "  ", None, "3dcg"])
def test_fill_empty_f95_tags_for_url_only_fills_empty_baselines(baseline: str | None):
    from core.api import Api
    from core.database import add_game, get_game, init_db, update_game

    init_db()
    url = "https://f95zone.to/threads/24/"
    game_id = add_game("Game", "", url, tags="rpg, Watched on F95")
    other_id = add_game("Other", "", "https://f95zone.to/threads/25/", tags="mine")
    assert game_id is not None and other_id is not None
    _ = update_game(game_id, {"f95_tags": baseline})
    _ = update_game(other_id, {"f95_tags": ""})

    changed = Api()._fill_empty_f95_tags_for_url(url, ["3dcg", "rpg"])  # pyright: ignore[reportPrivateUsage]
    game = get_game(game_id) or {}
    assert (get_game(other_id) or {})["f95_tags"] == ""
    values = game["tags"], game["f95_tags"], game["user_f95_tags"]
    if baseline in ("", "  "):
        assert changed == [game_id]
        assert values == ("3dcg, rpg, Watched on F95", "3dcg, rpg", "rpg")
    else:
        assert changed == []
        assert values == ("rpg, Watched on F95", baseline, "")


# The F95 account import adds "Watched on F95"; "Refresh F95 info" must keep it.


def _import_watched(thread_id: int) -> None:
    from core import f95_lists

    entry = f95_lists.ListEntry(
        str(thread_id), "G [v1] [D]", "G", "1", "D", "", [], ["watched"]
    )
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
    assert (
        Api()._backfill_missing_metadata_for_url(url, {"tags": ["3dcg", "sandbox"]})
        == 1
    )  # pyright: ignore[reportPrivateUsage]
    full = get_game(game_id) or {}
    assert (full["tags"], full["f95_tags"]) == (
        "3dcg, sandbox, Watched on F95",
        "3dcg, sandbox",
    )
    # A later refresh keeps the list tag.
    assert _refresh(13, "3dcg, sandbox, rpg") == "3dcg, sandbox, rpg, Watched on F95"


def test_backfill_keeps_tags_of_games_without_a_baseline():
    from core.api import Api
    from core.database import add_game, get_game, init_db

    init_db()
    game_id = add_game("Old", "", "https://f95zone.to/threads/14/", tags="mine")
    assert game_id is not None
    assert (
        Api()._backfill_missing_metadata_for_url(
            "https://f95zone.to/threads/14/", {"tags": ["3dcg"]}
        )
        == 0
    )  # pyright: ignore[reportPrivateUsage]
    assert (get_game(game_id) or {})["tags"] == "mine"


def test_refresh_keeps_a_user_tag_that_f95_adds_and_removes():
    from core.api import Api
    from core.database import add_game, get_game, init_db, update_game

    init_db()
    url = "https://f95zone.to/threads/15/"
    game_id = add_game("Kept", "", url, tags="rpg")
    assert game_id is not None
    _ = update_game(
        game_id, {"f95_tags": "rpg", "tags": "rpg, sandbox", "tags_user_edited": 1}
    )
    api = Api()
    first = api._refresh_f95_info_for_url(url, {"tags": "rpg, sandbox"})  # pyright: ignore[reportPrivateUsage]
    assert first[game_id] == {"f95_tags": "rpg", "user_f95_tags": ""}
    game = get_game(game_id) or {}
    assert (game["tags"], game["f95_tags"], game["user_f95_tags"]) == (
        "rpg, sandbox",
        "rpg, sandbox",
        "sandbox",
    )
    second = api._refresh_f95_info_for_url(url, {"tags": "rpg"})  # pyright: ignore[reportPrivateUsage]
    game = get_game(game_id) or {}
    assert (game["tags"], game["f95_tags"], game["user_f95_tags"]) == (
        "rpg, sandbox",
        "rpg",
        "",
    )

    # Undo restores all three tag columns.
    assert second[game_id] == {"f95_tags": "rpg, sandbox", "user_f95_tags": "sandbox"}
    _ = update_game(game_id, second[game_id])
    game = get_game(game_id) or {}
    assert (game["tags"], game["f95_tags"], game["user_f95_tags"]) == (
        "rpg, sandbox",
        "rpg, sandbox",
        "sandbox",
    )


def test_refresh_undo_restores_null_kept_tags_as_empty():
    from core.api import Api
    from core.database import add_game, get_connection, get_game, init_db, update_game

    init_db()
    url = "https://f95zone.to/threads/16/"
    game_id = add_game("Null", "", url, tags="rpg, sandbox")
    assert game_id is not None
    conn = get_connection()
    _ = conn.execute(
        "UPDATE games SET f95_tags = 'rpg', user_f95_tags = NULL WHERE id = ?",
        (game_id,),
    )
    conn.commit()
    conn.close()
    changes = Api()._refresh_f95_info_for_url(url, {"tags": "rpg, sandbox"})  # pyright: ignore[reportPrivateUsage]
    assert changes[game_id] == {"f95_tags": "rpg", "user_f95_tags": ""}
    _ = update_game(game_id, changes[game_id])
    assert (get_game(game_id) or {})["user_f95_tags"] == ""


def test_backfill_over_an_empty_baseline_records_kept_tags():
    from core.api import Api
    from core.database import add_game, get_game, init_db, update_game

    init_db()
    url = "https://f95zone.to/threads/17/"
    game_id = add_game("Empty", "", url, tags="rpg, Watched on F95")
    assert game_id is not None
    _ = update_game(game_id, {"f95_tags": ""})
    assert Api()._backfill_missing_metadata_for_url(url, {"tags": ["3dcg", "rpg"]}) == 1  # pyright: ignore[reportPrivateUsage]
    game = get_game(game_id) or {}
    assert (game["tags"], game["f95_tags"], game["user_f95_tags"]) == (
        "3dcg, rpg, Watched on F95",
        "3dcg, rpg",
        "rpg",
    )
