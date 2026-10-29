# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import os
import ssl
import sys
import threading
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import closing, contextmanager, suppress
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict, cast

from core import database
from core.database import (
    RPGMAKER_LINUX_RUNNER_SETTING,
    init_db,
    normalize_launch_mode,
)
from core.f95zone import NON_ENGINE_PREFIXES, normalize_prefix_label
from core.f95zone import normalize_thread_url as _normalize_thread_url
from core.host_platform import (
    PlatformCapabilities,
    get_cheat_engine_dir,
    get_default_wine_prefix,
    get_extension_dir,
    get_platform_capabilities as resolve_platform_capabilities,
    get_proton_dir,
    get_rtp_dir,
    host_command,
    host_which,
    is_linux,
    is_windows,
    open_windows_system_target,
    unsupported_on_host,
)
from core.game_saves import SaveLocation
from core.game_tools import CancelFn, ProgressFn, game_tool_folder
from core.launcher import Launcher, proton_compat_data_path
from core.scraper import Scraper
from core.versions import normalize_version as normalize_version

_tag_color_lock = threading.Lock()

if TYPE_CHECKING:
    from webview import Window


class ProgressStatus(TypedDict):
    running: bool
    done: int
    total: int
    current: str
    error: str


class ExtensionSyncStatus(TypedDict):
    success: bool
    updated: NotRequired[bool]
    path: NotRequired[str]
    bundled_version: NotRequired[str]
    installed_version: NotRequired[str]
    reason: NotRequired[str]
    error: NotRequired[str]


class OpenPathResult(TypedDict):
    success: bool
    error: NotRequired[str]


class PickerCommandResult(TypedDict):
    success: bool
    cancelled: bool
    stderr: str
    returncode: int | None
    path: NotRequired[str]
    error: NotRequired[str]


class BrowseBackend(TypedDict):
    name: str
    command: list[str]
    env: dict[str, str]
    continue_on_cancel_error: NotRequired[bool]
    cancel_error_markers: NotRequired[tuple[str, ...]]


class ExtensionServiceStatus(TypedDict):
    success: bool
    reachable: bool
    error: NotRequired[str]


class ScraperResponse(TypedDict, total=False):
    success: bool
    error: str
    message: str
    code: str
    version: str
    engine: str
    cover_image: str
    tags: str | list[object]
    thread_main_post_last_edit_at: str
    thread_main_post_checked_at: str
    thread_completed: bool


class LaunchTargetRecord(TypedDict):
    id: int
    game_id: int
    label: str
    exe_path: str
    sort_order: int
    created_at: str
    updated_at: str


class GameRecord(TypedDict):
    id: int
    title: str
    version: str
    f95_url: str
    exe_path: NotRequired[str]
    latest_version: NotRequired[str]
    skipped_version: NotRequired[str]
    thread_completed: NotRequired[bool]
    launch_mode: NotRequired[str]
    launch_targets: NotRequired[list[LaunchTargetRecord]]
    thread_main_post_last_edit_at: NotRequired[str | None]
    thread_main_post_checked_at: NotRequired[str | None]


class ExecutableModifiedTimeResponse(TypedDict):
    success: bool
    modified_at: str | None
    error: NotRequired[str]


class RuntimeInstallTarget(TypedDict):
    base_prefix: str
    resolved_prefix: str
    proton_path: str
    is_proton: bool


class WebviewModule(Protocol):
    class FileDialog(Protocol):
        FOLDER: int
        OPEN: int

    FOLDER_DIALOG: int
    OPEN_DIALOG: int


class URLResponse(Protocol):
    status: int

    def read(self, size: int = -1) -> bytes: ...


class URLResponseContext(Protocol):
    def __enter__(self) -> URLResponse: ...

    def __exit__(
        self, exc_type: object, exc_value: object, traceback: object
    ) -> None: ...


normalize_thread_url: Callable[[str], str] = cast(
    Callable[[str], str], cast(object, _normalize_thread_url)
)

