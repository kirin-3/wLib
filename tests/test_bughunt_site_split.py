"""Two synced games that each hold one site of a local game stay two games."""

import pytest

from core.database import add_game, get_all_games, init_db
from core.library_backup import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    SECTION_METADATA,
    SECTION_USER_STATE,
    apply_library_backup,
)

F95 = "https://f95zone.to/threads/split.301/"
LC = "https://lewdcorner.com/threads/302/"
F95_UID = "a" * 32
LC_UID = "c" * 32
SECTIONS = [SECTION_METADATA, SECTION_USER_STATE]


@pytest.fixture(autouse=True)
def _database() -> None:
    init_db()


def _backup(games: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "exported_at": "2026-05-05T12:00:00",
        "app": {"name": "wLib", "version": "test"},
        "selected_sections": SECTIONS,
        "games": games,
    }


def _games(lc_first: bool) -> list[dict[str, object]]:
    f95_game = {
        "uid": F95_UID,
        "metadata": {"title": "F95 Game", "f95_url": F95, "lc_url": ""},
        "user_state": {"progress": "F95 progress"},
    }
    lc_game = {
        "uid": LC_UID,
        "metadata": {"title": "LC Game", "f95_url": "", "lc_url": LC},
        "user_state": {"progress": "LC progress"},
    }
    return [lc_game, f95_game] if lc_first else [f95_game, lc_game]


@pytest.mark.parametrize("lc_first", [False, True])
@pytest.mark.parametrize("sync", [True, False])
def test_games_with_other_uids_on_each_site_stay_separate(lc_first, sync):
    local_id = add_game("Local", "", f95_url=F95, lc_url=LC)

    result = apply_library_backup(
        _backup(_games(lc_first)), {"sections": SECTIONS}, sync=sync
    )

    assert (result["created"], result["updated"]) == (1, 1)
    by_uid = {game["sync_uid"]: game for game in get_all_games()}
    assert set(by_uid) == {F95_UID, LC_UID}
    assert by_uid[F95_UID]["f95_url"] == F95
    assert by_uid[F95_UID]["progress"] == "F95 progress"
    assert by_uid[LC_UID]["lc_url"] == LC
    assert by_uid[LC_UID]["progress"] == "LC progress"
    # The first game in the file keeps the local row.
    kept = LC_UID if lc_first else F95_UID
    assert by_uid[kept]["id"] == local_id


def test_fill_missing_keeps_both_sites_on_the_local_row():
    local_id = add_game("Local", "", f95_url=F95, lc_url=LC)

    result = apply_library_backup(
        _backup(_games(False)), {"sections": SECTIONS}, merge_mode="fill_missing"
    )

    assert (result["created"], result["updated"]) == (1, 1)
    by_uid = {game["sync_uid"]: game for game in get_all_games()}
    assert set(by_uid) == {F95_UID, LC_UID}
    # Fill-missing never clears the local thread, so the new game goes without it.
    assert by_uid[F95_UID]["id"] == local_id
    assert (by_uid[F95_UID]["f95_url"], by_uid[F95_UID]["lc_url"]) == (F95, LC)
    assert (by_uid[LC_UID]["f95_url"], by_uid[LC_UID]["lc_url"]) == ("", "")
    assert by_uid[LC_UID]["title"] == "LC Game"


def test_same_thread_copies_with_other_uids_still_merge():
    add_game("Local", "", f95_url=F95, lc_url=LC)
    games = [
        {"uid": F95_UID, "metadata": {"title": "Copy A", "f95_url": F95}},
        {"uid": LC_UID, "metadata": {"title": "Copy B", "f95_url": F95}},
    ]

    result = apply_library_backup(_backup(games), {"sections": [SECTION_METADATA]})

    assert (result["created"], result["updated"]) == (0, 2)
    assert len(get_all_games()) == 1
