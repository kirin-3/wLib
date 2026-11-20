"""A thread moved to a new synced game must not merge it into the old one."""

import pytest

from core.database import add_game, get_all_games, init_db, update_game
from core.library_backup import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    SECTION_EXECUTABLE_PATHS,
    SECTION_METADATA,
    apply_library_backup,
)

THREAD = "https://f95zone.to/threads/moved.101/"
NEW_UID = "b" * 32


@pytest.fixture(autouse=True)
def _database() -> None:
    init_db()


def _backup(games: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "exported_at": "2026-05-05T12:00:00",
        "app": {"name": "wLib", "version": "test"},
        "selected_sections": [SECTION_METADATA, SECTION_EXECUTABLE_PATHS],
        "games": games,
    }


@pytest.mark.parametrize("new_game_first", [False, True])
@pytest.mark.parametrize("sync", [True, False])
def test_thread_moved_to_another_uid_stays_a_separate_game(new_game_first, sync):
    old_id = add_game("Original", "/games/original/game.sh", f95_url=THREAD)
    old_uid = next(game for game in get_all_games() if game["id"] == old_id)["sync_uid"]
    original = {
        "uid": old_uid,
        "metadata": {"title": "Original", "f95_url": ""},
        "executable_paths": {"exe_path": "/games/original/game.sh"},
    }
    moved = {
        "uid": NEW_UID,
        "metadata": {"title": "New Game", "f95_url": THREAD},
        "executable_paths": {"exe_path": "/games/new/game.sh"},
    }
    games = [moved, original] if new_game_first else [original, moved]

    result = apply_library_backup(
        _backup(games), {"sections": [SECTION_METADATA, SECTION_EXECUTABLE_PATHS]}, sync=sync
    )

    assert result["created"] == 1
    assert result["updated"] == 1
    by_uid = {game["sync_uid"]: game for game in get_all_games()}
    assert set(by_uid) == {old_uid, NEW_UID}
    assert by_uid[old_uid]["id"] == old_id
    assert by_uid[old_uid]["title"] == "Original"
    assert by_uid[old_uid]["f95_url"] == ""
    assert by_uid[old_uid]["exe_path"] == "/games/original/game.sh"
    assert by_uid[NEW_UID]["title"] == "New Game"
    assert by_uid[NEW_UID]["f95_url"] == THREAD
    assert by_uid[NEW_UID]["exe_path"] == "/games/new/game.sh"


def test_thread_moved_in_fill_missing_keeps_the_local_owner():
    old_id = add_game("Original", "/games/original/game.sh", f95_url=THREAD)
    old_uid = next(game for game in get_all_games() if game["id"] == old_id)["sync_uid"]
    games = [
        {"uid": NEW_UID, "metadata": {"title": "New Game", "f95_url": THREAD}},
        {"uid": old_uid, "metadata": {"title": "Original", "f95_url": ""}},
    ]

    result = apply_library_backup(
        _backup(games), {"sections": [SECTION_METADATA]}, merge_mode="fill_missing"
    )

    assert result["created"] == 1
    by_uid = {game["sync_uid"]: game for game in get_all_games()}
    assert set(by_uid) == {old_uid, NEW_UID}
    # Fill-missing never clears the local thread, so the new game goes without it.
    assert by_uid[old_uid]["f95_url"] == THREAD
    assert by_uid[NEW_UID]["f95_url"] == ""
    assert by_uid[NEW_UID]["title"] == "New Game"


def test_same_thread_without_uid_still_merges_into_the_uid_match():
    old_id = add_game("Original", "", f95_url=THREAD)
    old_uid = next(game for game in get_all_games() if game["id"] == old_id)["sync_uid"]
    update_game(old_id, {"title": "Original"})
    games = [
        {"uid": old_uid, "metadata": {"title": "Original", "f95_url": THREAD}},
        {"metadata": {"title": "Copy", "f95_url": THREAD}},
    ]

    result = apply_library_backup(_backup(games), {"sections": [SECTION_METADATA]})

    assert result["created"] == 0
    assert len(get_all_games()) == 1
