# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import os
import subprocess
import sys
import time
import types
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

import core
from core import database
from core.api import Api
from core.library_backup import build_sync_backup, encrypt_library_backup
from core.library_sync import (
    DropboxLocation,
    FolderLocation,
    GoogleDriveLocation,
    SyncConflict,
    SyncError,
    SyncManager,
    _gdrive_client_secret,
    timestamp,
    utc_now,
)


@pytest.fixture(autouse=True)
def sync_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "library.db"))
    database.init_db()


class MemoryLocation:
    def __init__(self, backup=None):
        self.data = json.dumps(backup).encode() if backup is not None else None
        self.rev = "1" if self.data else None
        self.reads = self.writes = 0
        self.unavailable = False

    def revision(self):
        if self.unavailable:
            raise SyncError("Unavailable")
        return self.rev

    def read(self):
        self.reads += 1
        return self.data, self.rev

    def write(self, data, expected_revision):
        if expected_revision != self.rev:
            raise SyncConflict()
        self.data = data
        self.writes += 1
        self.rev = str(int(self.rev or 0) + 1)
        return self.rev

    def change(self, backup):
        self.data = json.dumps(backup).encode()
        self.rev = str(int(self.rev or 0) + 1)


def remote_backup(title="Remote", modified="2026-10-09T10:00:00Z"):
    backup = build_sync_backup(modified, "remote-device")
    backup["games"] = (
        [
            {
                "metadata": {"title": title, "developer": "Dev"},
                "user_state": {"rating": "7"},
            }
        ]
        if title
        else []
    )
    return backup


def manager(location):
    database.update_setting("sync_location", "folder")
    return SyncManager(location)


def test_first_push_idle_and_local_change():
    location = MemoryLocation()
    sync = manager(location)
    game_id = database.add_game("Local", "/local.exe")
    assert sync.run()["state"] == "idle"
    assert location.writes == 1
    assert sync.run()["state"] == "idle"
    assert location.reads == 0 and location.writes == 1
    database.update_game(game_id, {"rating": "9"})
    assert sync.local_changed()
    sync.run()
    assert location.writes == 2
    assert json.loads(location.data)["games"][0]["user_state"]["rating"] == "9"


def test_finished_version_round_trips_between_libraries(tmp_path, monkeypatch):
    location = MemoryLocation()
    sync = manager(location)
    game_id = database.add_game("Synced", "/synced.exe", version="0.5")
    database.update_game(game_id, {"play_status": "Completed"})
    assert sync.run()["state"] == "idle"

    pushed = json.loads(location.data)
    assert pushed["games"][0]["user_state"]["finished_version"] == "0.5"

    # A second library pulls the same sync file and receives the value.
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "other-library.db"))
    database.init_db()
    other = manager(location)
    assert other.run()["state"] == "idle"
    game = database.get_all_games()[0]
    assert game["title"] == "Synced"
    assert game["finished_version"] == "0.5"


def test_screenshots_round_trip_between_libraries(tmp_path, monkeypatch):
    location = MemoryLocation()
    sync = manager(location)
    game_id = database.add_game(
        "Shots", "/shots.exe", f95_url="https://f95zone.to/threads/shots.400/"
    )
    conn = database.get_connection()
    conn.execute(
        "UPDATE games SET screenshots = ? WHERE id = ?",
        ('["https://example.com/sync-a.png"]', game_id),
    )
    conn.commit()
    conn.close()
    assert sync.run()["state"] == "idle"

    pushed = json.loads(location.data)
    assert pushed["games"][0]["metadata"]["screenshots"] == [
        "https://example.com/sync-a.png"
    ]

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "other-library.db"))
    database.init_db()
    other = manager(location)
    assert other.run()["state"] == "idle"
    game = database.get_all_games()[0]
    assert game["screenshots"] == ["https://example.com/sync-a.png"]


@pytest.mark.parametrize("choice", ["use-remote", "use-local", "merge-once"])
def test_first_connection_choice(choice):
    database.add_game("Local", "/local.exe", developer="Dev")
    location = MemoryLocation(remote_backup())
    sync = manager(location)
    assert sync.run()["state"] == "needs_choice"
    assert [game["title"] for game in database.get_all_games()] == ["Local"]
    assert location.writes == 0
    sync.run(choice)
    titles = {game["title"] for game in database.get_all_games()}
    assert titles == (
        {"Remote"}
        if choice == "use-remote"
        else {"Local", "Remote"} if choice == "merge-once" else {"Local"}
    )
    assert location.writes == (0 if choice == "use-remote" else 1)


@pytest.mark.parametrize("local_empty", [True, False])
def test_empty_side_automatic(local_empty):
    if not local_empty:
        database.add_game("Local", "/local.exe")
    location = MemoryLocation(remote_backup("Remote" if local_empty else ""))
    sync = manager(location)
    assert sync.run()["state"] == "idle"
    assert len(database.get_all_games()) == 1
    assert location.writes == (0 if local_empty else 1)


@pytest.mark.parametrize("winner", ["remote", "local"])
def test_both_changed_newest_wins(winner):
    location = MemoryLocation(remote_backup())
    sync = manager(location)
    sync.run()
    game = database.get_all_games()[0]
    database.update_game(game["id"], {"rating": "9"})
    database.update_setting("sync_local_modified_at", "2026-10-09T12:00:00Z")
    location.change(
        remote_backup(
            modified=(
                "2026-10-09T13:00:00Z" if winner == "remote" else "2026-10-09T11:00:00Z"
            )
        )
    )
    sync.run()
    assert database.get_all_games()[0]["rating"] == ("7" if winner == "remote" else "9")
    assert location.writes == (0 if winner == "remote" else 1)


