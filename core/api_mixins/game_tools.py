# SPDX-License-Identifier: GPL-3.0-or-later
"""Ren'Py, RPG Maker, BepInEx and URM tools."""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import cast

from core.game_tools import CancelFn, ProgressFn, game_tool_folder
from core.api_mixins.base import ApiBase


class GameToolsMixin(ApiBase):
    def _get_urm_game_dir(self, game_id: int) -> str:
        from core.database import get_all_games

        game = next((game for game in get_all_games() if game["id"] == game_id), None)
        if game is None:
            raise ValueError("Game not found")
        return game_tool_folder(game)

    def get_urm_status(self, game_id: int) -> dict[str, object]:
        import sqlite3

        from core.database import get_setting

        try:
            folder = self._get_urm_game_dir(game_id)
            raw_source = str(get_setting("urm_rpa_path") or "").strip()
            source = os.path.expanduser(raw_source) if raw_source else ""
            renpy = all(
                os.path.isdir(os.path.join(folder, part)) for part in ("renpy", "game")
            )
            return {
                "success": True,
                "renpy": renpy,
                "installed": renpy
                and os.path.isfile(os.path.join(folder, "game", "0x52_URM.rpa")),
                "source_configured": source.lower().endswith(".rpa")
                and os.path.isfile(source),
            }
        except (ValueError, OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc)}

    def set_urm_installed(self, game_id: int, installed: object) -> dict[str, object]:
        import shutil
        import sqlite3
        import tempfile
        from core.database import get_setting

        if not isinstance(installed, bool):
            return {"success": False, "error": "Installed must be a boolean"}
        status = self.get_urm_status(game_id)
        if not status.get("success"):
            return status
        if not status.get("renpy"):
            return {"success": False, "error": "This game folder is not a Ren'Py game"}
        if installed and not status.get("source_configured"):
            return {
                "success": False,
                "error": "Configure an existing URM .rpa source file in Settings",
            }
        temporary = ""
        try:
            target = os.path.join(
                self._get_urm_game_dir(game_id), "game", "0x52_URM.rpa"
            )
            if installed:
                with tempfile.NamedTemporaryFile(
                    dir=os.path.dirname(target), delete=False
                ) as file:
                    temporary = file.name
                source = os.path.expanduser(
                    str(get_setting("urm_rpa_path") or "").strip()
                )
                _ = shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            elif os.path.lexists(target):
                os.remove(target)
            return self.get_urm_status(game_id)
        except (ValueError, OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc)}
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError as exc:
                    print(f"Could not remove temporary URM file: {exc}")

    @contextmanager
    def _tool_run(self, game_id: int, action: str) -> Generator[None]:
        """Allow one tool action per game, and none while wLib runs the game."""
        from core.game_tools import GameToolError

        # _status_lock serializes this check with the launcher's reservation.
        with self._status_lock, self._tool_lock:
            if game_id in self._tool_status:
                raise GameToolError("A tool is already running for this game")
            if game_id in self.get_running_games():
                raise GameToolError("Close the game before using its tools")
            if self._archive_install_status["running"] and self._archive_install_game_id == game_id:
                raise GameToolError("This game is being updated. Wait for the install to finish.")
            files_busy = self._game_files_busy_reason(game_id)
            if files_busy:
                raise GameToolError(files_busy)
            self._tool_status[game_id] = {
                "running": True,
                "action": action,
                "done": 0,
                "total": 0,
                "current": "",
                "cancel": False,
            }
        try:
            yield
        finally:
            with self._tool_lock:
                _ = self._tool_status.pop(game_id, None)

    def _tool_progress(self, game_id: int) -> ProgressFn:
        def report(done: int, total: int, current: str) -> None:
            with self._tool_lock:
                status = self._tool_status.get(game_id)
                if status is not None:
                    status.update(done=done, total=total, current=current)

        return report

    def _tool_cancelled(self, game_id: int) -> CancelFn:
        def check() -> bool:
            with self._tool_lock:
                status = self._tool_status.get(game_id)
                return bool(status and status.get("cancel"))

        return check

    def get_game_tool_status(self, game_id: object = None) -> dict[str, object]:
        coerced_id = self._coerce_db_id(game_id)
        with self._tool_lock:
            status = self._tool_status.get(coerced_id) if coerced_id else None
            if status is None:
                return {
                    "success": True,
                    "running": False,
                    "action": "",
                    "done": 0,
                    "total": 0,
                    "current": "",
                    "cancel": False,
                }
            return {"success": True, **status}

    def cancel_game_tool(self, game_id: object = None) -> dict[str, object]:
        coerced_id = self._coerce_db_id(game_id)
        with self._tool_lock:
            status = self._tool_status.get(coerced_id) if coerced_id else None
            if status is None:
                return {"success": False, "error": "No tool is running for this game"}
            status["cancel"] = True
        return {"success": True}

    def get_game_tool_support(self, game_id: object = None) -> dict[str, object]:
        from core.game_tools import detect_engine

        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found", "engine": ""}
        return {"success": True, "engine": detect_engine(game[1], game[2])}

    def _renpy_tools_state(self, game_id: int, folder: str) -> dict[str, object]:
        from core.renpy_tools import console_state, list_archives

        urm = self.get_urm_status(game_id)
        return {
            "console": console_state(folder),
            "archives": [
                {key: value for key, value in archive.items() if key != "path"}
                for archive in list_archives(folder)
            ],
            "urm": urm,
        }

    @staticmethod
    def _rpgm_tools_state(root: str) -> dict[str, object]:
        from core import rpgm_tools

        assets = rpgm_tools.scan_assets(root)
        _key, source = rpgm_tools.resolve_key(root, assets)
        images = sum(1 for asset in assets if asset.image)
        return {
            "encrypted": len(assets),
            "images": images,
            "audio": len(assets) - images,
            "missing": len(rpgm_tools.missing_copies(assets)),
            "key_source": source,
            "use_decrypted": rpgm_tools.read_use_decrypted(root),
            "backup": rpgm_tools.has_backup(root),
        }

    def get_game_tools(self, game_id: object = None) -> dict[str, object]:
        from core.game_tools import (
            ENGINE_RENPY,
            ENGINE_RPGM,
            ENGINE_UNITY,
            detect_engine,
            rpgm_data_root,
        )

        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found"}
        coerced_id, folder, exe_path = game
        engine = detect_engine(folder, exe_path)
        try:
            if engine == ENGINE_UNITY:
                from dataclasses import asdict
                from core.unity_tools import detect_installed, detect_unity
                unity = detect_unity(folder, exe_path)
                if unity is not None:
                    return {"success": True, "engine": engine, "unity": {
                        "build": asdict(unity), "platform": unity.platform,
                        "installed": detect_installed(folder),
                        "unity_libs": bool(unity.version and os.path.isfile(os.path.join(folder, "BepInEx", "unity-libs", unity.version + ".zip"))),
                        "has_backup": os.path.isfile(os.path.join(folder, "BepInEx.wlib-backup", "manifest.json")),
                    }}
            if engine == ENGINE_RENPY:
                return {
                    "success": True,
                    "engine": engine,
                    "renpy": self._renpy_tools_state(coerced_id, folder),
                }
            root = rpgm_data_root(folder) if engine == ENGINE_RPGM else None
            if root is not None:
                return {
                    "success": True,
                    "engine": engine,
                    "rpgm": self._rpgm_tools_state(root),
                }
        except OSError as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "engine": ""}

    def _run_game_tool(
        self,
        game_id: object,
        engine: str,
        action: str,
        body: Callable[[str, ProgressFn, CancelFn], dict[str, object]],
    ) -> dict[str, object]:
        """Shared guard for every tool action: resolve, check engine, run once."""
        import sqlite3

        from core.game_tools import GameToolError, detect_engine
        from core.archive_install import ArchiveSafetyError
        from zipfile import BadZipFile
        from http.client import HTTPException

        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found"}
        coerced_id, folder, exe_path = game
        if detect_engine(folder, exe_path) != engine:
            label = {"renpy": "Ren'Py", "rpgm": "RPG Maker MV/MZ", "unity": "Unity"}.get(engine, engine)
            return {"success": False, "error": f"This game is not a {label} game"}
        try:
            with self._tool_run(coerced_id, action):
                result = body(
                    folder,
                    self._tool_progress(coerced_id),
                    self._tool_cancelled(coerced_id),
                )
        except (GameToolError, ArchiveSafetyError, BadZipFile, HTTPException, ValueError, OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, **result}

    def list_bepinex_versions(self, game_id: object = None) -> dict[str, object]:
        import time
        from core import unity_tools

        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found"}
        unity = unity_tools.detect_unity(game[1], game[2])
        if unity is None:
            return {"success": False, "error": "This game is not a Unity game"}
        if unity.platform != "windows" or not unity.arch or not unity.backend:
            return {"success": False, "error": "A Windows Unity game with known backend and architecture is required"}
        key = (unity.arch, unity.backend)
        cached = self._bepinex_cache.get(key)
        if cached is None or time.monotonic() - cached[0] >= 600:
            builds, warnings = unity_tools.list_versions(*key)
            cached = (time.monotonic(), builds, warnings)
            self._bepinex_cache[key] = cached
        return {"success": True, "builds": cached[1], "warnings": cached[2],
                "recommended": unity_tools.recommended_build(cached[1], unity.backend)}

    def install_bepinex(self, game_id: object = None, options: object = None) -> dict[str, object]:
        from core import unity_tools
        from core.game_tools import GameToolError
        from core.host_platform import get_data_dir

        if not isinstance(options, dict):
            return {"success": False, "error": "A build ID is required"}
        options_map = cast(dict[str, object], options)
        if not isinstance(options_map.get("build_id"), str):
            return {"success": False, "error": "A build ID is required"}
        build_id = options_map["build_id"]
        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found"}

        def body(folder: str, progress: ProgressFn, cancelled: CancelFn) -> dict[str, object]:
            unity = unity_tools.detect_unity(folder, game[2])
            if unity is None or unity.platform != "windows":
                raise GameToolError("BepInEx is not supported for Linux-native Unity games")
            if not unity.arch or not unity.backend:
                raise GameToolError("Unity backend and architecture must be known")
            versions = self.list_bepinex_versions(game_id)
            builds_value = versions.get("builds")
            if not isinstance(builds_value, list):
                raise GameToolError(str(versions.get("error") or "No builds available"))
            builds = cast(list[dict[str, object]], builds_value)
            build = next((b for b in builds if b.get("id") == build_id), None)
            if (build is None or build.get("backend") != unity.backend
                    or (unity.backend == "il2cpp" and build.get("major") != 6)):
                raise GameToolError("The selected BepInEx build is unavailable or incompatible")
            cache = os.path.join(get_data_dir(), "bepinex-cache")
            archive = unity_tools.download_cached(str(build["url"]), str(build["file_name"]), cache,
                                                  str(build["sha256"]), progress, cancelled)
            if archive is None:
                raise GameToolError("BepInEx download is unavailable")
            libs = None
            warning = ""
            if unity.backend == "il2cpp":
                if unity.version:
                    try:
                        libs = unity_tools.download_cached(f"https://unity.bepinex.dev/libraries/{unity.version}.zip",
                                                           f"unity-libs-{unity.version}.zip", cache, progress=progress,
                                                           cancelled=cancelled,
                                                           label="Downloading Unity base libraries ...")
                    except (OSError, GameToolError):
                        if cancelled():
                            raise GameToolError("Download cancelled") from None
                    if libs is None:
                        warning = "Unity base libraries unavailable; first launch needs internet."
                else:
                    warning = "Unity version unknown; first launch needs internet."
            result = unity_tools.install_build(folder, str(archive), str(libs) if libs else None, progress, cancelled)
            result["warning"] = warning
            return result
        return self._run_game_tool(game_id, "unity", "bepinex_install", body)

    def uninstall_bepinex(self, game_id: object = None, options: object = None) -> dict[str, object]:
        from core import unity_tools
        from core.game_tools import GameToolError
        if not isinstance(options, dict):
            return {"success": False, "error": "Invalid uninstall options"}
        options_map = cast(dict[str, object], options)
        if not isinstance(options_map.get("remove_user_data", False), bool):
            return {"success": False, "error": "Invalid uninstall options"}
        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found"}
        def body(folder: str, _progress: ProgressFn, _cancelled: CancelFn) -> dict[str, object]:
            unity = unity_tools.detect_unity(folder, game[2])
            if unity is None or unity.platform != "windows":
                raise GameToolError("BepInEx is not supported for Linux-native Unity games")
            return unity_tools.uninstall(folder, bool(options_map.get("remove_user_data", False)))
        return self._run_game_tool(game_id, "unity", "bepinex_uninstall", body)

    def restore_bepinex(self, game_id: object = None) -> dict[str, object]:
        from core import unity_tools
        from core.game_tools import GameToolError
        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found"}
        def body(folder: str, _progress: ProgressFn, _cancelled: CancelFn) -> dict[str, object]:
            unity = unity_tools.detect_unity(folder, game[2])
            if unity is None or unity.platform != "windows":
                raise GameToolError("BepInEx is not supported for Linux-native Unity games")
            return unity_tools.restore(folder)
        return self._run_game_tool(game_id, "unity", "bepinex_restore", body)

    def set_renpy_console(
        self, game_id: object = None, enabled: object = None, developer: object = False
    ) -> dict[str, object]:
        from core import renpy_tools

        if not isinstance(enabled, bool) or not isinstance(developer, bool):
            return {"success": False, "error": "Enabled and developer must be booleans"}

        def body(folder: str, _p: ProgressFn, _c: CancelFn) -> dict[str, object]:
            return {"state": renpy_tools.set_console(folder, enabled, developer)}

        return self._run_game_tool(game_id, "renpy", "console", body)

    def extract_renpy_archives(
        self, game_id: object = None, options: object = None
    ) -> dict[str, object]:
        from core import renpy_tools
        from core.game_tools import format_bytes

        opts = cast(dict[str, object], options) if isinstance(options, dict) else {}
        mode = str(opts.get("mode") or "folder")
        output = str(opts.get("output") or "")

        def body(
            folder: str, progress: ProgressFn, cancelled: CancelFn
        ) -> dict[str, object]:
            result = renpy_tools.extract_archives(
                folder, mode, output, progress, cancelled
            )
            parts = [
                f"Extracted {result['files']} file(s) "
                + f"({format_bytes(int(str(result['bytes'])))})"
            ]
            if result["skipped"]:
                parts.append(f"skipped {result['skipped']}")
            if result["failed"]:
                parts.append(f"{result['failed']} failed")
            unsupported = cast(list[str], result["unsupported"])
            if unsupported:
                parts.append("unsupported: " + ", ".join(unsupported))
            if result["cancelled"]:
                parts.append("cancelled")
            return {**result, "message": "; ".join(parts)}

        return self._run_game_tool(game_id, "renpy", "extract", body)

    def decrypt_rpgm_assets(self, game_id: object = None) -> dict[str, object]:
        from core import rpgm_tools
        from core.game_tools import GameToolError, rpgm_data_root

        def body(
            folder: str, progress: ProgressFn, cancelled: CancelFn
        ) -> dict[str, object]:
            root = rpgm_data_root(folder)
            if root is None:
                raise GameToolError("System.json was not found")
            result = rpgm_tools.decrypt_assets(root, progress, cancelled)
            message = f"Decrypted {result['decrypted']} file(s)"
            if result["already"]:
                message += f"; {result['already']} already decrypted"
            if result["skipped"]:
                message += f"; skipped {result['skipped']}"
            if result["key_source"] == rpgm_tools.KEY_SOURCE_RECOVERED:
                message += "; key recovered from an image"
            if result["cancelled"]:
                message += "; cancelled"
            return {**result, "message": message}

        return self._run_game_tool(game_id, "rpgm", "decrypt", body)

    def set_rpgm_use_decrypted(
        self, game_id: object = None, enabled: object = None
    ) -> dict[str, object]:
        from core import rpgm_tools
        from core.game_tools import GameToolError, rpgm_data_root

        if not isinstance(enabled, bool):
            return {"success": False, "error": "Enabled must be a boolean"}

        def body(folder: str, _p: ProgressFn, _c: CancelFn) -> dict[str, object]:
            root = rpgm_data_root(folder)
            if root is None:
                raise GameToolError("System.json was not found")
            rpgm_tools.set_use_decrypted(root, enabled)
            return {"use_decrypted": rpgm_tools.read_use_decrypted(root)}

        return self._run_game_tool(game_id, "rpgm", "switch", body)

    def browse_urm_file(self, start_path: str = "") -> str:
        # pywebview validates every filter label against [\w ]+, so the label
        # cannot carry the apostrophe in "Ren'Py" on the Qt dialog path.
        file_types = ("RenPy Archives (*.rpa)",)
        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog(
                "file", directory=start_path, file_types=file_types
            )
        return self._browse_qt_dialog(
            "file", directory=start_path, file_types=file_types
        )
