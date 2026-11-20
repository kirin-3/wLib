# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
from core.api import Api
from core.database import get_all_games, get_game, update_game


def test_attach_does_not_replace_a_thread_attached_after_the_check(monkeypatch):
    """A second attach that read the empty URL before the first one wrote
    must report a conflict instead of replacing the first thread."""
    import core.database as database

    api = Api()
    monkeypatch.setattr(api, "_fetch_added_game_metadata", lambda *args: None)
    links = [{"url": "https://vndb.org/v123"}]
    wishlisted = api.add_game("Work", "", links=links)
    game_id = wishlisted["id"]

    real_get_game = database.get_game
    stale_reads = [True]

    def get_game_seen_before_other_attach(requested_id):
        game = real_get_game(requested_id)
        if stale_reads and requested_id == game_id and game is not None:
            _ = stale_reads.pop()
            # The other request attaches its thread right after this read.
            _ = update_game(game_id, {"f95_url": "https://f95zone.to/threads/111/"})
            return {**game, "f95_url": ""}
        return game

    monkeypatch.setattr(database, "get_game", get_game_seen_before_other_attach)
    result = api.add_game("Thread", "", "https://f95zone.to/threads/222/", links=links)

    assert result["success"] is False
    assert result["error_code"] == "duplicate_url"
    assert get_game(game_id)["f95_url"] == "https://f95zone.to/threads/111/"
    assert len(get_all_games()) == 1


def test_remove_game_finishes_cleanup_when_cached_cover_cannot_be_deleted(monkeypatch):
    from core.database import add_game

    api = Api()
    game_id = add_game("Game", "/game.exe")
    assert game_id is not None

    def read_only_cache(_name):
        raise PermissionError("read-only cover cache")

    removed_attachments: list[int] = []
    monkeypatch.setattr("core.game_covers.delete_cached_cover", read_only_cache)
    monkeypatch.setattr(
        "core.game_attachments.remove_game_attachments", removed_attachments.append
    )

    assert api.delete_game(game_id) == {"success": True}
    assert get_game(game_id) is None
    assert removed_attachments == [game_id]