def test_remote_only_and_unavailable_leave_local_untouched():
    location = MemoryLocation(remote_backup())
    sync = manager(location)
    sync.run()
    location.change(remote_backup("Replacement", "2026-10-09T11:00:00Z"))
    sync.run()
    assert database.get_all_games()[0]["title"] == "Replacement"
    location.unavailable = True
    before = database.get_all_games()
    assert sync.run()["state"] == "error"
    assert database.get_all_games() == before


def test_encrypted_join_changed_passphrase_and_disable():
    location = MemoryLocation(encrypt_library_backup(remote_backup(), "first"))
    sync = manager(location)
    assert sync.run()["state"] == "needs_passphrase"
    assert location.writes == 0 and database.get_all_games() == []
    assert sync.unlock("wrong")["error_code"] == "decrypt_failed"
    assert sync.unlock("first")["success"]
    sync.run()
    assert database.get_setting("sync_encryption_key")
    assert "first" not in sync.status().values()
    database.update_game(database.get_all_games()[0]["id"], {"rating": "8"})
    location.change(
        encrypt_library_backup(remote_backup(modified="2026-10-09T11:00:00Z"), "second")
    )
    assert sync.run()["state"] == "needs_passphrase"
    assert location.writes == 0
    assert sync.unlock("second")["success"]
    sync.run()
    assert sync.set_passphrase(None)["success"]
    sync.run()
    assert json.loads(location.data)["format"] == "wlib.library_migration"
    assert not database.get_setting("sync_encryption_key")
    assert sync.set_passphrase("new")["success"]
    sync.run()
    assert json.loads(location.data)["format"] == "wlib.encrypted"


def test_folder_atomic_backup_conflict_missing(tmp_path):
    location = FolderLocation(str(tmp_path))
    assert location.revision() is None
    rev = location.write(b"one", None)
    assert location.read() == (b"one", rev)
    location.write(b"two", rev)
    assert (tmp_path / "wlib-library.json.bak").read_bytes() == b"one"
    with pytest.raises(SyncConflict):
        location.write(b"bad", rev)
    assert not (tmp_path / "wlib-library.json.tmp").exists()
    with pytest.raises(SyncError):
        FolderLocation(str(tmp_path / "missing")).revision()
    assert not (tmp_path / "missing").exists()


def response(payload, headers=None):
    result = MagicMock()
    result.__enter__.return_value = result
    result.read.return_value = (
        payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    )
    result.headers = headers or {}
    return result


def http_error(code, payload=None):
    return HTTPError(
        "https://api.dropboxapi.com/2/test",
        code,
        "error",
        {},
        io.BytesIO(json.dumps(payload or {}).encode()),
    )


def test_dropbox_refresh_revision_download_and_upload(monkeypatch, capsys):
    calls = []
    replies = iter(
        [
            response({"access_token": "sentinel-dropbox-access", "expires_in": 300}),
            http_error(409, {"error_summary": "path/not_found/test"}),
            response({"rev": "one"}),
            response(b"file", {"Dropbox-API-Result": '{"rev":"one"}'}),
            response({"rev": "two"}),
            response({"rev": "three"}),
        ]
    )

    def urlopen(request, **kwargs):
        calls.append(request)
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    location = DropboxLocation("sentinel-dropbox-refresh")
    assert location.revision() is None
    assert location.revision() == "one"
    assert location.read() == (b"file", "one")
    assert location.write(b"new", None) == "two"
    assert location.write(b"updated", "two") == "three"
    assert json.loads(calls[-2].get_header("Dropbox-api-arg"))["mode"] == "add"
    assert json.loads(calls[-1].get_header("Dropbox-api-arg"))["mode"] == {
        ".tag": "update",
        "update": "two",
    }
    assert (
        json.loads(calls[-1].get_header("Dropbox-api-arg"))["path"]
        == "/wlib-library.json"
    )
    assert b"client_secret" not in calls[0].data
    output = capsys.readouterr()
    assert "sentinel-dropbox" not in output.out + output.err


def test_dropbox_401_conflict_network(monkeypatch):
    mock = MagicMock(
        side_effect=[
            response({"access_token": "a"}),
            http_error(401),
            response({"access_token": "b"}),
            response({"rev": "r"}),
        ]
    )
    monkeypatch.setattr("urllib.request.urlopen", mock)
    location = DropboxLocation("refresh")
    assert location.revision() == "r"
    mock.side_effect = http_error(409)
    with pytest.raises(SyncConflict):
        location.write(b"file", "r")
    mock.side_effect = URLError("credential should never surface")
    with pytest.raises(SyncError) as error:
        location.revision()
    assert "credential" not in str(error.value)


def test_pkce_state_expiry_exchange_and_disconnect(monkeypatch):
    sync = SyncManager()
    assert sync.connect_dropbox()["error_code"] == "callback_unavailable"
    sync.server_available = True
    url = sync.connect_dropbox()["url"]
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["token_access_type"] == ["offline"]
    assert sync.complete_dropbox("code", "wrong")["error_code"] == "invalid_oauth_state"
    pending = sync._pending
    sync._pending = (pending[0], pending[1], time.monotonic() - 1, pending[3])
    assert sync.complete_dropbox("code", pending[0])["success"] is False
    query = parse_qs(urlparse(sync.connect_dropbox()["url"]).query)
    monkeypatch.setattr(
        "core.library_sync._token_request",
        lambda *args: {"refresh_token": "secret", "access_token": "access"},
    )
    monkeypatch.setattr(DropboxLocation, "account_name", lambda self: "Tester")
    assert sync.complete_dropbox("code", query["state"][0])["success"]
    assert database.get_setting("sync_dropbox_refresh_token") == "secret"
    assert sync.status()["account"] == "Tester"
    assert "secret" not in json.dumps(sync.status())
    monkeypatch.setattr(
        DropboxLocation, "revoke", MagicMock(side_effect=SyncError("offline"))
    )
    sync.disconnect()
    assert not database.get_setting("sync_dropbox_refresh_token")
    assert sync.status()["state"] == "off"


