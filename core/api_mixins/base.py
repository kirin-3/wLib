# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared state and helpers for every ``Api`` mixin.

``ApiBase`` owns ``__init__`` and each method that two or more mixins use."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import closing, contextmanager, suppress
from typing import TYPE_CHECKING, cast

from core import database
from core.database import (
    init_db,
    normalize_launch_mode,
)
from core.f95zone import NON_ENGINE_PREFIXES, normalize_prefix_label
from core.f95zone import normalize_thread_url as _normalize_thread_url
from core.host_platform import (
    get_default_wine_prefix,
    get_extension_dir,
    get_proton_dir,
    host_which,
    is_linux,
    is_windows,
    open_windows_system_target,
)
from core.game_tools import game_tool_folder
from core.bridge_exposure import hide_from_js
from core.launcher import Launcher, proton_compat_data_path
from core.scraper import Scraper
from core.versions import normalize_version
from core.app_version import APP_VERSION
from core.api_mixins.types import (
    ProgressStatus,
    ExtensionSyncStatus,
    OpenPathResult,
    PickerCommandResult,
    BrowseBackend,
    ExtensionServiceStatus,
    GameRecord,
    RuntimeInstallTarget,
    WebviewModule,
    URLResponseContext,
)

if TYPE_CHECKING:
    from webview import Window

    from core.game_files import GameHooks, MoveJob, MoveOutcome
    from core.web_video import Converter
    from core.downloads.manager import DownloadManager
    from core.downloads.queue import DownloadQueue


normalize_thread_url: Callable[[str], str] = cast(
    Callable[[str], str], cast(object, _normalize_thread_url)
)

_torrent_exit_hook = False


# Device-local: the catalog snapshot SHA-256 whose ratings were last filled.
CATALOG_RATING_FILL_SETTING = "catalog_rating_fill_sha"


