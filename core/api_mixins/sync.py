# SPDX-License-Identifier: GPL-3.0-or-later
"""Library sync, JSON backup and import from other apps."""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import cast

from core.bridge_exposure import python_only
from core.app_version import APP_VERSION
from core.api_mixins.base import ApiBase


class SyncMixin(ApiBase):
    def get_sync_status(self) -> dict[str, object]:
        return self.library_sync.status()

    def set_sync_folder(self, path: str) -> dict[str, object]:
        result = self.library_sync.set_folder(path)
        if result.get("success"):
            return self.library_sync.start_run()
        return result

    def _open_signin(
        self, result: dict[str, object], provider: str
    ) -> dict[str, object]:
        if result.get("success"):
            opened = self.open_in_browser(str(result["url"]))
            if not opened.get("success"):
                self.library_sync.cancel_signin()
                return {
                    "success": False,
                    "error": f"Could not open {provider} sign-in in your browser.",
                    "error_code": "browser_failed",
                }
        return result

    def connect_dropbox(self) -> dict[str, object]:
        return self._open_signin(self.library_sync.connect_dropbox(), "Dropbox")

    def connect_gdrive(self) -> dict[str, object]:
        return self._open_signin(self.library_sync.connect_gdrive(), "Google")

    @python_only
    def complete_dropbox_callback(self, code: str, state: str) -> dict[str, object]:
        result = self.library_sync.complete_dropbox(code, state)
        if result.get("success"):
            _ = self.library_sync.start_run()
        return result

    @python_only
    def complete_gdrive_callback(self, code: str, state: str) -> dict[str, object]:
        result = self.library_sync.complete_gdrive(code, state)
        if result.get("success"):
            _ = self.library_sync.start_run()
        return result

    def disconnect_sync(self) -> dict[str, object]:
        return self.library_sync.disconnect()

    def list_sync_snapshots(self) -> dict[str, object]:
        return self.library_sync.list_snapshots()

    def restore_sync_snapshot(self, name: str) -> dict[str, object]:
        result = self.library_sync.restore_snapshot(name)
        if result.get("success"):
            # Upload the restored library so other devices get it too.
            _ = self.library_sync.start_run()
        return result

    def sync_now(self) -> dict[str, object]:
        return self.library_sync.start_run()

    def resolve_sync_choice(self, choice: str) -> dict[str, object]:
        if choice not in ("use-remote", "use-local", "merge-once"):
            return {
                "success": False,
                "error": "Choose a valid library option.",
                "error_code": "invalid_sync_choice",
            }
        if self.library_sync.status().get("state") != "needs_choice":
            return {
                "success": False,
                "error": "No library choice is needed.",
                "error_code": "invalid_sync_choice",
            }
        return self.library_sync.start_run(choice)

    def set_sync_passphrase(self, passphrase: str | None) -> dict[str, object]:
        result = self.library_sync.set_passphrase(passphrase)
        if result.get("success"):
            return self.library_sync.start_run()
        return result

    def unlock_sync(self, passphrase: str) -> dict[str, object]:
        result = self.library_sync.unlock(passphrase)
        if result.get("success"):
            return self.library_sync.start_run()
        return result

    def _backup_error_payload(self, error: Exception) -> dict[str, object]:
        from core.library_backup import BackupValidationError

        if isinstance(error, BackupValidationError):
            return {
                "success": False,
                "error": str(error),
                "error_code": error.error_code,
            }
        return {"success": False, "error": str(error), "error_code": "backup_error"}

    def export_library_backup(
        self,
        options: Mapping[str, object] | None = None,
        destination_path: str = "",
        passphrase: str | None = None,
    ) -> dict[str, object]:
        from core.library_backup import write_library_backup

        try:
            payload = write_library_backup(
                destination_path,
                dict(options or {}),
                app_version=APP_VERSION,
                passphrase=passphrase,
            )
        except Exception as exc:
            return self._backup_error_payload(exc)

        return {"success": True, **payload}

    def inspect_library_backup(
        self, path: str, passphrase: str | None = None
    ) -> dict[str, object]:
        from core.library_backup import inspect_library_backup

        try:
            payload = inspect_library_backup(path, passphrase)
        except Exception as exc:
            return self._backup_error_payload(exc)

        return {"success": True, **payload}

    def import_library_backup(
        self,
        path: str,
        options: Mapping[str, object] | None = None,
        passphrase: str | None = None,
    ) -> dict[str, object]:
        import sqlite3

        from core.library_backup import import_library_backup

        try:
            payload = import_library_backup(path, dict(options or {}), passphrase)
        except sqlite3.IntegrityError as exc:
            return {
                "success": False,
                "error": f"Import could not be applied safely: {exc}",
                "error_code": "import_conflict",
            }
        except Exception as exc:
            return self._backup_error_payload(exc)

        self.cover_cache.wake()
        # Imported games can change which shortcuts Steam should show.
        self._schedule_steam_sync()
        return {"success": True, **payload}

    def detect_external_library(self, source: str) -> dict[str, object]:
        from core.external_import import locate_external_library
        from core.library_backup import BackupValidationError

        try:
            payload = locate_external_library(source)
        except BackupValidationError as exc:
            return self._backup_error_payload(exc)
        except Exception as exc:
            return self._backup_error_payload(exc)

        return {"success": True, **payload}

    def inspect_external_library(self, source: str, path: str) -> dict[str, object]:
        from core.library_backup import inspect_external_library

        try:
            payload = inspect_external_library(source, path)
        except Exception as exc:
            return self._backup_error_payload(exc)

        return {"success": True, **payload}

    def import_external_library(
        self,
        source: str,
        path: str,
        options: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        import sqlite3

        from core.library_backup import import_external_library

        try:
            payload = import_external_library(source, path, dict(options or {}))
        except sqlite3.IntegrityError as exc:
            return {
                "success": False,
                "error": f"Import could not be applied safely: {exc}",
                "error_code": "import_conflict",
            }
        except Exception as exc:
            return self._backup_error_payload(exc)

        game_ids = [
            *cast(list[int], payload.get("created_ids", [])),
            *cast(list[int], payload.get("updated_ids", [])),
        ]
        ids_with_url = self._game_ids_with_url(game_ids) if game_ids else []
        if ids_with_url:
            check_result = self.check_all_updates(ids_with_url)
            if check_result.get("success"):
                payload["metadata_check"] = "started"
            else:
                payload["metadata_check"] = "busy"
        else:
            payload["metadata_check"] = "none"

        self._refresh_synced_library()
        return {"success": True, **payload}

    def _game_ids_with_url(self, game_ids: Sequence[int]) -> list[int]:
        from core.database import get_all_games

        wanted: set[int] = set()
        for raw_id in game_ids:
            try:
                wanted.add(int(str(raw_id)))
            except (TypeError, ValueError):
                continue
        ids_with_url: list[int] = []
        for game in get_all_games():
            game_id = game.get("id")
            url = game.get("f95_url")
            if (
                isinstance(game_id, int)
                and game_id in wanted
                and isinstance(url, str)
                and url.strip()
            ):
                ids_with_url.append(game_id)
        return ids_with_url

    def browse_external_library_file(self, source: str, start_path: str = "") -> str:
        from core.external_import import (
            EXTERNAL_IMPORT_FILE_FILTERS,
            normalize_source,
        )

        try:
            key = normalize_source(source)
        except Exception:
            return ""
        file_types = EXTERNAL_IMPORT_FILE_FILTERS.get(key, ())
        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog(
                "file", directory=start_path, file_types=file_types
            )
        return self._browse_qt_dialog(
            "file", directory=start_path, file_types=file_types
        )

    def browse_backup_file(self, start_path: str = "") -> str:
        """Opens a native file dialog to select a wLib JSON backup."""
        file_types = (
            "wLib JSON Backups (*.json)",
            "All files (*.*)",
        )

        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog(
                "file", directory=start_path, file_types=file_types
            )

        return self._browse_qt_dialog(
            "file", directory=start_path, file_types=file_types
        )