def gdrive_urlopen(monkeypatch, replies):
    calls = []
    queue = iter(replies)

    def urlopen(request, **kwargs):
        calls.append(request)
        result = next(queue)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return calls


def no_gdrive_secret(monkeypatch):
    monkeypatch.delenv("WLIB_GDRIVE_CLIENT_SECRET", raising=False)
    # A generated module on disk is also bound as an attribute of the package.
    monkeypatch.delattr(core, "_gdrive_secret", raising=False)
    monkeypatch.setitem(sys.modules, "core._gdrive_secret", None)


def test_gdrive_client_secret_sources(monkeypatch):
    no_gdrive_secret(monkeypatch)
    assert _gdrive_client_secret() == ""
    module = types.ModuleType("core._gdrive_secret")
    module.CLIENT_SECRET = "from-module"
    monkeypatch.setitem(sys.modules, "core._gdrive_secret", module)
    assert _gdrive_client_secret() == "from-module"
    monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "from-env")
    assert _gdrive_client_secret() == "from-env"


def test_gdrive_secret_module_is_gitignored():
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, ".gitignore"), encoding="utf-8") as ignore:
        assert "core/_gdrive_secret.py" in ignore.read().splitlines()


def test_gdrive_refresh_lookup_download_and_upload(monkeypatch, capsys):
    monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")
    calls = gdrive_urlopen(
        monkeypatch,
        [
            response({"access_token": "access", "expires_in": 300}),
            response({"files": []}),
            response({"files": [{"id": "early", "md5Checksum": "5"}, {"id": "late", "md5Checksum": "9"}]}),
            response({"files": [{"id": "early", "md5Checksum": "5"}]}),
            response(b"file"),
            response({"files": []}),
            response({"md5Checksum": "1"}),
            response({"files": [{"id": "early", "md5Checksum": "1"}]}),
            response({"md5Checksum": "2"}),
        ],
    )
    location = GoogleDriveLocation("refresh")
    assert location.revision() is None
    assert location.revision() == "5"
    assert location.read() == (b"file", "5")
    assert location.write(b"new", None) == "1"
    assert location.write(b"updated", "1") == "2"
    refresh = parse_qs(calls[0].data.decode())
    assert refresh["client_secret"] == ["test-secret"]
    assert refresh["refresh_token"] == ["refresh"]
    assert refresh["grant_type"] == ["refresh_token"]
    lookup = parse_qs(urlparse(calls[1].full_url).query)
    assert lookup["spaces"] == ["appDataFolder"]
    assert lookup["orderBy"] == ["createdTime"]
    # Content checksum, not Drive's `version`, which keeps rising after an upload.
    assert lookup["fields"] == ["files(id,md5Checksum)"]
    assert "trashed=false" in lookup["q"][0] and "wlib-library.json" in lookup["q"][0]
    assert calls[1].get_header("Authorization") == "Bearer access"
    assert "files/early?alt=media" in calls[4].full_url
    create, update = calls[6], calls[8]
    assert create.get_method() == "POST"
    assert "uploadType=multipart" in create.full_url
    assert b'"parents": ["appDataFolder"]' in create.data and b"new" in create.data
    assert create.get_header("Content-type").startswith("multipart/related")
    assert update.get_method() == "PATCH"
    assert "files/early?uploadType=media" in update.full_url
    assert update.data == b"updated"
    assert "fields=md5Checksum" in update.full_url
    assert "fields=md5Checksum" in create.full_url
    output = capsys.readouterr()
    for secret in ("test-secret", "refresh", "access"):
        assert secret not in output.out + output.err


def test_gdrive_conflicts_401_and_errors(monkeypatch):
    monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")
    mock = MagicMock(
        side_effect=[
            response({"access_token": "a"}),
            response({"files": [{"id": "f", "md5Checksum": "7"}]}),
            response({"files": [{"id": "f", "md5Checksum": "7"}]}),
            http_error(401),
            response({"access_token": "b"}),
            response({"files": [{"id": "f", "md5Checksum": "8"}]}),
        ]
    )
    monkeypatch.setattr("urllib.request.urlopen", mock)
    location = GoogleDriveLocation("refresh")
    with pytest.raises(SyncConflict):
        location.write(b"file", "6")
    with pytest.raises(SyncConflict):
        location.write(b"file", None)
    assert location.revision() == "8"
    mock.side_effect = URLError("credential should never surface")
    with pytest.raises(SyncError) as error:
        location.revision()
    assert "credential" not in str(error.value)
    mock.side_effect = http_error(403, {"error": "credential should never surface"})
    with pytest.raises(SyncError) as error:
        location.revision()
    assert "credential" not in str(error.value)
    assert error.value.error_code == "gdrive_request_failed"


