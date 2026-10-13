# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import datetime, timezone
from http.client import HTTPResponse
from pathlib import Path
from typing import Protocol, cast, final

from core import database
from core.library_backup import (
    ENCRYPTED_FORMAT,
    SYNC_SECTIONS,
    BackupValidationError,
    apply_library_backup,
    build_sync_backup,
    decrypt_library_backup,
    derive_backup_key,
    encrypt_library_backup,
    encrypted_backup_salt,
    validate_library_backup,
)

SYNC_FILENAME = "wlib-library.json"
DROPBOX_APP_KEY = "l5ybaj3u18tq492"
DROPBOX_REDIRECT_URI = "http://127.0.0.1:8183/dropbox/callback"
# App-folder tokens already root paths at /Apps/wLib.
DROPBOX_PATH = "/wlib-library.json"
DROPBOX_SCOPES = (
    "files.content.read files.content.write files.metadata.read account_info.read"
)
DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
# Desktop client IDs are public. The matching secret is not; see _gdrive_client_secret.
GDRIVE_CLIENT_ID = (
    "176038770966-nglo31o6lcqd3l31klmvrbl4fisoo3na.apps.googleusercontent.com"
)
GDRIVE_REDIRECT_URI = "http://127.0.0.1:8183/gdrive/callback"
GDRIVE_SCOPE = "https://www.googleapis.com/auth/drive.appdata"
GDRIVE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GDRIVE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GDRIVE_API = "https://www.googleapis.com/drive/v3/"
GDRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/"
CALLBACK_UNAVAILABLE = "The local callback server on port 8183 is unavailable."


class SyncError(Exception):
    def __init__(self, message: str, error_code: str = "location_unavailable") -> None:
        super().__init__(message)
        self.error_code: str = error_code


class SyncConflict(SyncError):
    def __init__(self) -> None:
        super().__init__(
            "The sync file changed during this sync. Try again.", "sync_conflict"
        )


class SyncLocation(Protocol):
    def revision(self) -> str | None: ...
    def read(self) -> tuple[bytes, str]: ...
    def write(self, data: bytes, expected_revision: str | None) -> str: ...


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def timestamp(value: str) -> float:
    if not value:
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Missing timezone")
        return parsed.timestamp()
    except ValueError as exc:
        raise SyncError(
            "The sync file has an invalid change time.", "invalid_sync_file"
        ) from exc


def _folder_revision(stat: os.stat_result) -> str:
    # mtime ticks coarsely (ms on Linux); the inode changes on every os.replace.
    return f"{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"


try:
    import fcntl as _fcntl_module
except ImportError:  # Windows has no fcntl; threads still serialize below.
    _fcntl_module = None


_folder_write_locks: dict[str, threading.Lock] = {}
_folder_write_locks_guard = threading.Lock()


def _folder_write_lock(folder: Path) -> threading.Lock:
    key = str(folder)
    with _folder_write_locks_guard:
        existing = _folder_write_locks.get(key)
        if existing is None:
            existing = threading.Lock()
            _folder_write_locks[key] = existing
        return existing


class FolderLocation:
    def __init__(self, folder: str) -> None:
        self.folder: Path = Path(os.path.expanduser(folder))
        self.path: Path = self.folder / SYNC_FILENAME

    def _check_folder(self) -> None:
        if not self.folder.is_dir():
            raise SyncError("The sync folder is unavailable.")

    def revision(self) -> str | None:
        self._check_folder()
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        return _folder_revision(stat)

    def read(self) -> tuple[bytes, str]:
        self._check_folder()
        with self.path.open("rb") as source:
            stat = os.fstat(source.fileno())
            data = source.read()
        revision = _folder_revision(stat)
        if self.revision() != revision:
            raise SyncConflict()
        return data, revision

    def write(self, data: bytes, expected_revision: str | None) -> str:
        self._check_folder()
        # Serialize writers for one folder: the revision check below is only
        # meaningful if no other writer can slip in before os.replace.
        with _folder_write_lock(self.folder):
            return self._write_exclusive(data, expected_revision)

    def _write_exclusive(self, data: bytes, expected_revision: str | None) -> str:
        # The threading lock above only covers this process. A second wLib
        # process (or a manual edit) uses the same folder, so hold an
        # advisory lock file while checking and replacing.
        if _fcntl_module is None:
            return self._write_file(data, expected_revision)
        lock_path = self.path.with_name(SYNC_FILENAME + ".lock")
        with open(lock_path, "a+b") as guard:
            try:
                _fcntl_module.flock(guard.fileno(), _fcntl_module.LOCK_EX)
            except OSError:
                # Some FUSE/network filesystems do not support flock; keep the
                # in-process serialization rather than failing every write.
                return self._write_file(data, expected_revision)
            try:
                return self._write_file(data, expected_revision)
            finally:
                _fcntl_module.flock(guard.fileno(), _fcntl_module.LOCK_UN)

    def _write_file(self, data: bytes, expected_revision: str | None) -> str:
        if self.revision() != expected_revision:
            raise SyncConflict()
        # Unique per writer: concurrent writers must never share (and delete)
        # each other's temporary file.
        suffix = f"{os.getpid()}.{threading.get_ident():x}.{secrets.token_hex(4)}"
        temporary = self.path.with_name(f"{SYNC_FILENAME}.tmp.{suffix}")
        try:
            with temporary.open("wb") as target:
                _ = target.write(data)
                target.flush()
                os.fsync(target.fileno())
            if self.revision() != expected_revision:
                raise SyncConflict()
            if expected_revision is not None:
                _ = shutil.copy2(self.path, self.path.with_name(SYNC_FILENAME + ".bak"))
            if self.revision() != expected_revision:
                raise SyncConflict()
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        revision = self.revision()
        if revision is None:
            raise SyncConflict()
        return revision


