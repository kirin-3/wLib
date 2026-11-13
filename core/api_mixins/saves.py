# SPDX-License-Identifier: GPL-3.0-or-later
"""Game saves, RPG Maker saves and attachments."""
from __future__ import annotations

import os
import sys
from contextlib import closing
from typing import cast

from core.host_platform import (
    is_windows,
)
from core.game_saves import SaveLocation
from core.api_mixins.base import ApiBase


class SavesMixin(ApiBase):
    def browse_attachment_file(self, start_path: str = "") -> str:
        """Opens a native file dialog to select a document for attachments."""
        file_types = (
            "Documents (*.pdf;*.txt;*.md)",
            "All files (*.*)",
        )

        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog(
                "file", directory=start_path, file_types=file_types
            )

        return self._browse_qt_dialog(
            "file", directory=start_path, file_types=file_types
        )

    def _attachment_game_exists(self, game_id: int) -> bool:
        import sqlite3

        from core.database import get_connection

        try:
            with closing(get_connection()) as conn:
                row = cast(
                    object,
                    conn.execute(
                        "SELECT 1 FROM games WHERE id = ?", (game_id,)
                    ).fetchone(),
                )
        except (sqlite3.Error, TypeError, ValueError, OverflowError):
            return False
        return row is not None

    def list_game_attachments(self, game_id: object = None) -> dict[str, object]:
        from core.game_attachments import list_attachments

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        if not self._attachment_game_exists(coerced_id):
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        try:
            attachments = list_attachments(coerced_id)
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Could not list attachments"}
        return {"success": True, "attachments": attachments}

    def add_game_attachment(
        self, game_id: object = None, source_path: object = ""
    ) -> dict[str, object]:
        from core.game_attachments import (
            UnsupportedAttachmentError,
            add_attachment,
            list_attachments,
        )

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        if not self._attachment_game_exists(coerced_id):
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        try:
            attachment = add_attachment(coerced_id, source_path)
        except UnsupportedAttachmentError as exc:
            return {
                "success": False,
                "error": str(exc) or "Only PDF, TXT and MD files can be attached",
                "error_code": "unsupported_attachment",
            }
        except (OSError, ValueError) as exc:
            return {"success": False, "error": str(exc) or "Could not attach file"}
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Could not attach file"}
        try:
            attachments = list_attachments(coerced_id)
        except Exception:
            attachments = [attachment]
        return {
            "success": True,
            "attachment": attachment,
            "attachments": attachments,
        }

    def open_game_attachment(
        self, game_id: object = None, name: object = ""
    ) -> dict[str, object]:
        from core.game_attachments import AttachmentMissingError, resolve_attachment

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        if not self._attachment_game_exists(coerced_id):
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        try:
            resolved = resolve_attachment(coerced_id, name)
        except AttachmentMissingError as exc:
            return {
                "success": False,
                "error": str(exc) or "Attachment file is missing",
                "error_code": "attachment_missing",
            }
        except ValueError as exc:
            return {"success": False, "error": str(exc) or "Invalid attachment name"}
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Could not open attachment"}
        result = self._open_with_system_handler(
            resolved, "No application found to open this file"
        )
        if not result.get("success"):
            return {
                "success": False,
                "error": str(result.get("error") or "Could not open attachment"),
            }
        return {"success": True}

    def remove_game_attachment(
        self, game_id: object = None, name: object = ""
    ) -> dict[str, object]:
        from core.game_attachments import (
            AttachmentMissingError,
            list_attachments,
            remove_attachment,
        )

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        if not self._attachment_game_exists(coerced_id):
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        try:
            remove_attachment(coerced_id, name)
        except AttachmentMissingError as exc:
            return {
                "success": False,
                "error": str(exc) or "Attachment file is missing",
                "error_code": "attachment_missing",
            }
        except ValueError as exc:
            return {"success": False, "error": str(exc) or "Invalid attachment name"}
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not remove attachment",
            }
        try:
            attachments = list_attachments(coerced_id)
        except Exception:
            attachments = []
        return {"success": True, "attachments": attachments}

    @staticmethod
    def _game_saves_not_found() -> dict[str, object]:
        return {
            "success": False,
            "error": "Game was not found",
            "error_code": "game_not_found",
        }

    @staticmethod
    def _format_bytes(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def _saves_summary(self, game_id: int) -> list[dict[str, object]]:
        from core.game_saves import game_backup_dir, list_backups

        backup_dir = game_backup_dir(self._save_backups_root(), game_id)
        return [{"name": name} for name in list_backups(backup_dir)]

    def get_game_saves(self, game_id: object = None) -> dict[str, object]:
        """Override, detected folders, effective folder and backups for a game."""
        from core.database import get_save_backup_keep

        state = self._saves_game_state(game_id)
        if state is None:
            return self._game_saves_not_found()
        coerced_id, row, effective, detected = state
        override = str(row.get("save_path_override") or "").strip()
        return {
            "success": True,
            "override": override,
            "override_exists": bool(override) and os.path.isdir(override),
            "detected": detected,
            "effective": effective,
            "effective_exists": bool(effective) and os.path.isdir(effective),
            "backups": self._saves_summary(coerced_id),
            "keep": get_save_backup_keep() or "all",
        }

    def set_game_save_folder(
        self, game_id: object = None, path: object = ""
    ) -> dict[str, object]:
        """Set or (with an empty path) clear a game's saves-folder override."""
        import sqlite3

        from core.database import get_game, update_game
        from core.game_saves import GameSavesError, validate_save_override

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or get_game(coerced_id) is None:
            return self._game_saves_not_found()
        if path is not None and not isinstance(path, str):
            return {"success": False, "error": "The saves folder must be a path"}
        try:
            override = validate_save_override(path or "", self._save_backups_root())
            _ = update_game(coerced_id, {"save_path_override": override})
        except GameSavesError as exc:
            return {"success": False, "error": str(exc)}
        except (sqlite3.Error, OSError) as exc:
            return {"success": False, "error": str(exc) or "Could not save the folder"}
        return {
            "success": True,
            "override": override,
            "override_exists": bool(override) and os.path.isdir(override),
        }

    def backup_game_saves(self, game_id: object = None) -> dict[str, object]:
        from core.database import get_save_backup_keep
        from core.game_saves import GameSavesError, backup_saves

        state = self._saves_game_state(game_id)
        if state is None:
            return self._game_saves_not_found()
        coerced_id, _row, effective, _detected = state
        if not effective:
            return {"success": False, "error": "Choose a saves folder first"}
        try:
            with self._save_operation(coerced_id):
                info = backup_saves(
                    effective,
                    self._save_backups_root(),
                    coerced_id,
                    get_save_backup_keep(),
                )
        except GameSavesError as exc:
            return {"success": False, "error": str(exc)}
        message = f"Backed up {info.files} file(s) ({self._format_bytes(info.bytes)})"
        if info.skipped_links:
            message += f"; skipped {info.skipped_links} link(s)"
        return {
            "success": True,
            "message": message,
            "backup": info.name,
            "backups": self._saves_summary(coerced_id),
        }

    def _merge_into_game_saves(
        self, game_id: object, source: str, verb: str = "Imported"
    ) -> dict[str, object]:
        """Shared import/restore body."""
        from core.database import get_save_backup_keep
        from core.game_saves import GameSavesError, import_saves

        state = self._saves_game_state(game_id)
        if state is None:
            return self._game_saves_not_found()
        coerced_id, _row, effective, _detected = state
        if not effective:
            return {"success": False, "error": "Choose a saves folder first"}
        try:
            with self._save_operation(coerced_id, writes=True):
                info = import_saves(
                    source,
                    effective,
                    self._save_backups_root(),
                    coerced_id,
                    get_save_backup_keep(),
                )
        except GameSavesError as exc:
            return {"success": False, "error": str(exc)}
        message = f"{verb} {info.files} file(s) ({self._format_bytes(info.bytes)})"
        if info.skipped_links:
            message += f"; skipped {info.skipped_links} link(s)"
        if info.backup is not None:
            message += f". Previous saves backed up as {info.backup.name}"
        return {
            "success": True,
            "message": message,
            "backup": info.backup.name if info.backup else "",
            "backups": self._saves_summary(coerced_id),
        }

    def import_game_saves(
        self, game_id: object = None, source: object = ""
    ) -> dict[str, object]:
        """Merge a chosen folder into the game's saves folder."""
        if not isinstance(source, str) or not source.strip():
            return {"success": False, "error": "Choose a folder to import"}
        return self._merge_into_game_saves(
            game_id, self._normalize_selected_path(source)
        )

    def restore_game_saves_backup(
        self, game_id: object = None, backup_name: object = ""
    ) -> dict[str, object]:
        """Import one of the game's backups, like Import with that source."""
        from core.game_saves import GameSavesError, resolve_backup

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return self._game_saves_not_found()
        try:
            source = resolve_backup(
                self._save_backups_root(), coerced_id, str(backup_name or "")
            )
        except GameSavesError as exc:
            return {"success": False, "error": str(exc)}
        return self._merge_into_game_saves(coerced_id, source, "Restored")

    def _rpgm_save_context(
        self, game_id: object
    ) -> tuple[int, str, str, dict[str, object]] | dict[str, object]:
        """(id, data root, effective folder, row) or a failure payload."""
        from core.game_tools import ENGINE_RPGM, detect_engine, rpgm_data_root

        state = self._saves_game_state(game_id)
        if state is None:
            return self._game_saves_not_found()
        coerced_id, row, effective, _detected = state
        game = self._tool_game(coerced_id)
        if game is None:
            return self._game_saves_not_found()
        _id, folder, exe_path = game
        root = (
            rpgm_data_root(folder)
            if detect_engine(folder, exe_path) == ENGINE_RPGM
            else None
        )
        if root is None:
            return {
                "success": False,
                "error": "The save editor only works with RPG Maker MV/MZ games",
                "error_code": "unsupported_engine",
            }
        return coerced_id, root, effective, row

    @staticmethod
    def _rpgm_save_failure(exc: Exception) -> dict[str, object]:
        from core.rpgm_saves import RpgmSaveError

        code = exc.code if isinstance(exc, RpgmSaveError) else "error"
        return {"success": False, "error": str(exc), "error_code": code}

    def list_rpgm_saves(self, game_id: object = None) -> dict[str, object]:
        from core.rpgm_saves import list_slots

        context = self._rpgm_save_context(game_id)
        if isinstance(context, dict):
            return context
        _id, root, effective, _row = context
        try:
            slots = list_slots(root, effective)
        except OSError as exc:
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "slots": [
                {key: value for key, value in slot.items() if key != "folder"}
                for slot in slots
            ],
        }

    def load_rpgm_save(
        self, game_id: object = None, file: object = None
    ) -> dict[str, object]:
        from core.rpgm_saves import RpgmSaveError, load_names, load_save, resolve_slot

        context = self._rpgm_save_context(game_id)
        if isinstance(context, dict):
            return context
        _id, root, effective, _row = context
        try:
            path, _folder = resolve_slot(root, effective, file)
            payload = load_save(path, load_names(root))
        except RpgmSaveError as exc:
            return self._rpgm_save_failure(exc)
        except (OSError, RecursionError, MemoryError) as exc:
            return {"success": False, "error": str(exc) or "Could not read the save"}
        return {"success": True, **payload}

    def write_rpgm_save(
        self,
        game_id: object = None,
        file: object = None,
        patches: object = None,
        size: object = None,
        mtime_ns: object = None,
    ) -> dict[str, object]:
        from core.database import get_save_backup_keep
        from core.game_saves import GameSavesError, backup_saves
        from core.rpgm_saves import (
            BackupFailed,
            InvalidPatch,
            RpgmSaveError,
            load_names,
            resolve_slot,
            write_save,
        )

        context = self._rpgm_save_context(game_id)
        if isinstance(context, dict):
            return context
        coerced_id, root, effective, _row = context
        stamp = (
            int(mtime_ns)
            if isinstance(mtime_ns, str) and mtime_ns.isdigit()
            else mtime_ns
        )
        if (
            not isinstance(patches, list)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not isinstance(stamp, int)
            or isinstance(stamp, bool)
        ):
            return self._rpgm_save_failure(
                InvalidPatch("The edit request is not valid")
            )
        try:
            path, folder = resolve_slot(root, effective, file)
            with self._save_operation(coerced_id, writes=True):

                def backup() -> str:
                    try:
                        return backup_saves(
                            folder,
                            self._save_backups_root(),
                            coerced_id,
                            get_save_backup_keep(),
                        ).name
                    except GameSavesError as exc:
                        raise BackupFailed(str(exc)) from exc

                backup_name = write_save(
                    path, cast("list[object]", patches), size, stamp, load_names(root), backup
                )
        except GameSavesError as exc:
            running = "Close the game" in str(exc)
            return {
                "success": False,
                "error": str(exc),
                "error_code": "game_running" if running else "busy",
            }
        except RpgmSaveError as exc:
            return self._rpgm_save_failure(exc)
        except (OSError, RecursionError, MemoryError) as exc:
            return {"success": False, "error": str(exc) or "Could not write the save"}
        return {
            "success": True,
            "backup": backup_name,
            "message": f"Saved. Previous saves backed up as {backup_name}",
            "backups": self._saves_summary(coerced_id),
        }

    def open_game_save_backup(
        self, game_id: object = None, backup_name: object = ""
    ) -> dict[str, object]:
        from core.game_saves import GameSavesError, resolve_backup

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return self._game_saves_not_found()
        try:
            path = resolve_backup(
                self._save_backups_root(), coerced_id, str(backup_name or "")
            )
        except GameSavesError as exc:
            return {"success": False, "error": str(exc)}
        return dict(self._open_path_with_system_handler(path))

    def find_save_files(
        self,
        exe_path: str,
        title: str = "",
        engine: str = "",
        custom_prefix: str = "",
        proton_version: str = "",
    ) -> list[SaveLocation]:
        """
        Searches common save file locations for a game.
        Returns a list of {path, type, description, eligible} for each found location.
        """
        from core.game_saves import detect_save_locations

        _ = engine
        return detect_save_locations(
            exe_path,
            title,
            custom_prefix,
            proton_version,
            windows=is_windows(),
        )
