# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings, diagnostics, pickers, catalog and app updates."""
from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, cast

from core.database import (
    RPGMAKER_LINUX_RUNNER_SETTING,
)
from core.host_platform import (
    PlatformCapabilities,
    get_platform_capabilities as resolve_platform_capabilities,
    is_windows,
)
from core.bridge_exposure import hide_from_js, python_only
from core.app_version import APP_VERSION
from core.api_mixins.base import ApiBase
from core.api_mixins.types import (
    OpenPathResult,
    URLResponseContext,
)

if TYPE_CHECKING:
    from webview import Window

_tag_color_lock = threading.Lock()


class SettingsMixin(ApiBase):
    @python_only
    def set_window(self, window: Window | None) -> None:
        self.window: Window | None = hide_from_js(window) if window is not None else None

    def get_platform_capabilities(self) -> PlatformCapabilities:
        return resolve_platform_capabilities()

    def get_catalog_status(self) -> dict[str, object]:
        try:
            return {
                "success": True,
                "enabled": self._catalog_enabled(),
                **self.catalog.status(),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Could not read the catalog"}

    def check_catalog_now(self) -> dict[str, object]:
        if not self._catalog_enabled():
            return {"success": False, "error": "The game catalog is turned off"}
        started = self.catalog.start_background(force=True)
        return {**self.get_catalog_status(), "started": started}

    def get_settings(self) -> dict[str, object]:
        from core.database import (
            DEFAULT_LIBRARY_FOLDER_PATTERN,
            get_collections,
            get_custom_play_statuses,
            get_library_filter_presets,
            get_save_backup_keep,
            get_tag_colors,
            get_setting,
        )

        get_setting_fn = cast(Callable[[str], object | None], get_setting)

        rpgmaker_linux_runner_path = str(
            get_setting_fn(RPGMAKER_LINUX_RUNNER_SETTING) or ""
        )
        from core.database import get_library_folders

        folders, default_folder = get_library_folders()
        return {
            "proton_path": str(get_setting_fn("proton_path") or ""),
            "wine_prefix_path": str(get_setting_fn("wine_prefix_path") or ""),
            "custom_play_statuses": get_custom_play_statuses(),
            "collections": get_collections(),
            "library_filter_presets": get_library_filter_presets(),
            "tag_colors": get_tag_colors(),
            "save_backup_keep": get_save_backup_keep() or "all",
            "urm_rpa_path": str(get_setting_fn("urm_rpa_path") or ""),
            "enable_logging": get_setting_fn("enable_logging") == "true",
            "sandbox_new_games": get_setting_fn("sandbox_new_games") == "true",
            "gamemode_enabled": get_setting_fn("gamemode_enabled") == "true",
            "mangohud_enabled": get_setting_fn("mangohud_enabled") == "true",
            **self._gamescope_settings_payload(get_setting_fn),
            "html_builtin_player": get_setting_fn("html_builtin_player") != "false",
            "web_player_devtools": get_setting_fn("web_player_devtools") != "false",
            **self._torrent_settings_payload(),
            "sandboxie_custom_path": str(get_setting_fn("sandboxie_custom_path") or ""),
            "update_notifications": get_setting_fn("update_notifications") != "off",
            "rpgmaker_linux_runner_path": rpgmaker_linux_runner_path,
            "rpgmaker_linux_runner_status": self.launcher.get_rpgmaker_linux_runner_status(
                rpgmaker_linux_runner_path
            ),
            "library_folder": default_folder,
            "library_folders": folders,
            "library_folder_pattern": str(
                get_setting_fn("library_folder_pattern")
                or DEFAULT_LIBRARY_FOLDER_PATTERN
            ),
            "downloads_folder": str(get_setting_fn("downloads_folder") or ""),
            "downloads_folder_exists": (
                os.path.isdir(
                    os.path.abspath(
                        os.path.expanduser(
                            str(get_setting_fn("downloads_folder") or "")
                        )
                    )
                )
                if str(get_setting_fn("downloads_folder") or "").strip()
                else True
            ),
            "download_detection_enabled": (
                get_setting_fn("download_detection_enabled") or "true"
            )
            == "true",
            "catalog_enabled": get_setting_fn("catalog_enabled") != "0",
        }

    def get_diagnostics(self) -> dict[str, object]:
        from core.diagnostics import build_report, collect_basic

        try:
            basic = collect_basic(
                APP_VERSION,
                self._read_manifest_version(
                    os.path.join(self._get_bundled_extension_dir(), "manifest.json")
                ),
            )
            return {
                "success": True,
                "basic": basic,
                "report_basic": build_report(basic),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_diagnostics_tools(self) -> dict[str, object]:
        from core.diagnostics import collect_tools

        try:
            return {
                "success": True,
                "tools": collect_tools(
                    self.get_available_runners, self.get_extension_service_status
                ),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_diagnostics_report(self) -> dict[str, object]:
        from core.diagnostics import build_report, collect_basic, collect_tools

        try:
            basic = collect_basic(
                APP_VERSION,
                self._read_manifest_version(
                    os.path.join(self._get_bundled_extension_dir(), "manifest.json")
                ),
            )
            tools = collect_tools(
                self.get_available_runners, self.get_extension_service_status
            )
            return {"success": True, "report": build_report(basic, tools)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def open_log_folder(self) -> dict[str, object]:
        from core.host_platform import get_data_dir

        path = os.path.join(get_data_dir(), "logs")
        try:
            os.makedirs(path, exist_ok=True)
            result = self._open_path_with_system_handler(path)
            if not result["success"]:
                return {
                    "success": False,
                    "error": f"Could not open {path}: {result.get('error', 'Unknown error')}",
                }
            return {"success": True}
        except OSError as exc:
            return {"success": False, "error": f"Could not open {path}: {exc}"}

    def set_tag_color(self, tag: object, swatch: object = None) -> dict[str, object]:
        from core.database import TAG_COLOR_SWATCHES, get_tag_colors, update_setting

        if not isinstance(tag, str) or not tag.strip():
            return {"success": False, "error": "Tag must be a nonempty name"}
        if swatch is not None and swatch != "" and swatch not in TAG_COLOR_SWATCHES:
            return {"success": False, "error": "Unknown tag color"}
        with _tag_color_lock:
            colors = get_tag_colors()
            name = tag.strip().lower()
            if swatch is None or swatch == "":
                _ = colors.pop(name, None)
            else:
                colors[name] = str(swatch)
            update_setting("tag_colors", json.dumps(colors))
        return {"success": True, "tag_colors": colors}

    def save_settings(self, settings: object = None) -> dict[str, object]:
        import sqlite3

        from core.database import (
            CANONICAL_PLAY_STATUSES,
            DEFAULT_LIBRARY_FOLDER_PATTERN,
            SAVE_BACKUP_KEEP_MAX,
            get_setting,
            normalize_play_status,
            update_settings,
        )

        if not isinstance(settings, Mapping):
            return {"success": False, "error": "Settings must be an object"}

        settings_map = cast(Mapping[str, object], settings)
        # Checked first, so a bad gamescope value never leaves other settings half saved.
        from core import gamescope

        gamescope_values: dict[str, str] = {}
        for gamescope_key in gamescope.SETTINGS_DEFAULTS:
            if gamescope_key not in settings_map:
                continue
            raw_value = settings_map[gamescope_key]
            if gamescope_key == "gamescope_enabled":
                gamescope_values[gamescope_key] = "true" if raw_value is True else "false"
                continue
            if isinstance(raw_value, bool) or not (
                raw_value is None or isinstance(raw_value, (str, int))
            ):
                return {"success": False, "field": gamescope_key, "error": f"Setting '{gamescope_key}' must be a string"}
            text_value = "" if raw_value is None else str(raw_value)
            error = gamescope.settings_error(gamescope_key, text_value)
            if error:
                return {"success": False, "field": gamescope_key, "error": error}
            gamescope_values[gamescope_key] = gamescope.normalize_setting(gamescope_key, text_value)
        # Checked first, so a bad torrent value never leaves other settings half saved.
        from core.downloads import torrent_settings

        torrent_values: dict[str, str] = {}
        for torrent_key in (
            torrent_settings.SEED_MODE_KEY,
            torrent_settings.SEED_RATIO_KEY,
            torrent_settings.SEED_HOURS_KEY,
            torrent_settings.DOWNLOAD_LIMIT_KEY,
            torrent_settings.UPLOAD_LIMIT_KEY,
            torrent_settings.PORT_MAPPING_KEY,
            torrent_settings.EXPORT_WARNING_KEY,
        ):
            if torrent_key in settings_map:
                clean = torrent_settings.normalize_for_save(
                    torrent_key, settings_map[torrent_key]
                )
                if clean is None:
                    return {"success": False, "error": "Enter a valid torrent setting"}
                torrent_values[torrent_key] = clean
        if torrent_settings.WATCH_FOLDER_KEY in settings_map:
            watch = torrent_settings.normalize_watch_folder(
                settings_map[torrent_settings.WATCH_FOLDER_KEY]
            )
            if watch is None:
                return {"success": False, "error": torrent_settings.WATCH_FOLDER_ERROR}
            torrent_values[torrent_settings.WATCH_FOLDER_KEY] = watch
        from core.database import get_library_folders, normalize_library_folders

        folder_pair: tuple[list[str], str] | None = None
        if "library_folders" in settings_map or "library_folder" in settings_map:
            _existing, existing_default = get_library_folders()
            default = str(
                settings_map.get("library_folder", existing_default) or ""
            ).strip()
            try:
                folders = normalize_library_folders(
                    settings_map.get("library_folders", [default] if default else [])
                )
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            identity = os.path.realpath(os.path.expanduser(default)) if default else ""
            canonical = next(
                (
                    p
                    for p in folders
                    if os.path.realpath(os.path.expanduser(p)) == identity
                ),
                "",
            )
            if (folders and not canonical) or (default and not folders):
                return {
                    "success": False,
                    "error": "The default library folder must be in the library folder list.",
                }
            folder_pair = folders, canonical
        custom_statuses: list[str] = []
        if "custom_play_statuses" in settings_map:
            raw_statuses = settings_map["custom_play_statuses"]
            if not isinstance(raw_statuses, list):
                return {
                    "success": False,
                    "error": "Play statuses must be a list of names",
                }
            seen = {status.casefold() for status in CANONICAL_PLAY_STATUSES}
            for status in cast(list[object], raw_statuses):
                if not isinstance(status, str) or not 1 <= len(status.strip()) <= 40:
                    return {
                        "success": False,
                        "error": "Play status names must contain 1–40 characters",
                    }
                name = status.strip()
                if name.casefold() in seen:
                    return {
                        "success": False,
                        "error": f"Play status '{name}' already exists",
                    }
                # Legacy aliases (e.g. "Replaying") would be rewritten to a built-in status on save.
                if normalize_play_status(name) != name:
                    return {
                        "success": False,
                        "error": f"Play status '{name}' is reserved",
                    }
                seen.add(name.casefold())
                custom_statuses.append(name)

        save_backup_keep: str | None = None
        if "save_backup_keep" in settings_map:
            raw_keep = settings_map["save_backup_keep"]
            if isinstance(raw_keep, bool):
                raw_keep = None
            if isinstance(raw_keep, (int, str)):
                candidate = str(raw_keep).strip()
                if candidate == "all":
                    save_backup_keep = candidate
                elif candidate.isascii() and candidate.isdigit():
                    try:
                        parsed_keep = int(candidate)
                    except (ValueError, OverflowError):
                        # A bridge payload can contain more digits than Python
                        # will convert; treat it like any other invalid value.
                        parsed_keep = 0
                    if 1 <= parsed_keep <= SAVE_BACKUP_KEEP_MAX:
                        save_backup_keep = candidate
            if save_backup_keep is None:
                return {
                    "success": False,
                    "error": f"Save backups to keep must be 'all' or 1–{SAVE_BACKUP_KEEP_MAX}",
                }

        for key in (
            "proton_path",
            "wine_prefix_path",
            "urm_rpa_path",
            "library_folder",
            "library_folder_pattern",
            "downloads_folder",
            RPGMAKER_LINUX_RUNNER_SETTING,
            "sandboxie_custom_path",
        ):
            if key in settings_map:
                value = settings_map[key]
                if value is not None and not isinstance(value, str):
                    return {
                        "success": False,
                        "error": f"Setting '{key}' must be a string",
                    }

        if "sandboxie_custom_path" in settings_map:
            import ntpath

            custom = str(settings_map.get("sandboxie_custom_path") or "").strip()
            if custom and ntpath.basename(custom).casefold() != "start.exe":
                return {
                    "success": False,
                    "error": "Choose the Sandboxie Start.exe executable.",
                }

        get_setting_fn = cast(Callable[[str], object | None], get_setting)
        # Collect every write and commit them together, so a locked database
        # cannot leave only some of the settings saved.
        pending: dict[str, str] = {}

        def update_setting_fn(key: str, value: str) -> None:
            pending[key] = value

        def get_payload_or_existing(key: str, default: str = "") -> str:
            if key in settings_map:
                return str(settings_map.get(key, "") or "")
            return str(get_setting_fn(key) or default)

        try:
            rpgmaker_linux_runner_path = str(
                get_payload_or_existing(RPGMAKER_LINUX_RUNNER_SETTING) or ""
            ).strip()
            raw_enable_logging = (
                settings_map.get("enable_logging")
                if "enable_logging" in settings_map
                else get_setting_fn("enable_logging")
            )
            enable_logging = (
                "true"
                if raw_enable_logging is True
                or str(raw_enable_logging).strip().lower() == "true"
                else "false"
            )
            update_setting_fn("proton_path", get_payload_or_existing("proton_path"))
            update_setting_fn(
                "wine_prefix_path", get_payload_or_existing("wine_prefix_path")
            )
            update_setting_fn("enable_logging", enable_logging)
            for tool_key in (
                "gamemode_enabled",
                "mangohud_enabled",
                "html_builtin_player",
                "web_player_devtools",
            ):
                if tool_key in settings_map:
                    update_setting_fn(tool_key, "true" if settings_map[tool_key] is True else "false")
            if "sandbox_new_games" in settings_map:
                update_setting_fn(
                    "sandbox_new_games",
                    "true" if settings_map["sandbox_new_games"] is True else "false",
                )
            if "sandboxie_custom_path" in settings_map:
                update_setting_fn(
                    "sandboxie_custom_path",
                    str(settings_map["sandboxie_custom_path"] or "").strip(),
                )
            if "update_notifications" in settings_map:
                update_setting_fn(
                    "update_notifications",
                    "off" if settings_map["update_notifications"] is False else "on",
                )
            update_setting_fn(RPGMAKER_LINUX_RUNNER_SETTING, rpgmaker_linux_runner_path)
            update_setting_fn(
                "urm_rpa_path", get_payload_or_existing("urm_rpa_path").strip()
            )
            if folder_pair is not None:
                update_setting_fn("library_folders", json.dumps(folder_pair[0]))
                update_setting_fn("library_folder", folder_pair[1])
            if "library_folder_pattern" in settings_map:
                raw_pattern = str(
                    settings_map.get("library_folder_pattern") or ""
                ).strip()
                update_setting_fn(
                    "library_folder_pattern",
                    raw_pattern or DEFAULT_LIBRARY_FOLDER_PATTERN,
                )
            if "downloads_folder" in settings_map:
                update_setting_fn(
                    "downloads_folder",
                    str(settings_map.get("downloads_folder") or "").strip(),
                )
            if "download_detection_enabled" in settings_map:
                raw_detection = settings_map.get("download_detection_enabled")
                update_setting_fn(
                    "download_detection_enabled",
                    "true"
                    if raw_detection is True
                    or str(raw_detection).strip().lower() == "true"
                    else "false",
                )
            catalog_change: bool | None = None
            if "catalog_enabled" in settings_map:
                was_enabled = get_setting_fn("catalog_enabled") != "0"
                catalog_enabled = settings_map["catalog_enabled"] is not False
                update_setting_fn("catalog_enabled", "1" if catalog_enabled else "0")
                if not catalog_enabled or not was_enabled:
                    catalog_change = catalog_enabled
            if save_backup_keep is not None:
                update_setting_fn("save_backup_keep", save_backup_keep)
            pending.update(torrent_values)
            pending.update(gamescope_values)
            update_settings(
                pending,
                custom_play_statuses=custom_statuses
                if "custom_play_statuses" in settings_map
                else None,
            )
            if catalog_change is False:
                self.catalog.remove()
            elif catalog_change is True:
                _ = self.catalog.start_background(force=True)
            if any(key in torrent_values for key in torrent_settings.HELPER_KEYS):
                from core.downloads.torrents import supervisor as torrent_supervisor

                torrent_supervisor.apply_limits()
        except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not save settings"}
        return {"success": True}

    @staticmethod
    def _gamescope_settings_payload(
        get_setting_fn: Callable[[str], object | None],
    ) -> dict[str, object]:
        from core import gamescope

        payload: dict[str, object] = {}
        for key, default in gamescope.SETTINGS_DEFAULTS.items():
            raw = get_setting_fn(key)
            value = gamescope.normalize_setting(key, default if raw is None else raw)
            payload[key] = value == "true" if key == "gamescope_enabled" else value
        return payload

    @staticmethod
    def _torrent_settings_payload() -> dict[str, object]:
        from core.downloads import torrent_settings as ts

        download, upload = ts.limits_kib()
        ratio, hours = ts.seed_ratio(), ts.seed_hours()
        return {
            "torrent_seed_mode": ts.seed_mode(),
            "torrent_seed_ratio": ratio if ratio is not None else 0,
            "torrent_seed_hours": hours if hours is not None else "",
            "torrent_download_limit_kib": download,
            "torrent_upload_limit_kib": upload,
            "torrent_port_mapping": ts.port_mapping(),
            "torrent_watch_folder": ts.watch_folder(),
            "torrent_export_warning_accepted": ts.export_warning_accepted(),
        }

    def save_library_filter_presets(self, presets: object = None) -> dict[str, object]:
        import json
        import sqlite3

        from core.database import normalize_filter_preset, update_setting

        if not isinstance(presets, list):
            return {"success": False, "error": "Filter presets must be a list"}
        validated: list[dict[str, object]] = []
        seen: set[str] = set()
        for entry in cast(list[object], presets):
            preset = normalize_filter_preset(entry)
            if preset is None:
                return {
                    "success": False,
                    "error": "Each filter preset needs a name of 1–40 characters",
                }
            name = str(preset["name"])
            if name.casefold() in seen:
                return {
                    "success": False,
                    "error": f"Filter preset '{name}' already exists",
                }
            seen.add(name.casefold())
            validated.append(preset)
        try:
            update_setting("library_filter_presets", json.dumps(validated))
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not save filter presets",
            }
        return {"success": True}

    def browse_file(self, start_path: str = "") -> str:
        """Opens a native file dialog to select an executable or HTML game."""
        file_types = (
            (
                "Windows Game Files (*.exe;*.bat;*.cmd;*.jar;*.html;*.htm;*.swf)",
                "All files (*.*)",
            )
            if is_windows()
            else (
                "Game Files (*.exe;*.sh;*.AppImage;*.html;*.htm;*.swf)",
                "All files (*.*)",
            )
        )

        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog(
                "file", directory=start_path, file_types=file_types
            )

        return self._browse_qt_dialog(
            "file", directory=start_path, file_types=file_types
        )

    def browse_runner_file(self, start_path: str = "") -> str:
        """Opens a native file dialog to select a Proton or Wine executable."""
        file_types = ("Runner Binaries (*)",)

        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog(
                "file", directory=start_path, file_types=file_types
            )

        return self._browse_qt_dialog(
            "file", directory=start_path, file_types=file_types
        )

    def browse_directory(self, start_path: str = "") -> str:
        """Opens a native directory dialog, e.g. for choosing a wine prefix."""
        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog("directory", directory=start_path)

        return self._browse_qt_dialog("directory", directory=start_path)

    def open_folder(self, path: str) -> OpenPathResult:
        """Opens a folder in the system file manager."""
        path = os.path.expanduser(str(path or "").strip())
        if not path or not os.path.exists(path):
            return {"success": False, "error": f"Path does not exist: {path}"}

        return self._open_path_with_system_handler(path)

    def open_dev_tools(self) -> None:
        # pywebview 4 removed Window.toggle_inspect(); the Qt backend only offers
        # devtools through its context menu. Resolve it dynamically so a backend
        # without the method degrades to a no-op instead of raising into the
        # JS bridge (which would reject the promise the UI awaits).
        toggle_inspect = (
            getattr(self.window, "toggle_inspect", None) if self.window else None
        )
        if callable(toggle_inspect):
            try:
                _ = toggle_inspect()
            except Exception as e:
                print(f"[wLib] Could not open developer tools: {e}")

    def check_app_updates(self) -> dict[str, object]:
        """Latest wLib release, fetched once per session (GitHub allows 60 anonymous calls/hour)."""
        with self._app_release_lock:
            if self._app_release is None:
                result = self._fetch_app_release()
                if not result.get("success"):
                    return result
                self._app_release: dict[str, object] | None = result
            return dict(self._app_release)

    def _fetch_app_release(self) -> dict[str, object]:
        """Fetches the latest release from the kirin-3/wLib GitHub repository."""
        import json
        import logging
        import urllib.request

        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/kirin-3/wLib/releases/latest",
                headers={"User-Agent": "wLib-AppUpdater"},
            )
            with cast(
                URLResponseContext,
                urllib.request.urlopen(req, timeout=10),
            ) as response:
                if response.status == 200:
                    data = self._coerce_string_key_dict(
                        cast(object, json.loads(response.read().decode("utf-8")))
                    )
                    if data is None:
                        data = {}

                    latest_tag = str(data.get("tag_name") or "")
                    latest_tag = (
                        latest_tag if latest_tag.startswith("v") else f"v{latest_tag}"
                    )
                    assets = data.get("assets")
                    parsed_assets: list[dict[str, str]] = []
                    for asset_obj in (
                        cast(list[object], assets) if isinstance(assets, list) else []
                    ):
                        asset = self._coerce_string_key_dict(asset_obj)
                        if not asset:
                            continue
                        parsed_assets.append(
                            {
                                "name": str(asset.get("name") or ""),
                                "url": str(asset.get("browser_download_url") or ""),
                            }
                        )

                    return {
                        "success": True,
                        "version": latest_tag,
                        "current_version": f"v{APP_VERSION}",
                        "changelog": str(data.get("body") or ""),
                        "url": str(data.get("html_url") or ""),
                        "assets": parsed_assets,
                    }
            return {"success": False, "error": "Unexpected status from update API"}
        except Exception as e:
            logging.error(f"Failed to check app updates: {e}")
            return {"success": False, "error": str(e)}

    def get_app_version(self) -> dict[str, str]:
        """Returns the current app version."""
        return {"version": f"v{APP_VERSION}"}