def _json_object(data: bytes) -> dict[str, object]:
    try:
        value = cast(object, json.loads(data))
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        return cast(dict[str, object], value)
    except (ValueError, UnicodeError) as exc:
        raise SyncError(
            "The server or sync file returned invalid JSON.", "invalid_sync_file"
        ) from exc


def _gdrive_client_secret() -> str:
    secret = os.environ.get("WLIB_GDRIVE_CLIENT_SECRET", "")
    if secret:
        return secret
    try:
        # Written by the build scripts and gitignored. It must stay a plain import
        # statement: PyInstaller only bundles modules it finds that way.
        from core import _gdrive_secret  # pyright: ignore[reportMissingModuleSource]
    except ImportError:
        return ""
    return _gdrive_secret.CLIENT_SECRET


def _pkce() -> tuple[str, str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return verifier, challenge, secrets.token_urlsafe(32)


def _form_post(url: str, parameters: Mapping[str, str]) -> bytes:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(parameters).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with cast(
        HTTPResponse,
        urllib.request.urlopen(
            request, context=ssl.create_default_context(), timeout=8
        ),
    ) as response:
        return response.read()


def _token_request(
    url: str, parameters: Mapping[str, str], provider: str, error_code: str
) -> dict[str, object]:
    try:
        return _json_object(_form_post(url, parameters))
    except (OSError, urllib.error.URLError) as exc:
        raise SyncError(
            f"{provider} sign-in could not be completed. Connect again.", error_code
        ) from exc


class _OAuthLocation:
    provider: str = ""
    auth_error: str = ""
    _refresh_token: str
    _access_token: str
    _expires_at: float

    def __init__(self, refresh_token: str) -> None:
        self._refresh_token = refresh_token
        self._access_token = ""
        self._expires_at = 0.0

    def accept_token(self, payload: Mapping[str, object]) -> None:
        self._access_token = str(payload.get("access_token") or "")
        try:
            self._expires_at = (
                time.monotonic() + float(str(payload.get("expires_in", 3600))) - 30
            )
        except ValueError as exc:
            raise SyncError(
                f"{self.provider} returned an invalid token response.",
                self.auth_error,
            ) from exc
        if not self._access_token:
            raise SyncError(
                f"{self.provider} returned no access token.", self.auth_error
            )


@final
class DropboxLocation(_OAuthLocation):
    provider: str = "Dropbox"
    auth_error: str = "dropbox_auth_failed"

    def _refresh(self) -> None:
        self.accept_token(
            _token_request(
                DROPBOX_TOKEN_URL,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": DROPBOX_APP_KEY,
                },
                self.provider,
                self.auth_error,
            )
        )

    def _request(
        self,
        endpoint: str,
        *,
        arguments: Mapping[str, object] | None = None,
        data: bytes | None = None,
    ) -> tuple[bytes, str]:
        for attempt in range(2):
            if not self._access_token or time.monotonic() >= self._expires_at:
                self._refresh()
            content = endpoint in ("files/download", "files/upload")
            base = (
                "https://content.dropboxapi.com/2/"
                if content
                else "https://api.dropboxapi.com/2/"
            )
            headers = {"Authorization": f"Bearer {self._access_token}"}
            if content:
                headers["Dropbox-API-Arg"] = json.dumps(
                    arguments or {}, ensure_ascii=True
                )
                if data is not None:
                    headers["Content-Type"] = "application/octet-stream"
                body = data
            else:
                headers["Content-Type"] = "application/json"
                body = json.dumps(arguments).encode("utf-8")
            request = urllib.request.Request(
                base + endpoint, data=body, headers=headers, method="POST"
            )
            try:
                with cast(
                    HTTPResponse,
                    urllib.request.urlopen(
                        request, context=ssl.create_default_context(), timeout=8
                    ),
                ) as response:
                    return response.read(), str(
                        response.headers.get("Dropbox-API-Result", "")
                    )
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    if attempt == 0:
                        self._access_token = ""
                        continue
                    # The refresh itself was rejected: the grant is gone, so a
                    # generic request error would mislead. Report auth failure.
                    raise SyncError(
                        f"{self.provider} sign-in expired. Connect again.",
                        self.auth_error,
                    ) from exc
                if exc.code == 409:
                    if endpoint == "files/upload":
                        raise SyncConflict() from exc
                    error = _json_object(exc.read())
                    if endpoint == "files/get_metadata" and str(
                        error.get("error_summary", "")
                    ).startswith("path/not_found"):
                        return b"null", ""
                raise SyncError(
                    "Dropbox rejected the request. Check the connection and permissions.",
                    "dropbox_request_failed",
                ) from exc
            except (OSError, urllib.error.URLError) as exc:
                raise SyncError("Dropbox is unavailable. Try again later.") from exc
        raise SyncError(
            "Dropbox sign-in expired. Connect again.", "dropbox_auth_failed"
        )

    def revision(self) -> str | None:
        data, _ = self._request("files/get_metadata", arguments={"path": DROPBOX_PATH})
        if data == b"null":
            return None
        revision = str(_json_object(data).get("rev") or "")
        if not revision:
            raise SyncError(
                "Dropbox returned no file revision.", "dropbox_request_failed"
            )
        return revision

    def read(self) -> tuple[bytes, str]:
        data, metadata = self._request(
            "files/download", arguments={"path": DROPBOX_PATH}
        )
        revision = str(_json_object(metadata.encode("utf-8")).get("rev") or "")
        if not revision:
            raise SyncError(
                "Dropbox returned no file revision.", "dropbox_request_failed"
            )
        return data, revision

    def write(self, data: bytes, expected_revision: str | None) -> str:
        mode: object = (
            {".tag": "update", "update": expected_revision}
            if expected_revision
            else "add"
        )
        result, _ = self._request(
            "files/upload",
            arguments={
                "path": DROPBOX_PATH,
                "mode": mode,
                "autorename": False,
                "strict_conflict": True,
                "mute": True,
            },
            data=data,
        )
        revision = str(_json_object(result).get("rev") or "")
        if not revision:
            raise SyncError(
                "Dropbox returned no upload revision.", "dropbox_request_failed"
            )
        return revision

    def account_name(self) -> str:
        data, _ = self._request("users/get_current_account")
        name = _json_object(data).get("name")
        return (
            str(cast(dict[str, object], name).get("display_name") or "Dropbox")
            if isinstance(name, dict)
            else "Dropbox"
        )

    def revoke(self) -> None:
        _ = self._request("auth/token/revoke")