def test_gdrive_account_name_and_revoke(monkeypatch):
    calls = gdrive_urlopen(
        monkeypatch,
        [
            response({"access_token": "a"}),
            response({"user": {"displayName": "Name", "emailAddress": "me@example.com"}}),
            response({"user": {"displayName": "Name"}}),
            response({}),
            response(b""),
        ],
    )
    location = GoogleDriveLocation("refresh")
    assert location.account_name() == "me@example.com"
    assert location.account_name() == "Name"
    assert location.account_name() == "Google Drive"
    location.revoke()
    assert calls[-1].full_url == "https://oauth2.googleapis.com/revoke"
    assert parse_qs(calls[-1].data.decode()) == {"token": ["refresh"]}


def test_gdrive_unavailable_reasons(monkeypatch):
    no_gdrive_secret(monkeypatch)
    sync = SyncManager()
    sync.server_available = True
    status = sync.status()
    assert status["gdrive_available"] is False
    assert "credentials" in status["gdrive_unavailable_reason"]
    assert sync.connect_gdrive()["error_code"] == "gdrive_unavailable"
    monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")
    sync.server_available = False
    status = sync.status()
    assert status["gdrive_available"] is False
    assert "callback server" in status["gdrive_unavailable_reason"]
    assert sync.connect_gdrive()["success"] is False
    sync.server_available = True
    assert sync.status()["gdrive_available"] is True


def test_gdrive_signin_state_exchange_and_disconnect(monkeypatch):
    monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")
    sync = SyncManager()
    sync.server_available = True
    query = parse_qs(urlparse(sync.connect_gdrive()["url"]).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/drive.appdata"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8183/gdrive/callback"]
    assert "client_secret" not in query
    assert sync.complete_gdrive("code", "wrong")["error_code"] == "invalid_oauth_state"
    # A Dropbox callback must not complete a pending Google sign-in.
    result = sync.complete_dropbox("code", query["state"][0])
    assert result["error_code"] == "invalid_oauth_state"
    assert sync.status()["connecting"] is True
    monkeypatch.setattr(
        "core.library_sync._token_request", lambda *args: {"access_token": "access"}
    )
    result = sync.complete_gdrive("code", query["state"][0])
    assert result["error_code"] == "gdrive_auth_failed"
    assert not database.get_setting("sync_gdrive_refresh_token")
    query = parse_qs(urlparse(sync.connect_gdrive()["url"]).query)
    monkeypatch.setattr(
        "core.library_sync._token_request",
        lambda *args: {"refresh_token": "secret", "access_token": "access"},
    )
    monkeypatch.setattr(GoogleDriveLocation, "account_name", lambda self: "me@example.com")
    assert sync.complete_gdrive("code", query["state"][0])["success"]
    assert database.get_setting("sync_gdrive_refresh_token") == "secret"
    status = sync.status()
    assert status["location"] == "gdrive" and status["account"] == "me@example.com"
    assert "secret" not in json.dumps(status)
    # Reconnecting shares the old grant, so the old token must not be revoked.
    monkeypatch.setattr(
        "urllib.request.urlopen",
        MagicMock(side_effect=AssertionError("reconnect must not revoke")),
    )
    query = parse_qs(urlparse(sync.connect_gdrive()["url"]).query)
    assert sync.complete_gdrive("code", query["state"][0])["success"]
    calls = gdrive_urlopen(monkeypatch, [response(b"")])
    sync.disconnect()
    assert [call.full_url for call in calls] == ["https://oauth2.googleapis.com/revoke"]
    assert not database.get_setting("sync_gdrive_refresh_token")
    assert not database.get_setting("sync_gdrive_account")
    assert sync.status()["state"] == "off"


def test_exit_push_only_changes_and_off():
    location = MemoryLocation()
    sync = manager(location)
    database.add_game("Local", "/local.exe")
    sync.finish()
    assert location.writes == 1
    sync.finish()
    assert location.writes == 1
    sync.disconnect()
    sync.finish()
    assert location.writes == 1


def test_push_only_never_pulls_first_or_remote_changes():
    location = MemoryLocation(remote_backup())
    sync = manager(location)
    database.update_setting("enable_logging", "true")
    sync.run(push_only=True)
    assert database.get_all_games() == [] and location.writes == 0
    sync.run()
    before = database.get_all_games()
    location.change(remote_backup("Changed remotely", "2026-10-09T13:00:00Z"))
    sync.run(push_only=True)
    assert database.get_all_games() == before and location.writes == 0


def test_api_sync_methods(tmp_path, monkeypatch):
    api = Api()
    location = MemoryLocation(remote_backup())
    api.library_sync._location_override = location
    # Exercise bridge calls synchronously without changing their public contracts.
    monkeypatch.setattr(api.library_sync, "start_run", api.library_sync.run)
    assert api.get_sync_status()["state"] == "off"
    assert api.set_sync_folder(str(tmp_path))["state"] == "idle"
    assert api.sync_now()["success"]
    assert api.resolve_sync_choice("bad")["error_code"] == "invalid_sync_choice"
    assert api.set_sync_passphrase("phrase")["success"]
    assert api.unlock_sync("phrase")["success"]
    api.library_sync.server_available = True
    monkeypatch.setattr(api, "open_in_browser", lambda url: {"success": True})
    assert api.connect_dropbox()["success"]
    monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")
    assert api.connect_gdrive()["success"]
    assert api.disconnect_sync()["state"] == "off"
    assert (
        api.set_sync_folder(str(tmp_path / "missing"))["error_code"]
        == "invalid_sync_folder"
    )
    json.dumps(api.get_sync_status())