APP_VERSION = "0.4.2"
# Lunar.Engine.zip from visibou/lunarengine v.7.2 (uploaded 2020-11-29, 29738666 bytes).
LUNAR_ENGINE_SHA256 = "c208f52ca1873007df3f7ef71b61f3c2870a58ad0ddadff7944785f6bf36de23"
RTP_DOWNLOADS_PAGE_URL = "https://www.rpgmakerweb.com/run-time-package"
KOMODO_RTP_DOWNLOAD_HOSTS = {"dl.komodo.jp"}
# dl.komodo.jp omits its intermediate. Since its May 2026 renewal the leaf is issued by
# "Sectigo Public Server Authentication CA DV R36"; the older "Sectigo RSA Domain
# Validation Secure Server CA" stays in case the host switches back.
KOMODO_SECTIGO_INTERMEDIATE_CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIGTDCCBDSgAwIBAgIQOXpmzCdWNi4NqofKbqvjsTANBgkqhkiG9w0BAQwFADBf
MQswCQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQD
Ey1TZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBSNDYw
HhcNMjEwMzIyMDAwMDAwWhcNMzYwMzIxMjM1OTU5WjBgMQswCQYDVQQGEwJHQjEY
MBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTcwNQYDVQQDEy5TZWN0aWdvIFB1Ymxp
YyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gQ0EgRFYgUjM2MIIBojANBgkqhkiG9w0B
AQEFAAOCAY8AMIIBigKCAYEAljZf2HIz7+SPUPQCQObZYcrxLTHYdf1ZtMRe7Yeq
RPSwygz16qJ9cAWtWNTcuICc++p8Dct7zNGxCpqmEtqifO7NvuB5dEVexXn9RFFH
12Hm+NtPRQgXIFjx6MSJcNWuVO3XGE57L1mHlcQYj+g4hny90aFh2SCZCDEVkAja
EMMfYPKuCjHuuF+bzHFb/9gV8P9+ekcHENF2nR1efGWSKwnfG5RawlkaQDpRtZTm
M64TIsv/r7cyFO4nSjs1jLdXYdz5q3a4L0NoabZfbdxVb+CUEHfB0bpulZQtH1Rv
38e/lIdP7OTTIlZh6OYL6NhxP8So0/sht/4J9mqIGxRFc0/pC8suja+wcIUna0HB
pXKfXTKpzgis+zmXDL06ASJf5E4A2/m+Hp6b84sfPAwQ766rI65mh50S0Di9E3Pn
2WcaJc+PILsBmYpgtmgWTR9eV9otfKRUBfzHUHcVgarub/XluEpRlTtZudU5xbFN
xx/DgMrXLUAPaI60fZ6wA+PTAgMBAAGjggGBMIIBfTAfBgNVHSMEGDAWgBRWc1hk
lfmSGrASKgRieaFAFYghSTAdBgNVHQ4EFgQUaMASFhgOr872h6YyV6NGUV3LBycw
DgYDVR0PAQH/BAQDAgGGMBIGA1UdEwEB/wQIMAYBAf8CAQAwHQYDVR0lBBYwFAYI
KwYBBQUHAwEGCCsGAQUFBwMCMBsGA1UdIAQUMBIwBgYEVR0gADAIBgZngQwBAgEw
VAYDVR0fBE0wSzBJoEegRYZDaHR0cDovL2NybC5zZWN0aWdvLmNvbS9TZWN0aWdv
UHVibGljU2VydmVyQXV0aGVudGljYXRpb25Sb290UjQ2LmNybDCBhAYIKwYBBQUH
AQEEeDB2ME8GCCsGAQUFBzAChkNodHRwOi8vY3J0LnNlY3RpZ28uY29tL1NlY3Rp
Z29QdWJsaWNTZXJ2ZXJBdXRoZW50aWNhdGlvblJvb3RSNDYucDdjMCMGCCsGAQUF
BzABhhdodHRwOi8vb2NzcC5zZWN0aWdvLmNvbTANBgkqhkiG9w0BAQwFAAOCAgEA
YtOC9Fy+TqECFw40IospI92kLGgoSZGPOSQXMBqmsGWZUQ7rux7cj1du6d9rD6C8
ze1B2eQjkrGkIL/OF1s7vSmgYVafsRoZd/IHUrkoQvX8FZwUsmPu7amgBfaY3g+d
q1x0jNGKb6I6Bzdl6LgMD9qxp+3i7GQOnd9J8LFSietY6Z4jUBzVoOoz8iAU84OF
h2HhAuiPw1ai0VnY38RTI+8kepGWVfGxfBWzwH9uIjeooIeaosVFvE8cmYUB4TSH
5dUyD0jHct2+8ceKEtIoFU/FfHq/mDaVnvcDCZXtIgitdMFQdMZaVehmObyhRdDD
4NQCs0gaI9AAgFj4L9QtkARzhQLNyRf87Kln+YU0lgCGr9HLg3rGO8q+Y4ppLsOd
unQZ6ZxPNGIfOApbPVf5hCe58EZwiWdHIMn9lPP6+F404y8NNugbQixBber+x536
WrZhFZLjEkhp7fFXf9r32rNPfb74X/U90Bdy4lzp3+X1ukh1BuMxA/EEhDoTOS3l
7ABvc7BYSQubQ2490OcdkIzUh3ZwDrakMVrbaTxUM2p24N6dB+ns2zptWCva6jzW
r8IWKIMxzxLPv5Kt3ePKcUdvkBU/smqujSczTzzSjIoR5QqQA6lN1ZRSnuHIWCvh
JEltkYnTAH41QJ6SAWO66GrrUESwN/cgZzL4JLEqz1Y=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIGEzCCA/ugAwIBAgIQfVtRJrR2uhHbdBYLvFMNpzANBgkqhkiG9w0BAQwFADCB
iDELMAkGA1UEBhMCVVMxEzARBgNVBAgTCk5ldyBKZXJzZXkxFDASBgNVBAcTC0pl
cnNleSBDaXR5MR4wHAYDVQQKExVUaGUgVVNFUlRSVVNUIE5ldHdvcmsxLjAsBgNV
BAMTJVVTRVJUcnVzdCBSU0EgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMTgx
MTAyMDAwMDAwWhcNMzAxMjMxMjM1OTU5WjCBjzELMAkGA1UEBhMCR0IxGzAZBgNV
BAgTEkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UEBxMHU2FsZm9yZDEYMBYGA1UE
ChMPU2VjdGlnbyBMaW1pdGVkMTcwNQYDVQQDEy5TZWN0aWdvIFJTQSBEb21haW4g
VmFsaWRhdGlvbiBTZWN1cmUgU2VydmVyIENBMIIBIjANBgkqhkiG9w0BAQEFAAOC
AQ8AMIIBCgKCAQEA1nMz1tc8INAA0hdFuNY+B6I/x0HuMjDJsGz99J/LEpgPLT+N
TQEMgg8Xf2Iu6bhIefsWg06t1zIlk7cHv7lQP6lMw0Aq6Tn/2YHKHxYyQdqAJrkj
eocgHuP/IJo8lURvh3UGkEC0MpMWCRAIIz7S3YcPb11RFGoKacVPAXJpz9OTTG0E
oKMbgn6xmrntxZ7FN3ifmgg0+1YuWMQJDgZkW7w33PGfKGioVrCSo1yfu4iYCBsk
Haswha6vsC6eep3BwEIc4gLw6uBK0u+QDrTBQBbwb4VCSmT3pDCg/r8uoydajotY
uK3DGReEY+1vVv2Dy2A0xHS+5p3b4eTlygxfFQIDAQABo4IBbjCCAWowHwYDVR0j
BBgwFoAUU3m/WqorSs9UgOHYm8Cd8rIDZsswHQYDVR0OBBYEFI2MXsRUrYrhd+mb
+ZsF4bgBjWHhMA4GA1UdDwEB/wQEAwIBhjASBgNVHRMBAf8ECDAGAQH/AgEAMB0G
A1UdJQQWMBQGCCsGAQUFBwMBBggrBgEFBQcDAjAbBgNVHSAEFDASMAYGBFUdIAAw
CAYGZ4EMAQIBMFAGA1UdHwRJMEcwRaBDoEGGP2h0dHA6Ly9jcmwudXNlcnRydXN0
LmNvbS9VU0VSVHJ1c3RSU0FDZXJ0aWZpY2F0aW9uQXV0aG9yaXR5LmNybDB2Bggr
BgEFBQcBAQRqMGgwPwYIKwYBBQUHMAKGM2h0dHA6Ly9jcnQudXNlcnRydXN0LmNv
bS9VU0VSVHJ1c3RSU0FBZGRUcnVzdENBLmNydDAlBggrBgEFBQcwAYYZaHR0cDov
L29jc3AudXNlcnRydXN0LmNvbTANBgkqhkiG9w0BAQwFAAOCAgEAMr9hvQ5Iw0/H
ukdN+Jx4GQHcEx2Ab/zDcLRSmjEzmldS+zGea6TvVKqJjUAXaPgREHzSyrHxVYbH
7rM2kYb2OVG/Rr8PoLq0935JxCo2F57kaDl6r5ROVm+yezu/Coa9zcV3HAO4OLGi
H19+24rcRki2aArPsrW04jTkZ6k4Zgle0rj8nSg6F0AnwnJOKf0hPHzPE/uWLMUx
RP0T7dWbqWlod3zu4f+k+TY4CFM5ooQ0nBnzvg6s1SQ36yOoeNDT5++SR2RiOSLv
xvcRviKFxmZEJCaOEDKNyJOuB56DPi/Z+fVGjmO+wea03KbNIaiGCpXZLoUmGv38
sbZXQm2V0TP2ORQGgkE49Y9Y3IBbpNV9lXj9p5v//cWoaasm56ekBYdbqbe4oyAL
l6lFhd2zi+WJN44pDfwGF/Y4QA5C5BIG+3vzxhFoYt/jmPQT2BVPi7Fp2RBgvGQq
6jG35LWjOhSbJuMLe/0CjraZwTiXWTb2qHSihrZe68Zk6s+go/lunrotEbaGmAhY
LcmsJWTyXnW0OMGuf1pGg+pRyrbxmRE1a6Vqe8YAsOf4vmSyrcjC8azjUeqkk+B5
yOGBQMkKW+ESPMFgKuOXwIlCypTPRpgSabuY0MLTDXJLR27lk8QyKGOHQ+SwMj4K
00u/I5sUKUErmgQfky3xxzlIPK1aEn8=
-----END CERTIFICATE-----
"""


class Api:
    def __init__(self) -> None:
        self.window: Window | None = None
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

        self.library_sync: SyncManager = SyncManager(
            on_pull=self._refresh_synced_library
        )
        self.scraper: Scraper = Scraper()
        self.launcher: Launcher = Launcher()
        self._update_running: bool = False
        self._update_cancelled: bool = False
        self._update_outcome: str = "idle"
        self._update_error: str = ""
        self._update_total: int = 0
        self._update_checked: int = 0
        self._update_current: str = ""
        self._update_results: list[dict[str, object]] = []
        self._update_delay_seconds: int = self._get_bulk_check_delay_seconds()
        self._update_lock: threading.Lock = threading.Lock()
        self._window_focused: bool = True
        self._status_lock: threading.Lock = threading.Lock()
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
        # Games with a save backup/import/restore in flight (one at a time per game).
        self._saves_busy: set[int] = set()
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
        }
        from core.catalog import CatalogClient
        from core.host_platform import get_data_dir

        # Read-only game catalog next to the library database; the startup
        # check runs on its own thread and never blocks or raises.
        self.catalog: CatalogClient = CatalogClient(
            get_data_dir(), app_version=APP_VERSION, enabled=self._catalog_enabled
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

    def set_window(self, window: Window | None) -> None:
        self.window = window

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

    @staticmethod
    def _coerce_names_sequence(value: object) -> list[object] | None:
        """Validate a collection/target name list from the JS bridge."""
        if isinstance(value, str) or not isinstance(value, Sequence):
            return None
        return list(value)

    @staticmethod
    def _normalize_compare_path(value: object) -> str:
        raw = str(value or "")
        if not raw.strip():
            return ""
        expanded = os.path.expanduser(raw)
        try:
            resolved = os.path.realpath(os.path.abspath(expanded))
        except (OSError, ValueError):
            resolved = os.path.abspath(expanded)
        return os.path.normcase(resolved)

    def get_platform_capabilities(self) -> PlatformCapabilities:
        return resolve_platform_capabilities()

    def get_sandbox_status(self) -> dict[str, object]:
        from core.game_sandbox import get_sandbox_status

        return dict(get_sandbox_status())

    def recheck_sandbox_status(self) -> dict[str, object]:
        from core.game_sandbox import get_sandbox_status

        return dict(get_sandbox_status(recheck=True))

    def get_sandbox_install_info(self) -> dict[str, object]:
        from core.game_sandbox import get_sandbox_install_info

        return get_sandbox_install_info()

    def get_sandbox_install_status(self) -> dict[str, object]:
        with self._status_lock:
            return dict(self._sandbox_install_status)

    def install_sandbox_tool(self, tool: str = "") -> dict[str, object]:
        from core import game_sandbox

        expected = "bwrap" if is_linux() else "sandboxie" if is_windows() else ""
        if not expected or (tool and tool != expected):
            return {
                "success": False,
                "code": "unsupported_platform",
                "error": "This sandbox installer is unavailable on this host.",
            }
        with self._status_lock:
            if self._sandbox_install_status["running"]:
                return {
                    "success": False,
                    "error": "Sandbox installation is already running.",
                }
            self._sandbox_install_status = {
                "running": True,
                "done": 0,
                "total": 1,
                "current": "Installing sandbox tool",
                "error": "",
            }

        def install() -> None:
            error = ""
            try:
                game_sandbox.install_sandbox_tool(expected)
            except Exception as exc:
                error = str(exc)
            try:
                status = game_sandbox.get_sandbox_status(recheck=True)
                if not error and status["status"] != "ready":
                    error = status["reason"]
            except Exception as exc:
                error = error or str(exc)
            with self._status_lock:
                self._sandbox_install_status = {
                    "running": False,
                    "done": 0 if error else 1,
                    "total": 1,
                    "current": "",
                    "error": error,
                }

        threading.Thread(target=install, daemon=True).start()
        return {"success": True}

    def set_sandbox_for_all_games(self, enabled: object) -> dict[str, object]:
        from core.database import set_sandbox_for_all_games

        if not isinstance(enabled, bool):
            return {"success": False, "error": "Sandbox preference must be a boolean."}
        return {"success": True, "changed": set_sandbox_for_all_games(enabled)}

    def open_game_sandbox_folder(self, game_id: int) -> dict[str, object]:
        from core.database import get_game
        from core.game_sandbox import sandbox_folder

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or get_game(coerced_id) is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        folder = sandbox_folder(coerced_id)
        try:
            os.makedirs(folder, exist_ok=True)
            return dict(self._open_path_with_system_handler(folder))
        except OSError as exc:
            return {"success": False, "error": str(exc)}

    def _resolve_runtime_install_target(
        self, prefix_path: str | None = None, proton_path: str | None = None
    ) -> RuntimeInstallTarget:
        from core.database import get_setting

        get_setting_fn = cast(Callable[[str], object | None], get_setting)

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
        self, prefix_path: str | None = None, proton_path: str | None = None
    ) -> tuple[dict[str, str], RuntimeInstallTarget]:
        target = self._resolve_runtime_install_target(prefix_path, proton_path)
        env = os.environ.copy()
        env["WINEPREFIX"] = target["resolved_prefix"]
        if target["is_proton"]:
            env["STEAM_COMPAT_DATA_PATH"] = os.path.dirname(target["resolved_prefix"])
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = "/tmp/wlib"
            # winetricks must drive Proton's own Wine, not whatever `wine` is on PATH.
            for layout in ("files", "dist"):
                bin_dir = os.path.join(
                    os.path.dirname(target["proton_path"]), layout, "bin"
                )
                if os.path.isfile(os.path.join(bin_dir, "wine")):
                    env["WINE"] = os.path.join(bin_dir, "wine")
                    env["WINESERVER"] = os.path.join(bin_dir, "wineserver")
                    break

        os.makedirs(env["WINEPREFIX"], exist_ok=True)
        return env, target

    def _create_download_ssl_context(
        self, url: str, *, include_komodo_intermediate: bool = False
    ) -> ssl.SSLContext:
        import urllib.parse

        cafile = (os.environ.get("SSL_CERT_FILE") or "").strip()
        if cafile and not os.path.isfile(cafile):
            cafile = ""

        context = ssl.create_default_context(cafile=cafile or None)

        hostname = (urllib.parse.urlparse(url).hostname or "").lower()
        if include_komodo_intermediate and hostname in KOMODO_RTP_DOWNLOAD_HOSTS:
            context.load_verify_locations(cadata=KOMODO_SECTIGO_INTERMEDIATE_CERT_PEM)

        return context

    def _is_tls_verification_error(self, error: BaseException) -> bool:
        import ssl
        import urllib.error

        if isinstance(error, ssl.SSLCertVerificationError):
            return True

        if isinstance(error, urllib.error.URLError):
            reason = error.reason
            if isinstance(reason, ssl.SSLCertVerificationError):
                return True

        return "CERTIFICATE_VERIFY_FAILED" in str(error)

    def _open_url_with_targeted_tls_fallback(
        self, request: object, timeout: int = 30
    ) -> URLResponseContext:
        import urllib.error
        import urllib.parse
        import urllib.request

        request_obj: urllib.request.Request | str = cast(
            urllib.request.Request | str, request
        )
        request_url = str(getattr(request_obj, "full_url", request_obj))
        hostname = (urllib.parse.urlparse(request_url).hostname or "").lower()
        default_context = self._create_download_ssl_context(request_url)

        try:
            return cast(
                URLResponseContext,
                urllib.request.urlopen(
                    request_obj, context=default_context, timeout=timeout
                ),
            )
        except urllib.error.URLError as error:
            if hostname not in KOMODO_RTP_DOWNLOAD_HOSTS:
                raise

            if not self._is_tls_verification_error(error):
                raise

            print(
                f"Retrying {request_url} with bundled Sectigo intermediate certificate after TLS verification failure."
            )
            fallback_context = self._create_download_ssl_context(
                request_url, include_komodo_intermediate=True
            )
            return cast(
                URLResponseContext,
                urllib.request.urlopen(
                    request_obj, context=fallback_context, timeout=timeout
                ),
            )

    def _format_rtp_download_error(self, url: str, error: BaseException) -> str:
        import urllib.parse

        hostname = (urllib.parse.urlparse(url).hostname or "").lower()
        if hostname in KOMODO_RTP_DOWNLOAD_HOSTS and self._is_tls_verification_error(
            error
        ):
            return (
                "download failed because the official RPG Maker file host is serving an incomplete TLS certificate chain. "
                f"wLib retried with the required Sectigo intermediate certificate but the download still could not be verified. "
                f"Download it manually from {RTP_DOWNLOADS_PAGE_URL}."
            )

        return f"download failed ({error})"

    def _get_rtp_packages(self) -> list[dict[str, object]]:
        return [
            {
                "name": "VX Ace",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/RPGVXAce_RTP.zip",
                "filename": "vxace_rtp.zip",
                "sentinels": (
                    "drive_c/windows/system32/RGSS300.dll",
                    "drive_c/windows/system32/RGSS301.dll",
                    "drive_c/Program Files/Common Files/Enterbrain/RGSS3/RPGVXAce",
                    "drive_c/Program Files (x86)/Common Files/Enterbrain/RGSS3/RPGVXAce",
                ),
            },
            {
                "name": "VX",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/vx_rtp102e.zip",
                "filename": "vx_rtp.zip",
                "sentinels": (
                    "drive_c/windows/system32/RGSS202E.dll",
                    "drive_c/Program Files/Common Files/Enterbrain/RGSS2/RPGVX",
                    "drive_c/Program Files (x86)/Common Files/Enterbrain/RGSS2/RPGVX",
                ),
            },
            {
                "name": "XP",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/xp_rtp104e.exe",
                "filename": "xp_rtp104e.exe",
                "sentinels": (
                    "drive_c/windows/system32/RGSS104E.dll",
                    "drive_c/Program Files/Common Files/Enterbrain/RGSS/Standard",
                    "drive_c/Program Files (x86)/Common Files/Enterbrain/RGSS/Standard",
                ),
            },
            {
                "name": "2003",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/rpg2003_rtp_installer.zip",
                "filename": "rpg2003_rtp.zip",
                "sentinels": (
                    "drive_c/Program Files/Common Files/ASCII/RPG2003",
                    "drive_c/Program Files (x86)/Common Files/ASCII/RPG2003",
                    "drive_c/Program Files/Common Files/ASCII/RPG2003RTP",
                    "drive_c/Program Files (x86)/Common Files/ASCII/RPG2003RTP",
                    "drive_c/Program Files/Common Files/Enterbrain/RTP/2003",
                    "drive_c/Program Files (x86)/Common Files/Enterbrain/RTP/2003",
                    "drive_c/users/{user}/AppData/Roaming/KADOKAWA/Common/RPG Maker 2003 RTP",
                ),
            },
        ]

    def _runtime_sentinel_exists(
        self, resolved_prefix: str, relative_path: str
    ) -> bool:
        if "{user}" not in relative_path:
            return os.path.exists(os.path.join(resolved_prefix, relative_path))

        users_dir = os.path.join(resolved_prefix, "drive_c", "users")
        if not os.path.isdir(users_dir):
            return False

        for user_name in os.listdir(users_dir):
            candidate = os.path.join(
                resolved_prefix, relative_path.replace("{user}", user_name)
            )
            if os.path.exists(candidate):
                return True

        return False

    def _verify_rtp_packages(self, resolved_prefix: str) -> dict[str, bool]:
        verified: dict[str, bool] = {}
        for package in self._get_rtp_packages():
            package_name = str(package["name"])
            sentinels = cast(tuple[str, ...], package["sentinels"])
            verified[package_name] = any(
                self._runtime_sentinel_exists(resolved_prefix, relative_path)
                for relative_path in sentinels
            )
        return verified

    def _rtps_installed_in_prefix(self, resolved_prefix: str) -> bool:
        package_status = self._verify_rtp_packages(resolved_prefix)
        return bool(package_status) and all(package_status.values())

    def _dlls_installed_in_prefix(self, resolved_prefix: str) -> bool:
        sentinels = (
            "drive_c/windows/Fonts/arial.ttf",
            "drive_c/windows/system32/d3dcompiler_47.dll",
            "drive_c/windows/system32/msvcr120.dll",
            "drive_c/windows/system32/vcruntime140.dll",
            "drive_c/windows/Microsoft.NET/Framework/v4.0.30319",
        )
        return all(
            os.path.exists(os.path.join(resolved_prefix, relative_path))
            for relative_path in sentinels
        )

    def _get_bundled_extension_dir(self) -> str:
        if getattr(sys, "frozen", False):
            bundle_root = getattr(
                sys,
                "_MEIPASS",
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            return os.path.join(bundle_root, "extension")

        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension"
        )

    def _get_persistent_extension_dir(self) -> str:
        return get_extension_dir()

    def _coerce_string_key_dict(self, payload: object) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return None

        payload_dict = cast(dict[object, object], payload)
        return {str(key): value for key, value in payload_dict.items()}

    def _read_manifest_version(self, manifest_path: str) -> str:
        import json

        if not os.path.isfile(manifest_path):
            return ""

        with open(manifest_path, encoding="utf-8") as f:
            manifest_data = self._coerce_string_key_dict(cast(object, json.load(f)))

        if manifest_data is None:
            return ""

        return str(manifest_data.get("version", "")).strip()

    def set_startup_extension_sync_status(
        self, status: ExtensionSyncStatus | None
    ) -> None:
        payload: ExtensionSyncStatus = {
            "success": True,
            "updated": False,
            "path": self._get_persistent_extension_dir(),
            "bundled_version": "",
            "installed_version": "",
            "reason": "not-run",
        }
        if status:
            payload.update(status)
        self._startup_extension_sync_status = payload

    def get_startup_extension_sync_status(self) -> ExtensionSyncStatus:
        return cast(
            ExtensionSyncStatus,
            cast(object, dict(self._startup_extension_sync_status)),
        )

    def _write_unsigned_firefox_xpi(self, source_dir: str, target_path: str) -> None:
        import zipfile

        # strict_timestamps=False clamps the 1970 mtimes of Nix store files to 1980.
        with zipfile.ZipFile(
            target_path, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False
        ) as xpi:
            for root, dirs, files in os.walk(source_dir):
                dirs[:] = sorted(d for d in dirs if d != "firefox")
                for filename in sorted(files):
                    file_path = os.path.join(root, filename)
                    archive_name = os.path.relpath(file_path, source_dir)
                    xpi.write(file_path, archive_name)

    @staticmethod
    def _make_tree_owner_writable(path: str) -> None:
        # Copies from a read-only install (the Nix store) keep its 0444/0555
        # modes, which would stop later syncs from editing or removing them.
        import stat

        # Never walk through a link or junction: that would chmod files outside
        # the app data directory.
        isjunction = getattr(os.path, "isjunction", None)
        if os.path.islink(path) or (callable(isjunction) and isjunction(path)):
            return

        for root, _dirs, files in os.walk(path):
            for entry in (root, *(os.path.join(root, name) for name in files)):
                mode = os.lstat(entry).st_mode
                if not stat.S_ISLNK(mode):
                    os.chmod(entry, stat.S_IMODE(mode) | stat.S_IWUSR)

    def sync_extension_files(self) -> ExtensionSyncStatus:
        import shutil

        def remove_tree_or_link(path: str) -> None:
            # A leftover staging/live path can be a symlink or a Windows
            # junction. Remove the link itself; never recurse into its target.
            isjunction = getattr(os.path, "isjunction", None)
            if os.path.islink(path):
                try:
                    os.remove(path)
                except OSError:
                    # Windows directory symlinks need rmdir, not unlink.
                    os.rmdir(path)
            elif callable(isjunction) and isjunction(path):
                os.rmdir(path)
            elif os.path.isdir(path):
                self._make_tree_owner_writable(path)
                shutil.rmtree(path)
            elif os.path.lexists(path):
                os.remove(path)

        bundled_ext_dir = self._get_bundled_extension_dir()
        persistent_ext_dir = self._get_persistent_extension_dir()
        chrome_dir = os.path.join(persistent_ext_dir, "chrome")
        chrome_new_dir = os.path.join(persistent_ext_dir, "chrome.new")
        chrome_old_dir = os.path.join(persistent_ext_dir, "chrome.old")
        firefox_dir = os.path.join(persistent_ext_dir, "firefox")
        bundled_manifest = os.path.join(bundled_ext_dir, "manifest.json")
        installed_manifest = os.path.join(chrome_dir, "manifest.json")
        bundled_firefox_xpi = os.path.join(bundled_ext_dir, "firefox", "wLib.xpi")
        installed_firefox_xpi = os.path.join(firefox_dir, "wLib.xpi")

        try:
            # Leftover staging folders from an interrupted earlier sync must go
            # even when the installed files already look complete.
            if os.path.isdir(bundled_ext_dir):
                for stale_dir in (chrome_new_dir, chrome_old_dir):
                    remove_tree_or_link(stale_dir)

            bundled_version = self._read_manifest_version(bundled_manifest)
            installed_version = self._read_manifest_version(installed_manifest)
            needs_copy = False
            reason = "up-to-date"
            if not os.path.isdir(chrome_dir) or not os.path.isfile(
                installed_firefox_xpi
            ):
                needs_copy = True
                reason = "missing-installed-files"
            else:
                if bundled_version and installed_version:
                    if bundled_version != installed_version:
                        needs_copy = True
                        reason = "version-changed"
                elif os.path.isfile(bundled_manifest):
                    needs_copy = True
                    reason = "missing-installed-manifest"

                if not needs_copy and os.path.isfile(bundled_firefox_xpi):
                    import filecmp

                    if not filecmp.cmp(
                        bundled_firefox_xpi, installed_firefox_xpi, shallow=False
                    ):
                        needs_copy = True
                        reason = "signed-firefox-xpi-changed"

            if needs_copy and os.path.isdir(bundled_ext_dir):
                os.makedirs(persistent_ext_dir, exist_ok=True)

                _ = shutil.copytree(
                    bundled_ext_dir,
                    chrome_new_dir,
                    ignore=shutil.ignore_patterns("firefox"),
                )
                self._make_tree_owner_writable(chrome_new_dir)

                chrome_manifest_path = os.path.join(chrome_new_dir, "manifest.json")
                import json

                with open(chrome_manifest_path, "r", encoding="utf-8") as f:
                    chrome_manifest = self._coerce_string_key_dict(
                        cast(object, json.load(f))
                    )

                if chrome_manifest is None:
                    raise ValueError("Bundled extension manifest must be a JSON object")

                background = chrome_manifest.get("background")

                if isinstance(background, dict) and "scripts" in background:
                    del background["scripts"]
                    with open(chrome_manifest_path, "w", encoding="utf-8") as f:
                        json.dump(chrome_manifest, f, indent=4)

                # Swap the staged folder into place. The live path holds either
                # the complete old version or the complete new version; only
                # the renames themselves briefly remove it.
                if os.path.isdir(chrome_dir) or os.path.lexists(chrome_dir):
                    os.rename(chrome_dir, chrome_old_dir)
                os.rename(chrome_new_dir, chrome_dir)
                remove_tree_or_link(chrome_old_dir)

                os.makedirs(firefox_dir, exist_ok=True)
                firefox_tmp_path = installed_firefox_xpi + ".tmp"
                try:
                    if os.path.isfile(bundled_firefox_xpi):
                        _ = shutil.copy2(bundled_firefox_xpi, firefox_tmp_path)
                    else:
                        self._write_unsigned_firefox_xpi(
                            bundled_ext_dir, firefox_tmp_path
                        )
                    os.replace(firefox_tmp_path, installed_firefox_xpi)
                finally:
                    if os.path.lexists(firefox_tmp_path):
                        with suppress(OSError):
                            os.remove(firefox_tmp_path)
                installed_version = self._read_manifest_version(
                    os.path.join(chrome_dir, "manifest.json")
                )

            return {
                "success": True,
                "path": persistent_ext_dir,
                "updated": needs_copy,
                "bundled_version": bundled_version,
                "installed_version": installed_version,
                "reason": reason,
            }
        except Exception as e:
            print(f"[wLib] Failed to sync extension: {e}")
            return {"success": False, "error": str(e)}

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

    def open_extension_folder(self) -> ExtensionSyncStatus | OpenPathResult:
        sync_result = self.sync_extension_files()
        if sync_result.get("success") is False:
            return sync_result

        persistent_ext_dir = self._get_persistent_extension_dir()
        return self._open_path_with_system_handler(persistent_ext_dir)

    def get_extension_service_status(self) -> ExtensionServiceStatus:
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

    # ==========================
    # Database / Game API
    # ==========================
    def get_games(self) -> list[dict[str, object]]:
        from core.database import get_all_games

        games = get_all_games()
        with self._metadata_lock:
            pending = set(self._metadata_pending)
        for game in games:
            self._add_local_cover_url(game)
            if game.get("id") in pending:
                game["metadata_pending"] = True
        return games

    def _add_local_cover_url(self, game: dict[str, object]) -> None:
        from core.game_covers import cover_path

        path = cover_path(game.get("cover_local"))
        if self.library_sync.server_available and path is not None and path.is_file():
            game["cover_local_url"] = f"http://127.0.0.1:8183/covers/{path.name}"

    def set_game_cover_file(
        self, game_id: object = None, path: object = ""
    ) -> dict[str, object]:
        from core.game_covers import set_cover

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or database.get_game(coerced_id) is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        if not isinstance(path, str) or not path.strip():
            return {"success": False, "error": "Choose an image file."}
        try:
            _ = set_cover(coerced_id, os.path.expanduser(path))
            game = database.get_game(coerced_id)
            if game is not None:
                self._add_local_cover_url(game)
            return {"success": True, "game": game}
        except (OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def choose_game_cover(self, game_id: object = None) -> dict[str, object]:
        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or database.get_game(coerced_id) is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        try:
            picker = (
                self._browse_linux_dialog
                if sys.platform.startswith("linux")
                else self._browse_qt_dialog
            )
            selected = picker(
                "file",
                file_types=(
                    "Images (*.jpg;*.jpeg;*.png;*.webp;*.gif)",
                    "All files (*.*)",
                ),
            )
            if not selected:
                return {"success": True, "cancelled": True}
            return self.set_game_cover_file(coerced_id, selected)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def clear_game_cover(self, game_id: object = None) -> dict[str, object]:
        from core.game_covers import clear_cover

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or database.get_game(coerced_id) is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        try:
            clear_cover(coerced_id)
            return {"success": True, "game": database.get_game(coerced_id)}
        except (OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    # ==========================
    # Collections
    # ==========================
    def get_collections(self) -> list[str]:
        from core.database import get_collections as fetch_collections

        return fetch_collections()

    def create_collection(
        self, name: object = "", game_id: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import create_collection

        coerced_game_id: int | None = None
        if game_id is not None:
            coerced_game_id = self._coerce_db_id(game_id)
            if coerced_game_id is None:
                return {
                    "success": False,
                    "error": "Game not found",
                    "error_code": "game_not_found",
                }

        try:
            collections = create_collection(name, coerced_game_id)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {"success": False, "error": str(e) or "Could not create collection"}
        return {"success": True, "collections": collections}

    def rename_collection(
        self, old_name: object = "", new_name: object = ""
    ) -> dict[str, object]:
        import sqlite3

        from core.database import rename_collection

        try:
            collections = rename_collection(old_name, new_name)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {"success": False, "error": str(e) or "Could not rename collection"}
        return {"success": True, "collections": collections}

    def delete_collection(self, name: object = "") -> dict[str, object]:
        import sqlite3

        from core.database import delete_collection

        try:
            collections = delete_collection(name)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {"success": False, "error": str(e) or "Could not delete collection"}
        return {"success": True, "collections": collections}

    def reorder_collections(self, names: object = None) -> dict[str, object]:
        import sqlite3

        from core.database import reorder_collections

        coerced = self._coerce_names_sequence(names)
        if coerced is None:
            return {
                "success": False,
                "error": "Collection order must be a list of collection names",
            }

        try:
            collections = reorder_collections(coerced)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {
                "success": False,
                "error": str(e) or "Could not reorder collections",
            }
        return {"success": True, "collections": collections}

    def set_game_collections(
        self, game_id: object = None, names: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import set_game_collections

        coerced_game_id = self._coerce_db_id(game_id)
        if coerced_game_id is None:
            return {
                "success": False,
                "error": "Game not found",
                "error_code": "game_not_found",
            }
        coerced_names = self._coerce_names_sequence(names)
        if coerced_names is None:
            return {
                "success": False,
                "error": "Collections must be a list of collection names",
            }

        try:
            collections = set_game_collections(coerced_game_id, coerced_names)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {
                "success": False,
                "error": str(e) or "Could not update game collections",
            }
        return {"success": True, "collections": collections}

    def check_path(self, path: object = "", kind: object = "") -> dict[str, object]:
        """Report whether a typed path points at what its field expects.

        Purely informational: a not-found result never blocks saving.
        """
        if kind not in ("file", "dir", "parent", "runner"):
            return {"success": False, "error": f"Unknown path kind '{kind}'"}

        value = str(path or "").strip()
        if not value:
            return {"ok": False, "reason": "Enter a path"}
        expanded = os.path.expanduser(value)

        if kind == "runner":
            has_separator = "/" in expanded or os.sep in expanded
            if not has_separator and os.altsep is not None:
                has_separator = os.altsep in expanded
            if not has_separator:
                if host_which(expanded) is not None:
                    return {"ok": True, "reason": ""}
                return {"ok": False, "reason": "Command not found on PATH"}
            if os.path.isfile(expanded):
                return {"ok": True, "reason": ""}
            if os.path.isdir(expanded):
                return {"ok": False, "reason": "Expected a file"}
            return {"ok": False, "reason": "File not found"}

        if kind == "file":
            if os.path.isfile(expanded):
                return {"ok": True, "reason": ""}
            if os.path.isdir(expanded):
                return {"ok": False, "reason": "Expected a file"}
            return {"ok": False, "reason": "File not found"}

        if kind == "dir":
            if os.path.isdir(expanded):
                return {"ok": True, "reason": ""}
            if os.path.isfile(expanded):
                return {"ok": False, "reason": "Expected a folder"}
            return {"ok": False, "reason": "Folder not found"}

        # parent: the file may not exist yet, but its folder must.
        if os.path.isdir(expanded) or expanded.endswith(("/", "\\")):
            return {"ok": False, "reason": "Expected a file"}
        parent = os.path.dirname(expanded) or "."
        if os.path.isdir(parent):
            return {"ok": True, "reason": ""}
        return {"ok": False, "reason": "Folder not found"}

    def inspect_game_path(self, exe_path: object = "") -> dict[str, object]:
        from core.game_scan import inspect_game_path

        exe_path_str = os.path.expanduser(str(exe_path or "").strip())
        if not exe_path_str or not os.path.isfile(exe_path_str):
            return {"success": False, "error": "File not found"}
        try:
            return {"success": True, **inspect_game_path(exe_path_str)}
        except (OSError, ValueError) as e:
            return {"success": False, "error": str(e) or "File not found"}

    def scan_games_folder(self, folder: object = "") -> dict[str, object]:
        from core.database import get_all_games
        from core.game_scan import scan_games_folder

        folder_str = os.path.expanduser(str(folder or "").strip())
        if not folder_str or not os.path.isdir(folder_str):
            return {"success": False, "error": "Folder not found"}
        try:
            known = {
                self._normalize_compare_path(game.get("exe_path"))
                for game in get_all_games()
                if str(game.get("exe_path") or "").strip()
            }
            games = [
                {
                    **game,
                    "in_library": self._normalize_compare_path(game["exe_path"])
                    in known,
                }
                for game in scan_games_folder(folder_str)
            ]
        except (OSError, ValueError) as e:
            return {"success": False, "error": str(e) or "Could not scan folder"}
        return {"success": True, "games": games}

    def _refresh_synced_library(self) -> None:
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

    def complete_dropbox_callback(self, code: str, state: str) -> dict[str, object]:
        result = self.library_sync.complete_dropbox(code, state)
        if result.get("success"):
            _ = self.library_sync.start_run()
        return result

    def complete_gdrive_callback(self, code: str, state: str) -> dict[str, object]:
        result = self.library_sync.complete_gdrive(code, state)
        if result.get("success"):
            _ = self.library_sync.start_run()
        return result

    def disconnect_sync(self) -> dict[str, object]:
        return self.library_sync.disconnect()

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

    def add_game(
        self,
        title: object = "",
        exe_path: object = None,
        f95_url: str = "",
        version: str = "",
        cover_image: str = "",
        tags: str = "",
        rating: str = "",
        developer: str = "",
        engine: str = "",
        run_japanese_locale: bool = False,
        run_wayland: bool = False,
        auto_inject_ce: bool = False,
        custom_prefix: str = "",
        proton_version: str = "",
        launch_mode: str = "auto",
        command_line_args: str = "",
        links: object = None,
    ) -> dict[str, object]:
        import sqlite3

        from core.database import (
            add_game,
            find_game_by_f95_url,
            find_games_by_links,
            get_game,
            replace_game_links,
            update_game,
        )
        from core.store_links import coerce_store_links

        if not isinstance(title, str) or not title.strip():
            return {
                "success": False,
                "error": "Game title is required",
                "error_code": "invalid_title",
            }
        title = title.strip()

        if not isinstance(exe_path, str):
            return {
                "success": False,
                "error": "Game executable path must be a string",
                "error_code": "invalid_exe_path",
            }

        add_game_fn = cast(
            Callable[..., int | None],
            add_game,
        )

        normalized_url = normalize_thread_url(f95_url)
        normalized_launch_mode = normalize_launch_mode(launch_mode)
        incoming_links = coerce_store_links(links)
        linked_ids = find_games_by_links(incoming_links)
        existing_thread = (
            find_game_by_f95_url(normalized_url) if normalized_url else None
        )
        attach_id: int | None = None
        attach_fields: dict[str, object] = {"f95_url": normalized_url}
        if existing_thread or linked_ids:
            if not existing_thread and len(linked_ids) == 1 and normalized_url:
                linked_game = get_game(linked_ids[0])
                if linked_game and not linked_game.get("f95_url"):
                    attach_id = linked_ids[0]
                    # Adding with an executable installs a wishlisted match
                    # instead of dropping the path the user just chose.
                    if exe_path.strip() and not str(
                        linked_game.get("exe_path") or ""
                    ).strip():
                        attach_fields["exe_path"] = exe_path
                        if version.strip():
                            attach_fields["version"] = version
            if attach_id is None:
                return {
                    "success": False,
                    "error": "A game with this URL is already in your library",
                    "error_code": "duplicate_url",
                }

        try:
            if attach_id is not None:
                _ = update_game(attach_id, attach_fields)
                game_id = attach_id
            else:
                game_id = add_game_fn(
                    title,
                    exe_path,
                    normalized_url,
                    version=version,
                    cover_image=cover_image,
                    tags=tags,
                    rating=rating,
                    developer=developer,
                    engine=engine,
                    run_japanese_locale=run_japanese_locale,
                    run_wayland=run_wayland,
                    auto_inject_ce=auto_inject_ce,
                    custom_prefix=custom_prefix,
                    proton_version=proton_version,
                    launch_mode=normalized_launch_mode,
                    command_line_args=command_line_args,
                )
            if game_id is not None and incoming_links:
                replace_game_links(
                    game_id, "f95_post" if normalized_url else "page", incoming_links
                )
        except sqlite3.IntegrityError as exc:
            return self._integrity_error_response(exc)
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not add game"}

        metadata_pending = bool(normalized_url) and game_id is not None
        if metadata_pending:
            assert game_id is not None
            needs_metadata_backfill = (
                attach_id is not None
                or self._is_missing_text(engine)
                or self._is_missing_text(cover_image)
                or not self._normalize_tags_csv(tags)
            )
            # F95 requests are paced app-wide and can queue behind a folder import,
            # so the game is saved now and its card shows a loading state until this finishes.
            with self._metadata_lock:
                self._metadata_pending.add(game_id)
            threading.Thread(
                target=self._fetch_added_game_metadata,
                args=(game_id, normalized_url, needs_metadata_backfill),
                daemon=True,
            ).start()

        return {
            "success": True,
            "id": game_id,
            "title": title,
            "metadata_pending": metadata_pending,
            "attached": attach_id is not None,
        }

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
            with self._metadata_lock:
                self._metadata_pending.discard(game_id)
            if self.window:
                with suppress(Exception):
                    self.window.evaluate_js(
                        "window.dispatchEvent(new Event('wlib-refresh-library'))"
                    )

    @staticmethod
    def _integrity_error_response(exc: Exception) -> dict[str, object]:
        # Both the app-level guard ("duplicate f95_url") and the unique index
        # ("UNIQUE constraint failed: games.f95_url") name the column.
        if "f95_url" in str(exc):
            return {
                "success": False,
                "error": "A game with this URL is already in your library",
                "error_code": "duplicate_url",
            }
        return {"success": False, "error": str(exc)}

    def delete_game(self, game_id: object = None) -> dict[str, object]:
        import sqlite3

        from core.database import delete_game, get_connection, get_game

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }

        try:
            previous_game = get_game(coerced_id)
            with closing(get_connection()) as conn:
                exists = cast(
                    object,
                    conn.execute(
                        "SELECT 1 FROM games WHERE id = ?", (coerced_id,)
                    ).fetchone(),
                )
            if exists is None:
                return {
                    "success": False,
                    "error": "Game was not found",
                    "error_code": "game_not_found",
                }
            delete_game_fn = cast(Callable[[int], None], delete_game)
            delete_game_fn(coerced_id)
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not delete game"}
        try:
            from core.game_attachments import remove_game_attachments

            remove_game_attachments(coerced_id)
        except Exception as exc:
            print(f"[wLib] Failed to remove attachments for game {coerced_id}: {exc}")
        try:
            from core.game_covers import delete_cover_file

            if previous_game:
                delete_cover_file(previous_game.get("cover_local"))
        except Exception as exc:
            print(f"[wLib] Failed to remove cover for game {coerced_id}: {exc}")
        with self._metadata_lock:
            self._metadata_pending.discard(coerced_id)
        return {"success": True}

    def update_game(
        self, game_id: object = None, fields: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import get_connection, update_game

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }

        if fields is not None and not isinstance(fields, Mapping):
            return {
                "success": False,
                "error": "Fields must be an object",
                "error_code": "invalid_fields",
            }

        updated_fields: dict[str, object] = dict(
            cast(Mapping[str, object], fields or {})
        )
        title = updated_fields.get("title")
        if title is not None:
            # None is ignored by the database helper, like every other field.
            if not isinstance(title, str) or not title.strip():
                return {
                    "success": False,
                    "error": "Game title cannot be empty",
                    "error_code": "invalid_title",
                }
            updated_fields["title"] = title.strip()

        update_game_fn = cast(
            Callable[[int, dict[str, object]], dict[str, object]], update_game
        )

        try:
            with closing(get_connection()) as conn:
                exists = cast(
                    object,
                    conn.execute(
                        "SELECT 1 FROM games WHERE id = ?", (coerced_id,)
                    ).fetchone(),
                )
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not update game"}
        if exists is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }

        if "f95_url" in updated_fields:
            updated_fields["f95_url"] = normalize_thread_url(
                str(updated_fields["f95_url"] or "")
            )
        if "launch_mode" in updated_fields:
            updated_fields["launch_mode"] = normalize_launch_mode(
                updated_fields["launch_mode"]
            )
        if updated_fields.get("save_path_override") is not None:
            # Same rules as set_game_save_folder, so this path can't bypass them.
            from core.game_saves import GameSavesError, validate_save_override

            raw_override = updated_fields["save_path_override"]
            if not isinstance(raw_override, str):
                return {"success": False, "error": "The saves folder must be a path"}
            try:
                updated_fields["save_path_override"] = validate_save_override(
                    raw_override, self._save_backups_root()
                )
            except GameSavesError as exc:
                return {"success": False, "error": str(exc)}

        try:
            applied_fields = update_game_fn(coerced_id, updated_fields)
        except sqlite3.IntegrityError as exc:
            return self._integrity_error_response(exc)
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not update game"}

        response: dict[str, object] = {"success": True}
        if (
            "finished_version" in applied_fields
            and updated_fields.get("finished_version") is None
        ):
            response["finished_version"] = applied_fields["finished_version"]
        return response

    def list_companion_tools(self) -> dict[str, object]:
        return {"success": True, "tools": database.list_companion_tools()}

    def save_companion_tool(self, tool: object = None) -> dict[str, object]:
        import sqlite3

        if not isinstance(tool, Mapping):
            return {"success": False, "error": "Tool definition must be an object"}
        fields = dict(cast(Mapping[str, object], tool))
        if "id" in fields:
            fields["id"] = self._coerce_db_id(fields["id"])
            if fields["id"] is None:
                return {"success": False, "error": "Tool ID is invalid"}
        try:
            return {"success": True, "tool": database.save_companion_tool(fields)}
        except (ValueError, TypeError, sqlite3.Error) as exc:
            return {"success": False, "error": f"{fields.get('name', 'Tool')}: {exc}"}

    def delete_companion_tool(self, tool_id: object = None) -> dict[str, object]:
        tool_id = self._coerce_db_id(tool_id)
        if tool_id is None or not database.delete_companion_tool(tool_id):
            return {"success": False, "error": "Tool was not found"}
        return {"success": True}

    def get_game_companion_tools(self, game_id: object = None) -> dict[str, object]:
        game_id = self._coerce_db_id(game_id)
        if game_id is None or database.get_game(game_id) is None:
            return {"success": False, "error": "Game was not found"}
        return {"success": True, "tools": database.get_game_companion_tools(game_id)}

    def set_game_companion_tool_override(
        self, game_id: object = None, tool_id: object = None, visible: object = None
    ) -> dict[str, object]:
        game_id = self._coerce_db_id(game_id)
        tool_id = self._coerce_db_id(tool_id)
        tool = database.get_companion_tool(tool_id) if tool_id is not None else None
        name = tool["name"] if tool else "Tool"
        if game_id is None or database.get_game(game_id) is None:
            return {"success": False, "error": f"{name}: game was not found"}
        if tool is None or tool_id is None:
            return {"success": False, "error": "Tool was not found"}
        if visible is not None and not isinstance(visible, bool):
            return {
                "success": False,
                "error": f"{name}: visible must be true, false or null",
            }
        database.set_game_companion_tool_override(game_id, tool_id, visible)
        return {"success": True}

    def list_launchable_companion_tools(
        self, game_id: object = None
    ) -> dict[str, object]:
        if not is_linux():
            return unsupported_on_host("Custom tools")
        game_id = self._coerce_db_id(game_id)
        if game_id is None or database.get_game(game_id) is None:
            return {"success": False, "error": "Game was not found"}
        tools: list[database.CompanionTool] = [
            tool
            for tool in database.get_game_companion_tools(game_id)
            if tool["visible"]
        ]
        return {
            "success": True,
            "tools": self.launcher.list_launchable_tools(game_id, tools),
        }

    def launch_companion_tool(
        self, game_id: object = None, tool_id: object = None
    ) -> dict[str, object]:
        game_id = self._coerce_db_id(game_id)
        tool_id = self._coerce_db_id(tool_id)
        tool = database.get_companion_tool(tool_id) if tool_id is not None else None
        name = tool["name"] if tool else f"Tool {tool_id}"
        if not is_linux():
            return unsupported_on_host(name)
        if tool is None:
            return {"success": False, "error": f"{name}: tool was not found"}
        if game_id is None or database.get_game(game_id) is None:
            return {"success": False, "error": f"{name}: game was not found"}
        if not any(
            entry["id"] == tool_id and entry["visible"]
            for entry in database.get_game_companion_tools(game_id)
        ):
            return {
                "success": False,
                "error": f"{name}: tool is not shown for this game",
            }
        return self.launcher.launch_companion_tool(game_id, tool)

    def get_launch_targets(self, game_id: object = None) -> list[dict[str, object]]:
        import sqlite3

        from core.database import list_game_launch_targets

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return []

        list_targets_fn = cast(
            Callable[[int], list[dict[str, object]]], list_game_launch_targets
        )

        try:
            return list_targets_fn(coerced_id)
        except (sqlite3.Error, TypeError, OverflowError) as exc:
            print(f"Failed to list launch targets for game {coerced_id}: {exc}")
            return []

    def create_launch_target(
        self,
        game_id: object = None,
        label: object = "",
        exe_path: object = "",
        sort_order: object | None = None,
    ) -> dict[str, object]:
        import sqlite3

        from core.database import add_game_launch_target

        coerced_game_id = self._coerce_db_id(game_id)
        if coerced_game_id is None:
            return {
                "success": False,
                "error": "Parent game was not found",
                "error_code": "game_not_found",
            }

        add_target_fn = cast(
            Callable[[int, object, object, object | None], dict[str, object]],
            add_game_launch_target,
        )

        try:
            target = add_target_fn(coerced_game_id, label, exe_path, sort_order)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_code": "invalid_target"}
        except sqlite3.IntegrityError:
            return {
                "success": False,
                "error": "Parent game was not found",
                "error_code": "game_not_found",
            }
        except (sqlite3.Error, TypeError, OverflowError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not create launch target",
                "error_code": "invalid_target",
            }

        return {"success": True, "target": target}

    def update_launch_target(
        self, target_id: object = None, fields: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import update_game_launch_target

        coerced_id = self._coerce_db_id(target_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Launch target was not found",
                "error_code": "target_not_found",
            }
        if fields is not None and not isinstance(fields, Mapping):
            return {
                "success": False,
                "error": "Fields must be an object",
                "error_code": "invalid_target",
            }

        update_target_fn = cast(
            Callable[[int, Mapping[str, object]], dict[str, object] | None],
            update_game_launch_target,
        )

        try:
            target = update_target_fn(
                coerced_id, dict(cast(Mapping[str, object], fields or {}))
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_code": "invalid_target"}
        except (sqlite3.Error, TypeError, OverflowError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not update launch target",
                "error_code": "invalid_target",
            }

        if target is None:
            return {
                "success": False,
                "error": "Launch target was not found",
                "error_code": "target_not_found",
            }

        return {"success": True, "target": target}

    def delete_launch_target(self, target_id: object = None) -> dict[str, object]:
        import sqlite3

        from core.database import delete_game_launch_target

        coerced_id = self._coerce_db_id(target_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Launch target was not found",
                "error_code": "target_not_found",
            }

        delete_target_fn = cast(Callable[[int], bool], delete_game_launch_target)

        try:
            deleted = delete_target_fn(coerced_id)
        except (sqlite3.Error, TypeError, OverflowError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not delete launch target",
                "error_code": "target_not_found",
            }
        if not deleted:
            return {
                "success": False,
                "error": "Launch target was not found",
                "error_code": "target_not_found",
            }

        return {"success": True}

    def reorder_launch_targets(
        self, game_id: object = None, target_ids: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import reorder_game_launch_targets

        coerced_game_id = self._coerce_db_id(game_id)
        if coerced_game_id is None:
            return {
                "success": False,
                "error": "Parent game was not found",
                "error_code": "game_not_found",
            }

        reorder_targets_fn = cast(
            Callable[[int, Sequence[int]], list[dict[str, object]]],
            reorder_game_launch_targets,
        )

        if isinstance(target_ids, str) or not isinstance(target_ids, Sequence):
            return {
                "success": False,
                "error": "Launch target order must be a list of target IDs",
                "error_code": "invalid_target_order",
            }

        try:
            normalized_ids = [int(str(target_id)) for target_id in target_ids]
            targets = reorder_targets_fn(coerced_game_id, normalized_ids)
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "error": str(exc),
                "error_code": "invalid_target_order",
            }
        except (sqlite3.Error, OverflowError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not reorder launch targets",
                "error_code": "invalid_target_order",
            }

        return {"success": True, "targets": targets}

    # ==========================
    # Scraper API
    # ==========================
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
            if fallback_value in ("unavailable", "failed"):
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

        engine_value = str(metadata.get("engine") or "").strip()
        tags_value = self._normalize_tags_csv(metadata.get("tags"))
        cover_value = str(metadata.get("cover_image") or "").strip()

        if not engine_value and not tags_value and not cover_value:
            return 0

        import sqlite3

        from core.database import get_connection

        updated_rows = 0
        with closing(get_connection()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            _ = cursor.execute(
                "SELECT id, engine, tags, cover_image_path FROM games WHERE f95_url = ?",
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
                if tags_value and self._is_missing_text(tags_current):
                    fields["tags"] = tags_value
                if cover_value and self._is_missing_text(cover_current):
                    fields["cover_image_path"] = cover_value

                if fields:
                    set_clause = ", ".join(f"{key} = ?" for key in fields)
                    values: list[str | int] = list(fields.values()) + [game_id]
                    _ = cursor.execute(
                        f"UPDATE games SET {set_clause} WHERE id = ?", values
                    )
                    updated_rows += 1

            if updated_rows:
                conn.commit()

        return updated_rows

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

    def check_for_updates(self, url: str) -> Mapping[str, object]:
        """
        Reads the version from F95Zone's checker endpoint and refreshes thread metadata.
        Stores it in latest_version for comparison with the current version.
        """
        if not url.strip():
            return {"success": False, "error": "A valid thread URL is required"}

        # Stored f95_url values are normalized; exact-match lookups below need the same form.
        url = normalize_thread_url(url) or url.strip()
        print(f"Checking for updates for {url}")
        try:
            version_info = self._coerce_string_key_dict(
                cast(
                    object,
                    self.scraper.get_thread_version(
                        url,
                        include_metadata=True,
                    ),
                )
            )
            if version_info is None:
                version_info = {}

            if not version_info.get("success"):
                return self._build_scraper_error_payload(
                    version_info, "Failed to check for updates"
                )

            newly_finished_ids = self._update_thread_edit_metadata_for_url(
                url, version_info
            )

            remote_version = version_info.get("version")
            if not self._is_actionable_remote_version(remote_version):
                return {
                    "success": False,
                    "error": "Could not extract a version from thread",
                    "error_code": "extract_failed",
                }

            # Store in latest_version field
            import sqlite3

            from core.database import get_connection

            get_connection_fn = get_connection

            conn: sqlite3.Connection | None = None
            try:
                conn = get_connection_fn()
                cursor = conn.cursor()
                _ = cursor.execute(
                    "UPDATE games SET latest_version = ? WHERE f95_url = ?",
                    (str(remote_version).strip(), url),
                )
                # Check if it differs from current version
                _ = cursor.execute(
                    "SELECT id, version, exe_path, skipped_version FROM games WHERE f95_url = ?",
                    (url,),
                )
                row = cast(object, cursor.fetchone())
                current_version = ""
                wishlisted = False
                newly_finished = False
                skipped_version = ""
                if isinstance(row, Sequence) and len(row) >= 3:
                    current_version = str(row[1] or "").strip()
                    wishlisted = not str(row[2] or "").strip()
                    newly_finished = wishlisted and row[0] in newly_finished_ids
                    skipped_version = str(row[3] or "") if len(row) > 3 else ""
                conn.commit()
            finally:
                if conn is not None:
                    conn.close()

            has_update = not wishlisted and self._has_update(
                current_version, remote_version, skipped_version
            )
            metadata_updated = self._backfill_missing_metadata_for_url(
                url, version_info
            )
            return {
                "success": True,
                "version": str(remote_version).strip(),
                "has_update": has_update,
                "skipped": bool(normalize_version(skipped_version))
                and normalize_version(skipped_version)
                == normalize_version(remote_version),
                "newly_finished": newly_finished,
                "metadata_updated": metadata_updated,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_code": "check_failed",
            }

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
        self, game_ids: Sequence[int] | None = None, *, automatic: bool = False
    ) -> dict[str, object]:
        """Start a background thread to check all games for updates."""
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
                if ryuu_link:
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
            self._update_delay_seconds = self._get_bulk_check_delay_seconds()

        games_by_url = {g["f95_url"]: g for g in games_with_url}
        next_title_by_url = {
            g["f95_url"]: nxt["title"]
            for g, nxt in zip(games_with_url, games_with_url[1:])
        }
        current_versions_by_url = {g["f95_url"]: g["version"] for g in games_with_url}
        previous_latest_versions = {g["id"]: g.get("latest_version", "") for g in games_with_url}
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
                    wishlisted = not str(game.get("exe_path") or "").strip()
                    callback_error = str(result_dict.get("error") or "")
                    callback_error_code = str(result_dict.get("code") or "")
                    callback_fallback = result_dict.get("browser_fallback")
                    callback_browser_fallback = (
                        str(callback_fallback)
                        if callback_fallback in ("unavailable", "failed")
                        else ""
                    )
                    if not result_dict.get("success") and not callback_error:
                        callback_error = "Update check failed"

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
                            for key in ("thread_completed", "screenshots", "store_links")
                        )
                        if not callback_error and page_metadata:
                            try:
                                finished_ids = (
                                    self._update_thread_edit_metadata_for_url(
                                        url, result_dict
                                    )
                                )
                                newly_finished = (
                                    wishlisted and game["id"] in finished_ids
                                )
                            except Exception:
                                pass
                        elif (
                            not callback_error
                            and wishlisted
                            and str(game.get("latest_version") or "").strip()
                            and str(game.get("latest_version") or "").strip()
                            != remote_version
                        ):
                            try:
                                metadata = self._coerce_string_key_dict(
                                    self.scraper.get_thread_metadata(url)
                                )
                                if metadata and metadata.get("success"):
                                    newly_finished = game["id"] in (
                                        self._update_thread_edit_metadata_for_url(
                                            url, metadata
                                        )
                                    )
                            except Exception:
                                pass
                    elif result_dict.get("success"):
                        callback_error = "Could not extract a version from thread"
                        callback_error_code = "extract_failed"

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
                    from datetime import datetime

                    from core.database import update_setting

                    try:
                        update_setting("last_update_check", datetime.now().isoformat())
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
                        != normalize_version(previous_latest_versions.get(int(str(result["id"])), ""))
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

    def get_update_status(self) -> dict[str, object]:
        """Get the current status of the bulk update check."""
        with self._update_lock:
            return {
                "running": getattr(self, "_update_running", False),
                "cancelling": self._update_running and self._update_cancelled,
                "outcome": self._update_outcome,
                "error": self._update_error,
                "total": getattr(self, "_update_total", 0),
                "checked": getattr(self, "_update_checked", 0),
                "current": getattr(self, "_update_current", ""),
                "results": list(getattr(self, "_update_results", [])),
                "delay_seconds": getattr(self, "_update_delay_seconds", 2),
            }

    def cancel_update_check(self) -> dict[str, bool]:
        """Cancel an in-progress bulk update check."""
        with self._update_lock:
            self._update_cancelled = True
        return {"success": True}

    def get_executable_modified_time(
        self, exe_path: str
    ) -> ExecutableModifiedTimeResponse:
        from datetime import datetime

        normalized_path = str(exe_path or "").strip()
        if not normalized_path:
            return {
                "success": False,
                "modified_at": None,
                "error": "Executable path is required",
            }

        try:
            stat_result = os.stat(normalized_path)
        except OSError as e:
            return {"success": False, "modified_at": None, "error": str(e)}

        try:
            modified_at = datetime.fromtimestamp(stat_result.st_mtime).isoformat()
        except (OSError, OverflowError, ValueError) as e:
            return {"success": False, "modified_at": None, "error": str(e)}

        return {"success": True, "modified_at": modified_at}

    def get_auto_check_setting(self) -> dict[str, str]:
        """Get the auto update check frequency setting."""
        from core.database import get_connection

        get_connection_fn = get_connection

        with closing(get_connection_fn()) as conn:
            cursor = conn.cursor()
            _ = cursor.execute(
                "SELECT value FROM settings WHERE key = 'auto_update_check'"
            )
            row = cast(object, cursor.fetchone())
            freq = "weekly"
            if isinstance(row, Sequence) and len(row) > 0 and row[0] is not None:
                freq = str(row[0])
            _ = cursor.execute(
                "SELECT value FROM settings WHERE key = 'last_update_check'"
            )
            row2 = cast(object, cursor.fetchone())
            last = ""
            if isinstance(row2, Sequence) and len(row2) > 0 and row2[0] is not None:
                last = str(row2[0])
        return {"frequency": freq, "last_check": last}

    def set_auto_check_setting(self, frequency: object = "") -> dict[str, object]:
        """Set auto update check frequency: 'weekly', 'monthly', or 'off'."""
        import sqlite3

        from core.database import update_setting

        if not isinstance(frequency, str):
            return {
                "success": False,
                "error": "Frequency must be weekly, monthly or off",
            }
        normalized = frequency.strip().lower()
        if normalized not in {"weekly", "monthly", "off"}:
            return {
                "success": False,
                "error": "Frequency must be weekly, monthly or off",
            }
        try:
            update_setting("auto_update_check", normalized)
        except sqlite3.Error as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not save update frequency",
            }
        return {"success": True}

    def maybe_auto_check(self) -> dict[str, object]:
        """Check if auto update should run based on frequency and last check time."""
        from datetime import datetime, timedelta

        from core.database import get_connection

        get_connection_fn = get_connection

        conn = get_connection_fn()
        cursor = conn.cursor()
        _ = cursor.execute("SELECT value FROM settings WHERE key = 'auto_update_check'")
        row = cast(object, cursor.fetchone())
        freq = "weekly"
        if isinstance(row, Sequence) and len(row) > 0 and row[0] is not None:
            freq = str(row[0])

        if freq == "off":
            conn.close()
            return {"triggered": False, "reason": "Auto-check is disabled"}

        _ = cursor.execute("SELECT value FROM settings WHERE key = 'last_update_check'")
        row2 = cast(object, cursor.fetchone())
        last_check_str = ""
        if isinstance(row2, Sequence) and len(row2) > 0 and row2[0] is not None:
            last_check_str = str(row2[0])
        conn.close()

        now = datetime.now()
        should_check = False

        if not last_check_str:
            should_check = True
        else:
            try:
                last_check = datetime.fromisoformat(last_check_str)
                if freq == "weekly" and now - last_check > timedelta(weeks=1):
                    should_check = True
                elif freq == "monthly" and now - last_check > timedelta(days=30):
                    should_check = True
            except ValueError:
                should_check = True

        if should_check:
            result = self.check_all_updates(automatic=True)
            return {"triggered": True, "result": result}

        return {"triggered": False, "reason": f"Last checked: {last_check_str}"}

    # ==========================
    # Launcher API
    # ==========================
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

    def launch_game(
        self,
        game_id: int | None = None,
        exe_path: str = "",
        command_line_args: str = "",
        run_japanese_locale: bool = False,
        run_wayland: bool = False,
        auto_inject_ce: bool = False,
        custom_prefix: str = "",
        proton_version: str = "",
        launch_mode: str = "auto",
    ) -> Mapping[str, object]:
        """
        Uses the Launcher class to execute the game via Proton/Wine.
        """
        target_path = (exe_path or "").strip()
        if game_id is not None and not (target_path and os.path.exists(target_path)):
            # Missing/empty path: fall back to the default exe, then other targets.
            # Only the default launch (empty request or the canonical path
            # itself) may fall back. An explicit launch-target path that was
            # deleted must error instead of silently launching a different file.
            # When nothing else exists either, pass the request through so the
            # launcher reports the missing file (and mocked launchers in tests
            # still see the requested path).
            from core.database import get_all_games, list_game_launch_targets

            canonical_path = next(
                (
                    str(g.get("exe_path") or "").strip()
                    for g in get_all_games()
                    if g.get("id") == game_id
                ),
                "",
            )
            candidates = [canonical_path] + [
                str(t.get("exe_path") or "").strip()
                for t in list_game_launch_targets(game_id)
            ]
            existing = [c for c in candidates if c and os.path.exists(c)]
            is_default_request = not target_path or (
                canonical_path
                and self._normalize_compare_path(target_path)
                == self._normalize_compare_path(canonical_path)
            )
            if not is_default_request and existing:
                return {
                    "success": False,
                    "error": f"Executable not found at {target_path}",
                }
            target_path = next(
                iter(existing),
                target_path or canonical_path,
            )

        print(
            f"Launching {target_path} (Args: {command_line_args}, JP Locale: {run_japanese_locale}, Wayland: {run_wayland}, Auto Inject CE: {auto_inject_ce}, Custom Prefix: {custom_prefix}, Proton Version: {proton_version}, Launch Mode: {launch_mode})"
        )

        from core.database import (
            get_all_games,
            record_play_tick,
            start_play_session,
            update_playtime,
        )

        empty_game: dict[str, object] = {}
        stored_game = (
            next((game for game in get_all_games() if game.get("id") == game_id), empty_game)
            if game_id is not None
            else empty_game
        )
        launch_version = str(stored_game.get("version") or "")
        session_id: int | None = None
        session_failed = False
        session_lock = threading.Lock()
        pending_ticks: list[tuple[int, bool]] = []

        def save_tick(delta: int, is_final: bool) -> None:
            if game_id is None:
                return
            last_played_version: str | None = None
            if session_id is None:
                # No journal row could be created; still count the playtime.
                update_playtime(game_id, delta)
            else:
                last_played_version = record_play_tick(
                    game_id, session_id, delta, is_final
                )
            # evaluate_js can block forever once the window is gone at exit.
            if self.window and not self.launcher.shutting_down:
                detail = json.dumps(
                    {
                        "gameId": game_id,
                        "delta": max(int(delta), 0),
                        "isFinal": bool(is_final),
                        "lastPlayedVersion": last_played_version,
                        "sessionId": session_id,
                    }
                )
                self.window.evaluate_js(
                    f"window.dispatchEvent(new CustomEvent('wlib-playtime-tick', {{ detail: {detail} }}))"
                )

        def on_exit(delta: int, is_final: bool = True) -> None:
            if game_id is None:
                return
            try:
                with session_lock:
                    if session_id is None and not session_failed:
                        pending_ticks.append((delta, is_final))
                    else:
                        save_tick(delta, is_final)
            except Exception as e:
                print(f"Failed to update playtime for game {game_id}: {e}")

        try:
            result = self.launcher.launch(
                target_path,
                command_line_args,
                run_japanese_locale,
                run_wayland,
                auto_inject_ce,
                custom_prefix,
                proton_version,
                normalize_launch_mode(launch_mode),
                on_exit_callback=on_exit,
                game_id=game_id,
                sandbox_enabled=bool(stored_game.get("sandbox_enabled")),
                game_title=str(stored_game.get("title") or ""),
            )
        except Exception as exc:
            # Launcher setup can still fail outside its own error paths (for
            # example creating the default Wine prefix). Keep the bridge
            # contract: report a failure payload instead of rejecting the call.
            print(f"Failed to launch game {game_id}: {exc}")
            return {"success": False, "error": str(exc) or "Could not launch the game."}
        if result.get("success") and result.get("tracks_playtime", True) and game_id is not None:
            with session_lock:
                try:
                    session_id = start_play_session(game_id, launch_version)
                except Exception as exc:
                    session_failed = True
                    print(f"Failed to start play session for game {game_id}: {exc}")
                for delta, is_final in pending_ticks:
                    try:
                        save_tick(delta, is_final)
                    except Exception as exc:
                        print(f"Failed to update playtime for game {game_id}: {exc}")
                pending_ticks.clear()
        return result

    def get_game_play_sessions(self, game_id: int) -> dict[str, object]:
        from core.database import get_play_sessions

        try:
            return {"success": True, "sessions": get_play_sessions(game_id)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def set_window_focused(self, focused: bool) -> dict[str, bool]:
        self._window_focused = bool(focused)
        return {"success": True}

    def stop_game(self, game_id: int) -> dict[str, object]:
        return self.launcher.stop(game_id)

    def get_running_games(self) -> list[int]:
        return self.launcher.get_running_games()

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

    # ── Game mod tools (Ren'Py / RPG Maker MV/MZ) ──

    @contextmanager
    def _tool_run(self, game_id: int, action: str) -> Generator[None]:
        """Allow one tool action per game, and none while wLib runs the game."""
        from core.game_tools import GameToolError

        with self._tool_lock:
            if game_id in self._tool_status:
                raise GameToolError("A tool is already running for this game")
            if game_id in self.get_running_games():
                raise GameToolError("Close the game before using its tools")
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

    def _tool_game(self, game_id: object) -> tuple[int, str] | None:
        """(id, executable folder) from the stored row, or None for an unknown id."""
        from core.database import get_game

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return None
        row = get_game(coerced_id)
        if row is None:
            return None
        try:
            return coerced_id, game_tool_folder(row)
        except ValueError:
            return coerced_id, ""

    def get_game_tool_support(self, game_id: object = None) -> dict[str, object]:
        from core.game_tools import detect_engine

        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found", "engine": ""}
        return {"success": True, "engine": detect_engine(game[1])}

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
            detect_engine,
            rpgm_data_root,
        )

        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found"}
        coerced_id, folder = game
        engine = detect_engine(folder)
        try:
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

        game = self._tool_game(game_id)
        if game is None:
            return {"success": False, "error": "Game not found"}
        coerced_id, folder = game
        if detect_engine(folder) != engine:
            label = "Ren'Py" if engine == "renpy" else "RPG Maker MV/MZ"
            return {"success": False, "error": f"This game is not a {label} game"}
        try:
            with self._tool_run(coerced_id, action):
                result = body(
                    folder,
                    self._tool_progress(coerced_id),
                    self._tool_cancelled(coerced_id),
                )
        except (GameToolError, ValueError, OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, **result}

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

    def install_rpgmaker_dependencies(
        self, prefix_path: str | None = None, proton_path: str | None = None
    ) -> dict[str, object]:
        """
        Runs winetricks to install the common dependencies needed for RPGMaker/Unity visual novels.
        """
        if not is_linux():
            return unsupported_on_host("Winetricks dependency installation")

        import subprocess

        winetricks_path = host_which("winetricks")
        if not winetricks_path:
            return {
                "success": False,
                "error": "Winetricks is not installed. Install it first, then try again.",
            }

        try:
            env, target = self._build_runtime_install_env(prefix_path, proton_path)
        except (OSError, ValueError) as exc:
            return {
                "success": False,
                "error": f"Could not prepare the Wine prefix: {exc}",
            }

        verbs = [
            "corefonts",
            "d3dcompiler_42",
            "d3dcompiler_43",
            "d3dcompiler_47",
            "d3dx9",
            "d3dx11_42",
            "d3dx11_43",
            "quartz",
            "directshow",
            "wmp9",
            "dotnetdesktop8",
            "vcrun2013",
            "vcrun2022",
            "dotnet40",
            "dotnet45",
            "dotnet46",
            "dotnet461",
            "dotnet462",
            "dotnet472",
            "allfonts",
            "cjkfonts",
            "consolas",
            "unifont",
            "vcrun6",
            "vcrun2010",
            "dotnetcoredesktop3",
        ]
        command = [winetricks_path, "-q"] + verbs
        print(
            f"Running winetricks command in {env.get('WINEPREFIX')}: {' '.join(command)}"
        )

        with self._status_lock:
            # winetricks verbs run wineserver -k/-w, which would break a parallel install.
            if (
                self._deps_install_status["running"]
                or self._rtp_install_status["running"]
            ):
                return {
                    "success": False,
                    "error": "Another install is already running. Wait for it to finish.",
                }
            self._deps_install_status = {
                "running": True,
                "done": 0,
                "total": len(verbs),
                "current": verbs[0],
                "error": "",
            }

        # We run this in a background thread so we don't block the UI returning 'success' immediately
        # (Winetricks downloads huge MS packages that take longer than the IPC timeout)
        def _install_deps():
            try:
                for i, verb in enumerate(verbs):
                    with self._status_lock:
                        self._deps_install_status["done"] = i
                        self._deps_install_status["current"] = verb
                    print(f"Installing winetricks verb {i + 1}/{len(verbs)}: {verb}")
                    result = subprocess.run(
                        host_command([winetricks_path, "-q", verb], env=env),
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"Winetricks failed while installing '{verb}' (exit code {result.returncode})."
                        )
                with self._status_lock:
                    self._deps_install_status["done"] = len(verbs)
                    self._deps_install_status["current"] = ""
                    self._deps_install_status["running"] = False
                    self._deps_install_status["error"] = ""
                print(
                    f"Finished installing winetricks dependencies for {target['resolved_prefix']}."
                )
            except Exception as e:
                with self._status_lock:
                    self._deps_install_status["running"] = False
                    self._deps_install_status["current"] = ""
                    self._deps_install_status["error"] = str(e)
                print(f"Winetricks encountered an error: {e}")

        threading.Thread(target=_install_deps, daemon=True).start()
        return {"success": True}

    def install_rpgmaker_rtp(
        self, prefix_path: str | None = None, proton_path: str | None = None
    ) -> dict[str, object]:
        """
        Downloads and installs RPG Maker VX Ace, VX, XP, and 2003 RTPs into the configured wine prefix.
        Currently downloads the official zips and triggers their setup.exe silently.
        """
        if not is_linux():
            return unsupported_on_host("Wine-prefix RPG Maker RTP installation")

        import shutil
        import subprocess
        import urllib.request
        import zipfile

        try:
            env, target = self._build_runtime_install_env(prefix_path, proton_path)
        except (OSError, ValueError) as exc:
            return {
                "success": False,
                "error": f"Could not prepare the Wine prefix: {exc}",
            }
        rtps = self._get_rtp_packages()

        runner_command: list[str]
        if target["is_proton"]:
            if not target["proton_path"] or not os.path.exists(target["proton_path"]):
                return {
                    "success": False,
                    "error": "Selected Proton executable was not found. Update the path and try again.",
                }
            runner_command = [target["proton_path"], "run"]
        else:
            wine_binary = host_which("wine")
            if not wine_binary:
                return {
                    "success": False,
                    "error": "Wine is not installed. Install Wine first, then try again.",
                }
            runner_command = [wine_binary]

        # We run this in a background thread so we don't block the UI returning 'success' immediately
        def _install_rtps():
            rtp_dir = get_rtp_dir()
            os.makedirs(rtp_dir, exist_ok=True)

            failures: list[str] = []

            for i, rtp in enumerate(rtps):
                rtp_name = str(rtp["name"])
                with self._status_lock:
                    self._rtp_install_status["done"] = i
                    self._rtp_install_status["current"] = rtp_name
                download_path = os.path.join(rtp_dir, str(rtp["filename"]))
                extract_path = os.path.join(rtp_dir, rtp_name.replace(" ", "_"))

                print(f"Downloading {rtp_name} RTP...")
                # Earlier builds could leave an empty file behind after a failed download.
                if (
                    not os.path.isfile(download_path)
                    or os.path.getsize(download_path) == 0
                ):
                    part_path = f"{download_path}.part"
                    try:
                        req = urllib.request.Request(
                            str(rtp["url"]), headers={"User-Agent": "Mozilla/5.0"}
                        )
                        with (
                            self._open_url_with_targeted_tls_fallback(
                                req, timeout=30
                            ) as response,
                            open(part_path, "wb") as out_file,
                        ):
                            shutil.copyfileobj(response, out_file)
                        os.replace(part_path, download_path)
                    except Exception as e:
                        formatted_error = self._format_rtp_download_error(
                            str(rtp["url"]), e
                        )
                        failures.append(f"{rtp_name}: {formatted_error}")
                        print(f"Failed to download {rtp_name} RTP: {formatted_error}")
                        continue

                setup_exe = None

                if download_path.endswith(".exe"):
                    setup_exe = download_path
                else:
                    print(f"Extracting {rtp_name} RTP...")
                    if not os.path.exists(extract_path):
                        staging_path = f"{extract_path}.part"
                        try:
                            shutil.rmtree(staging_path, ignore_errors=True)
                            with zipfile.ZipFile(download_path, "r") as zf:
                                zf.extractall(staging_path)
                            os.replace(staging_path, extract_path)
                        except Exception as e:
                            # Start clean next time: re-download and re-extract.
                            shutil.rmtree(staging_path, ignore_errors=True)
                            with suppress(OSError):
                                os.remove(download_path)
                            failures.append(f"{rtp_name}: extract failed ({e})")
                            print(f"Failed to extract {rtp_name} RTP: {e}")
                            continue

                    # Find the setup executable inside the extracted folder
                    for root, _dirs, files in os.walk(extract_path):
                        for f in files:
                            if f.lower().endswith(".exe"):
                                setup_exe = os.path.join(root, f)
                                break
                        if setup_exe:
                            break

                if setup_exe is None:
                    # Likely a half-extracted folder from an earlier build; redo it next time.
                    shutil.rmtree(extract_path, ignore_errors=True)
                    failures.append(f"{rtp_name}: installer executable not found")
                    continue

                print(f"Installing {rtp_name} RTP silently in prefix...")
                setup_exe_str = str(setup_exe)
                command = [*runner_command, setup_exe_str, "/S", "/v/qn"]
                try:
                    _ = subprocess.run(
                        host_command(command, env=env),
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=120,
                        check=True,
                    )
                    if not self._verify_rtp_packages(target["resolved_prefix"]).get(
                        rtp_name, False
                    ):
                        failures.append(
                            f"{rtp_name}: required runtime files were not found in the prefix after install"
                        )
                    else:
                        print(f"Finished installing {rtp_name} RTP.")
                except Exception as e:
                    failures.append(f"{rtp_name}: install failed ({e})")
                    print(f"Wine failed to run {rtp_name} setup: {e}")
                    # A silently truncated download would otherwise be reused
                    # forever; force the next attempt to download it again.
                    if os.path.abspath(str(setup_exe)) == os.path.abspath(
                        download_path
                    ):
                        with suppress(OSError):
                            os.remove(download_path)

            final_error = ""
            if failures:
                final_error = "; ".join(failures)

            if not final_error and not self._rtps_installed_in_prefix(
                target["resolved_prefix"]
            ):
                final_error = "Required RTP files were not found in the target prefix after installation."

            with self._status_lock:
                self._rtp_install_status["done"] = len(rtps)
                self._rtp_install_status["current"] = ""
                self._rtp_install_status["running"] = False
                self._rtp_install_status["error"] = final_error

            if final_error:
                print(
                    f"RTP installation did not verify successfully for {target['resolved_prefix']}: {final_error}"
                )
            else:
                print(
                    f"All RTPs installed successfully in {target['resolved_prefix']}."
                )

        def _download_and_install():
            # A failure before the per-package loop (for example the RTP cache
            # folder being unwritable) would otherwise leave the status stuck on
            # running forever and block every later installer.
            try:
                _install_rtps()
            except Exception as exc:
                message = str(exc) or "RTP installation failed."
                with self._status_lock:
                    self._rtp_install_status["running"] = False
                    self._rtp_install_status["current"] = ""
                    self._rtp_install_status["error"] = message
                print(f"RTP installation stopped unexpectedly: {message}")

        with self._status_lock:
            if (
                self._deps_install_status["running"]
                or self._rtp_install_status["running"]
            ):
                return {
                    "success": False,
                    "error": "Another install is already running. Wait for it to finish.",
                }
            self._rtp_install_status = {
                "running": True,
                "done": 0,
                "total": len(rtps),
                "current": str(rtps[0]["name"]),
                "error": "",
            }
        threading.Thread(target=_download_and_install, daemon=True).start()
        return {"success": True}

    def get_install_status(
        self, prefix_path: str | None = None, proton_path: str | None = None
    ) -> dict[str, object]:
        """Returns the current status of background DLL and RTP installs, plus whether they've previously completed."""
        if not is_linux():
            # The frontend types deps/rtps as full progress payloads, so report
            # idle installers here instead of empty objects.
            return {
                "success": False,
                "error": "Wine/Proton runtime installers are only available on Linux.",
                "code": "unsupported_platform",
                "deps": {
                    "running": False,
                    "done": 0,
                    "total": 0,
                    "current": "",
                    "error": "",
                },
                "rtps": {
                    "running": False,
                    "done": 0,
                    "total": 0,
                    "current": "",
                    "error": "",
                },
                "dlls_installed": False,
                "rtps_installed": False,
            }

        with self._status_lock:
            deps_status = dict(
                getattr(
                    self,
                    "_deps_install_status",
                    {
                        "running": False,
                        "done": 0,
                        "total": 0,
                        "current": "",
                        "error": "",
                    },
                )
            )
            rtp_status = dict(
                getattr(
                    self,
                    "_rtp_install_status",
                    {
                        "running": False,
                        "done": 0,
                        "total": 0,
                        "current": "",
                        "error": "",
                    },
                )
            )

        target = self._resolve_runtime_install_target(prefix_path, proton_path)

        return {
            "success": True,
            "deps": deps_status,
            "rtps": rtp_status,
            "dlls_installed": self._dlls_installed_in_prefix(target["resolved_prefix"]),
            "rtps_installed": self._rtps_installed_in_prefix(target["resolved_prefix"]),
        }

    def get_system_deps_command(self) -> dict[str, object]:
        """Detects the system package manager and returns the command to install 32-bit GStreamer/multimedia dependencies."""
        if not is_linux():
            return {
                "detected": False,
                "package_manager": "unsupported",
                "distro": "Windows",
                "command": "",
                "error": "Linux multimedia dependencies are not used on Windows.",
                "code": "unsupported_platform",
            }

        commands = {
            "dnf": {
                "name": "Fedora / RHEL",
                "command": "sudo dnf install -y gstreamer1-plugins-base.i686 gstreamer1-plugins-good.i686 gstreamer1-plugins-bad-free.i686 gstreamer1.i686 libvpx.i686 opus.i686 libvorbis.i686 libtheora.i686 libogg.i686 flac-libs.i686 speex.i686 libjpeg-turbo.i686 libsndfile.i686 libwebp.i686",
            },
            "apt": {
                "name": "Debian / Ubuntu",
                "command": "sudo dpkg --add-architecture i386 && sudo apt update && sudo apt install -y libgstreamer1.0-0:i386 gstreamer1.0-plugins-base:i386 gstreamer1.0-plugins-good:i386 gstreamer1.0-plugins-bad:i386 libvpx-dev:i386 libopus0:i386 libvorbis0a:i386 libtheora0:i386 libogg0:i386 libflac8:i386 libspeex1:i386 libjpeg62-turbo:i386 libsndfile1:i386 libwebp-dev:i386",
            },
            "pacman": {
                "name": "Arch Linux",
                "command": "sudo pacman -S --noconfirm lib32-gstreamer lib32-gst-plugins-base lib32-gst-plugins-good lib32-gst-plugins-bad-libs lib32-libvpx lib32-opus lib32-libvorbis lib32-libtheora lib32-libogg lib32-flac lib32-speex lib32-libjpeg-turbo lib32-libsndfile lib32-libwebp",
            },
            "zypper": {
                "name": "openSUSE",
                "command": "sudo zypper install -y gstreamer-plugins-base-32bit gstreamer-plugins-good-32bit gstreamer-plugins-bad-32bit libvpx-32bit libopus0-32bit libvorbis-32bit libtheora-32bit libogg-32bit libFLAC8-32bit speex-32bit libjpeg62-32bit libsndfile1-32bit libwebp-32bit",
            },
            "xbps-install": {
                "name": "Void Linux",
                "command": "sudo xbps-install -y gst-plugins-base1-32bit gst-plugins-good1-32bit gst-plugins-bad1-32bit libvpx-32bit libopus-32bit libvorbis-32bit libtheora-32bit libogg-32bit libflac-32bit speex-32bit libjpeg-turbo-32bit libsndfile-32bit libwebp-32bit",
            },
        }

        for pkg_mgr, info in commands.items():
            if host_which(pkg_mgr):
                return {
                    "detected": True,
                    "package_manager": pkg_mgr,
                    "distro": info["name"],
                    "command": info["command"],
                }

        return {
            "detected": False,
            "package_manager": "unknown",
            "distro": "Unknown",
            "command": "# Could not detect your package manager. Please install 32-bit GStreamer plugins manually.",
        }

    # ==========================
    # Settings & Utils API
    # ==========================
    def download_proton_ge(self) -> dict[str, object]:
        """
        Downloads the latest Proton-GE release and extracts it to the local share directory.
        """
        if not is_linux():
            return unsupported_on_host("Proton-GE installation")
        with self._status_lock:
            if self._proton_download_status["running"]:
                return {"success": False, "error": "GE-Proton is already downloading."}
            self._proton_download_status = {
                "running": True,
                "stage": "Fetching release info",
                "done": 0,
                "total": 0,
            }
        try:
            return self._download_proton_ge()
        finally:
            with self._status_lock:
                self._proton_download_status = {
                    "running": False,
                    "stage": "",
                    "done": 0,
                    "total": 0,
                }

    def get_proton_download_status(self) -> dict[str, object]:
        with self._status_lock:
            return dict(self._proton_download_status)

    def _set_proton_download_stage(
        self, stage: str, done: int = 0, total: int = 0
    ) -> None:
        with self._status_lock:
            self._proton_download_status.update(stage=stage, done=done, total=total)

    def _download_proton_ge(self) -> dict[str, object]:
        import json
        import os
        import tarfile
        import tempfile
        import urllib.request

        tar_path = ""
        try:
            print("Fetching latest GE-Proton release info...")
            api_url = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases/latest"
            req = urllib.request.Request(api_url, headers={"User-Agent": "wLib"})

            import ssl

            ctx = ssl.create_default_context()

            with cast(
                URLResponseContext,
                urllib.request.urlopen(req, context=ctx, timeout=30),
            ) as response:
                data = self._coerce_string_key_dict(
                    cast(object, json.loads(response.read().decode()))
                )
            if data is None:
                data = {}

            release_name = str(data.get("tag_name") or "proton-ge")
            assets = data.get("assets")
            asset_urls: dict[str, str] = {}
            for asset_obj in (
                cast(list[object], assets) if isinstance(assets, list) else []
            ):
                asset = self._coerce_string_key_dict(asset_obj)
                if asset:
                    asset_urls[str(asset.get("name") or "")] = str(
                        asset.get("browser_download_url") or ""
                    )

            # Newer releases ship one tarball per architecture
            # (e.g. GE-Proton11-7-x86_64.tar.gz and -aarch64.tar.gz);
            # older ones ship a single unsuffixed tarball.
            import platform

            arch = platform.machine().lower()
            arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(arch, arch)
            tarballs = [name for name in asset_urls if name.endswith(".tar.gz")]
            tarball = next(
                (name for name in tarballs if name.endswith(f"-{arch}.tar.gz")),
                tarballs[0] if len(tarballs) == 1 else "",
            )
            download_url = asset_urls.get(tarball, "")
            checksum_url = asset_urls.get(
                tarball.removesuffix(".tar.gz") + ".sha512sum", ""
            )

            if not download_url:
                return {
                    "success": False,
                    "error": "Could not find a valid release tarball.",
                }
            if not checksum_url:
                return {
                    "success": False,
                    "error": "Release has no .sha512sum file to verify the download.",
                }

            wlib_share_dir = get_proton_dir()
            os.makedirs(wlib_share_dir, exist_ok=True)

            # A predictable name in the shared temp directory can be pre-created
            # as a symlink by another local user, so create the file exclusively.
            tar_fd, tar_path = tempfile.mkstemp(
                prefix="wlib-proton-", suffix=".tar.gz"
            )
            os.close(tar_fd)

            # The .sha512sum file reads "<hash>  <tarball name>".
            checksum_req = urllib.request.Request(
                checksum_url, headers={"User-Agent": "wLib"}
            )
            with cast(
                URLResponseContext,
                urllib.request.urlopen(checksum_req, context=ctx, timeout=30),
            ) as response:
                checksum_text = response.read().decode("utf-8", "replace")
            expected_sha512 = (checksum_text.split() or [""])[0].lower()

            print(f"Downloading {release_name} from {download_url}...")
            # We must use urlopen with our context here instead of urlretrieve
            # because urlretrieve doesn't easily accept a custom SSL context.
            download_req = urllib.request.Request(
                download_url, headers={"User-Agent": "wLib"}
            )
            with (
                cast(
                    URLResponseContext,
                    urllib.request.urlopen(download_req, context=ctx, timeout=30),
                ) as response,
                open(tar_path, "wb") as out_file,
            ):
                # http.client.HTTPResponse.length holds Content-Length before reading.
                length = cast(object, getattr(response, "length", None))
                total = length if isinstance(length, int) else 0
                done = 0
                stage = f"Downloading {release_name}"
                self._set_proton_download_stage(stage, 0, total)
                while chunk := response.read(1024 * 1024):
                    _ = out_file.write(chunk)
                    done += len(chunk)
                    self._set_proton_download_stage(stage, done, total)

            self._set_proton_download_stage("Verifying download")

            with open(tar_path, "rb") as tar_file:
                actual_sha512 = hashlib.file_digest(tar_file, "sha512").hexdigest()
            if actual_sha512 != expected_sha512:
                return {
                    "success": False,
                    "error": "Downloaded GE-Proton archive failed its SHA-512 check.",
                }

            print(f"Extracting {tar_path}...")
            self._set_proton_download_stage("Extracting")
            with tarfile.open(tar_path, "r:gz") as tar:
                members = tar.getmembers()
                # Find the root extracted folder name (usually GE-Proton-X)
                root_dir = members[0].name.split("/")[0]
                tar.extractall(path=wlib_share_dir, filter="tar")

            proton_executable = os.path.join(wlib_share_dir, root_dir, "proton")
            if not os.path.exists(proton_executable):
                return {
                    "success": False,
                    "error": f"Proton executable not found at {proton_executable}",
                }

            from core.database import update_setting

            update_setting("proton_path", proton_executable)

            print(f"Successfully installed GE-Proton to {proton_executable}")
            return {"success": True, "path": proton_executable}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            # mkstemp names are unique, so a failed attempt would otherwise
            # leave a new partial tarball behind in the temp dir every time.
            if tar_path:
                with suppress(OSError):
                    os.remove(tar_path)

    def _catalog_enabled(self) -> bool:
        import sqlite3

        from core.database import get_setting

        try:
            return get_setting("catalog_enabled") != "0"
        except sqlite3.Error:
            return True

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
            "sandboxie_custom_path": str(get_setting_fn("sandboxie_custom_path") or ""),
            "update_notifications": get_setting_fn("update_notifications") != "off",
            "rpgmaker_linux_runner_path": rpgmaker_linux_runner_path,
            "rpgmaker_linux_runner_status": self.launcher.get_rpgmaker_linux_runner_status(
                rpgmaker_linux_runner_path
            ),
            "library_folder": str(get_setting_fn("library_folder") or ""),
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
            update_custom_play_statuses,
            update_setting,
        )

        if not isinstance(settings, Mapping):
            return {"success": False, "error": "Settings must be an object"}

        settings_map = cast(Mapping[str, object], settings)
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
        update_setting_fn = cast(Callable[[str, str], None], update_setting)

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
            if "library_folder" in settings_map:
                update_setting_fn(
                    "library_folder",
                    str(settings_map.get("library_folder") or "").strip(),
                )
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
            if "catalog_enabled" in settings_map:
                was_enabled = get_setting_fn("catalog_enabled") != "0"
                catalog_enabled = settings_map["catalog_enabled"] is not False
                update_setting_fn("catalog_enabled", "1" if catalog_enabled else "0")
                if not catalog_enabled:
                    self.catalog.remove()
                elif not was_enabled:
                    _ = self.catalog.start_background(force=True)
            if save_backup_keep is not None:
                update_setting_fn("save_backup_keep", save_backup_keep)
            if "custom_play_statuses" in settings_map:
                update_custom_play_statuses(custom_statuses)
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not save settings"}
        return {"success": True}

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

    def browse_file(self, start_path: str = "") -> str:
        """Opens a native file dialog to select an executable or HTML game."""
        file_types = (
            (
                "Windows Game Files (*.exe;*.bat;*.cmd;*.jar;*.html;*.htm)",
                "All files (*.*)",
            )
            if is_windows()
            else (
                "Game Files (*.exe;*.sh;*.AppImage;*.html;*.htm)",
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

    def browse_directory(self, start_path: str = "") -> str:
        """Opens a native directory dialog, e.g. for choosing a wine prefix."""
        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog("directory", directory=start_path)

        return self._browse_qt_dialog("directory", directory=start_path)

    def browse_archive_file(self, start_path: str = "") -> str:
        """Opens a native file dialog to select a game archive (.zip/.7z/.rar)."""
        file_types = (
            "Game Archives (*.zip;*.7z;*.rar)",
            "All files (*.*)",
        )

        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog(
                "file", directory=start_path, file_types=file_types
            )

        return self._browse_qt_dialog(
            "file", directory=start_path, file_types=file_types
        )

    def browse_pdf_file(self, start_path: str = "") -> str:
        """Opens a native file dialog to select a PDF file for attachments."""
        file_types = (
            "PDF Files (*.pdf)",
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
            NotAPdfError,
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
        except NotAPdfError as exc:
            return {
                "success": False,
                "error": str(exc) or "Only PDF files can be attached",
                "error_code": "not_a_pdf",
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
        result = self._open_with_system_handler(resolved, "No PDF viewer opener found")
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
            return {"success": False, "error": str(exc) or "Could not remove attachment"}
        try:
            attachments = list_attachments(coerced_id)
        except Exception:
            attachments = []
        return {"success": True, "attachments": attachments}

    def lookup_thread_details(self, url: str) -> dict[str, object]:
        """Read thread metadata for a new archive install through the paced scraper."""
        from core.f95zone import extract_thread_id, split_thread_title

        normalized = normalize_thread_url(url)
        if not extract_thread_id(normalized):
            return {
                "success": False,
                "error": "A valid F95Zone thread URL is required.",
            }
        try:
            metadata = self.scraper.get_thread_metadata(normalized)
            if not metadata.get("success"):
                return {
                    "success": False,
                    "error": metadata.get("error") or "Could not load thread details.",
                }
            title, version, creator = split_thread_title(
                str(metadata.get("title") or "")
            )
            return {
                "success": True,
                "title": title,
                "version": version or metadata.get("version") or "",
                "creator": creator,
                "engine": metadata.get("engine") or "",
                "cover_image": metadata.get("cover_image") or "",
                "tags": metadata.get("tags") or [],
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not load thread details.",
            }

    def preview_install_destination(
        self, options: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Recompute the install path without touching the filesystem."""
        from core.archive_install import build_destination
        from core.database import DEFAULT_LIBRARY_FOLDER_PATTERN, get_setting

        payload = dict(options or {})
        folder = get_setting("library_folder") or ""
        pattern = (
            get_setting("library_folder_pattern") or DEFAULT_LIBRARY_FOLDER_PATTERN
        )
        engine = str(payload.get("engine") or "").strip()
        title = str(payload.get("title") or "").strip()
        return {
            "destination": build_destination(
                folder,
                pattern,
                str(payload.get("creator") or ""),
                title,
                str(payload.get("version") or ""),
                engine,
            )
            if folder.strip() and title
            else "",
            "engine_pending": "{engine}" in pattern and not engine,
        }

    def inspect_archive(
        self, path: str, game_id: int | None = None
    ) -> dict[str, object]:
        """Read-only preview for the install-from-archive dialog."""
        from core.archive_install import (
            archive_format,
            build_destination,
            can_remove_old_folder,
            detect_archive_tool,
        )
        from core.database import DEFAULT_LIBRARY_FOLDER_PATTERN, get_setting
        from core.game_scan import guess_title_and_version

        get_setting_fn = cast(Callable[[str], object | None], get_setting)

        archive_path = (path or "").strip()
        if not archive_path or not os.path.isfile(archive_path):
            return {"success": False, "error": "Archive file was not found."}
        if archive_format(archive_path) not in ("zip", "7z", "rar"):
            return {
                "success": False,
                "error": "Unsupported archive format. Choose a .zip, .7z, or .rar file.",
            }

        stem = os.path.splitext(os.path.basename(archive_path))[0]
        guessed_title, guessed_version = guess_title_and_version(stem)

        library_folder = str(get_setting_fn("library_folder") or "")
        _tool, tool_error = detect_archive_tool(
            archive_path, library_folder=os.path.expanduser(library_folder)
        )
        pattern = str(
            get_setting_fn("library_folder_pattern") or DEFAULT_LIBRARY_FOLDER_PATTERN
        )

        title = guessed_title
        version = guessed_version
        creator = ""
        engine = ""
        old_folder = ""
        old_exe = ""
        if game_id is not None:
            from core.database import get_all_games

            for game in get_all_games():
                try:
                    if int(str(game.get("id"))) != int(game_id):
                        continue
                except (TypeError, ValueError):
                    continue
                old_exe = str(game.get("exe_path") or "")
                if not title.strip():
                    title = str(game.get("title") or "")
                latest = str(game.get("latest_version") or "").strip()
                if latest:
                    version = latest
                creator = str(game.get("developer") or "")
                engine = str(game.get("engine") or "")
                break
            if old_exe:
                old_folder = os.path.dirname(os.path.abspath(old_exe))

        destination = ""
        if library_folder.strip() and title.strip():
            destination = build_destination(
                library_folder, pattern, creator, title, version, engine
            )

        guard: dict[str, object] = {"removable": False, "reason": ""}
        if old_folder:
            from core.archive_install import other_game_paths

            downloads_folder = str(get_setting_fn("downloads_folder") or "")
            guard = can_remove_old_folder(
                old_folder,
                destination,
                library_folder,
                downloads_folder,
                other_game_paths(int(game_id) if game_id is not None else None),
                old_exe,
            )

        return {
            "success": True,
            "title": title,
            "version": version,
            "creator": creator,
            "engine": engine,
            "engine_pending": "{engine}" in pattern and not engine.strip(),
            "destination": destination,
            "tool_error": tool_error or "",
            "old_folder": old_folder,
            "removable": guard.get("removable", False),
            "remove_reason": guard.get("reason", ""),
        }

    def get_archive_install_status(self) -> dict[str, object]:
        with self._status_lock:
            return dict(self._archive_install_status)

    def _set_archive_install_status(
        self, *, running: bool, stage: str = "", done: int = 0, total: int = 0
    ) -> None:
        with self._status_lock:
            self._archive_install_status = {
                "running": running,
                "stage": stage,
                "done": done,
                "total": total,
            }

    def install_archive(
        self, options: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Blocking install from an archive; the dialog polls status meanwhile."""
        import sqlite3

        from core.archive_install import ArchiveInstallError
        from core.archive_install import install_archive as run_install
        from core.database import (
            DEFAULT_LIBRARY_FOLDER_PATTERN,
            find_game_by_f95_url,
            get_setting,
        )

        payload = dict(options or {})
        archive_path = str(
            payload.get("archive_path") or payload.get("archive") or ""
        ).strip()
        game_id_raw = payload.get("game_id", payload.get("gameId"))
        game_id: int | None = None
        if game_id_raw not in (None, ""):
            try:
                game_id = int(str(game_id_raw))
            except (TypeError, ValueError):
                return {"success": False, "error": "Invalid game id."}

        if game_id is not None and game_id in self.get_running_games():
            return {
                "success": False,
                "error": "Close the game before installing an update.",
                "error_code": "game_running",
            }

        get_setting_fn = cast(Callable[[str], object | None], get_setting)
        from core.database import find_games_by_links
        from core.store_links import coerce_store_links

        incoming_links = coerce_store_links(payload.get("links"))
        linked_ids = [
            value for value in find_games_by_links(incoming_links) if value != game_id
        ]
        if linked_ids:
            return {
                "success": False,
                "error": "A game with these store links is already in your library.",
                "error_code": "duplicate_url",
                "game_id": linked_ids[0],
            }
        f95_url = normalize_thread_url(
            str(payload.get("f95_url") or payload.get("url") or "")
        )
        if f95_url:
            existing = find_game_by_f95_url(f95_url, exclude_id=game_id)
            if existing is not None:
                return {
                    "success": False,
                    "error": "That thread URL is already in your library.",
                    "error_code": "duplicate_url",
                    "game_id": existing.get("id"),
                }

        title = str(payload.get("title") or "").strip()
        version = str(payload.get("version") or "").strip()
        creator = str(payload.get("creator") or payload.get("developer") or "").strip()
        engine = str(payload.get("engine") or "").strip()
        library_folder = str(
            payload.get("library_folder")
            if "library_folder" in payload
            else (get_setting_fn("library_folder") or "")
        )
        pattern = str(
            payload.get("pattern")
            if "pattern" in payload
            else (
                get_setting_fn("library_folder_pattern")
                or DEFAULT_LIBRARY_FOLDER_PATTERN
            )
        )
        remove_old_raw = payload.get("remove_old", payload.get("removeOld", True))
        remove_old = (
            remove_old_raw is True or str(remove_old_raw).strip().lower() == "true"
        )
        delete_raw = payload.get("delete_archive", payload.get("deleteArchive", False))
        delete_archive = delete_raw is True or str(delete_raw).strip().lower() == "true"

        # Fill title/version guesses when the dialog sent only the archive path.
        if (not title or not version) and archive_path:
            from core.game_scan import guess_title_and_version as _guess

            guessed_title, guessed_version = _guess(
                os.path.splitext(os.path.basename(archive_path))[0]
            )
            title = title or guessed_title
            version = version or guessed_version

        # One install at a time: each install starts by deleting leftover
        # .wlib-staging-* folders, which would include a running install's.
        with self._status_lock:
            if self._archive_install_status["running"]:
                return {
                    "success": False,
                    "error": "Another archive install is running. Wait for it to finish.",
                    "error_code": "install_running",
                }
            self._archive_install_status = {
                "running": True,
                "stage": "starting",
                "done": 0,
                "total": 0,
            }

        def _on_stage(stage: str) -> None:
            self._set_archive_install_status(running=True, stage=stage)

        try:
            result = run_install(
                archive_path,
                library_folder=library_folder,
                pattern=pattern,
                title=title,
                version=version,
                creator=creator,
                engine=engine,
                cover_image=str(payload.get("cover_image") or ""),
                tags=self._normalize_tags_csv(payload.get("tags")),
                rating=str(payload.get("rating") or ""),
                game_id=game_id,
                remove_old=remove_old,
                delete_archive=delete_archive,
                progress_cb=_on_stage,
            )
        except ArchiveInstallError as exc:
            self._set_archive_install_status(running=False, stage="failed")
            return {"success": False, "error": str(exc)}
        except sqlite3.IntegrityError:
            self._set_archive_install_status(running=False, stage="failed")
            return {
                "success": False,
                "error": "A game with this URL is already in your library",
                "error_code": "duplicate_url",
            }
        except Exception as exc:
            self._set_archive_install_status(running=False, stage="failed")
            return {"success": False, "error": str(exc)}

        self._set_archive_install_status(running=False, stage="done")
        installed_id = result.get("game_id")
        if incoming_links and isinstance(installed_id, int):
            from core.database import replace_game_links

            replace_game_links(
                installed_id, "f95_post" if f95_url else "page", incoming_links
            )

        # New installs with a thread URL get the same metadata fetch as add_game.
        if game_id is None and f95_url and isinstance(installed_id, int):
            from core.database import update_game

            try:
                _ = update_game(installed_id, {"f95_url": f95_url})
            except sqlite3.IntegrityError:
                pass
            else:
                with self._metadata_lock:
                    self._metadata_pending.add(installed_id)
                threading.Thread(
                    target=self._fetch_added_game_metadata,
                    args=(installed_id, f95_url, True),
                    daemon=True,
                ).start()
        elif f95_url and game_id is not None:
            from core.database import update_game as update_game_row

            try:
                _ = update_game_row(game_id, {"f95_url": f95_url})
            except sqlite3.IntegrityError:
                pass

        self._refresh_synced_library()
        return {"success": True, **result}

    def find_download_offers(self) -> dict[str, object]:
        from core.archive_install import find_download_offers as scan_offers

        try:
            offers = scan_offers()
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "offers": offers}

    def dismiss_download_offer(self, key: str = "") -> dict[str, object]:
        from core.archive_install import dismiss_download_offer as dismiss_offer

        try:
            dismiss_offer(str(key or ""))
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True}

    # ── Game saves folder ──

    @staticmethod
    def _save_backups_root() -> str:
        from core.host_platform import get_data_subdir

        return get_data_subdir("save-backups")

    @contextmanager
    def _save_operation(self, game_id: int) -> Generator[None]:
        """Allow one backup/import/restore per game at a time."""
        from core.game_saves import GameSavesError

        with self._saves_busy_lock:
            if game_id in self._saves_busy:
                raise GameSavesError(
                    "A save operation is already running for this game"
                )
            self._saves_busy.add(game_id)
        try:
            yield
        finally:
            with self._saves_busy_lock:
                self._saves_busy.discard(game_id)

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
            locations = detect_save_locations(
                str(row.get("exe_path") or ""),
                str(row.get("title") or ""),
                "" if native else str(row.get("custom_prefix") or ""),
                "" if native else str(row.get("proton_version") or ""),
                windows=is_windows(),
                game_id=coerced_id,
                sandbox_enabled=bool(row.get("sandbox_enabled")),
            )
            detected = [dict(location) for location in locations]
            effective = resolve_effective_folder("", locations)
        else:
            effective = override
        return coerced_id, row, effective, detected

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
            with self._save_operation(coerced_id):
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
                self._app_release = result
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

    # ==========================
    # Cheat Engine API
    # ==========================
    def _find_cheat_engine_executable(self, install_dir: str) -> str:
        import os

        candidates = (
            os.path.join(install_dir, "Lunar Engine", "lunarengine-x86_64.exe"),
            os.path.join(install_dir, "lunarengine-x86_64.exe"),
        )
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return ""

    def is_cheat_engine_installed(self) -> dict[str, object]:
        """Checks if Cheat Engine (Lunar Engine) is installed in the wLib data directory."""
        if not is_linux():
            return {"installed": False, "path": "", "supported": False}

        ce_dir = get_cheat_engine_dir()
        executable_path = self._find_cheat_engine_executable(ce_dir)
        if executable_path:
            return {"installed": True, "path": executable_path}
        return {"installed": False, "path": ""}

    def _restore_cheat_engine_backup(self, ce_dir: str, backup_dir: str | None) -> str:
        if not backup_dir:
            return ""

        import os
        import shutil

        if not os.path.exists(backup_dir):
            return ""

        try:
            if os.path.exists(ce_dir):
                shutil.rmtree(ce_dir)
            _ = shutil.move(backup_dir, ce_dir)
        except Exception as e:
            return f"failed to restore previous Cheat Engine install: {e}"
        return ""

    def download_cheat_engine(self) -> dict[str, object]:
        """
        Downloads a safe, portable build of Cheat Engine (Lunar Engine v7.2).
        Lunar Engine is an undetected CE fork that works perfectly in Wine.
        """
        if not is_linux():
            return unsupported_on_host("Cheat Engine injection support")

        import os
        import shutil
        import tempfile
        import urllib.request
        import zipfile

        url = "https://github.com/visibou/lunarengine/releases/download/v.7.2/Lunar.Engine.zip"
        ce_dir = get_cheat_engine_dir()
        parent_dir = os.path.dirname(ce_dir)
        stage_dir: str | None = None
        backup_dir: str | None = None
        replacement_finished = False

        try:
            os.makedirs(parent_dir, exist_ok=True)
            stage_dir = tempfile.mkdtemp(prefix="CheatEngine-download-", dir=parent_dir)
            extract_dir = os.path.join(stage_dir, "install")
            os.makedirs(extract_dir, exist_ok=True)
            zip_path = os.path.join(stage_dir, "ce.zip")

            print(f"Downloading Cheat Engine from {url}...")

            req = urllib.request.Request(url, headers={"User-Agent": "wLib"})
            with (
                cast(
                    URLResponseContext, urllib.request.urlopen(req, timeout=30)
                ) as response,
                open(zip_path, "wb") as out_file,
            ):
                _ = out_file.write(response.read())

            with open(zip_path, "rb") as zip_file:
                if (
                    hashlib.file_digest(zip_file, "sha256").hexdigest()
                    != LUNAR_ENGINE_SHA256
                ):
                    return {
                        "success": False,
                        "error": "Downloaded Cheat Engine archive failed its SHA-256 check.",
                    }

            print(f"Extracting Cheat Engine to staging directory {extract_dir}...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)

            os.remove(zip_path)  # cleanup

            staged_executable = self._find_cheat_engine_executable(extract_dir)
            if not staged_executable:
                return {
                    "success": False,
                    "error": "Extracted successfully but could not find lunarengine-x86_64.exe",
                }

            if os.path.exists(ce_dir):
                backup_dir = tempfile.mkdtemp(
                    prefix="CheatEngine-backup-", dir=parent_dir
                )
                os.rmdir(backup_dir)
                _ = shutil.move(ce_dir, backup_dir)

            _ = shutil.move(extract_dir, ce_dir)

            check = self.is_cheat_engine_installed()
            if check["installed"]:
                print("Cheat Engine successfully installed!")
                cleanup_errors: list[str] = []
                for cleanup_path in (backup_dir, stage_dir):
                    if not cleanup_path or not os.path.exists(cleanup_path):
                        continue
                    try:
                        shutil.rmtree(cleanup_path)
                    except Exception as cleanup_error:
                        cleanup_errors.append(str(cleanup_error))

                replacement_finished = True
                if cleanup_errors:
                    return {
                        "success": False,
                        "path": check["path"],
                        "error": "Cheat Engine installed but cleanup failed: "
                        + "; ".join(cleanup_errors),
                    }
                return {"success": True, "path": check["path"]}

            restore_error = self._restore_cheat_engine_backup(ce_dir, backup_dir)
            backup_dir = None
            error = "Extracted successfully but could not find lunarengine-x86_64.exe"
            if restore_error:
                error = f"{error}; {restore_error}"
            return {
                "success": False,
                "error": error,
            }

        except Exception as e:
            restore_error = self._restore_cheat_engine_backup(ce_dir, backup_dir)
            error = str(e)
            if restore_error:
                error = f"{error}; {restore_error}"
            return {"success": False, "error": error}
        finally:
            if not replacement_finished and stage_dir and os.path.exists(stage_dir):
                try:
                    shutil.rmtree(stage_dir)
                except Exception:
                    pass
