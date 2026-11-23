# SPDX-License-Identifier: GPL-3.0-or-later
"""A Google Drive upload that replaced another device's upload is a conflict."""

import hashlib
import json
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.parse import urlparse

import pytest

from core import database
from core.library_backup import build_sync_backup
from core.library_sync import (
    RESTORE_ATTEMPTS,
    DriveRestoreFailed,
    GoogleDriveLocation,
    SyncConflict,
    SyncError,
    SyncManager,
)


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
        # Revisions marked keepForever. Drive serves an earlier revision's
        # content only when it has this mark.
        self.kept = set()
        self.fail_keep = False
        # HTTP status of a failed keepForever change.
        self.keep_status = 403
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
        if url.path == "/drive/v3/files/f" and url.query == "fields=headRevisionId":
            return reply({"headRevisionId": self.revisions[-1][0]})
        if url.path == "/drive/v3/files/f/revisions":
            return reply(
                {
                    "revisions": [
                        {"id": revision_id, "md5Checksum": md5(data)}
                        for revision_id, data in self.revisions
                    ]
                }
            )
        if url.path.startswith("/drive/v3/files/f/revisions/") and method == "PATCH":
            if self.fail_keep:
                raise HTTPError(request.full_url, self.keep_status, "failed", {}, None)
            wanted = url.path.rsplit("/", 1)[1]
            if json.loads(request.data)["keepForever"]:
                self.kept.add(wanted)
            else:
                self.kept.discard(wanted)
            return reply({"id": wanted})
        if url.path.startswith("/drive/v3/files/f/revisions/"):
            wanted = url.path.rsplit("/", 1)[1]
            if wanted != self.revisions[-1][0] and wanted not in self.kept:
                raise HTTPError(request.full_url, 403, "forbidden", {}, None)
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
    # The mark needed for the download is removed once the content is back.
    assert drive.kept == set()


def test_failed_restore_reports_an_error_and_keeps_the_revision(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    drive.before_upload = lambda: drive.upload(b"first device")
    drive.fail_keep = True
    with pytest.raises(SyncError) as error:
        GoogleDriveLocation("refresh").write(b"second device", md5(b"base"))
    assert error.value.error_code == "gdrive_restore_failed"
    assert not isinstance(error.value, SyncConflict)
    assert drive.content == b"second device"


def test_rate_limit_during_restore_keeps_its_error_code(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    drive.before_upload = lambda: drive.upload(b"first device")
    drive.fail_keep, drive.keep_status = True, 429
    with pytest.raises(DriveRestoreFailed) as error:
        GoogleDriveLocation("refresh").write(b"second device", md5(b"base"))
    assert error.value.error_code == "rate_limited"
    assert error.value.replaced_revision == "r1"


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


def test_no_restore_over_upload_during_revision_download(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    drive.before_upload = lambda: drive.upload(b"before")
    urlopen = drive.urlopen

    def upload_during_download(request, **kwargs):
        url = urlparse(request.full_url)
        if url.path == "/drive/v3/files/f/revisions/r1" and url.query == "alt=media":
            drive.upload(b"after")
        return urlopen(request, **kwargs)

    monkeypatch.setattr("urllib.request.urlopen", upload_during_download)
    with pytest.raises(SyncConflict):
        GoogleDriveLocation("refresh").write(b"mine", md5(b"base"))

    assert drive.content == b"after" and drive.uploads == 3
    # The abandoned restore leaves no revision marked.
    assert drive.kept == set()


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


def test_sync_asks_after_a_replaced_upload_it_could_not_restore(monkeypatch):
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
        drive.fail_keep = True

    drive.before_upload = other_device_uploads
    status = sync.run()
    assert status["error_code"] == "gdrive_restore_failed"
    assert drive.revisions[-2][1] == other
    # The next run puts the other device's upload back first, then asks.
    drive.fail_keep = False
    status = sync.run()
    assert status["state"] == "needs_choice"
    assert status["choice_reason"] == "conflict"
    assert drive.content == other
    assert drive.kept == set()
    assert database.get_setting("sync_gdrive_replaced") == ""
    assert database.get_all_games()[0]["rating"] == "9"


def test_restore_is_released_after_a_later_upload(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    drive.before_upload = lambda: drive.upload(b"first device")
    drive.fail_keep = True
    location = GoogleDriveLocation("refresh")
    with pytest.raises(DriveRestoreFailed) as error:
        location.write(b"second device", md5(b"base"))
    drive.fail_keep = False
    drive.kept.add(error.value.replaced_revision)
    _ = drive.upload(b"third device")
    # The file is no longer the replacing upload: nothing is put back.
    assert not location.restore_replaced(
        error.value.replaced_revision, error.value.own_checksum
    )
    assert drive.content == b"third device"
    assert drive.kept == set()


def test_sync_stops_retrying_a_restore_after_the_limit(monkeypatch):
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
        drive.fail_keep = True

    drive.before_upload = other_device_uploads
    assert sync.run()["error_code"] == "gdrive_restore_failed"
    for _ in range(RESTORE_ATTEMPTS - 1):
        assert sync.run()["state"] != "needs_choice"
    # Sync goes on and asks; it does not stay blocked.
    status = sync.run()
    assert status["state"] == "needs_choice"
    assert database.get_setting("sync_gdrive_replaced") == ""


def test_deferred_restore_skips_an_upload_during_its_download(monkeypatch):
    drive = FakeDrive(monkeypatch, b"base")
    drive.before_upload = lambda: drive.upload(b"first device")
    drive.fail_keep = True
    location = GoogleDriveLocation("refresh")
    with pytest.raises(DriveRestoreFailed) as error:
        location.write(b"second device", md5(b"base"))
    drive.fail_keep = False
    replaced = error.value.replaced_revision
    urlopen = drive.urlopen

    def upload_during_download(request, **kwargs):
        url = urlparse(request.full_url)
        if url.path == f"/drive/v3/files/f/revisions/{replaced}" and (
            url.query == "alt=media"
        ):
            _ = drive.upload(b"third device")
        return urlopen(request, **kwargs)

    monkeypatch.setattr("urllib.request.urlopen", upload_during_download)
    # The third device's upload landed while the old revision downloaded:
    # putting the old content back would replace it.
    assert not location.restore_replaced(replaced, error.value.own_checksum)
    assert drive.content == b"third device"
    assert drive.kept == set()


def test_offline_or_rate_limited_runs_use_up_no_restore_attempt(monkeypatch):
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
        drive.fail_keep = True

    drive.before_upload = other_device_uploads
    assert sync.run()["error_code"] == "gdrive_restore_failed"
    record = json.loads(database.get_setting("sync_gdrive_replaced"))
    # Drive answers 429 for longer than the attempt limit.
    drive.keep_status = 429
    for _ in range(RESTORE_ATTEMPTS + 2):
        assert sync.run()["error_code"] == "rate_limited"
    assert json.loads(database.get_setting("sync_gdrive_replaced")) == record
    # Once Drive answers again, the other device's upload is put back.
    drive.fail_keep = False
    status = sync.run()
    assert status["state"] == "needs_choice"
    assert drive.content == other
    assert database.get_setting("sync_gdrive_replaced") == ""