@pytest.mark.parametrize("encrypted", [False, True])
def test_two_data_directories_folder_sync(tmp_path, monkeypatch, encrypted):
    folder = tmp_path / "shared"
    folder.mkdir()
    db_a, db_b = tmp_path / "a" / "wlib.db", tmp_path / "b" / "wlib.db"
    db_a.parent.mkdir()
    db_b.parent.mkdir()
    monkeypatch.setattr(database, "DB_PATH", str(db_a))
    database.init_db()
    game_a = database.add_game("Manual", "/a/game.exe")
    deleted_a = database.add_game("Deleted", "/a/deleted.exe")
    a = SyncManager()
    a.set_folder(str(folder))
    a.run()
    if encrypted:
        a.set_passphrase("shared phrase")
        a.run()
    monkeypatch.setattr(database, "DB_PATH", str(db_b))
    database.init_db()
    database.add_game("Manual", "/b/game.exe", launch_mode="native")
    b = SyncManager()
    b.set_folder(str(folder))
    if encrypted:
        assert b.run()["state"] == "needs_passphrase"
        b.unlock("shared phrase")
    assert b.run()["state"] == "needs_choice"
    b.run("use-remote")
    monkeypatch.setattr(database, "DB_PATH", str(db_a))
    database.update_game(game_a, {"rating": "9"})
    database.delete_game(deleted_a)
    a.run()
    monkeypatch.setattr(database, "DB_PATH", str(db_b))
    b.run()
    games = database.get_all_games()
    assert len(games) == 1 and games[0]["rating"] == "9"
    assert games[0]["exe_path"] == "/b/game.exe" and games[0]["launch_mode"] == "native"
    assert (db_b.parent / "sync" / "last-local.json").exists()


def test_collections_and_presets_sync_between_data_directories(tmp_path, monkeypatch):
    folder = tmp_path / "shared"
    folder.mkdir()
    db_a, db_b = tmp_path / "a" / "wlib.db", tmp_path / "b" / "wlib.db"
    db_a.parent.mkdir()
    db_b.parent.mkdir()
    monkeypatch.setattr(database, "DB_PATH", str(db_a))
    database.init_db()
    alpha = database.add_game("Alpha", "/a/alpha.exe")
    beta = database.add_game("Beta", "/a/beta.exe")
    database.create_collection("Backlog", alpha)
    database.create_collection("Best of")
    database.set_game_collections(beta, ["Backlog", "Best of"])
    preset = {"name": "Unity", "filterEngines": ["Unity"], "sortBy": "rating", "sortDir": "desc"}
    assert Api().save_library_filter_presets([preset])["success"]
    assert Api().set_tag_color(" Sandbox ", "green")["success"]
    a = SyncManager()
    a.set_folder(str(folder))
    a.run()

    monkeypatch.setattr(database, "DB_PATH", str(db_b))
    database.init_db()
    b = SyncManager()
    b.set_folder(str(folder))
    b.run()
    members = {game["title"]: game["collections"] for game in database.get_all_games()}
    assert database.get_collections() == ["Backlog", "Best of"]
    assert members == {"Alpha": ["Backlog"], "Beta": ["Backlog", "Best of"]}
    assert [p["name"] for p in database.get_library_filter_presets()] == ["Unity"]
    assert database.get_tag_colors() == {"sandbox": "green"}

    # Rename and change membership on B; A picks up both on its next sync.
    database.rename_collection("Backlog", "To play")
    beta_b = next(g for g in database.get_all_games() if g["title"] == "Beta")
    database.set_game_collections(int(str(beta_b["id"])), ["Best of"])
    b.run()
    monkeypatch.setattr(database, "DB_PATH", str(db_a))
    a.run()
    members = {game["title"]: game["collections"] for game in database.get_all_games()}
    assert database.get_collections() == ["To play", "Best of"]
    assert members == {"Alpha": ["To play"], "Beta": ["Best of"]}


@pytest.mark.parametrize("encrypted", [False, True])
def test_two_home_processes_folder_sync(tmp_path, encrypted):
    folder = tmp_path / "shared-processes"
    folder.mkdir()
    worker = """
import json, sys
from core import database
from core.library_sync import SyncManager
database.init_db()
action, folder, encrypted = sys.argv[1:]
sync = SyncManager()
if action == 'seed':
    database.add_game('Shared', '/a/game.exe')
    database.add_game('To delete', '/a/delete.exe')
    assert sync.set_folder(folder)['success']
    assert sync.run()['success']
    if encrypted == 'yes':
        assert sync.set_passphrase('process test phrase')['success']
        assert sync.run()['success']
elif action == 'join':
    database.add_game('Shared', '/b/game.exe', launch_mode='native')
    assert sync.set_folder(folder)['success']
    result = sync.run()
    if result['state'] == 'needs_passphrase':
        assert sync.unlock('process test phrase')['success']
        result = sync.run()
    assert result['state'] == 'needs_choice'
    assert sync.run('use-remote')['success']
elif action == 'edit':
    for game in database.get_all_games():
        if game['title'] == 'Shared': database.update_game(game['id'], {'rating': '9'})
        else: database.delete_game(game['id'])
    assert sync.run()['success']
else:
    assert sync.run()['success']
    games = database.get_all_games()
    assert len(games) == 1
    assert games[0]['rating'] == '9'
    assert games[0]['exe_path'] == '/b/game.exe'
    assert games[0]['launch_mode'] == 'native'
print(json.dumps({'success': True}))
"""
    for home, action in (("a", "seed"), ("b", "join"), ("a", "edit"), ("b", "verify")):
        environment = {**os.environ, "HOME": str(tmp_path / home), "LOCALAPPDATA": str(tmp_path / home)}
        environment.pop("WLIB_DATA_DIR", None)
        environment.pop("XDG_DATA_HOME", None)
        result = subprocess.run([sys.executable, "-c", worker, action, str(folder), "yes" if encrypted else "no"], env=environment, text=True, capture_output=True, check=True)
        assert json.loads(result.stdout)["success"]