@final
class GoogleDriveLocation(_OAuthLocation):
    provider: str = "Google"
    auth_error: str = "gdrive_auth_failed"

    def __init__(self, refresh_token: str) -> None:
        super().__init__(refresh_token)
        self._file_id: str = ""

    def _refresh(self) -> None:
        self.accept_token(
            _token_request(
                GDRIVE_TOKEN_URL,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": GDRIVE_CLIENT_ID,
                    "client_secret": _gdrive_client_secret(),
                },
                self.provider,
                self.auth_error,
            )
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        content_type: str = "application/json",
    ) -> bytes:
        for attempt in range(2):
            if not self._access_token or time.monotonic() >= self._expires_at:
                self._refresh()
            headers = {"Authorization": f"Bearer {self._access_token}"}
            if data is not None:
                headers["Content-Type"] = content_type
            request = urllib.request.Request(
                url, data=data, headers=headers, method=method
            )
            try:
                with cast(
                    HTTPResponse,
                    urllib.request.urlopen(
                        request, context=ssl.create_default_context(), timeout=8
                    ),
                ) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    if attempt == 0:
                        self._access_token = ""
                        continue
                    # The refresh itself was rejected: the grant is gone, so a
                    # generic request error would mislead. Report auth failure.
                    raise SyncError(
                        f"{self.provider} sign-in expired. Connect again.",
                        self.auth_error,
                    ) from exc
                raise SyncError(
                    "Google Drive rejected the request. Check the connection and permissions.",
                    "gdrive_request_failed",
                ) from exc
            except (OSError, urllib.error.URLError) as exc:
                raise SyncError("Google Drive is unavailable. Try again later.") from exc
        raise SyncError("Google sign-in expired. Connect again.", "gdrive_auth_failed")

    def _lookup(self) -> tuple[str, str] | None:
        # Earliest-created wins so every machine agrees if duplicates exist. The
        # revision is the content checksum: Drive's `version` keeps rising after an
        # upload, so a value read from the upload response is already stale.
        query = urllib.parse.urlencode(
            {
                "spaces": "appDataFolder",
                "q": f"name='{SYNC_FILENAME}' and trashed=false",
                "orderBy": "createdTime",
                "pageSize": "1",
                "fields": "files(id,md5Checksum)",
            }
        )
        files = _json_object(self._request("GET", GDRIVE_API + "files?" + query)).get(
            "files"
        )
        if not isinstance(files, list) or not files:
            self._file_id = ""
            return None
        first = cast(list[object], files)[0]
        item = cast(dict[str, object], first) if isinstance(first, dict) else {}
        file_id, checksum = (
            str(item.get("id") or ""),
            str(item.get("md5Checksum") or ""),
        )
        if not file_id or not checksum:
            raise SyncError(
                "Google Drive returned no file revision.", "gdrive_request_failed"
            )
        self._file_id = file_id
        return file_id, checksum

    def revision(self) -> str | None:
        found = self._lookup()
        return found[1] if found else None

    def read(self) -> tuple[bytes, str]:
        found = self._lookup()
        if found is None:
            raise SyncConflict()
        file_id, checksum = found
        data = self._request(
            "GET", GDRIVE_API + "files/" + urllib.parse.quote(file_id) + "?alt=media"
        )
        return data, checksum

    def write(self, data: bytes, expected_revision: str | None) -> str:
        # ponytail: Drive has no conditional upload, so a write by another machine
        # between this check and the upload still wins the race. Use one machine at a time.
        if self.revision() != expected_revision:
            raise SyncConflict()
        if self._file_id:
            result = self._request(
                "PATCH",
                GDRIVE_UPLOAD
                + "files/"
                + urllib.parse.quote(self._file_id)
                + "?uploadType=media&fields=md5Checksum",
                data=data,
            )
        else:
            boundary = secrets.token_hex(16)
            metadata = json.dumps(
                {"name": SYNC_FILENAME, "parents": ["appDataFolder"]}
            ).encode("utf-8")
            body = b"".join(
                (
                    f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
                    metadata,
                    f"\r\n--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode(),
                    data,
                    f"\r\n--{boundary}--".encode(),
                )
            )
            result = self._request(
                "POST",
                GDRIVE_UPLOAD + "files?uploadType=multipart&fields=md5Checksum",
                data=body,
                content_type=f"multipart/related; boundary={boundary}",
            )
        checksum = str(_json_object(result).get("md5Checksum") or "")
        if not checksum:
            raise SyncError(
                "Google Drive returned no upload revision.", "gdrive_request_failed"
            )
        return checksum

    def account_name(self) -> str:
        query = urllib.parse.urlencode({"fields": "user(displayName,emailAddress)"})
        user = _json_object(self._request("GET", GDRIVE_API + "about?" + query)).get(
            "user"
        )
        fields = cast(dict[str, object], user) if isinstance(user, dict) else {}
        return str(
            fields.get("emailAddress") or fields.get("displayName") or "Google Drive"
        )

    def revoke(self) -> None:
        _ = _form_post(GDRIVE_REVOKE_URL, {"token": self._refresh_token})


