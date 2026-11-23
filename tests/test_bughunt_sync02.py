# pyright: reportMissingImports=false, reportPrivateUsage=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from core import database, steam_sync
from core.library_backup import build_sync_backup
from core.library_sync import (
    DropboxLocation,
    SyncConflict,
    SyncError,
    SyncManager,
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

    def revision(self):
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


# --- Bug 1: Cancelling cloud sign-in must not break the active sync connection ---


@pytest.mark.parametrize("provider", ["dropbox", "gdrive"])
def test_cancelled_cloud_signin_keeps_active_folder_syncing(
    tmp_path, monkeypatch, provider
):
    folder = tmp_path / "sync-folder"
    folder.mkdir()
    sync = SyncManager()
    sync.server_available = True
    if provider == "gdrive":
        monkeypatch.setenv("WLIB_GDRIVE_CLIENT_SECRET", "test-secret")

    assert sync.set_folder(str(folder))["state"] == "idle"
    database.add_game("Local Game", "/local.exe")
    assert sync.run()["state"] == "idle"
    next_before = sync._next_full_run
    failures_before = sync._failures

    start = sync.connect_dropbox() if provider == "dropbox" else sync.connect_gdrive()
    assert start["success"] is True
    state = parse_qs(urlparse(str(start["url"])).query)["state"][0]

    # User declines consent: OAuth callback arrives with an empty code.
    result = (
        sync.complete_dropbox("", state)
        if provider == "dropbox"
        else sync.complete_gdrive("", state)
    )
    assert result["success"] is False
    assert result["error_code"] == f"{provider}_auth_failed"
    assert "cancelled" in str(result["error"]).lower()

    # Active folder connection stays healthy and unblocked.
    status = sync.status()
    assert status["state"] == "idle"
    assert status["location"] == "folder"
    assert status["error_code"] == ""
    assert status["last_error"] == ""
    assert status["connecting"] is False
    assert status["signin_error"] == result["error"]
    assert result["signin_error"] == result["error"]
    assert database.get_setting("sync_last_error") == ""
    assert sync._failures == failures_before
    assert sync._next_full_run == next_before

    # The folder watcher still runs both scheduled pushes and full syncs.
    started: list[bool] = []
    monkeypatch.setattr(
        sync,
        "start_run",
        lambda choice=None, *, push_only=False: started.append(push_only),
    )
    monkeypatch.setattr(sync, "_auto_push_due", lambda: True)
    sync._watch_tick()
    assert started == [True]

    sync._next_full_run = 0.0
    sync._watch_tick()
    assert started == [True, False]


def test_cancelled_resignin_preserves_expired_grant_and_signin_error(monkeypatch):
    sync = SyncManager()
    sync.server_available = True
    database.update_settings(
        {
            "sync_location": "dropbox",
            "sync_dropbox_refresh_token": "expired-token",
            "sync_dropbox_account": "Tester",
            "sync_last_synced_at": "2026-10-09T10:00:00Z",
            "sync_last_revision": "rev-1",
        }
    )
    expired = SyncError(
        "Dropbox sign-in expired. Connect again.", "dropbox_auth_failed"
    )
    monkeypatch.setattr(DropboxLocation, "revision", MagicMock(side_effect=expired))

    failed = sync.run()
    assert failed["state"] == "error"
    assert failed["error_code"] == "dropbox_auth_failed"

    # Active expired grant stops automatic retries.
    started: list[bool] = []
    monkeypatch.setattr(
        sync,
        "start_run",
        lambda choice=None, *, push_only=False: started.append(push_only),
    )
    sync._next_full_run = 0.0
    sync._watch_tick()
    assert started == []

    # User clicks "Sign in again" and cancels: Sign in again stays available,
    # watcher stays paused on the expired grant, and sign-in failure is reported.
    url = str(sync.connect_dropbox()["url"])
    state = parse_qs(urlparse(url).query)["state"][0]
    cancelled = sync.complete_dropbox("", state)
    assert cancelled["success"] is False
    assert cancelled["error_code"] == "dropbox_auth_failed"

    status = sync.status()
    assert status["state"] == "error"
    assert status["error_code"] == "dropbox_auth_failed"
    assert status["last_error"] == "Dropbox sign-in expired. Connect again."
    assert status["signin_error"] == cancelled["error"]

    sync._next_full_run = 0.0
    sync._watch_tick()
    assert started == []

    # A network failure during "Sign in again" must not overwrite the expired-grant
    # error_code and hide the "Sign in again" button.
    url = str(sync.connect_dropbox()["url"])
    assert sync.status()["signin_error"] == ""
    state = parse_qs(urlparse(url).query)["state"][0]
    monkeypatch.setattr(
        "core.library_sync._token_request",
        MagicMock(
            side_effect=SyncError(
                "Dropbox could not be reached. wLib will try again later."
            )
        ),
    )
    offline = sync.complete_dropbox("code", state)
    assert offline["success"] is False
    assert offline["error_code"] == "location_unavailable"
    status = sync.status()
    assert status["state"] == "error"
    assert status["error_code"] == "dropbox_auth_failed"
    assert status["signin_error"] == offline["error"]


def test_failed_signin_while_sync_off_reports_signin_error():
    sync = SyncManager()
    sync.server_available = True
    url = str(sync.connect_dropbox()["url"])
    state = parse_qs(urlparse(url).query)["state"][0]

    result = sync.complete_dropbox("", state)
    assert result["success"] is False
    assert result["error_code"] == "dropbox_auth_failed"

    status = sync.status()
    assert status["state"] == "off"
    assert status["signin_error"] == result["error"]
    assert status["last_error"] == ""
    assert not database.get_setting("sync_last_error")

    # Starting a new sign-in clears the previous sign-in failure.
    assert sync.connect_dropbox()["success"] is True
    assert sync.status()["signin_error"] == ""


# --- Bug 2: Push-only first connection must not pull remote settings ---


def test_push_only_first_connection_does_not_overwrite_local_settings_when_both_empty():
    database.update_setting("enable_logging", "false")
    remote = build_sync_backup("2026-10-09T10:00:00Z", "remote-device")
    assert remote["games"] == []
    assert remote["settings"]["general"]["enable_logging"] == "false"

    # Local library is also empty, with a different general setting.
    database.update_setting("enable_logging", "true")

    pulled = False

    def on_pull():
        nonlocal pulled
        pulled = True

    location = MemoryLocation(remote)
    database.update_setting("sync_location", "folder")
    sync = SyncManager(location, on_pull=on_pull)

    result = sync.run(push_only=True)
    assert result["success"] is True
    assert result["state"] == "idle"
    assert pulled is False
    assert location.writes == 0
    assert database.get_setting("enable_logging") == "true"
    assert not database.get_setting("sync_last_synced_at")

    # A full sync still imports the remote settings on first connection.
    full = sync.run()
    assert full["success"] is True
    assert full["state"] == "idle"
    assert pulled is True
    assert database.get_setting("enable_logging") == "false"
    assert database.get_setting("sync_last_synced_at") == "2026-10-09T10:00:00Z"


def test_push_only_first_connection_still_pushes_nonempty_local_to_empty_remote():
    remote = build_sync_backup("2026-10-09T10:00:00Z", "remote-device")
    assert remote["games"] == []
    database.add_game("Local", "/local.exe")

    location = MemoryLocation(remote)
    database.update_setting("sync_location", "folder")
    sync = SyncManager(location)

    assert sync.run(push_only=True)["state"] == "idle"
    assert location.writes == 1
    assert [g["metadata"]["title"] for g in json.loads(location.data)["games"]] == [
        "Local"
    ]


# --- Bug 3: Expired Steam timer must not clear a newer timer ---


def test_superseded_steam_timer_does_not_clear_newer_timer_or_retry_flag(
    monkeypatch, tmp_path
):
    timers = []

    class FakeTimer:
        def __init__(self, delay, target, kwargs=None):
            self.delay = delay
            self.target = target
            self.kwargs = kwargs or {}
            self.cancelled = False
            self.daemon = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(steam_sync.threading, "Timer", FakeTimer)
    monkeypatch.setattr(
        steam_sync, "environment", lambda: {"root": str(tmp_path), "account_id": "123"}
    )
    monkeypatch.setattr(steam_sync.database, "get_setting", lambda _: "")
    calls: list[bool] = []

    def fake_sync(_root, _account, _script, *, retry_removed=False):
        calls.append(retry_removed)
        return {"success": True, "pending": 1}

    monkeypatch.setattr(steam_sync, "sync_games", fake_sync)

    worker = steam_sync.SteamSync(Path("/icon"))

    # Wrap _lock so we know deterministically when T1 is waiting on it.
    real_lock = worker._lock
    waiting = threading.Event()

    class HookedLock:
        def __enter__(self):
            if threading.current_thread().name == "stale-timer":
                waiting.set()
            real_lock.acquire()
            return self

        def __exit__(self, *exc):
            real_lock.release()

    worker._lock = HookedLock()  # type: ignore[assignment]

    worker.schedule(delay=5)
    assert len(timers) == 1
    stale_timer = timers[0]

    # Hold the run lock while the first timer fires and blocks on it.
    real_lock.acquire()
    stale_thread = threading.Thread(
        target=lambda: stale_timer.target(**stale_timer.kwargs),
        name="stale-timer",
        daemon=True,
    )
    stale_thread.start()
    assert waiting.wait(timeout=5)
    # Give the stale thread a moment to enter real_lock.acquire().
    time.sleep(0.05)

    # A newer manual retry arrives before the stale callback acquires the lock.
    worker.schedule(delay=5, retry_removed=True)
    assert stale_timer.cancelled is True
    assert len(timers) == 2
    newer_timer = timers[1]

    # Release the run lock so the stale callback runs to completion.
    real_lock.release()
    stale_thread.join(timeout=5)
    assert not stale_thread.is_alive()

    # The stale callback must not have run sync_games or cleared the newer timer.
    assert calls == []
    assert worker._timer is newer_timer
    assert worker._retry_removed is True

    # Polling only_if_idle must see the newer timer as still pending.
    worker.schedule(delay=30, only_if_idle=True)
    assert len(timers) == 2

    # A subsequent debounced edit cancels newer_timer and preserves retry_removed.
    worker.schedule(delay=5)
    assert newer_timer.cancelled is True
    assert len(timers) == 3
    latest_timer = timers[2]

    # Running the latest timer executes once with retry_removed=True.
    latest_timer.target(**latest_timer.kwargs)
    assert calls == [True]
    # Because pending=1, a 30s poll timer is now scheduled cleanly.
    assert len(timers) == 4
    assert timers[3].delay == 30
    worker.stop()