@pytest.mark.parametrize(
    ("provider", "cls", "error_code"),
    [
        ("dropbox", DropboxLocation, "dropbox_auth_failed"),
        ("gdrive", GoogleDriveLocation, "gdrive_auth_failed"),
    ],
)
def test_repeated_401_reports_expired_signin(monkeypatch, provider, cls, error_code):
    if provider == "gdrive":
        monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")
    mock = MagicMock(
        side_effect=[
            response({"access_token": "a", "expires_in": 300}),
            http_error(401),
            response({"access_token": "b", "expires_in": 300}),
            http_error(401),
        ]
    )
    monkeypatch.setattr("urllib.request.urlopen", mock)
    with pytest.raises(SyncError) as error:
        cls("refresh").revision()
    assert error.value.error_code == error_code


def test_folder_concurrent_writes_serialize(tmp_path, monkeypatch):
    import threading

    location = FolderLocation(str(tmp_path))
    rev = location.write(b"base", None)
    real_replace = os.replace
    entered_replace = threading.Event()
    rival_started = threading.Event()

    def hooked_replace(src, dst):
        # Pause the first writer between its final revision check and the
        # rename long enough for an unserialized rival to slip into that window.
        if threading.current_thread().name == "first-writer":
            entered_replace.set()
            assert rival_started.wait(5)
            time.sleep(0.2)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", hooked_replace)
    outcomes: dict[str, object] = {}

    def first() -> None:
        try:
            outcomes["first"] = location.write(b"AAA", rev)
        except Exception as error:
            outcomes["first"] = type(error).__name__

    def rival() -> None:
        assert entered_replace.wait(10)
        rival_started.set()
        try:
            outcomes["rival"] = FolderLocation(str(tmp_path)).write(b"BBB", rev)
        except SyncConflict:
            outcomes["rival"] = "conflict"

    first_thread = threading.Thread(target=first, name="first-writer")
    rival_thread = threading.Thread(target=rival, name="rival-writer")
    first_thread.start()
    rival_thread.start()
    first_thread.join(30)
    rival_thread.join(30)
    # Exactly one writer wins; the loser must see SyncConflict, never a
    # silent clobber (or a crash on the other writer's temp file).
    assert outcomes.get("rival") == "conflict"
    assert isinstance(outcomes.get("first"), str) and outcomes["first"] != "conflict"
    assert (tmp_path / "wlib-library.json").read_bytes() == b"AAA"
    assert list(tmp_path.glob("wlib-library.json.tmp.*")) == []


def test_folder_write_survives_unsupported_flock(tmp_path, monkeypatch):
    import core.library_sync as library_sync

    def no_flock(*_args):
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr(library_sync, "_fcntl_module", types.SimpleNamespace(flock=no_flock, LOCK_EX=2, LOCK_UN=8))
    location = FolderLocation(str(tmp_path))
    assert location.write(b"data", None) == location.revision()
    assert (tmp_path / "wlib-library.json").read_bytes() == b"data"


def test_merge_once_triggers_on_pull_and_records_ambiguous():
    pulled = False

    def on_pull():
        nonlocal pulled
        pulled = True

    remote = remote_backup("Ambiguous Game")
    database.add_game("Ambiguous Game", "/game1.exe")
    database.add_game("Ambiguous Game", "/game2.exe")
    for g in database.get_all_games():
        database.update_game(g["id"], {"developer": "Dev"})

    location = MemoryLocation(remote)
    sync = SyncManager(location, on_pull=on_pull)
    database.update_setting("sync_location", "folder")

    res = sync.run("merge-once")
    assert res["success"]
    assert pulled is True
    assert (
        "more than one local game"
        in (database.get_setting("sync_last_error") or "").lower()
    )


def test_set_passphrase_monotonic_with_clock_skew(monkeypatch):
    location = MemoryLocation()
    sync = manager(location)
    database.add_game("Game", "/path.exe")
    sync.run()
    database.update_setting("sync_last_synced_at", "2026-10-10T12:00:00.000Z")
    database.update_setting("sync_local_modified_at", "2026-10-10T12:00:00.000Z")

    monkeypatch.setattr("core.library_sync.utc_now", lambda: "2026-10-09T00:00:00.000Z")
    res = sync.set_passphrase("newpass")
    assert res["success"]
    local_mod = database.get_setting("sync_local_modified_at")
    assert local_mod > "2026-10-10T12:00:00.000Z"
    assert local_mod == "2026-10-10T12:00:00.001Z"


def test_start_run_status_not_stuck_at_syncing_when_lock_held():
    location = MemoryLocation()
    sync = manager(location)
    import threading

    held = threading.Event()
    release = threading.Event()

    def hold_lock():
        with sync._lock:
            held.set()
            release.wait(timeout=2)

    t = threading.Thread(target=hold_lock)
    t.start()
    held.wait(timeout=1)

    res = sync.start_run()
    assert res["state"] == "syncing"

    release.set()
    t.join()
    if sync._worker:
        sync._worker.join(timeout=2)

    status = sync.status()
    assert status["state"] == "idle"


