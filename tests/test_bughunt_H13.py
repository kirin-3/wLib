# SPDX-License-Identifier: GPL-3.0-or-later
"""A library choice applies only to the sync file revision it was asked for."""

import json

import pytest

from core import database
from core.library_backup import build_sync_backup
from core.library_sync import SyncConflict, SyncManager

STALE = "The sync file changed before your choice was applied. Choose again."


@pytest.fixture(autouse=True)
def sync_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "library.db"))
    database.init_db()


class MemoryLocation:
    def __init__(self, backup):
        self.data = json.dumps(backup).encode()
        self.rev = "1"
        self.writes = 0

    def revision(self):
        return self.rev

    def read(self):
        return self.data, self.rev

    def write(self, data, expected_revision):
        if expected_revision != self.rev:
            raise SyncConflict()
        self.data = data
        self.writes += 1
        self.rev = str(int(self.rev) + 1)
        return self.rev

    def change(self, backup):
        self.data = json.dumps(backup).encode()
        self.rev = str(int(self.rev) + 1)


def remote_backup(rating="7", modified="2026-10-09T10:00:00Z", games=True):
    backup = build_sync_backup(modified, "remote-device")
    backup["games"] = (
        [
            {
                "metadata": {"title": "Remote", "developer": "Dev"},
                "user_state": {"rating": rating},
            }
        ]
        if games
        else []
    )
    return backup


def conflicted():
    """A synced library where both sides then changed: the user is asked."""
    location = MemoryLocation(remote_backup())
    database.update_setting("sync_location", "folder")
    sync = SyncManager(location)
    assert sync.run()["state"] == "idle"
    game = database.get_all_games()[0]
    database.update_game(game["id"], {"rating": "9"})
    database.update_setting("sync_local_modified_at", "2026-10-09T12:00:00Z")
    location.change(remote_backup(rating="8", modified="2026-10-09T13:00:00Z"))
    status = sync.run()
    assert status["state"] == "needs_choice"
    assert status["choice_reason"] == "conflict"
    return location, sync


def remote_rating(location):
    return json.loads(location.data)["games"][0]["user_state"]["rating"]


def test_use_local_does_not_overwrite_a_newer_remote():
    location, sync = conflicted()
    # Another device writes while the dialog is open.
    location.change(remote_backup(rating="5", modified="2026-10-09T14:00:00Z"))
    status = sync.run("use-local")
    assert status["state"] == "needs_choice"
    assert status["choice_reason"] == "conflict"
    assert status["last_error"] == STALE
    assert location.writes == 0 and remote_rating(location) == "5"
    assert database.get_all_games()[0]["rating"] == "9"
    # Asked again for the new file, the answer now applies.
    assert sync.run("use-local")["state"] == "idle"
    assert location.writes == 1 and remote_rating(location) == "9"
    assert sync.status()["last_error"] == ""


def test_use_remote_does_not_apply_removals_it_was_not_asked_about():
    location, sync = conflicted()
    location.change(remote_backup(modified="2026-10-09T14:00:00Z", games=False))
    status = sync.run("use-remote")
    # Asked again (both sides still changed), with nothing removed yet.
    assert status["state"] == "needs_choice"
    assert status["choice_reason"] == "conflict"
    assert status["last_error"] == STALE
    assert [game["title"] for game in database.get_all_games()] == ["Remote"]


def test_retry_after_write_conflict_asks_again():
    location, sync = conflicted()
    write = location.write

    def another_device_writes_first(data, expected_revision):
        location.write = write
        location.change(remote_backup(rating="5", modified="2026-10-09T14:00:00Z"))
        return write(data, expected_revision)

    location.write = another_device_writes_first
    status = sync.run("use-local")
    assert status["state"] == "needs_choice"
    assert status["last_error"] == STALE
    assert location.writes == 0 and remote_rating(location) == "5"


def test_choice_for_the_same_revision_still_applies():
    location, sync = conflicted()
    assert sync.run("use-remote")["state"] == "idle"
    assert database.get_all_games()[0]["rating"] == "8"
    assert location.writes == 0


def first_connection_prompt():
    """Both libraries have games on the first connection: the user is asked."""
    database.add_game("Local", "/local.exe", developer="Dev")
    location = MemoryLocation(remote_backup())
    database.update_setting("sync_location", "folder")
    sync = SyncManager(location)
    assert sync.run()["state"] == "needs_choice"
    return location, sync


def test_first_connection_use_remote_does_not_mirror_an_empty_replacement():
    location, sync = first_connection_prompt()
    # The sync file is replaced by an empty library while the dialog is open.
    location.change(remote_backup(modified="2026-10-09T14:00:00Z", games=False))
    _ = sync.run("use-remote")
    assert [game["title"] for game in database.get_all_games()] == ["Local"]


def test_first_connection_choice_for_a_replaced_file_asks_again():
    location, sync = first_connection_prompt()
    location.change(remote_backup(rating="5", modified="2026-10-09T14:00:00Z"))
    status = sync.run("use-remote")
    assert status["state"] == "needs_choice"
    assert status["last_error"] == STALE
    assert [game["title"] for game in database.get_all_games()] == ["Local"]
    assert location.writes == 0
    # Asked again for the new file, the answer now applies.
    assert sync.run("use-remote")["state"] == "idle"
    assert [game["title"] for game in database.get_all_games()] == ["Remote"]
    assert database.get_all_games()[0]["rating"] == "5"
