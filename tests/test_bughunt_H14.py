# SPDX-License-Identifier: GPL-3.0-or-later
"""A Google Drive upload that replaced another device's upload is a conflict."""

import hashlib
import json
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest

from core import database
from core.library_backup import build_sync_backup
from core.library_sync import GoogleDriveLocation, SyncConflict, SyncManager


@pytest.fixture(autouse=True)
def sync_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "library.db"))
    database.init_db()
    monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")


def reply(payload):
    result = MagicMock()
    result.__enter__.return_value = result
    result.read.return_value = (
        payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    )
    result.headers = {}
    return result


def md5(data):
    return hashlib.md5(data).hexdigest()


class FakeDrive:
    """One appDataFolder file with Drive's ordered revision history."""

    def __init__(self, monkeypatch, content):
        self.revisions = [("r0", content)]
        self.uploads = 0
        # Runs once, before the next upload is stored.
        self.before_upload = None
        monkeypatch.setattr("urllib.request.urlopen", self.urlopen)

    @property
    def content(self):
        return self.revisions[-1][1]

    def upload(self, data):
        self.uploads += 1
        revision_id = f"r{len(self.revisions)}"
        self.revisions.append((revision_id, data))
        return {"md5Checksum": md5(data), "headRevisionId": revision_id}

    def urlopen(self, request, **kwargs):
        url = urlparse(request.full_url)
        method = request.get_method()
        if url.netloc == "oauth2.googleapis.com":
            return reply({"access_token": "access", "expires_in": 3600})
        if url.path == "/drive/v3/files":
            return reply({"files": [{"id": "f", "md5Checksum": md5(self.content)}]})
        if url.path == "/drive/v3/files/f" and url.query == "alt=media":
            return reply(self.content)
        if url.path == "/drive/v3/files/f/revisions":
            return reply(
                {
                    "revisions": [
                        {"id": revision_id, "md5Checksum": md5(data)}
                        for revision_id, data in self.revisions
                    ]
                }
            )
        if url.path.startswith("/drive/v3/files/f/revisions/"):
            wanted = url.path.rsplit("/", 1)[1]
            return reply(dict(self.revisions)[wanted])
        if url.path == "/upload/drive/v3/files/f" and method == "PATCH":
            hook, self.before_upload = self.before_upload, None
            if hook is not None:
                hook()
            return reply(self.upload(request.data))
        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_second_of_two_racing_uploads_restores_the_first_and_conflicts(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    first, second = GoogleDriveLocation("refresh"), GoogleDriveLocation("refresh")
    results = {}

    def first_device_uploads():
        # Both devices passed the checksum check; the first upload lands first.
        results["first"] = first.write(b"first device", md5(b"base"))

    drive.before_upload = first_device_uploads
    with pytest.raises(SyncConflict):
        second.write(b"second device", md5(b"base"))
    # The first device's upload is the file again, under the checksum that
    # device recorded as synced, so it does not pull the second device's file.
    assert results["first"] == md5(b"first device")
    assert drive.content == b"first device"
    assert first.revision() == results["first"]
    # The replaced upload stays in the history; nothing is lost.
    assert [data for _, data in drive.revisions] == [
        b"base",
        b"first device",
        b"second device",
        b"first device",
    ]


def test_upload_from_the_expected_revision_succeeds(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    location = GoogleDriveLocation("refresh")
    assert location.write(b"next", md5(b"base")) == md5(b"next")
    assert drive.content == b"next" and drive.uploads == 1


def test_racing_upload_with_the_same_content_is_not_a_conflict(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    other = GoogleDriveLocation("refresh")
    drive.before_upload = lambda: other.write(b"same", md5(b"base"))
    assert GoogleDriveLocation("refresh").write(b"same", md5(b"base")) == md5(b"same")
    assert drive.uploads == 2


def test_no_restore_over_a_later_upload(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    location = GoogleDriveLocation("refresh")
    upload = drive.upload

    def racing_uploads(data):
        _ = upload(b"before")
        result = upload(data)
        _ = upload(b"after")
        return result

    drive.upload = racing_uploads
    with pytest.raises(SyncConflict):
        location.write(b"mine", md5(b"base"))
    assert drive.content == b"after" and drive.uploads == 3


def test_unreadable_revision_history_keeps_the_upload(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    urlopen = drive.urlopen

    def no_history(request, **kwargs):
        if "/revisions" in request.full_url:
            from urllib.error import HTTPError

            raise HTTPError(request.full_url, 403, "forbidden", {}, None)
        return urlopen(request, **kwargs)

    monkeypatch.setattr("urllib.request.urlopen", no_history)
    assert GoogleDriveLocation("refresh").write(b"next", md5(b"base")) == md5(b"next")
    assert drive.content == b"next"


def backup_bytes(rating, modified, device):
    backup = build_sync_backup(modified, device)
    backup["games"] = [
        {
            "metadata": {"title": "Game", "developer": "Dev"},
            "user_state": {"rating": rating},
        }
    ]
    return json.dumps(backup).encode()


def test_sync_asks_after_a_replaced_upload(monkeypatch):
    base = backup_bytes("7", "2026-10-09T10:00:00Z", "other-device")
    drive = FakeDrive(monkeypatch, base)
    database.update_setting("sync_location", "gdrive")
    sync = SyncManager(GoogleDriveLocation("refresh"))
    assert sync.run()["state"] == "idle"
    game = database.get_all_games()[0]
    database.update_game(game["id"], {"rating": "9"})
    database.update_setting("sync_local_modified_at", "2026-10-09T12:00:00Z")
    other = backup_bytes("5", "2026-10-09T13:00:00Z", "other-device")

    def other_device_uploads():
        assert GoogleDriveLocation("refresh").write(other, md5(base)) == md5(other)

    drive.before_upload = other_device_uploads
    status = sync.run()
    # Before the fix this run reported success and the other device's edit
    # was gone; now the other upload is back and this device is asked.
    assert status["state"] == "needs_choice"
    assert status["choice_reason"] == "conflict"
    assert drive.content == other
    assert database.get_all_games()[0]["rating"] == "9"