def test_open_signin_clears_pending_on_browser_failure():
    api = Api()
    api.library_sync.server_available = True
    api.open_in_browser = MagicMock(return_value={"success": False, "error": "No browser"})
    result = api.connect_dropbox()
    assert not result["success"]
    assert result["error_code"] == "browser_failed"
    assert api.library_sync.status()["connecting"] is False



def test_folder_read_missing_is_conflict(tmp_path):
    location = FolderLocation(str(tmp_path))
    with open(tmp_path / "wlib-library.json", "wb") as f:
        _ = f.write(b'{"a": 1}')
    assert location.revision() is not None
    (tmp_path / "wlib-library.json").unlink()
    with pytest.raises(SyncConflict):
        _ = location.read()


def test_folder_delete_race_recovers_via_push(tmp_path):
    folder = tmp_path / "shared"
    folder.mkdir()
    sync = SyncManager()
    sync.set_folder(str(folder))
    database.add_game("Local", "/local.exe")
    assert sync.run()["state"] == "idle"
    last_rev = database.get_setting("sync_last_revision")
    assert last_rev
    real = FolderLocation(str(folder))
    # Externally replace with newer remote content so next run must read.
    new_doc = remote_backup("Remote", "2026-10-10T10:00:00Z")
    rev_after_external = real.write(json.dumps(new_doc).encode(), real.revision())
    assert rev_after_external != last_rev

    class DeleteRace:
        def __init__(self, inner: FolderLocation) -> None:
            self.inner = inner
            self.deleted = False

        def revision(self):
            rev = self.inner.revision()
            if not self.deleted:
                self.deleted = True
                (folder / "wlib-library.json").unlink()
            return rev

        def read(self):
            return self.inner.read()

        def write(self, data, expected_revision):
            return self.inner.write(data, expected_revision)

    sync._location_override = DeleteRace(FolderLocation(str(folder)))
    result = sync.run()
    assert result["state"] == "idle"
    assert result["success"]
    assert (folder / "wlib-library.json").exists()