class SyncManager:
    def __init__(
        self,
        location: SyncLocation | None = None,
        on_pull: Callable[[], None] | None = None,
    ) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._worker_lock: threading.Lock = threading.Lock()
        self._location_override: SyncLocation | None = location
        self._dropbox: DropboxLocation | None = None
        self._gdrive: GoogleDriveLocation | None = None
        self._on_pull: Callable[[], None] | None = on_pull
        self._state: str = "idle"
        self._error_code: str = ""
        self.server_available: bool = False
        self._remote_encrypted: bool = False
        # (state, PKCE verifier, expiry, provider)
        self._pending: tuple[str, str, float, str] | None = None
        self._stop: threading.Event = threading.Event()
        self._worker: threading.Thread | None = None
        self._timer: threading.Thread | None = None

    def _setting(self, key: str) -> str:
        return database.get_setting("sync_" + key) or ""

    def _gdrive_unavailable(self) -> str:
        if not GDRIVE_CLIENT_ID or not _gdrive_client_secret():
            return "This build of wLib has no Google Drive credentials."
        return "" if self.server_available else CALLBACK_UNAVAILABLE

    def status(self) -> dict[str, object]:
        location = self._setting("location")
        enabled = bool(location)
        gdrive_reason = self._gdrive_unavailable()
        error = self._setting("last_error")
        return {
            "success": True,
            "state": (
                (
                    self._state
                    if self._state != "idle"
                    else ("error" if error else "idle")
                )
                if enabled
                else "off"
            ),
            "location": location,
            "folder_path": self._setting("folder_path"),
            "account": (
                self._setting(location + "_account")
                if location in ("dropbox", "gdrive")
                else ""
            ),
            "encrypted": bool(self._setting("encryption_key")),
            "last_synced_at": self._setting("last_synced_at"),
            "last_device": self._setting("last_device"),
            "last_error": error,
            "error_code": self._error_code,
            "dropbox_available": self.server_available,
            "dropbox_unavailable_reason": (
                "" if self.server_available else CALLBACK_UNAVAILABLE
            ),
            "gdrive_available": not gdrive_reason,
            "gdrive_unavailable_reason": gdrive_reason,
            "connecting": self._pending is not None,
        }

    def _error(self, error: Exception) -> dict[str, object]:
        code = (
            error.error_code
            if isinstance(error, (SyncError, BackupValidationError))
            else "sync_failed"
        )
        # Provider errors may include credentials in their body or URL. Never expose raw exceptions.
        message = (
            str(error)
            if isinstance(error, (SyncError, BackupValidationError))
            else "Sync could not be completed. Check the location and try again."
        )
        self._state = (
            "needs_passphrase"
            if code in ("passphrase_required", "decrypt_failed")
            else "error"
        )
        self._error_code = code
        database.update_setting("sync_last_error", message)
        return {**self.status(), "success": False, "error": message, "error_code": code}

    def _location(self) -> SyncLocation:
        if self._location_override is not None:
            return self._location_override
        if self._setting("location") == "folder":
            return FolderLocation(self._setting("folder_path"))
        if self._setting("location") == "dropbox" and self._setting(
            "dropbox_refresh_token"
        ):
            if self._dropbox is None:
                self._dropbox = DropboxLocation(self._setting("dropbox_refresh_token"))
            return self._dropbox
        if self._setting("location") == "gdrive" and self._setting(
            "gdrive_refresh_token"
        ):
            if self._gdrive is None:
                self._gdrive = GoogleDriveLocation(
                    self._setting("gdrive_refresh_token")
                )
            return self._gdrive
        raise SyncError("Choose a sync location first.", "sync_off")

    def _reset_connection(self, values: Mapping[str, str]) -> None:
        database.update_settings(
            {
                "sync_last_revision": "",
                "sync_last_synced_at": "",
                "sync_last_error": "",
                "sync_last_device": "",
                **values,
            }
        )
        self._state, self._error_code = "idle", ""

    def set_folder(self, folder: str) -> dict[str, object]:
        with self._lock:
            location = FolderLocation(folder)
            try:
                if (
                    not folder.strip()
                    or not location.folder.is_dir()
                    or not os.access(location.folder, os.W_OK)
                ):
                    raise SyncError(
                        "Choose an existing writable sync folder.",
                        "invalid_sync_folder",
                    )
                self._disconnect_locked()
                self._reset_connection(
                    {
                        "sync_location": "folder",
                        "sync_folder_path": str(location.folder.resolve()),
                    }
                )
                return self.status()
            except Exception as error:
                return self._error(error)

    def _decode(self, document: Mapping[str, object]) -> dict[str, object]:
        if document.get("format") != ENCRYPTED_FORMAT:
            return validate_library_backup(document)
        salt = encrypted_backup_salt(document)
        try:
            stored_salt = base64.b64decode(
                self._setting("encryption_salt"), validate=True
            )
            key = base64.b64decode(self._setting("encryption_key"), validate=True)
        except ValueError:
            stored_salt, key = b"", b""
        if salt != stored_salt or len(key) != 32:
            raise BackupValidationError(
                "Enter the passphrase for the sync file.", "passphrase_required"
            )
        return decrypt_library_backup(document, key=key)

    def _remote(self, location: SyncLocation) -> tuple[dict[str, object], str]:
        data, revision = location.read()
        document = _json_object(data)
        self._remote_encrypted = document.get("format") == ENCRYPTED_FORMAT
        backup = self._decode(document)
        sync = backup.get("sync")
        if not isinstance(sync, dict) or not timestamp(
            str(cast(dict[str, object], sync).get("modified_at") or "")
        ):
            raise SyncError(
                "This file has no valid sync change time.", "invalid_sync_file"
            )
        return backup, revision

    def _winning_settings(
        self, modified: str, device: str, revision: str
    ) -> dict[str, str]:
        return {
            "sync_last_synced_at": modified,
            "sync_last_revision": revision,
            "sync_last_device": device,
            "sync_last_error": "",
            "sync_encryption_change": "",
        }

    def _pull(
        self, backup: dict[str, object], revision: str, local_modified: str
    ) -> None:
        sync = cast(dict[str, object], backup["sync"])
        modified, device = str(sync["modified_at"]), str(sync.get("device") or "")
        # Recheck inside the write transaction so an edit made during a download is not lost.
        settings = {
            **self._winning_settings(modified, device, revision),
            "sync_local_modified_at": modified,
        }
        if not self._remote_encrypted:
            settings.update({"sync_encryption_key": "", "sync_encryption_salt": ""})
        result = apply_library_backup(
            backup,
            {"sections": SYNC_SECTIONS},
            mirror=True,
            sync=True,
            sync_settings=settings,
            expected_local_modified_at=local_modified,
        )
        if result["ambiguous"]:
            database.update_setting(
                "sync_last_error",
                "Some games matched more than one local game and were left unchanged.",
            )
        if self._on_pull is not None:
            self._on_pull()

    def _push(self, location: SyncLocation, revision: str | None) -> None:
        # Capture payload and its timestamp under one SQLite write lock.
        with closing(database.get_connection()) as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            modified = self._setting("local_modified_at") or utc_now()
            device = socket.gethostname()
            backup = build_sync_backup(modified, device)
            connection.commit()
        document = backup
        if self._setting("encryption_key"):
            document = encrypt_library_backup(
                backup,
                key=base64.b64decode(self._setting("encryption_key"), validate=True),
                salt=base64.b64decode(self._setting("encryption_salt"), validate=True),
            )
        new_revision = location.write(
            json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"), revision
        )
        # Do not reset local_modified_at: another local edit may have occurred during upload.
        database.update_settings(self._winning_settings(modified, device, new_revision))

    def local_changed(self) -> bool:
        return bool(self._setting("location")) and (
            timestamp(self._setting("local_modified_at"))
            > timestamp(self._setting("last_synced_at"))
            or bool(self._setting("encryption_change"))
        )

    def run(
        self, choice: str | None = None, *, push_only: bool = False
    ) -> dict[str, object]:
        if not self._lock.acquire(blocking=False):
            return self.status()
        try:
            if not self._setting("location"):
                return self.status()
            if choice is not None and choice not in (
                "use-remote",
                "use-local",
                "merge-once",
            ):
                raise SyncError(
                    "Choose one of the three library options.", "invalid_sync_choice"
                )
            self._state = "syncing"
            for attempt in range(2):
                try:
                    self._run_once(choice, push_only=push_only)
                    self._error_code = ""
                    if self._state == "syncing":
                        self._state = "idle"
                    return self.status()
                except (SyncConflict, BackupValidationError) as error:
                    if error.error_code != "sync_conflict" or attempt:
                        raise
            return self.status()
        except Exception as error:
            return self._error(error)
        finally:
            self._lock.release()

    def _run_once(self, choice: str | None, *, push_only: bool) -> None:
        location = self._location()
        last_revision = self._setting("last_revision") or None
        revision = location.revision()
        first = not self._setting("last_synced_at")
        local_modified = self._setting("local_modified_at")
        local_changed = self.local_changed()
        remote_changed = revision != last_revision
        if not first and not local_changed and not remote_changed:
            database.update_setting("sync_last_error", "")
            return
        if revision is None:
            self._push(location, None)
            return
        backup: dict[str, object] | None = None
        if first or remote_changed:
            backup, revision = self._remote(location)
        if first:
            assert backup is not None
            local_games, remote_games = database.get_all_games(), cast(
                list[object], backup["games"]
            )
            if push_only and remote_games and choice is None:
                if local_games:
                    self._state = "needs_choice"
                return
            if local_games and remote_games and choice is None:
                self._state = "needs_choice"
                return
            if choice == "merge-once":
                _ = apply_library_backup(backup, {"sections": SYNC_SECTIONS}, sync=True)
                self._push(location, revision)
            elif choice == "use-local" or (local_games and not remote_games):
                self._push(location, revision)
            else:
                self._pull(backup, revision, local_modified)
            return
        if remote_changed:
            assert backup is not None
            remote_time = str(cast(dict[str, object], backup["sync"])["modified_at"])
            if not local_changed or timestamp(remote_time) >= timestamp(local_modified):
                if not push_only:
                    self._pull(backup, revision, local_modified)
                return
        if local_changed:
            self._push(location, revision)

    def set_passphrase(self, passphrase: str | None) -> dict[str, object]:
        with self._lock:
            try:
                if (
                    not self._setting("location")
                    or not self._setting("last_synced_at")
                    or self._state in ("needs_choice", "needs_passphrase")
                ):
                    raise SyncError(
                        "Complete the first sync before changing encryption.",
                        "sync_not_ready",
                    )
                if passphrase is None:
                    key, salt = "", ""
                else:
                    raw_salt = secrets.token_bytes(16)
                    key = base64.b64encode(
                        derive_backup_key(passphrase, raw_salt)
                    ).decode("ascii")
                    salt = base64.b64encode(raw_salt).decode("ascii")
                database.update_settings(
                    {
                        "sync_encryption_key": key,
                        "sync_encryption_salt": salt,
                        "sync_encryption_change": "true",
                        "sync_local_modified_at": utc_now(),
                    }
                )
                return self.status()
            except Exception as error:
                return self._error(error)

    def unlock(self, passphrase: str) -> dict[str, object]:
        with self._lock:
            try:
                data, _ = self._location().read()
                document = _json_object(data)
                salt = encrypted_backup_salt(document)
                key = derive_backup_key(passphrase, salt)
                _ = decrypt_library_backup(document, key=key)
                database.update_settings(
                    {
                        "sync_encryption_key": base64.b64encode(key).decode("ascii"),
                        "sync_encryption_salt": base64.b64encode(salt).decode("ascii"),
                        "sync_last_error": "",
                    }
                )
                self._state, self._error_code = "idle", ""
                return self.status()
            except Exception as error:
                return self._error(error)

    def _start_signin(
        self, provider: str, authorize_url: str, parameters: Mapping[str, str]
    ) -> dict[str, object]:
        verifier, challenge, state = _pkce()
        self._pending = (state, verifier, time.monotonic() + 600, provider)
        query = urllib.parse.urlencode(
            {
                **parameters,
                "response_type": "code",
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "state": state,
            }
        )
        return {"success": True, "url": authorize_url + "?" + query}

    def _take_pending(self, provider: str, label: str, code: str, state: str) -> str:
        """Check the callback against the pending sign-in and return its PKCE verifier."""
        pending = self._pending
        if (
            pending is None
            or pending[3] != provider
            or not secrets.compare_digest(state, pending[0])
            or time.monotonic() > pending[2]
        ):
            raise SyncError(
                f"{label} sign-in expired or did not match. Connect again.",
                "invalid_oauth_state",
            )
        self._pending = None
        if not code:
            raise SyncError(
                f"{label} sign-in was cancelled. Connect again.",
                provider + "_auth_failed",
            )
        return pending[1]

    def connect_dropbox(self) -> dict[str, object]:
        with self._lock:
            if not self.server_available:
                return {
                    "success": False,
                    "error": CALLBACK_UNAVAILABLE,
                    "error_code": "callback_unavailable",
                }
            return self._start_signin(
                "dropbox",
                "https://www.dropbox.com/oauth2/authorize",
                {
                    "client_id": DROPBOX_APP_KEY,
                    "redirect_uri": DROPBOX_REDIRECT_URI,
                    "token_access_type": "offline",
                    "scope": DROPBOX_SCOPES,
                },
            )

    def connect_gdrive(self) -> dict[str, object]:
        with self._lock:
            reason = self._gdrive_unavailable()
            if reason:
                return {
                    "success": False,
                    "error": reason,
                    "error_code": "gdrive_unavailable",
                }
            return self._start_signin(
                "gdrive",
                "https://accounts.google.com/o/oauth2/v2/auth",
                {
                    "client_id": GDRIVE_CLIENT_ID,
                    "redirect_uri": GDRIVE_REDIRECT_URI,
                    "scope": GDRIVE_SCOPE,
                    "access_type": "offline",
                    "prompt": "consent",
                },
            )

    def complete_gdrive(self, code: str, state: str) -> dict[str, object]:
        with self._lock:
            try:
                verifier = self._take_pending("gdrive", "Google", code, state)
                token = _token_request(
                    GDRIVE_TOKEN_URL,
                    {
                        "client_id": GDRIVE_CLIENT_ID,
                        "client_secret": _gdrive_client_secret(),
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": GDRIVE_REDIRECT_URI,
                        "code_verifier": verifier,
                    },
                    "Google",
                    "gdrive_auth_failed",
                )
                refresh = str(token.get("refresh_token") or "")
                if not refresh:
                    raise SyncError(
                        "Google returned no refresh token. Connect again.",
                        "gdrive_auth_failed",
                    )
                location = GoogleDriveLocation(refresh)
                location.accept_token(token)
                name = location.account_name()
                # Same account, same grant: revoking the old token would kill the new one.
                database.update_setting("sync_gdrive_refresh_token", "")
                self._gdrive = None
                self._disconnect_locked()
                self._gdrive = location
                self._reset_connection(
                    {
                        "sync_location": "gdrive",
                        "sync_folder_path": "",
                        "sync_gdrive_refresh_token": refresh,
                        "sync_gdrive_account": name,
                    }
                )
                return self.status()
            except Exception as error:
                return self._error(error)

    def complete_dropbox(self, code: str, state: str) -> dict[str, object]:
        with self._lock:
            try:
                verifier = self._take_pending("dropbox", "Dropbox", code, state)
                token = _token_request(
                    DROPBOX_TOKEN_URL,
                    {
                        "client_id": DROPBOX_APP_KEY,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": DROPBOX_REDIRECT_URI,
                        "code_verifier": verifier,
                    },
                    "Dropbox",
                    "dropbox_auth_failed",
                )
                refresh = str(token.get("refresh_token") or "")
                if not refresh:
                    raise SyncError(
                        "Dropbox returned no refresh token. Connect again.",
                        "dropbox_auth_failed",
                    )
                location = DropboxLocation(refresh)
                location.accept_token(token)
                name = location.account_name()
                self._disconnect_locked()
                self._dropbox = location
                self._reset_connection(
                    {
                        "sync_location": "dropbox",
                        "sync_folder_path": "",
                        "sync_dropbox_refresh_token": refresh,
                        "sync_dropbox_account": name,
                    }
                )
                return self.status()
            except Exception as error:
                return self._error(error)

    def _disconnect_locked(self) -> None:
        for provider, cached, cls in (
            ("dropbox", self._dropbox, DropboxLocation),
            ("gdrive", self._gdrive, GoogleDriveLocation),
        ):
            token = self._setting(provider + "_refresh_token")
            if token:
                try:
                    (cached or cls(token)).revoke()
                except Exception:
                    pass
        database.update_settings(
            {
                "sync_" + key: ""
                for key in (
                    "location",
                    "folder_path",
                    "dropbox_refresh_token",
                    "dropbox_account",
                    "gdrive_refresh_token",
                    "gdrive_account",
                    "encryption_key",
                    "encryption_salt",
                    "encryption_change",
                    "last_revision",
                    "last_synced_at",
                    "last_device",
                    "last_error",
                )
            }
        )
        self._dropbox, self._gdrive, self._pending = None, None, None
        self._state, self._error_code = "idle", ""

    def disconnect(self) -> dict[str, object]:
        with self._lock:
            self._disconnect_locked()
            return self.status()

    def start_run(
        self, choice: str | None = None, *, push_only: bool = False
    ) -> dict[str, object]:
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return self.status()
            if self._setting("location"):
                self._state = "syncing"
            self._worker = threading.Thread(
                target=self.run,
                args=(choice,),
                kwargs={"push_only": push_only},
                daemon=True,
            )
            self._worker.start()
            return self.status()

    def start(self) -> None:
        _ = self.start_run()

        def watch() -> None:
            while not self._stop.wait(60):
                try:
                    if (
                        self.local_changed()
                        and time.time() - timestamp(self._setting("local_modified_at"))
                        >= 30
                    ):
                        _ = self.start_run(push_only=True)
                except SyncError:
                    pass

        self._timer = threading.Thread(target=watch, daemon=True)
        self._timer.start()

    def finish(self, timeout: float = 10) -> None:
        self._stop.set()
        if not self._setting("location"):
            return

        def final_push() -> None:
            with self._lock:
                pass
            if self.local_changed():
                _ = self.run(push_only=True)

        final = threading.Thread(target=final_push, daemon=True)
        final.start()
        final.join(timeout)
