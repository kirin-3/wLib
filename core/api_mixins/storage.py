# SPDX-License-Identifier: GPL-3.0-or-later
"""PyWebView bridge methods for the Storage page (design Decision 9)."""

from __future__ import annotations

import os

from core.api_mixins.base import ApiBase
from core.archive_install import absolute_game_path
from core.host_platform import get_data_dir, get_default_wine_prefix, get_proton_dir
from core.storage_usage import (
    build_root_context,
    clear_picked_root,
    load_picked_roots,
    save_picked_roots,
    validate_picked_folder,
)


class StorageMixin(ApiBase):
    def get_storage_state(self) -> dict[str, object]:
        try:
            state = self.storage.state()
            report = self.storage.get_report()
            return {
                "success": True,
                **state,
                "actions": self.storage_cleanup.action_statuses(report),
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not load storage state",
            }

    def start_storage_scan(self) -> dict[str, object]:
        try:
            return self.storage.start_scan()
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not start storage scan",
            }

    def stop_storage_scan(self) -> dict[str, object]:
        try:
            return self.storage.stop_scan(reason="user")
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not stop storage scan",
            }

    def preview_storage_cleanup(self, action: object = "") -> dict[str, object]:
        if not isinstance(action, str) or not action.strip():
            return {"success": False, "error": "Choose a cleanup action."}
        try:
            return self.storage_cleanup.preview(action.strip())
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not prepare cleanup preview",
            }

    def run_storage_cleanup(self, preview_id: object = "") -> dict[str, object]:
        if not isinstance(preview_id, str) or not preview_id.strip():
            return {"success": False, "error": "Cleanup preview token is missing."}
        try:
            return self.storage_cleanup.run(preview_id.strip())
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Cleanup failed"}

    def set_storage_game_folder(
        self, game_id: object = None, path: object = ""
    ) -> dict[str, object]:
        from core.database import (
            DEFAULT_LIBRARY_FOLDER_PATTERN,
            get_all_games,
            get_game,
            get_library_folders,
            get_setting,
        )

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        if not isinstance(path, str) or not path.strip():
            return {"success": False, "error": "Choose a folder."}

        try:
            game = get_game(coerced_id)
            if game is None:
                return {
                    "success": False,
                    "error": "Game was not found",
                    "error_code": "game_not_found",
                }
            games = get_all_games()
            folders, _default = get_library_folders()
            pattern = str(
                get_setting("library_folder_pattern") or DEFAULT_LIBRARY_FOLDER_PATTERN
            )
            data_dir = get_data_dir()
            picked = load_picked_roots(data_dir=data_dir)
            ctx = build_root_context(games, folders, pattern, picked)
            normalized, error = validate_picked_folder(game, path, ctx)
            if error:
                return {"success": False, "error": error}
            picked[coerced_id] = normalized
            save_picked_roots(picked, data_dir=data_dir)

            report = self.storage.get_report()
            if report is not None:
                row = report["games"].get(str(coerced_id))
                if row is not None:
                    row["root"] = normalized
                    row["root_source"] = "picked"
                    row["picked"] = True
                    row["uncertain"] = False
                    row["unsafe"] = False
                    row["unsafe_reason"] = ""
                    row["unavailable"] = False
                    row["changed"] = True
                    self.storage.replace_report(report)
            return {"success": True, "root": normalized}
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Could not save game folder"}

    def clear_storage_game_folder(self, game_id: object = None) -> dict[str, object]:
        from core.database import get_game

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or get_game(coerced_id) is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        try:
            cleared = clear_picked_root(coerced_id, data_dir=get_data_dir())
            report = self.storage.get_report()
            if report is not None:
                row = report["games"].get(str(coerced_id))
                if row is not None and row.get("picked"):
                    row["picked"] = False
                    row["changed"] = True
                    self.storage.replace_report(report)
            return {"success": True, "cleared": cleared}
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not clear game folder",
            }

    def open_storage_location(self, target: object = "") -> dict[str, object]:
        from core.database import get_game, get_setting

        if not isinstance(target, str) or not target.strip():
            return {"success": False, "error": "Choose a location to open."}
        raw = target.strip()
        resolved = ""

        if raw.startswith("game:"):
            gid = self._coerce_db_id(raw[5:])
            if gid is None:
                return {"success": False, "error": "Game was not found"}
            game = get_game(gid)
            if game is None:
                return {"success": False, "error": "Game was not found"}
            report = self.storage.get_report()
            if report is not None:
                row = report["games"].get(str(gid))
                if row is not None and row.get("root"):
                    resolved = str(row["root"])
            if not resolved:
                exe = absolute_game_path(game.get("exe_path"))
                resolved = os.path.dirname(exe) if exe else ""
        else:
            data_dir = get_data_dir()
            category_paths: dict[str, str] = {
                "download_archives": os.path.join(data_dir, "downloads"),
                "torrent_data": os.path.join(data_dir, "downloads", "torrents"),
                "proton_builds": get_proton_dir(),
                "shared_wine_prefix": os.path.abspath(
                    os.path.expanduser(
                        str(get_setting("wine_prefix_path") or "").strip()
                        or get_default_wine_prefix()
                    )
                ),
                "save_backups": os.path.join(data_dir, "save-backups"),
                "web_player": os.path.join(data_dir, "web-player"),
                "sandboxes": os.path.join(data_dir, "sandbox"),
                "removed_game_data": os.path.join(data_dir, "sandbox", "removed"),
                "cover_cache": os.path.join(data_dir, "covers", "cache"),
                "other_caches": os.path.join(data_dir, "install-scratch"),
                "catalog": data_dir,
                "logs_and_backups": os.path.join(data_dir, "logs"),
            }
            if raw in category_paths:
                resolved = category_paths[raw]
            else:
                resolved = os.path.abspath(os.path.expanduser(raw))

        if not resolved or not os.path.isdir(resolved):
            return {
                "success": False,
                "error": f"Folder does not exist: {resolved or raw}",
            }
        return dict(self._open_path_with_system_handler(resolved))