def test_dropbox_download_not_found_is_conflict(monkeypatch):
    def fake_urlopen(request, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "oauth2" in url or "api.dropboxapi.com/oauth2" in url:
            return response({"access_token": "a", "expires_in": 300})
        raise http_error(409, {"error_summary": "path/not_found/.."})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(SyncConflict):
        _ = DropboxLocation("refresh").read()


def test_dropbox_download_bad_json_is_request_failed(monkeypatch):
    def fake_urlopen(request, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "oauth2" in url or "token" in url:
            return response({"access_token": "a", "expires_in": 300})
        raise HTTPError(
            "https://content.dropboxapi.com/2/files/download",
            409,
            "error",
            {},
            io.BytesIO(b"<html>not json</html>"),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(SyncError) as exc:
        _ = DropboxLocation("refresh").read()
    assert exc.value.error_code == "dropbox_request_failed"


def test_gdrive_404_is_conflict(monkeypatch):
    monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")

    def fake_urlopen(request, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "oauth2.googleapis.com/token" in url:
            return response({"access_token": "a", "expires_in": 300})
        if "drive/v3/files?" in url:
            return response({"files": [{"id": "f", "md5Checksum": "abc"}]})
        raise http_error(404, {"error": "not found"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(SyncConflict):
        _ = GoogleDriveLocation("refresh").read()
    with pytest.raises(SyncConflict):
        _ = GoogleDriveLocation("refresh").write(b"data", "abc")


def test_token_non_json_is_auth_failed(monkeypatch):
    monkeypatch.setattr(
        "core.library_sync._form_post", lambda url, params: b"<html>nope</html>"
    )
    from core.library_sync import _token_request

    with pytest.raises(SyncError) as exc:
        _ = _token_request(
            "https://example.com/token", {"a": "b"}, "Dropbox", "dropbox_auth_failed"
        )
    assert exc.value.error_code == "dropbox_auth_failed"


def test_choice_when_not_first_is_rejected():
    location = MemoryLocation(remote_backup())
    sync = manager(location)
    assert sync.run()["state"] == "idle"
    result = sync.run("use-remote")
    assert result["success"] is False
    assert result["error_code"] == "invalid_sync_choice"


def test_api_resolve_choice_when_not_needed():
    api = Api()
    location = MemoryLocation(remote_backup())
    api.library_sync._location_override = location
    # Make Api synchronous for the test without changing contracts.
    api.library_sync.start_run = api.library_sync.run  # type: ignore[method-assign]
    database.update_setting("sync_location", "folder")
    assert api.sync_now()["state"] == "idle"
    result = api.resolve_sync_choice("use-remote")
    assert result["success"] is False
    assert result["error_code"] == "invalid_sync_choice"


def test_first_use_remote_with_empty_remote_wipes_local():
    database.add_game("Local", "/local.exe")
    location = MemoryLocation(remote_backup("", "2026-10-09T10:00:00Z"))
    sync = manager(location)
    result = sync.run("use-remote")
    assert result["success"]
    assert database.get_all_games() == []
    assert location.writes == 0


def test_gdrive_404_outside_file_request_is_request_failed(monkeypatch):
    monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")

    def fake_urlopen(request, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "oauth2.googleapis.com/token" in url:
            return response({"access_token": "a", "expires_in": 300})
        raise http_error(404, {"error": "not found"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(SyncError) as exc:
        _ = GoogleDriveLocation("refresh").revision()
    assert exc.value.error_code == "gdrive_request_failed"


def test_journal_push_pull_replaces_matched_sessions_and_skip_marks_local_change():
    location = MemoryLocation()
    sync = manager(location)
    game_id = database.add_game("Journal", "/game", version="0.7")
    first = database.start_play_session(game_id, "0.7")
    database.record_play_tick(game_id, first, 300, True)
    assert sync.run()["state"] == "idle"
    pushed = json.loads(location.data)
    state = pushed["games"][0]["user_state"]
    assert state["last_played_version"] == "0.7"
    assert len(state["play_sessions"]) == 1
    database.update_game(game_id, {"skipped_version": "0.8"})
    assert sync.local_changed()
    sync.run()
    pushed = json.loads(location.data)
    assert pushed["games"][0]["user_state"]["skipped_version"] == "0.8"
    state = pushed["games"][0]["user_state"]
    state["play_sessions"] *= 2
    state["last_played_version"] = "0.6"
    pushed["sync"]["modified_at"] = "2099-01-01T00:00:00Z"
    location.change(pushed)
    assert sync.run()["state"] == "idle"
    assert len(database.get_play_sessions(game_id)) == 2
    assert database.get_all_games()[0]["last_played_version"] == "0.6"


def test_save_path_override_never_syncs_and_survives_a_pull(tmp_path, monkeypatch):
    location = MemoryLocation()
    sync = manager(location)
    url = "https://f95zone.to/threads/saver.500/"
    game_id = database.add_game("Saver", "/saver.exe", f95_url=url)
    database.update_game(game_id, {"save_path_override": "/secret/saves"})
    assert "save_path_override" not in database.SYNC_GAME_COLUMNS
    assert sync.run()["state"] == "idle"
    assert "/secret/saves" not in location.data.decode()
    assert "save_path_override" not in location.data.decode()

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "other-library.db"))
    database.init_db()
    other = manager(location)
    assert other.run()["state"] == "idle"
    pulled = database.get_all_games()[0]
    assert pulled["title"] == "Saver" and pulled["save_path_override"] == ""
    database.update_game(pulled["id"], {"save_path_override": "/other/saves"})

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "library.db"))
    database.update_game(game_id, {"rating": "9"})
    assert sync.run()["state"] == "idle"

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "other-library.db"))
    assert other.run()["state"] == "idle"
    after = database.get_all_games()[0]
    assert after["rating"] == "9"
    assert after["save_path_override"] == "/other/saves"


def test_installed_catalog_stays_out_of_sync(tmp_path):
    database.add_game("Synced", "/synced.exe")
    database.update_setting("catalog_enabled", "1")
    (tmp_path / "catalog.db").write_bytes(b"CATALOG_DB_CONTENT")
    (tmp_path / "catalog.json").write_text(json.dumps({"last_error": "CATALOG_STATUS"}))
    location = MemoryLocation()
    manager(location).run()
    text = location.data.decode()
    assert "Synced" in text
    assert "catalog" not in text.lower()
    assert "CATALOG_DB_CONTENT" not in text and "CATALOG_STATUS" not in text


def test_newer_sync_replaces_matched_links_and_link_edits_mark_changed():
    game_id = database.add_game("Original", "")
    database.replace_game_links(game_id, "page", [{"url": "https://vndb.org/v1"}])
    remote = remote_backup("Renamed", modified="2099-01-01T00:00:00Z")
    remote["games"][0]["metadata"]["links"] = [
        {"source": "vndb", "id": "v1", "url": "https://vndb.org/v1"},
        {
            "source": "steam",
            "id": "123",
            "url": "https://store.steampowered.com/app/123/",
        },
    ]
    location = MemoryLocation(remote)
    sync = manager(location)
    assert sync.run()["state"] == "needs_choice"
    assert sync.run("use-remote")["state"] == "idle"
    assert len(database.get_all_games()) == 1
    assert len(database.get_game_links(game_id)) == 2
    assert not sync.local_changed()
    database.replace_game_links(game_id, "f95_post", [{"url": "https://vndb.org/v2"}])
    assert sync.local_changed()


def test_auto_push_due_waits_for_quiet_but_not_for_a_future_change_time():
    location = MemoryLocation()
    sync = manager(location)
    database.add_game("Clock", "/clock.exe")
    assert sync.run()["state"] == "idle"

    database.update_setting("sync_last_synced_at", "2000-01-01T00:00:00Z")
    database.update_setting("sync_local_modified_at", utc_now())
    assert sync.local_changed()
    assert sync._auto_push_due() is False

    # A pull from a device with a fast clock stores a change time ahead of
    # this machine's clock; auto-sync must not stall until it catches up.
    database.update_setting("sync_local_modified_at", "2099-01-01T00:00:00Z")
    assert timestamp(database.get_setting("sync_local_modified_at")) > time.time()
    assert sync._auto_push_due() is True


def test_reselecting_the_same_folder_keeps_sync_state(tmp_path):
    sync = SyncManager()
    assert sync.set_folder(str(tmp_path))["success"]
    database.update_settings(
        {
            "sync_last_synced_at": "2026-10-01T00:00:00.000Z",
            "sync_last_revision": "rev",
            "sync_encryption_key": "a2V5",
            "sync_encryption_salt": "c2FsdA==",
        }
    )

    assert sync.set_folder(str(tmp_path))["success"]
    assert database.get_setting("sync_last_revision") == "rev"
    assert database.get_setting("sync_last_synced_at") == "2026-10-01T00:00:00.000Z"
    assert database.get_setting("sync_encryption_key") == "a2V5"

    other = tmp_path / "other"
    other.mkdir()
    assert sync.set_folder(str(other))["success"]
    assert database.get_setting("sync_last_revision") == ""
    assert database.get_setting("sync_encryption_key") == ""