class ApiBase:
    def __init__(self) -> None:
        self.window: Window | None = None
        from core.steam_sync import SteamSync

        self.steam_sync: SteamSync | None = None
        self._startup_extension_sync_status: ExtensionSyncStatus = {
            "success": True,
            "updated": False,
            "path": get_extension_dir(),
            "bundled_version": "",
            "installed_version": "",
            "reason": "not-run",
        }
        # Make sure our database is initialized
        init_db()
        try:
            from core.database import get_all_games as _list_games_for_prune
            from core.game_attachments import (
                prune_orphan_attachments as _prune_on_startup,
            )

            _existing_ids = {
                int(str(game.get("id")))
                for game in _list_games_for_prune()
                if isinstance(game.get("id"), int)
            }
            _prune_on_startup(_existing_ids)
        except Exception as exc:
            print(f"[wLib] Failed to prune orphan attachments on startup: {exc}")
        from core.library_sync import SyncManager

        self.library_sync: SyncManager = hide_from_js(
            SyncManager(on_pull=self._refresh_synced_library)
        )
        from core.game_covers import CoverCacheService

        from core.cover_cards import CoverCardService

        self.cover_cards: CoverCardService = hide_from_js(
            CoverCardService(on_copies_ready=self._notify_card_copies_ready)
        )
        self.cover_cache: CoverCacheService = hide_from_js(
            CoverCacheService(on_change=self._cover_cache_changed)
        )
        from core.downloads.manager import manager as download_manager
        from core.update_downloads import UpdateDownloads

        self._update_downloads: UpdateDownloads = UpdateDownloads(download_manager)
        try:
            from core.downloads.torrents import supervisor as torrent_supervisor

            torrent_supervisor.load()
            torrent_supervisor.cleanup()
            global _torrent_exit_hook
            if not _torrent_exit_hook:
                import atexit

                _ = atexit.register(torrent_supervisor.shutdown)
                _torrent_exit_hook = True
        except Exception as exc:
            print(f"[wLib] Torrent startup failed: {type(exc).__name__}")
        self.scraper: Scraper = hide_from_js(Scraper())
        self.launcher: Launcher = hide_from_js(Launcher())
        self._download_queue: DownloadQueue = self._build_download_queue(download_manager)
        self._update_running: bool = False
        self._update_cancelled: bool = False
        self._update_outcome: str = "idle"
        self._update_error: str = ""
        self._update_total: int = 0
        self._update_checked: int = 0
        self._update_current: str = ""
        self._update_results: list[dict[str, object]] = []
        self._update_delay_seconds: int = self._get_bulk_check_delay_seconds()
        self._update_refresh_info: bool = False
        self._update_lock: threading.Lock = threading.Lock()
        self._window_focused: bool = True
        self._status_lock: threading.Lock = threading.Lock()
        self._performance_tools: dict[str, bool] | None = None
        self._prefix_tool: subprocess.Popen[bytes] | None = None
        self._prefix_tool_prefix: str = ""
        self._prefix_tool_name: str = ""
        self.launcher.prefix_guard_lock = self._status_lock
        self.launcher.prefix_guard = self._prefix_tool_conflict
        if is_linux():
            from core.steam_runner import runner_pid

            self.launcher.external_alive = lambda game_id: runner_pid(game_id) is not None
        from core.umu import UmuDownload, UmuRuntimePrepare

        self._umu_download: UmuDownload = UmuDownload()
        self._umu_prepare: UmuRuntimePrepare = UmuRuntimePrepare()
        self._app_release: dict[str, object] | None = None
        self._app_release_lock: threading.Lock = threading.Lock()
        # Polled by Settings while download_proton_ge runs; done/total are bytes.
        self._proton_download_status: dict[str, object] = {
            "running": False,
            "stage": "",
            "done": 0,
            "total": 0,
        }
        # Games whose F95 metadata is still being fetched after add_game returned.
        self._metadata_pending: set[int] = set()
        self._metadata_lock: threading.Lock = threading.Lock()
        # Per-game mod tool run (extract/decrypt/switch), polled by the Tools tab.
        self._tool_status: dict[int, dict[str, object]] = {}
        self._tool_lock: threading.Lock = threading.Lock()
        self._bepinex_cache: dict[tuple[str, str], tuple[float, list[dict[str, object]], list[str]]] = {}
        # Games with a save backup/import/restore in flight (one at a time per game).
        self._saves_busy: set[int] = set()
        # Subset of _saves_busy that writes into the saves folder (import/restore).
        self._saves_writing: set[int] = set()
        # Prefixes the Wine dependency and RTP installers are working on.
        self._deps_install_prefix: str = ""
        self._rtp_install_prefix: str = ""
        # Library game an archive install is updating, if any.
        self._archive_install_game_id: int | None = None
        self._saves_busy_lock: threading.Lock = threading.Lock()
        self._deps_install_status: ProgressStatus = {
            "running": False,
            "done": 0,
            "total": 0,
            "current": "",
            "error": "",
        }
        self._rtp_install_status: ProgressStatus = {
            "running": False,
            "done": 0,
            "total": 0,
            "current": "",
            "error": "",
        }
        self._sandbox_install_status: dict[str, object] = {
            "running": False,
            "done": 0,
            "total": 1,
            "current": "",
            "error": "",
        }
        # Polled by the install-from-archive dialog while install_archive runs.
        self._archive_install_status: dict[str, object] = {
            "running": False,
            "stage": "",
            "done": 0,
            "total": 0,
            "cancellable": False,
        }
        # Set by cancel_archive_install(); the install checks it until the new
        # version starts moving into place.
        self._archive_install_cancel: threading.Event = threading.Event()
        # Games whose uninstall runs now; guarded by _status_lock.
        self._uninstalling: set[int] = set()
        from core.game_files import MoveQueue

        # Background game moves, one at a time; polled by get_move_status().
        self._game_moves: MoveQueue = MoveQueue(self._run_move_job)
        import atexit

        _ = atexit.register(self._game_moves.shutdown)
        # Video conversion for the built-in player, one game at a time.
        # _web_media_jobs holds the running job; guarded by _status_lock.
        self._web_media_jobs: dict[int, dict[str, object]] = {}
        # Last finished job per game, for the status poll after the job ends.
        self._web_media_results: dict[int, dict[str, object]] = {}
        self._web_media_cancel: threading.Event = threading.Event()
        self._web_media_converter: Converter | None = None
        _ = atexit.register(self._web_media_shutdown)
        from core.catalog import CatalogClient
        from core.host_platform import get_data_dir

        # Read-only game catalog next to the library database; the startup
        # check runs on its own thread and never blocks or raises.
        self.catalog: CatalogClient = hide_from_js(
            CatalogClient(
                get_data_dir(),
                app_version=APP_VERSION,
                enabled=self._catalog_enabled,
                on_installed=self._fill_ratings_from_catalog,
            )
        )
        if self._catalog_enabled():
            _ = self.catalog.start_background()

    def _load_webview_module(self) -> WebviewModule | None:
        import importlib

        try:
            module = importlib.import_module("webview")
            return cast(WebviewModule, cast(object, module))
        except Exception:
            return None

    @staticmethod
    def _coerce_db_id(value: object) -> int | None:
        """Validate a SQLite row id from the JS bridge.

        Returns the int id, or None for missing/wrong-type/negative/huge ids.
        Bool is rejected (True == 1 in SQLite and would delete the wrong row).
        """
        if isinstance(value, bool):
            return None
        try:
            if isinstance(value, int):
                candidate = value
            elif isinstance(value, str) and value.strip():
                candidate = int(value.strip())
            else:
                candidate = int(str(value))
        except (TypeError, ValueError):
            return None
        # SQLite INTEGER is signed 64-bit; larger Python ints raise OverflowError
        # on bind. Game/target ids are positive autoincrement values.
        if not 1 <= candidate <= 9223372036854775807:
            return None
        return candidate

    def _resolve_runtime_install_target(
        self,
        prefix_path: str | None = None,
        proton_path: str | None = None,
        game_id: object = None,
    ) -> RuntimeInstallTarget:
        from core.database import get_setting

        get_setting_fn = cast(Callable[[str], object | None], get_setting)
        coerced_id = self._coerce_db_id(game_id) if game_id is not None else None
        if coerced_id is not None and not is_windows():
            from core.database import get_game

            row = get_game(coerced_id)
            if row is not None and bool(row.get("sandbox_enabled")):
                # The sandbox mounts the game's prefix read-write, so the game
                # can plant DLLs or registry entries there. Prefix tools and
                # installers run on the host without the sandbox and would run
                # that planted code (game_sandbox.sandbox_wine_prefix).
                raise ValueError(
                    "Wine prefix tools are not available for sandboxed games. Turn off the sandbox for this game first."
                )

        # Like the launcher, an empty per-game value (what the UI sends when no
        # override is set) means "use the global setting", not "use nothing".
        prefix_source = (prefix_path or "").strip() or get_setting_fn(
            "wine_prefix_path"
        )
        proton_source = (proton_path or "").strip() or get_setting_fn("proton_path")

        base_prefix = str(prefix_source or "").strip()
        if not base_prefix:
            base_prefix = get_default_wine_prefix()
        base_prefix = os.path.abspath(os.path.expanduser(base_prefix))

        proton_path_to_use = os.path.expanduser(str(proton_source or "").strip())
        if is_linux() and not proton_path_to_use:
            from core.umu import resolve_runner

            proton_path_to_use = resolve_runner("")
        is_proton = bool(
            proton_path_to_use
            and "proton" in os.path.basename(proton_path_to_use).lower()
        )

        # Mirror the launcher's layout so installs land in the prefix games run in.
        if is_proton:
            resolved_prefix = os.path.join(proton_compat_data_path(base_prefix), "pfx")
        else:
            pfx_path = os.path.join(base_prefix, "pfx")
            resolved_prefix = pfx_path if os.path.isdir(pfx_path) else base_prefix

        return {
            "base_prefix": base_prefix,
            "resolved_prefix": resolved_prefix,
            "proton_path": proton_path_to_use,
            "is_proton": is_proton,
        }

    def _build_runtime_install_env(
        self,
        prefix_path: str | None = None,
        proton_path: str | None = None,
        game_id: object = None,
    ) -> tuple[dict[str, str], RuntimeInstallTarget]:
        target = self._resolve_runtime_install_target(prefix_path, proton_path, game_id)
        env = os.environ.copy()
        if target["is_proton"]:
            from core.umu import build_umu_invocation, locate_umu, runtime_status

            _ = env.pop("WINE", None)
            _ = env.pop("WINESERVER", None)
            if not locate_umu()[0]:
                raise ValueError("umu-launcher is missing. Install it in Settings.")
            if not runtime_status(target["proton_path"])["ready"]:
                raise ValueError("Prepare the Steam Linux Runtime in Settings first.")
            _, env = build_umu_invocation(
                "winetricks",
                prefix=target["base_prefix"],
                runner=target["proton_path"],
                environment=env,
            )
        else:
            env["WINEPREFIX"] = target["resolved_prefix"]
            os.makedirs(env["WINEPREFIX"], exist_ok=True)
        return env, target

    def _get_bundled_extension_dir(self) -> str:
        if getattr(sys, "frozen", False):
            bundle_root = getattr(
                sys,
                "_MEIPASS",
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            )
            return os.path.join(bundle_root, "extension")

        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "extension"
        )

    def _coerce_string_key_dict(self, payload: object) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return None

        payload_dict = cast(dict[object, object], payload)
        return {str(key): value for key, value in payload_dict.items()}

    def _read_manifest_version(self, manifest_path: str) -> str:
        import json

        if not os.path.isfile(manifest_path):
            return ""

        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest_data = self._coerce_string_key_dict(cast(object, json.load(f)))
        except (OSError, ValueError):
            # A truncated or hand-edited manifest must read as "unknown" so the
            # next sync replaces the folder instead of failing forever.
            return ""

        if manifest_data is None:
            return ""

        return str(manifest_data.get("version", "")).strip()

    def _build_host_open_env(self) -> dict[str, str]:
        clean_env: dict[str, str] = os.environ.copy()

        for var in (
            "APPIMAGE",
            "APPDIR",
            "ARGV0",
            "APPIMAGE_SILENT_INSTALL",
            "OWD",
            "APPIMAGE_EXTRACT_AND_RUN",
        ):
            _ = clean_env.pop(var, None)

        original_library_path = str(clean_env.get("LD_LIBRARY_PATH_ORIG") or "").strip()
        if original_library_path:
            clean_env["LD_LIBRARY_PATH"] = original_library_path
        else:
            _ = clean_env.pop("LD_LIBRARY_PATH", None)

        return clean_env

    def _open_with_system_handler(
        self, target: str, missing_opener_error: str
    ) -> OpenPathResult:
        if is_windows():
            opened, error = open_windows_system_target(target)
            if opened:
                return {"success": True}
            return {"success": False, "error": error or missing_opener_error}

        import shutil
        import subprocess

        clean_env = self._build_host_open_env()
        commands: list[list[str]] = [
            ["xdg-open", target],
            ["gio", "open", target],
            ["kde-open5", target],
            ["kde-open", target],
            ["gnome-open", target],
        ]

        launch_errors: list[str] = []
        for command in commands:
            binary = shutil.which(command[0])
            if not binary:
                continue

            resolved_command = [binary, *command[1:]]
            try:
                _ = subprocess.Popen(
                    resolved_command,
                    env=clean_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return {"success": True}
            except Exception as e:
                launch_errors.append(f"{binary}: {e}")

        if launch_errors:
            return {"success": False, "error": "; ".join(launch_errors)}

        return {"success": False, "error": missing_opener_error}

    def _open_path_with_system_handler(self, path: str) -> OpenPathResult:
        normalized_path = os.path.abspath(path)
        return self._open_with_system_handler(
            normalized_path, "No file manager opener found"
        )

    def _normalize_selected_path(self, value: object) -> str:
        from urllib.parse import unquote, urlparse

        raw_value = str(value or "").strip()
        if not raw_value:
            return ""

        if raw_value.startswith("file://"):
            parsed = urlparse(raw_value)
            raw_value = unquote(parsed.path or "")

        return os.path.abspath(os.path.expanduser(raw_value))

    def _coerce_browse_directory(self, start_path: str = "") -> str:
        normalized_path = self._normalize_selected_path(start_path)
        if normalized_path and os.path.isdir(normalized_path):
            return normalized_path
        if normalized_path and os.path.isfile(normalized_path):
            return os.path.dirname(normalized_path)

        parent = os.path.dirname(normalized_path)
        if parent and os.path.isdir(parent):
            return parent

        return os.path.expanduser("~")

    def _run_picker_command(
        self, command: Sequence[str], env: dict[str, str] | None = None
    ) -> PickerCommandResult:
        import subprocess

        try:
            completed = subprocess.run(
                command,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as e:
            return {
                "success": False,
                "cancelled": False,
                "error": str(e),
                "stderr": "",
                "returncode": None,
            }

        stdout = str(completed.stdout or "").strip()
        stderr = str(completed.stderr or "").strip()

        if completed.returncode == 0:
            first_line = stdout.splitlines()[0] if stdout else ""
            selected_path = self._normalize_selected_path(first_line)
            if selected_path:
                return {
                    "success": True,
                    "cancelled": False,
                    "path": selected_path,
                    "stderr": stderr,
                    "returncode": completed.returncode,
                }
            return {
                "success": False,
                "cancelled": True,
                "error": "Picker completed without a selected path",
                "stderr": stderr,
                "returncode": completed.returncode,
            }

        if completed.returncode in (1, 130):
            return {
                "success": False,
                "cancelled": True,
                "error": stderr or "Picker cancelled",
                "stderr": stderr,
                "returncode": completed.returncode,
            }

        return {
            "success": False,
            "cancelled": False,
            "error": stderr or f"Picker exited with status {completed.returncode}",
            "stderr": stderr,
            "returncode": completed.returncode,
        }

    def _desktop_portal_available(self, env: dict[str, str]) -> bool:
        import shutil
        import subprocess

        commands: list[list[str]] = []

        gdbus_path = shutil.which("gdbus")
        if gdbus_path:
            commands.append(
                [
                    gdbus_path,
                    "introspect",
                    "--session",
                    "--dest",
                    "org.freedesktop.portal.Desktop",
                    "--object-path",
                    "/org/freedesktop/portal/desktop",
                ]
            )

        busctl_path = shutil.which("busctl")
        if busctl_path:
            commands.append(
                [busctl_path, "--user", "status", "org.freedesktop.portal.Desktop"]
            )

        dbus_send_path = shutil.which("dbus-send")
        if dbus_send_path:
            commands.append(
                [
                    dbus_send_path,
                    "--session",
                    "--dest=org.freedesktop.portal.Desktop",
                    "--type=method_call",
                    "--print-reply",
                    "/org/freedesktop/portal/desktop",
                    "org.freedesktop.DBus.Peer.Ping",
                ]
            )

        for command in commands:
            try:
                completed = subprocess.run(
                    command,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                continue

            if completed.returncode == 0:
                return True

        return False

    def _should_continue_after_cancel(
        self, backend: BrowseBackend, result: PickerCommandResult
    ) -> bool:
        if not backend.get("continue_on_cancel_error"):
            return False

        stderr = str(result.get("stderr") or "").strip().lower()
        error = str(result.get("error") or "").strip().lower()
        details = f"{stderr}\n{error}"

        if not details.strip():
            return False

        known_failure_markers = backend.get("cancel_error_markers") or ()
        return any(marker.lower() in details for marker in known_failure_markers)

    def _build_linux_browse_backends(
        self, dialog_kind: str, directory: str, file_types: Sequence[str] = ()
    ) -> list[BrowseBackend]:
        import shutil

        backends: list[BrowseBackend] = []
        clean_env = self._build_host_open_env()
        normalized_directory = self._coerce_browse_directory(directory)

        zenity_filters: list[str] = []
        kdialog_filter_parts: list[str] = []
        if dialog_kind != "directory":
            for file_type in file_types:
                label, _, raw_patterns = file_type.partition("(")
                patterns = raw_patterns.rstrip(")").replace(";", " ").strip()
                normalized_patterns = patterns if patterns not in {"*.*", ""} else "*"
                filter_label = label.strip() or "Files"
                zenity_filters.append(
                    f"--file-filter={filter_label} | {normalized_patterns}"
                )
                kdialog_filter_parts.append(f"{filter_label} ({normalized_patterns})")

        zenity_path = shutil.which("zenity")
        if zenity_path:
            zenity_command = [
                zenity_path,
                "--file-selection",
                f"--title={'Select Folder' if dialog_kind == 'directory' else 'Select File'}",
                f"--filename={normalized_directory.rstrip(os.sep) + os.sep}",
            ]
            if dialog_kind == "directory":
                zenity_command.append("--directory")
            else:
                zenity_command.extend(zenity_filters)

            if self._desktop_portal_available(clean_env):
                portal_env = clean_env.copy()
                portal_env["GTK_USE_PORTAL"] = "1"
                backends.append(
                    {
                        "name": "portal",
                        "command": list(zenity_command),
                        "env": portal_env,
                        "continue_on_cancel_error": True,
                        "cancel_error_markers": (
                            "org.freedesktop.portal.desktop",
                            "failed to talk to portal",
                            "cannot open display",
                            "no such name",
                            "service unknown",
                            "name has no owner",
                        ),
                    }
                )
            backends.append(
                {
                    "name": "zenity",
                    "command": list(zenity_command),
                    "env": clean_env,
                }
            )

        kdialog_path = shutil.which("kdialog")
        if kdialog_path:
            if dialog_kind == "directory":
                kdialog_command = [
                    kdialog_path,
                    "--getexistingdirectory",
                    normalized_directory,
                ]
            else:
                filter_string = "\n".join(kdialog_filter_parts)
                kdialog_command = [
                    kdialog_path,
                    "--getopenfilename",
                    normalized_directory,
                    filter_string,
                ]

            backends.append(
                {
                    "name": "kdialog",
                    "command": kdialog_command,
                    "env": clean_env,
                }
            )

        return backends

    def _browse_qt_dialog(
        self, dialog_kind: str, directory: str = "", file_types: Sequence[str] = ()
    ) -> str:
        if not self.window:
            return ""

        webview = self._load_webview_module()
        if webview is None:
            return ""

        if dialog_kind == "directory":
            try:
                dialog_type = webview.FileDialog.FOLDER
            except AttributeError:
                dialog_type = webview.FOLDER_DIALOG
        else:
            try:
                dialog_type = webview.FileDialog.OPEN
            except AttributeError:
                dialog_type = webview.OPEN_DIALOG

        start_directory = self._coerce_browse_directory(directory)
        if file_types:
            result = self.window.create_file_dialog(
                dialog_type,
                allow_multiple=False,
                directory=start_directory,
                file_types=file_types,
            )
        else:
            result = self.window.create_file_dialog(
                dialog_type,
                allow_multiple=False,
                directory=start_directory,
            )
        if result and len(result) > 0:
            return self._normalize_selected_path(result[0])

        return ""

    def _browse_linux_dialog(
        self, dialog_kind: str, directory: str = "", file_types: Sequence[str] = ()
    ) -> str:
        errors: list[str] = []

        for backend in self._build_linux_browse_backends(
            dialog_kind, directory, file_types=file_types
        ):
            result = self._run_picker_command(backend["command"], env=backend["env"])
            if result.get("success"):
                return result.get("path", "")
            if result.get("cancelled"):
                if self._should_continue_after_cancel(backend, result):
                    errors.append(
                        f"{backend['name']}: {result.get('error', 'unknown error')}"
                    )
                    continue
                return ""
            errors.append(f"{backend['name']}: {result.get('error', 'unknown error')}")

        qt_result = self._browse_qt_dialog(
            dialog_kind, directory=directory, file_types=file_types
        )
        if qt_result:
            return qt_result

        if errors:
            print(f"[wLib] Linux chooser fallbacks failed: {'; '.join(errors)}")

        return ""

    def get_extension_service_status(self) -> ExtensionServiceStatus:
        from core import extension_bridge

        status = self._probe_extension_service()
        # The local server answering says nothing about the browser side; the
        # bridge snapshot shows whether an extension is connected and logged in.
        status["bridge"] = extension_bridge.status_snapshot()
        return status

    def _probe_extension_service(self) -> ExtensionServiceStatus:
        import json
        import urllib.request

        request = urllib.request.Request(
            "http://127.0.0.1:8183/api/check?url=__ping__",
            headers={"User-Agent": "wLib-ExtensionStatus"},
        )
        # Never route this loopback probe through a configured HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        try:
            with cast(
                URLResponseContext,
                opener.open(request, timeout=2),
            ) as response:
                if getattr(response, "status", None) != 200:
                    return {
                        "success": True,
                        "reachable": False,
                        "error": f"Unexpected status: {getattr(response, 'status', 'unknown')}",
                    }

                response_bytes = response.read()
                response_text = response_bytes.decode("utf-8")
                payload = self._coerce_string_key_dict(
                    cast(object, json.loads(response_text))
                )
                if payload is None or "exists" not in payload:
                    return {
                        "success": True,
                        "reachable": False,
                        "error": "Unexpected response payload",
                    }

                return {"success": True, "reachable": True}
        except Exception as e:
            return {"success": True, "reachable": False, "error": str(e)}

    def _cover_cache_changed(self) -> None:
        self._schedule_steam_sync()
        self.cover_cards.wake()

    def _notify_card_copies_ready(self) -> None:
        # Device-local copies only: no sync or dirty marking, just a reload.
        if self.window is not None:
            with suppress(Exception):
                _ = cast(
                    object,
                    self.window.evaluate_js(
                        "window.dispatchEvent(new Event('wlib-refresh-library'));"
                    ),
                )

    def _refresh_synced_library(self) -> None:
        self.cover_cache.wake()
        self._schedule_steam_sync()
        if self.window is not None:
            try:
                _ = cast(
                    object,
                    self.window.evaluate_js(
                        "window.dispatchEvent(new Event('wlib-refresh-library'));"
                    ),
                )
            except Exception:
                pass

    def _fetch_added_game_metadata(
        self, game_id: int, normalized_url: str, needs_metadata_backfill: bool
    ) -> None:
        try:
            metadata_result = (
                self._coerce_string_key_dict(
                    self.scraper.get_thread_metadata(normalized_url)
                )
                or {}
            )

            if metadata_result.get("success"):
                _ = self._update_thread_edit_metadata_for_url(
                    normalized_url, metadata_result
                )
                if needs_metadata_backfill:
                    _ = self._backfill_missing_metadata_for_url(
                        normalized_url, metadata_result
                    )
        except Exception as e:
            print(f"Failed to fetch metadata for game {game_id}: {e}")
        finally:
            self.cover_cache.wake()
            with self._metadata_lock:
                self._metadata_pending.discard(game_id)
            if self.window:
                with suppress(Exception):
                    self.window.evaluate_js(
                        "window.dispatchEvent(new Event('wlib-refresh-library'))"
                    )

    def _has_update(
        self,
        current_version: object,
        remote_version: object,
        skipped_version: object = "",
    ) -> bool:
        # Same rule as hasAvailableUpdate() in ui/src/utils/libraryGames.ts: an
        # unknown installed version counts as an update.
        remote = normalize_version(remote_version)
        return (
            bool(remote)
            and normalize_version(current_version) != remote
            and normalize_version(skipped_version) != remote
        )

    def _is_actionable_remote_version(self, version: object | None) -> bool:
        if version is None:
            return False
        value = str(version).strip().lower()
        return value not in ("", "unknown", "n/a", "na", "none", "null")

    def _build_scraper_error_payload(
        self, payload: object, fallback_message: str
    ) -> dict[str, str | bool]:
        code: str | None = None
        message = fallback_message
        browser_fallback: str | None = None

        if isinstance(payload, dict):
            payload_dict = cast(dict[str, object], payload)
            code_value = payload_dict.get("code")
            if isinstance(code_value, str) and code_value.strip():
                code = code_value.strip()
            message = str(
                payload_dict.get("error")
                or payload_dict.get("message")
                or fallback_message
            )
            fallback_value = payload_dict.get("browser_fallback")
            if fallback_value in ("unavailable", "failed", "logged_out"):
                browser_fallback = cast(str, fallback_value)
        elif isinstance(payload, str) and payload.strip():
            message = payload.strip()

        result = {"success": False, "error": message}
        if code:
            result["error_code"] = code
        if browser_fallback is not None:
            result["browser_fallback"] = browser_fallback

        if code == "blocked":
            if browser_fallback == "unavailable":
                result["error"] = (
                    f"{message}. Keep a browser with the wLib extension open "
                    "and try again."
                )
            elif browser_fallback == "failed":
                result["error"] = (
                    f"{message}. Open F95Zone in that browser, complete any "
                    "check it shows, and try again."
                )
            elif browser_fallback is None:
                result["error"] = (
                    f"{message}. F95Zone is challenging HTTP requests; try again later."
                )
        elif code == "login_required":
            if browser_fallback == "unavailable":
                result["error"] = (
                    f"{message}. The wLib browser extension is not connected; "
                    "keep the browser where it is installed open and try again."
                )
            elif browser_fallback == "logged_out":
                result["error"] = (
                    f"{message}. The wLib extension loaded it without an F95Zone "
                    "login. Log in to F95Zone in that browser and keep an "
                    "F95Zone tab open."
                )
            elif browser_fallback == "failed":
                result["error"] = (
                    f"{message}. The wLib extension could not load it; try again."
                )
            else:
                result["error"] = (
                    f"{message}. Log in to F95Zone in the browser where the wLib "
                    "extension is installed."
                )
        elif code == "rate_limited":
            result["error"] = f"{message}; try again later."
        return result

    def _get_bulk_check_delay_seconds(self) -> int:
        from core.scraper import get_request_interval_seconds

        return get_request_interval_seconds()

    def _normalize_tags_csv(self, tags: object) -> str:
        if isinstance(tags, str):
            values: list[str] = [
                part.strip() for part in tags.split(",") if part.strip()
            ]
        elif isinstance(tags, list):
            tag_items = cast(list[object], tags)
            values = [str(part).strip() for part in tag_items if str(part).strip()]
        else:
            values = []

        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(value)
        return ", ".join(deduped)

    def _is_missing_text(self, value: object) -> bool:
        return not isinstance(value, str) or not value.strip()

    def _backfill_missing_metadata_for_url(
        self, url: str, metadata: Mapping[str, object]
    ) -> int:
        normalized_url = normalize_thread_url(url)
        if not normalized_url:
            return 0

        from core.f95zone import split_thread_title

        engine_value = str(metadata.get("engine") or "").strip()
        tags_value = self._normalize_tags_csv(metadata.get("tags"))
        cover_value = str(metadata.get("cover_image") or "").strip()
        # The thread heading names the developer ("Title [v1] [Dev]").
        developer_value = split_thread_title(str(metadata.get("title") or ""))[2]

        if not engine_value and not tags_value and not cover_value and not developer_value:
            return 0

        import sqlite3

        from core.database import get_connection

        updated_rows = 0
        with closing(get_connection()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            _ = cursor.execute(
                "SELECT id, engine, tags, tags_user_edited, cover_image_path, developer FROM games WHERE f95_url = ?",
                (normalized_url,),
            )
            games = cast(list[sqlite3.Row], cursor.fetchall())

            for game in games:
                fields: dict[str, str] = {}
                engine_current = str(cast(object, game["engine"]) or "").strip()
                tags_current = str(cast(object, game["tags"]) or "").strip()
                cover_current = str(
                    cast(object, game["cover_image_path"]) or ""
                ).strip()
                game_id = cast(object, game["id"])
                if not isinstance(game_id, int):
                    continue

                # Older scrapes stored a non-engine prefix such as "VN"; replace it.
                if engine_value and (
                    self._is_missing_text(engine_current)
                    or normalize_prefix_label(engine_current) in NON_ENGINE_PREFIXES
                ):
                    fields["engine"] = engine_value
                tags_user_edited = bool(cast(object, game["tags_user_edited"]))
                if (
                    tags_value
                    and not tags_user_edited
                    and self._is_missing_text(tags_current)
                ):
                    fields["tags"] = tags_value
                    # The tags came from F95, so they are the F95 baseline too.
                    fields["f95_tags"] = tags_value
                if cover_value and self._is_missing_text(cover_current):
                    fields["cover_image_path"] = cover_value
                developer_current = str(cast(object, game["developer"]) or "").strip()
                if developer_value and not developer_current:
                    fields["developer"] = developer_value

                if fields:
                    set_clause = ", ".join(f"{key} = ?" for key in fields)
                    values: list[str | int] = list(fields.values()) + [game_id]
                    _ = cursor.execute(
                        f"UPDATE games SET {set_clause} WHERE id = ?", values
                    )
                    updated_rows += 1

            if updated_rows:
                conn.commit()

        if updated_rows:
            self.cover_cache.wake()
        return updated_rows

    def _store_f95_rating_for_url(
        self, url: str, metadata: Mapping[str, object]
    ) -> dict[int, dict[str, object]]:
        """Store the thread page's F95 rating on each game with ``url``.

        Writes ``rating`` as "X.X/5 (count)" (or "X.X/5" with no count) and
        ``rating_updated_at`` as the read time. A page with no rating changes
        nothing. Returns, for each game whose rating text changed, its
        previous ``rating`` and ``rating_updated_at``.
        """
        from datetime import UTC, datetime

        normalized_url = normalize_thread_url(url)
        raw_rating = metadata.get("rating")
        if (
            not normalized_url
            or isinstance(raw_rating, bool)
            or not isinstance(raw_rating, int | float)
            or not 0 < raw_rating <= 5
        ):
            return {}
        count = str(metadata.get("rating_count") or "").strip()
        rating_text = (
            f"{raw_rating:.1f}/5 ({count})" if count else f"{raw_rating:.1f}/5"
        )
        read_at = datetime.now(UTC).isoformat()

        import sqlite3

        from core.database import get_connection

        previous: dict[int, dict[str, object]] = {}
        with closing(get_connection()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            _ = cursor.execute(
                "SELECT id, rating, rating_updated_at FROM games WHERE f95_url = ?",
                (normalized_url,),
            )
            rows = cast(list[sqlite3.Row], cursor.fetchall())
            for game in rows:
                game_id = cast(object, game["id"])
                if not isinstance(game_id, int):
                    continue
                current = str(cast(object, game["rating"]) or "")
                if current != rating_text:
                    previous[game_id] = {
                        "rating": current,
                        "rating_updated_at": str(
                            cast(object, game["rating_updated_at"]) or ""
                        ),
                    }
            if rows:
                _ = cursor.execute(
                    "UPDATE games SET rating = ?, rating_updated_at = ? WHERE f95_url = ?",
                    (rating_text, read_at, normalized_url),
                )
                conn.commit()
        return previous

    def _fill_ratings_from_catalog(self) -> int:
        """Fill F95 ratings from the installed catalog, once per snapshot.

        Runs on the catalog thread after a snapshot installs and once at
        startup. A catalog rating fills an empty rating, and replaces a stored
        one only when the catalog was built after that rating was read (or
        the read time is unknown) and the value at one decimal differs. Zero,
        missing and out-of-range catalog ratings are skipped. Tags are never
        touched. Returns the number of games changed.
        """
        import re
        import sqlite3
        from datetime import UTC, datetime

        from core.database import get_connection, get_setting, update_setting
        from core.f95zone import extract_thread_id
        from core.game_lookup import f95_thread_ratings

        if not self._catalog_enabled():
            return 0
        snapshot_sha = self.catalog.installed_sha256()
        if not snapshot_sha or get_setting(CATALOG_RATING_FILL_SETTING) == snapshot_sha:
            return 0

        def parse_time(value: object) -> datetime | None:
            try:
                parsed = datetime.fromisoformat(str(value or "").strip())
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

        changed = 0
        with closing(get_connection()) as conn:
            conn.row_factory = sqlite3.Row
            rows = cast(
                list[sqlite3.Row],
                conn.execute(
                    "SELECT id, f95_url, rating, rating_updated_at FROM games WHERE f95_url IS NOT NULL AND f95_url != ''"
                ).fetchall(),
            )
            games: list[tuple[int, int, str, str]] = []
            for row in rows:
                thread_id = extract_thread_id(cast(object, row["f95_url"]))
                game_id = cast(object, row["id"])
                if thread_id.isdigit() and isinstance(game_id, int):
                    games.append(
                        (
                            game_id,
                            int(thread_id),
                            str(cast(object, row["rating"]) or "").strip(),
                            str(cast(object, row["rating_updated_at"]) or "").strip(),
                        )
                    )
            if not games:
                update_setting(CATALOG_RATING_FILL_SETTING, snapshot_sha)
                return 0
            built_at, ratings = f95_thread_ratings([game[1] for game in games])
            built_time = parse_time(built_at)
            if built_time is None:
                return 0  # an unreadable catalog; try again with the next run
            for game_id, thread_id, stored, read_at in games:
                catalog_rating = ratings.get(thread_id)
                if catalog_rating is None or not 0 < catalog_rating <= 5:
                    continue
                if stored:
                    read_time = parse_time(read_at)
                    if read_time is not None and read_time >= built_time:
                        continue
                    match = re.search(r"\d+(?:\.\d+)?", stored)
                    if match and round(float(match.group()), 1) == round(
                        catalog_rating, 1
                    ):
                        continue
                _ = conn.execute(
                    "UPDATE games SET rating = ?, rating_updated_at = ? WHERE id = ?",
                    (f"{round(catalog_rating, 1):.1f}/5", built_at, game_id),
                )
                changed += 1
            if changed:
                conn.commit()
        update_setting(CATALOG_RATING_FILL_SETTING, snapshot_sha)
        if changed:
            self._refresh_synced_library()
        return changed

    def _update_cancelled_now(self) -> bool:
        with self._update_lock:
            return self._update_cancelled

    def _refresh_f95_info_for_url(
        self, url: str, metadata: Mapping[str, object]
    ) -> dict[int, dict[str, object]]:
        """Refresh the cover and the F95 tags of each game with ``url``.

        Replaces a stored remote cover that differs from the thread's, and
        merges the thread's tags with the user's tag changes
        (``core.f95_tags``). Returns, for each game where a field changed,
        the previous values of only the changed fields. Only "Refresh F95
        info" calls this; the automatic backfill never overwrites a cover.
        """
        from core.f95_tags import merge_f95_tags, split_tags

        normalized_url = normalize_thread_url(url)
        if not normalized_url:
            return {}
        cover_value = str(metadata.get("cover_image") or "").strip()
        f95_tags = split_tags(self._normalize_tags_csv(metadata.get("tags"))) or []

        import sqlite3

        from core.database import get_connection

        changes: dict[int, dict[str, object]] = {}
        cover_changed = False
        with closing(get_connection()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            _ = cursor.execute(
                "SELECT id, cover_image_path, tags, f95_tags, tags_user_edited FROM games WHERE f95_url = ?",
                (normalized_url,),
            )
            for game in cast(list[sqlite3.Row], cursor.fetchall()):
                game_id = cast(object, game["id"])
                if not isinstance(game_id, int):
                    continue
                fields: dict[str, object] = {}
                previous: dict[str, object] = {}
                current_cover = str(cast(object, game["cover_image_path"]) or "").strip()
                if cover_value and current_cover != cover_value:
                    fields["cover_image_path"] = cover_value
                    previous["cover_image_path"] = current_cover
                    cover_changed = True

                stored_tags = str(cast(object, game["tags"]) or "")
                raw_baseline = cast(object, game["f95_tags"])
                stored_baseline = None if raw_baseline is None else str(raw_baseline)
                tags, baseline = merge_f95_tags(
                    split_tags(stored_tags) or [],
                    split_tags(stored_baseline),
                    f95_tags,
                    bool(cast(object, game["tags_user_edited"])),
                )
                new_tags = ", ".join(tags)
                if new_tags != ", ".join(split_tags(stored_tags) or []):
                    fields["tags"] = new_tags
                    previous["tags"] = stored_tags
                new_baseline = None if baseline is None else ", ".join(baseline)
                if new_baseline != stored_baseline:
                    fields["f95_tags"] = new_baseline
                    previous["f95_tags"] = stored_baseline

                if fields:
                    set_clause = ", ".join(f"{key} = ?" for key in fields)
                    _ = cursor.execute(
                        f"UPDATE games SET {set_clause} WHERE id = ?",
                        (*fields.values(), game_id),
                    )
                    changes[game_id] = previous
            if changes:
                conn.commit()
        if cover_changed:
            self.cover_cache.wake()
        return changes

    def _update_thread_edit_metadata_for_url(
        self, url: str, metadata: Mapping[str, object]
    ) -> list[int]:
        normalized_url = normalize_thread_url(url)
        if not normalized_url:
            return []

        checked_at_value = str(
            metadata.get("thread_main_post_checked_at") or ""
        ).strip()
        has_edit_metadata = bool(checked_at_value)
        has_completion = "thread_completed" in metadata
        raw_screenshots = metadata.get("screenshots")
        incoming_screenshots: list[str] = []
        if isinstance(raw_screenshots, list):
            for item in cast(list[object], raw_screenshots):
                if isinstance(item, str) and item.strip():
                    incoming_screenshots.append(item.strip())
        has_screenshots = len(incoming_screenshots) > 0
        from core.store_links import coerce_store_links

        incoming_links = coerce_store_links(metadata.get("store_links"))
        if not (
            has_edit_metadata or has_completion or has_screenshots or incoming_links
        ):
            return []

        last_edit_raw = str(metadata.get("thread_main_post_last_edit_at") or "").strip()
        last_edit_value: str | None = last_edit_raw or None

        import json
        import sqlite3

        from core.database import get_connection, replace_game_links

        newly_finished_ids: list[int] = []
        link_game_ids: list[int] = []
        updated_rows = 0
        with closing(get_connection()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            _ = cursor.execute(
                "SELECT id, thread_main_post_last_edit_at, thread_main_post_checked_at, thread_completed, screenshots FROM games WHERE f95_url = ?",
                (normalized_url,),
            )
            games = cast(list[sqlite3.Row], cursor.fetchall())

            for game in games:
                game_id = cast(object, game["id"])
                if not isinstance(game_id, int):
                    continue

                if incoming_links:
                    link_game_ids.append(game_id)
                current_last_edit = cast(object, game["thread_main_post_last_edit_at"])
                current_checked_at = cast(object, game["thread_main_post_checked_at"])
                fields: dict[str, object] = {}
                if has_edit_metadata and (
                    current_last_edit != last_edit_value
                    or current_checked_at != checked_at_value
                ):
                    fields["thread_main_post_last_edit_at"] = last_edit_value
                    fields["thread_main_post_checked_at"] = checked_at_value
                if has_completion:
                    completed = bool(metadata["thread_completed"])
                    was_completed = bool(cast(object, game["thread_completed"]))
                    if completed != was_completed:
                        fields["thread_completed"] = int(completed)
                        if completed:
                            newly_finished_ids.append(game_id)
                if has_screenshots:
                    try:
                        stored_raw = cast(object, game["screenshots"])
                        parsed_screenshots = cast(
                            object, json.loads(str(stored_raw or "[]"))
                        )
                        if isinstance(parsed_screenshots, list):
                            stored_values = cast(list[object], parsed_screenshots)
                        else:
                            stored_values = []
                    except (ValueError, TypeError):
                        stored_values = []
                    if stored_values != incoming_screenshots:
                        fields["screenshots"] = json.dumps(incoming_screenshots)
                if not fields:
                    continue

                set_clause = ", ".join(f"{field} = ?" for field in fields)
                _ = cursor.execute(
                    f"UPDATE games SET {set_clause} WHERE id = ?",
                    (*fields.values(), game_id),
                )
                updated_rows += 1

            if updated_rows:
                conn.commit()

        for game_id in link_game_ids:
            replace_game_links(game_id, "f95_post", incoming_links)

        return newly_finished_ids

    def open_in_browser(self, url: str) -> OpenPathResult:
        """Opens a URL in the user's default browser."""
        from urllib.parse import urlparse

        normalized_url = str(url or "").strip()
        if not normalized_url:
            return {"success": False, "error": "URL is required"}

        # Stored URLs can come from imports; never hand a local path or other
        # scheme to os.startfile/xdg-open, which would run or open it.
        parsed_url = urlparse(normalized_url)
        if parsed_url.scheme.lower() not in ("http", "https") or not parsed_url.netloc:
            return {"success": False, "error": "Only http(s) URLs can be opened"}

        return self._open_with_system_handler(normalized_url, "No browser opener found")

    def check_all_updates(
        self,
        game_ids: Sequence[int] | None = None,
        refresh_info: bool = False,
        *,
        automatic: bool = False,
    ) -> dict[str, object]:
        """Start a background thread to check all games for updates.

        With ``refresh_info`` the pass also reads each F95 thread and
        refreshes the remote cover URL, the F95 rating and the F95 tags
        (merged with the user's tag changes). Each result entry then has
        ``info_changed`` and ``previous`` (the old values of the changed
        fields) for one Undo. Catalog-only (Ryuu) games are left out of such
        a pass.
        """
        from core.database import get_all_games

        wanted_ids: set[int] | None = None
        if game_ids is not None:
            wanted_ids = set()
            for raw_id in game_ids:
                try:
                    wanted_ids.add(int(str(raw_id)))
                except (TypeError, ValueError):
                    continue

        all_games_raw = get_all_games()
        if wanted_ids is not None:
            all_games_raw = [
                game
                for game in all_games_raw
                if isinstance(game.get("id"), int)
                and int(str(game.get("id"))) in wanted_ids
            ]
        games_with_url: list[GameRecord] = []
        from core.store_links import coerce_store_links

        ryuu_games: list[tuple[dict[str, object], str]] = []
        for game in all_games_raw:
            game_id = game.get("id")
            url = game.get("f95_url")
            if isinstance(game_id, int) and not url and self._catalog_enabled():
                ryuu_link = next(
                    (
                        link
                        for link in coerce_store_links(game.get("links"))
                        if link["source"] == "ryuugames"
                    ),
                    None,
                )
                if ryuu_link and not refresh_info:
                    ryuu_games.append((game, ryuu_link["url"]))
            if (
                not isinstance(game_id, int)
                or not isinstance(url, str)
                or not url.strip()
            ):
                continue
            games_with_url.append(
                {
                    "id": game_id,
                    "title": str(game.get("title") or ""),
                    "version": str(game.get("version") or ""),
                    "f95_url": url.strip(),
                    "exe_path": str(game.get("exe_path") or ""),
                    "latest_version": str(game.get("latest_version") or ""),
                    "skipped_version": str(game.get("skipped_version") or ""),
                    "thread_completed": bool(game.get("thread_completed")),
                }
            )

        # Check-and-set in one critical section. _update_running stays True
        # until the worker exits (even after cancel), so runs never overlap.
        with self._update_lock:
            if self._update_running:
                return {"success": False, "error": "Update check already in progress"}
            self._update_running = True
            self._update_cancelled = False
            self._update_outcome = "running"
            self._update_error = ""
            self._update_total = len(games_with_url) + len(ryuu_games)
            self._update_checked = 0
            # The callback fires after a game finishes, so show the game being checked now.
            self._update_current = games_with_url[0]["title"] if games_with_url else ""
            self._update_results = []
            self._update_refresh_info = bool(refresh_info)
            self._update_delay_seconds = self._get_bulk_check_delay_seconds()

        games_by_url = {g["f95_url"]: g for g in games_with_url}
        next_title_by_url = {
            g["f95_url"]: nxt["title"]
            for g, nxt in zip(games_with_url, games_with_url[1:], strict=False)
        }
        current_versions_by_url = {g["f95_url"]: g["version"] for g in games_with_url}
        previous_latest_versions = {
            g["id"]: g.get("latest_version", "") for g in games_with_url
        }
        previous_latest_versions.update(
            {
                int(str(game["id"])): str(game.get("latest_version") or "")
                for game, _ in ryuu_games
            }
        )

        def run_checks() -> None:
            outcome = "failed"
            error = "Update check interrupted before completion"
            try:
                urls = [game["f95_url"] for game in games_with_url]

                def check_callback(url: str, result: object) -> bool:
                    game = games_by_url.get(url)
                    if not game:
                        return True

                    with self._update_lock:
                        if self._update_cancelled:
                            return False  # Stop checking

                    result_dict = self._coerce_string_key_dict(result)
                    if result_dict is None:
                        result_dict = {}

                    has_update = False
                    skipped = False
                    newly_finished = False
                    thread_metadata: dict[str, object] | None = None
                    # Previous rating of each game whose rating a page read changed.
                    rating_previous: dict[int, dict[str, object]] = {}
                    wishlisted = not str(game.get("exe_path") or "").strip()
                    callback_error = str(result_dict.get("error") or "")
                    callback_error_code = str(result_dict.get("code") or "")
                    callback_fallback = result_dict.get("browser_fallback")
                    callback_browser_fallback = (
                        str(callback_fallback)
                        if callback_fallback in ("unavailable", "failed", "logged_out")
                        else ""
                    )
                    if not result_dict.get("success"):
                        # Same guidance as a single check, so the results list
                        # says what to do about blocked and login pages.
                        callback_error = str(
                            self._build_scraper_error_payload(
                                result_dict, "Update check failed"
                            )["error"]
                        )

                    if result_dict.get(
                        "success"
                    ) and self._is_actionable_remote_version(
                        result_dict.get("version")
                    ):
                        remote_version = str(result_dict.get("version") or "").strip()
                        import sqlite3

                        from core.database import get_connection

                        get_connection_fn: Callable[[], sqlite3.Connection] = (
                            get_connection
                        )

                        conn: sqlite3.Connection | None = None
                        try:
                            conn = get_connection_fn()
                            cursor = conn.cursor()
                            _ = cursor.execute(
                                "UPDATE games SET latest_version = ? WHERE f95_url = ?",
                                (remote_version, url),
                            )
                            row = cast(
                                tuple[object, ...] | None,
                                cursor.execute(
                                    "SELECT version, skipped_version, exe_path FROM games WHERE id = ?",
                                    (game["id"],),
                                ).fetchone(),
                            )
                            current_version = current_versions_by_url.get(url, "")
                            skipped_version = game.get("skipped_version", "")
                            if row is not None:
                                current_version = str(row[0] or "")
                                skipped_version = str(row[1] or "")
                                wishlisted = not str(row[2] or "").strip()
                            conn.commit()
                            skipped = bool(normalize_version(skipped_version)) and (
                                normalize_version(skipped_version)
                                == normalize_version(remote_version)
                            )
                            has_update = not wishlisted and self._has_update(
                                current_version, remote_version, skipped_version
                            )
                        except Exception as e:
                            callback_error = f"Failed to save update data: {e}"
                            callback_error_code = "db_write_failed"
                        finally:
                            if conn is not None:
                                conn.close()
                        # A page fallback already read the thread; store what it
                        # found for this game too, not only wishlist prefixes.
                        page_metadata = any(
                            key in result_dict
                            for key in (
                                "thread_completed",
                                "screenshots",
                                "store_links",
                            )
                        )
                        if (
                            not callback_error
                            and not page_metadata
                            and wishlisted
                            and str(game.get("latest_version") or "").strip()
                            and str(game.get("latest_version") or "").strip()
                            != remote_version
                        ):
                            try:
                                metadata = self._coerce_string_key_dict(
                                    self.scraper.get_thread_metadata(url)
                                )
                                thread_metadata = metadata
                                if metadata and metadata.get("success"):
                                    newly_finished = game["id"] in (
                                        self._update_thread_edit_metadata_for_url(
                                            url, metadata
                                        )
                                    )
                                    rating_previous.update(
                                        self._store_f95_rating_for_url(url, metadata)
                                    )
                            except Exception:
                                pass
                    elif result_dict.get("success"):
                        callback_error = "Could not extract a version from thread"
                        callback_error_code = "extract_failed"

                    # A page fallback may have read the thread even when its
                    # version could not be used: keep the Completed flag,
                    # screenshots, links and edit metadata it found.
                    if any(
                        key in result_dict
                        for key in ("thread_completed", "screenshots", "store_links")
                    ):
                        try:
                            finished_ids = self._update_thread_edit_metadata_for_url(
                                url, result_dict
                            )
                            newly_finished = wishlisted and game["id"] in finished_ids
                        except Exception:
                            pass
                        try:
                            rating_previous.update(
                                self._store_f95_rating_for_url(url, result_dict)
                            )
                        except Exception:
                            pass

                    info_previous: dict[str, object] = {}
                    if refresh_info and not self._update_cancelled_now():
                        try:
                            if thread_metadata is None:
                                if "cover_image" in result_dict and (
                                    "thread_main_post_checked_at" in result_dict
                                ):
                                    # The page fallback already read the thread.
                                    thread_metadata = dict(result_dict)
                                    thread_metadata["success"] = True
                                else:
                                    thread_metadata = self._coerce_string_key_dict(
                                        self.scraper.get_thread_metadata(url)
                                    )
                            if thread_metadata and thread_metadata.get("success"):
                                for game_id, old in self._store_f95_rating_for_url(
                                    url, thread_metadata
                                ).items():
                                    _ = rating_previous.setdefault(game_id, old)
                                info_previous.update(
                                    self._refresh_f95_info_for_url(
                                        url, thread_metadata
                                    ).get(game["id"], {})
                                )
                        except Exception:
                            pass  # A failed thread read keeps the current info.
                        info_previous.update(rating_previous.get(game["id"], {}))

                    with self._update_lock:
                        result_entry: dict[str, object] = {
                            "id": game["id"],
                            "title": game["title"],
                            "current_version": game["version"],
                            "latest_version": str(result_dict.get("version") or ""),
                            "has_update": has_update,
                            "skipped": skipped,
                            "newly_finished": newly_finished,
                            "error": callback_error,
                            "error_code": callback_error_code,
                        }
                        if callback_browser_fallback:
                            result_entry["browser_fallback"] = callback_browser_fallback
                        if refresh_info:
                            # Only a visible change counts; a new F95 tag
                            # baseline alone is bookkeeping.
                            result_entry["info_changed"] = any(
                                field in info_previous
                                for field in ("cover_image_path", "rating", "tags")
                            )
                            result_entry["previous"] = info_previous
                        self._update_results.append(result_entry)
                        self._update_checked += 1
                        self._update_current = next_title_by_url.get(url, "")
                    return True

                bulk_results = self._coerce_string_key_dict(
                    self.scraper.check_versions(urls, check_callback)
                )
                if bulk_results is None:
                    bulk_results = {}

                batch_error_payload = self._coerce_string_key_dict(
                    bulk_results.get("__batch_error__")
                )
                if batch_error_payload is None:
                    batch_error_payload = {}

                batch_error = bool(batch_error_payload)

                with self._update_lock:
                    should_append_batch_error = (
                        batch_error
                        and self._update_checked == 0
                        and bool(games_with_url)
                    )

                if should_append_batch_error:
                    with self._update_lock:
                        for game in games_with_url:
                            self._update_results.append(
                                {
                                    "id": game["id"],
                                    "title": game["title"],
                                    "current_version": game["version"],
                                    "latest_version": "",
                                    "has_update": False,
                                    "error": str(
                                        batch_error_payload.get(
                                            "error", "Batch update check failed"
                                        )
                                    ),
                                    "error_code": str(
                                        batch_error_payload.get("code", "batch_failed")
                                    ),
                                }
                            )

                from core.game_lookup import resolve_page
                from core.database import get_game, update_game

                for catalog_game, page_url in ryuu_games:
                    with self._update_lock:
                        if self._update_cancelled:
                            break
                        self._update_current = str(catalog_game.get("title") or "")
                    try:
                        remote = resolve_page(page_url)["catalog_entry"].get("version")
                        if self._is_actionable_remote_version(remote):
                            game_id = int(str(catalog_game["id"]))
                            current = get_game(game_id)
                            if current is not None and not current.get("f95_url"):
                                version = str(remote).strip()
                                _ = update_game(game_id, {"latest_version": version})
                                skipped = bool(
                                    normalize_version(current.get("skipped_version"))
                                ) and normalize_version(
                                    current.get("skipped_version")
                                ) == normalize_version(version)
                                catalog_result: dict[str, object] = {
                                    "id": game_id,
                                    "title": str(current.get("title") or ""),
                                    "current_version": str(
                                        current.get("version") or ""
                                    ),
                                    "latest_version": version,
                                    "has_update": bool(
                                        str(current.get("exe_path") or "").strip()
                                    )
                                    and self._has_update(
                                        current.get("version"),
                                        version,
                                        current.get("skipped_version"),
                                    ),
                                    "skipped": skipped,
                                    "newly_finished": False,
                                    "error": "",
                                    "error_code": "",
                                }
                                with self._update_lock:
                                    self._update_results.append(catalog_result)
                    except Exception:
                        pass  # Catalog problems never fail a library update pass.
                    finally:
                        with self._update_lock:
                            self._update_checked += 1
                            self._update_current = ""

                # A full pass postpones the next automatic check as long as at least
                # one game was checked; a deleted thread or one timeout must not make
                # every launch re-check the library. All-failed passes (offline,
                # blocked, logged out) stay due.
                with self._update_lock:
                    ran_through = (
                        not self._update_cancelled
                        and not batch_error
                        and self._update_checked == self._update_total
                    )
                    completed = ran_through and all(
                        not result["error"] for result in self._update_results
                    )
                    postpone_next_check = ran_through and (
                        completed
                        or any(not result["error"] for result in self._update_results)
                    )
                if postpone_next_check:
                    from core.database import update_setting

                    try:
                        update_setting("last_update_check", database.local_now_iso())
                    except Exception as e:
                        print(f"[wLib] Failed to update last_update_check setting: {e}")
                if completed:
                    outcome = "completed"
                    error = ""
                elif batch_error:
                    error = str(
                        batch_error_payload.get("error") or "Batch update check failed"
                    )
                elif any(result["error"] for result in self._update_results):
                    error = "Some games could not be checked; see results for details"
                if ran_through:
                    from core.database import get_setting
                    from core.desktop_notify import (
                        build_update_summary,
                        send_notification,
                    )

                    updated_titles = [
                        str(result["title"])
                        for result in self._update_results
                        if result.get("has_update")
                        and not result.get("error")
                        and normalize_version(result.get("latest_version"))
                        != normalize_version(
                            previous_latest_versions.get(int(str(result["id"])), "")
                        )
                    ]
                    completed_titles = [
                        str(result["title"])
                        for result in self._update_results
                        if result.get("newly_finished") and not result.get("error")
                    ]
                    summary = build_update_summary(updated_titles, completed_titles)
                    with self._update_lock:
                        notify = not self._update_cancelled and (
                            automatic or not self._window_focused
                        )
                    if (
                        notify
                        and summary
                        and get_setting("update_notifications") != "off"
                    ):
                        send_notification(*summary)
            except Exception as e:
                error = f"Update check failed: {e}"
            finally:
                with self._update_lock:
                    self._update_outcome = (
                        "cancelled" if self._update_cancelled else outcome
                    )
                    self._update_error = "" if self._update_cancelled else error
                    self._update_current = ""
                    self.cover_cache.wake()
                    self._update_running = False

        thread = threading.Thread(target=run_checks, daemon=True)
        try:
            thread.start()
        except Exception as exc:
            with self._update_lock:
                self._update_running = False
                self._update_outcome = "failed"
                self._update_error = f"Update check failed to start: {exc}"
                self._update_current = ""
            return {"success": False, "error": f"Update check failed to start: {exc}"}
        with self._update_lock:
            total = self._update_total
            delay_seconds = self._update_delay_seconds
        return {
            "success": True,
            "total": total,
            "delay_seconds": delay_seconds,
        }

    def get_available_runners(self) -> dict[str, object]:
        """Find Wine/Proton in wLib, Steam and Lutris installations."""
        if not is_linux():
            return {"success": True, "runners": []}

        import os
        from pathlib import Path

        runners: list[dict[str, str]] = []
        seen: set[str] = set()

        def add_runner(name: str, path: str, resolved_path: str | None = None) -> None:
            real_path = os.path.realpath(resolved_path or path)
            if real_path not in seen:
                seen.add(real_path)
                runners.append({"name": name, "path": path})

        # Check system wine
        system_wine = host_which("wine")
        if system_wine:
            add_runner("System Wine", "wine", system_wine)
        runners.extend(
            [
                {"name": "GE-Proton (latest)", "path": "GE-Proton"},
                {"name": "UMU-Proton", "path": "UMU-Proton"},
            ]
        )

        proton_dir = get_proton_dir()
        if os.path.exists(proton_dir):
            try:
                proton_entries = sorted(os.listdir(proton_dir))
            except OSError as e:
                # An unreadable proton folder must not fail the whole listing.
                print(f"[wLib] Could not read {proton_dir}: {e}")
                proton_entries = []
            for entry in proton_entries:
                full_path = os.path.join(proton_dir, entry)
                if os.path.isdir(full_path):
                    proton_exec = os.path.join(full_path, "proton")
                    if os.path.exists(proton_exec):
                        add_runner(entry, proton_exec)

        home = Path(os.path.expanduser("~"))
        steam_runners: list[Path] = []
        for root in (
            ".steam/root",
            ".steam/steam",
            ".local/share/Steam",
            ".var/app/com.valvesoftware.Steam/data/Steam",
            ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        ):
            steam_runners.extend((home / root).glob("compatibilitytools.d/*/proton"))
            steam_runners.extend((home / root).glob("steamapps/common/Proton*/proton"))
        for path in sorted(
            steam_runners, key=lambda path: (path.parent.name, str(path))
        ):
            if path.is_file():
                add_runner(f"{path.parent.name} (Steam)", str(path))

        lutris_runners: list[Path] = []
        for root in (
            ".local/share/lutris/runners/wine",
            ".var/app/net.lutris.Lutris/data/lutris/runners/wine",
        ):
            lutris_runners.extend((home / root).glob("*/bin/wine"))
        for path in sorted(lutris_runners, key=lambda path: path.parent.parent.name):
            if path.is_file():
                add_runner(f"{path.parent.parent.name} (Lutris)", str(path))

        return {"success": True, "runners": runners}

    def _schedule_steam_sync(self) -> None:
        if self.steam_sync is not None:
            self.steam_sync.schedule()

    def _build_download_queue(self, download_manager: DownloadManager) -> DownloadQueue:
        """The one download queue over both workers; restores the saved queue."""
        from core import power
        from core.downloads import torrents
        from core.downloads.queue import DownloadQueue
        from core.downloads.workers import HostWorker, TorrentWorker

        def fetch_for(rpdl_id: int) -> Callable[[], bytes]:
            return lambda: self._fetch_checked_rpdl_torrent(rpdl_id)

        queue = DownloadQueue(
            [
                HostWorker(download_manager, self._update_downloads.relink),
                TorrentWorker(lambda: torrents.supervisor, fetch_for),
            ],
            running_games=self.get_running_games,
            on_battery=power.on_battery,
        )
        self._update_downloads.queue = queue
        try:
            queue.load()
        except Exception as exc:
            print(f"[wLib] Download queue startup failed: {type(exc).__name__}")
        return queue

    def get_running_games(self) -> list[int]:
        games = set(self.launcher.get_running_games())
        if is_linux():
            from core.steam_runner import running_games

            games.update(running_games())
        return sorted(games)

    def _tool_game(self, game_id: object) -> tuple[int, str, str] | None:
        """(id, executable folder) from the stored row, or None for an unknown id."""
        from core.database import get_game

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return None
        row = get_game(coerced_id)
        if row is None:
            return None
        try:
            return coerced_id, game_tool_folder(row), str(row.get("exe_path") or "")
        except ValueError:
            return coerced_id, "", ""

    def _winetricks_command(
        self, target: RuntimeInstallTarget, env: dict[str, str]
    ) -> tuple[list[str], dict[str, str]]:
        if target["is_proton"]:
            from core.umu import build_umu_invocation, resolve_codename

            runner = resolve_codename(target["proton_path"])
            folder = runner if os.path.isdir(runner) else os.path.dirname(runner)
            if not os.path.isfile(os.path.join(folder, "protonfixes", "winetricks")):
                raise ValueError("Pick GE-Proton or UMU-Proton with Winetricks installed.")
            return build_umu_invocation("winetricks", prefix=target["base_prefix"], runner=runner, environment=env)
        path = host_which("winetricks")
        if not path:
            raise ValueError("Winetricks is not installed. Install it first, then try again.")
        return [path], env

    def _prefix_tool_conflict(self, prefix: str, game_id: int | None = None) -> str | None:
        # The caller holds _status_lock while reserving the game in Launcher.
        if game_id is not None:
            conflict = self._game_busy_reason(game_id)
            if conflict:
                return conflict
        if not prefix:
            return None
        if self._prefix_tool_running() and prefix == self._prefix_tool_prefix:
            return f"{self._prefix_tool_name} is open on this Wine prefix. Close it first."
        # winetricks and the RTP setups end with wineserver -k, which kills the game.
        if (self._deps_install_status["running"] and prefix == self._deps_install_prefix) or (
            self._rtp_install_status["running"] and prefix == self._rtp_install_prefix
        ):
            return "A Wine runtime install is running on this prefix. Wait for it to finish."
        return None

    @contextmanager
    def _save_operation(self, game_id: int, writes: bool = False) -> Generator[None]:
        """Allow one backup/import/restore per game at a time.

        Writing operations also refuse while the game runs: it would overwrite
        the restored saves when it quits. _status_lock serializes this with
        the launcher's reservation, which refuses launching meanwhile.
        """
        from core.game_saves import GameSavesError

        with self._status_lock, self._saves_busy_lock:
            if game_id in self._saves_busy:
                raise GameSavesError(
                    "A save operation is already running for this game"
                )
            if writes and game_id in self.get_running_games():
                raise GameSavesError("Close the game before changing its saves")
            if writes:
                files_busy = self._game_files_busy_reason(game_id)
                if files_busy:
                    raise GameSavesError(files_busy)
            self._saves_busy.add(game_id)
            if writes:
                self._saves_writing.add(game_id)
        try:
            yield
        finally:
            with self._saves_busy_lock:
                self._saves_busy.discard(game_id)
                self._saves_writing.discard(game_id)

    def _saves_game_state(
        self, game_id: object
    ) -> tuple[int, dict[str, object], str, list[dict[str, object]]] | None:
        """(id, row, effective folder, detected) from the stored row, or None."""
        from core.database import get_game
        from core.game_saves import detect_save_locations, resolve_effective_folder

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return None
        row = get_game(coerced_id)
        if row is None:
            return None
        override = str(row.get("save_path_override") or "").strip()
        detected: list[dict[str, object]] = []
        if not override:
            native = normalize_launch_mode(row.get("launch_mode")) == "native"
            from core.database import html_builtin_player_enabled
            from core.web_launch import resolve_web_player

            uses_player = (
                resolve_web_player(
                    row.get("exe_path"),
                    row.get("launch_mode"),
                    row.get("html_player"),
                    html_builtin_player_enabled(),
                )
                == "builtin"
            )
            locations = detect_save_locations(
                str(row.get("exe_path") or ""),
                str(row.get("title") or ""),
                "" if native else str(row.get("custom_prefix") or ""),
                "" if native else str(row.get("proton_version") or ""),
                windows=is_windows(),
                game_id=coerced_id,
                sandbox_enabled=bool(row.get("sandbox_enabled")),
                web_player=uses_player,
            )
            detected = [dict(location) for location in locations]
            effective = resolve_effective_folder("", locations)
        else:
            effective = override
        return coerced_id, row, effective, detected

    def _game_files_busy_reason(self, game_id: int, *, ignore_move: bool = False) -> str | None:
        """The uninstall or move that changes the game's folder, or None.

        Caller holds _status_lock. Takes no other Api lock, so the mod tool and
        save checks can call it while they hold their own locks.
        """
        from core.game_files import MOVING_BUSY, UNINSTALLING_BUSY

        if game_id in self._uninstalling:
            return UNINSTALLING_BUSY
        if not ignore_move and self._game_moves.state(game_id):
            return MOVING_BUSY
        if game_id in self._web_media_jobs:
            from core.web_video import WEB_MEDIA_BUSY

            return WEB_MEDIA_BUSY
        return None

    def _set_web_media_converter(self, converter: Converter | None) -> None:
        self._web_media_converter = converter

    def _web_media_shutdown(self) -> None:
        """Stop a running conversion when wLib quits; the partial file is deleted."""
        self._web_media_cancel.set()
        converter = self._web_media_converter
        if converter is not None:
            converter.kill()

    def _game_busy_reason(self, game_id: int, *, ignore_move: bool = False) -> str | None:
        """Why the game's files are in use by wLib, or None. Caller holds _status_lock.

        `ignore_move` leaves out the game's own move, for the move job itself.
        """
        files_busy = self._game_files_busy_reason(game_id, ignore_move=ignore_move)
        if files_busy:
            return files_busy
        with self._tool_lock:
            if game_id in self._tool_status:
                return "A mod tool is changing this game's files. Wait for it to finish."
        with self._saves_busy_lock:
            if game_id in self._saves_writing:
                return "Saves are being restored for this game. Wait for it to finish."
        if self._archive_install_status["running"] and self._archive_install_game_id == game_id:
            return "This game is being updated. Wait for the install to finish."
        return None

    def _effective_saves_folder(self, game_id: int) -> str:
        state = self._saves_game_state(game_id)
        return state[2] if state is not None else ""

    def _game_file_hooks(self, *, ignore_move: bool = False) -> GameHooks:
        from core.game_files import GameHooks

        def busy(game_id: int) -> str | None:
            with self._status_lock:
                return self._game_busy_reason(game_id, ignore_move=ignore_move)

        return GameHooks(
            running=lambda game_id: game_id in self.get_running_games(),
            busy=busy,
            saves_folder=self._effective_saves_folder,
        )

    def _game_moved(self, game_id: int) -> None:
        """After a move changed a game's paths: Steam shortcuts and the library view."""
        _ = game_id
        self._schedule_steam_sync()
        if self.window:
            with suppress(Exception):
                self.window.evaluate_js(
                    "window.dispatchEvent(new Event('wlib-refresh-library'))"
                )

    def _run_move_job(
        self,
        job: MoveJob,
        cancel: threading.Event,
        on_bytes: Callable[[int], None],
        set_total: Callable[[int], None],
    ) -> MoveOutcome:
        from core.game_files import MoveHooks, run_move_job

        base = self._game_file_hooks(ignore_move=True)
        hooks = MoveHooks(
            running=base.running,
            busy=base.busy,
            saves_folder=lambda _game_id: "",
            moved=self._game_moved,
        )
        return run_move_job(job, cancel, on_bytes, set_total, hooks)

    def _prefix_tool_running(self) -> bool:
        return self._prefix_tool is not None and self._prefix_tool.poll() is None

    def _catalog_enabled(self) -> bool:
        import sqlite3

        from core.database import get_setting

        try:
            return get_setting("catalog_enabled") != "0"
        except sqlite3.Error:
            return True

    @staticmethod
    def _save_backups_root() -> str:
        from core.host_platform import get_data_subdir

        return get_data_subdir("save-backups")

    @staticmethod
    def _fetch_checked_rpdl_torrent(torrent_id: int) -> bytes:
        """The RPDL `.torrent` bytes, checked against RPDL's info hash when it has one.

        Raises `rpdl.RpdlError` (or another `DownloadError`). The bytes hold the
        user's passkey: never log them.
        """
        import re

        from core.downloads.bencode import parse_torrent
        from core.downloads.hosts import rpdl

        data = rpdl.fetch_torrent(torrent_id)
        try:
            info_hash = str(rpdl.details(torrent_id).get("info_hash") or "")
        except rpdl.RpdlError:
            info_hash = ""  # Details are optional; the .torrent is enough.
        # Only a hex v1 hash is comparable; RPDL may also send base32 or v2 hashes.
        hex_hash = info_hash.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", info_hash) else ""
        if hex_hash and hex_hash != parse_torrent(data).info_hash:
            raise rpdl.RpdlError(rpdl.KIND_INVALID, "RPDL sent an invalid response")
        return data
