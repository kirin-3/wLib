from __future__ import annotations

import os
import ssl
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict, cast

from core.database import init_db
from core.f95zone import normalize_thread_url as _normalize_thread_url
from core.launcher import Launcher
from core.scraper import Scraper

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


class BrowseLocation(TypedDict):
    path: str
    label: str
    source: str


class BrowseLocationsResponse(TypedDict):
    success: bool
    locations: list[BrowseLocation]


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


class GameRecord(TypedDict):
    id: int
    title: str
    version: str
    f95_url: str
    thread_main_post_last_edit_at: NotRequired[str | None]
    thread_main_post_checked_at: NotRequired[str | None]


class ExecutableModifiedTimeResponse(TypedDict):
    success: bool
    modified_at: str | None
    error: NotRequired[str]


class SaveLocation(TypedDict):
    path: str
    type: str
    description: str


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

    def read(self) -> bytes: ...


class URLResponseContext(Protocol):
    def __enter__(self) -> URLResponse: ...

    def __exit__(
        self, exc_type: object, exc_value: object, traceback: object
    ) -> None: ...


normalize_thread_url: Callable[[str], str] = cast(
    Callable[[str], str], cast(object, _normalize_thread_url)
)

APP_VERSION = "0.3.2"
DEFAULT_PLAYWRIGHT_BROWSERS_PATH = os.path.expanduser("~/.cache/ms-playwright")
RTP_DOWNLOADS_PAGE_URL = "https://www.rpgmakerweb.com/run-time-package"
KOMODO_RTP_DOWNLOAD_HOSTS = {"dl.komodo.jp"}
KOMODO_SECTIGO_INTERMEDIATE_CERT_PEM = """-----BEGIN CERTIFICATE-----
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
            "path": os.path.expanduser("~/.local/share/wLib/extension"),
            "bundled_version": "",
            "installed_version": "",
            "reason": "not-run",
        }
        # Make sure our database is initialized
        init_db()
        self.scraper: Scraper = Scraper()
        self.launcher: Launcher = Launcher()
        self._update_running: bool = False
        self._update_total: int = 0
        self._update_checked: int = 0
        self._update_current: str = ""
        self._update_results: list[dict[str, object]] = []
        self._update_delay_seconds: int = self._get_bulk_check_delay_seconds()
        self._update_lock: threading.Lock = threading.Lock()
        self._status_lock: threading.Lock = threading.Lock()
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

    def _load_webview_module(self) -> WebviewModule | None:
        import importlib

        try:
            module = importlib.import_module("webview")
            return cast(WebviewModule, cast(object, module))
        except Exception:
            return None

    def set_window(self, window: Window | None) -> None:
        self.window = window

    def _resolve_runtime_install_target(
        self, prefix_path: str | None = None, proton_path: str | None = None
    ) -> RuntimeInstallTarget:
        from core.database import get_setting

        get_setting_fn = cast(Callable[[str], object | None], get_setting)

        prefix_source = (
            get_setting_fn("wine_prefix_path") if prefix_path is None else prefix_path
        )
        proton_source = (
            get_setting_fn("proton_path") if proton_path is None else proton_path
        )

        base_prefix = str(prefix_source or "").strip()
        if not base_prefix:
            base_prefix = os.path.expanduser("~/.local/share/wLib/prefix")
        base_prefix = os.path.abspath(os.path.expanduser(base_prefix))

        proton_path_to_use = str(proton_source or "").strip()
        is_proton = bool(
            proton_path_to_use
            and "proton" in os.path.basename(proton_path_to_use).lower()
        )

        resolved_prefix = base_prefix
        if is_proton:
            pfx_path = os.path.join(base_prefix, "pfx")
            if os.path.isdir(pfx_path):
                resolved_prefix = pfx_path

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

        os.makedirs(target["base_prefix"], exist_ok=True)
        if target["is_proton"]:
            env["WINEPREFIX"] = target["resolved_prefix"]
            env["STEAM_COMPAT_DATA_PATH"] = target["base_prefix"]
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = "/tmp/wlib"
        else:
            env["WINEPREFIX"] = target["resolved_prefix"]

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
        return os.path.expanduser("~/.local/share/wLib/extension")

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

    def sync_extension_files(self) -> ExtensionSyncStatus:
        import shutil

        bundled_ext_dir = self._get_bundled_extension_dir()
        persistent_ext_dir = self._get_persistent_extension_dir()
        chrome_dir = os.path.join(persistent_ext_dir, "chrome")
        firefox_dir = os.path.join(persistent_ext_dir, "firefox")
        bundled_manifest = os.path.join(bundled_ext_dir, "manifest.json")
        installed_manifest = os.path.join(chrome_dir, "manifest.json")

        try:
            bundled_version = self._read_manifest_version(bundled_manifest)
            installed_version = self._read_manifest_version(installed_manifest)
            needs_copy = False
            reason = "up-to-date"
            if not os.path.isdir(chrome_dir) or not os.path.isfile(
                os.path.join(firefox_dir, "wLib.xpi")
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

            if needs_copy and os.path.isdir(bundled_ext_dir):
                if os.path.exists(persistent_ext_dir):
                    shutil.rmtree(persistent_ext_dir)
                os.makedirs(persistent_ext_dir, exist_ok=True)

                _ = shutil.copytree(bundled_ext_dir, chrome_dir)

                chrome_manifest_path = os.path.join(chrome_dir, "manifest.json")
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

                os.makedirs(firefox_dir, exist_ok=True)
                xpi_path = os.path.join(firefox_dir, "wLib")
                _ = shutil.make_archive(xpi_path, "zip", bundled_ext_dir)
                os.rename(xpi_path + ".zip", xpi_path + ".xpi")
                installed_version = self._read_manifest_version(chrome_manifest_path)

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
            selected_path = self._normalize_selected_path(stdout.splitlines()[0])
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

    def _iter_proc_mounts(self) -> list[tuple[str, str, str]]:
        import re

        def decode_mount_value(value: str) -> str:
            def replace_octal(match: re.Match[str]) -> str:
                return chr(int(match.group(1), 8))

            return re.sub(r"\\([0-7]{3})", replace_octal, value)

        mount_entries: list[tuple[str, str, str]] = []

        try:
            with open("/proc/mounts", "r", encoding="utf-8") as mounts_file:
                for line in mounts_file:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    mount_entries.append(
                        (
                            decode_mount_value(parts[0]),
                            decode_mount_value(parts[1]),
                            decode_mount_value(parts[2]),
                        )
                    )
        except OSError:
            return []

        return mount_entries

    def _is_user_relevant_mount(
        self, source: str, mount_point: str, fs_type: str
    ) -> bool:
        skipped_fs = {
            "autofs",
            "cgroup",
            "cgroup2",
            "configfs",
            "debugfs",
            "devpts",
            "devtmpfs",
            "fusectl",
            "hugetlbfs",
            "mqueue",
            "nsfs",
            "overlay",
            "proc",
            "pstore",
            "ramfs",
            "securityfs",
            "squashfs",
            "sysfs",
            "tmpfs",
            "tracefs",
        }
        if fs_type in skipped_fs:
            return False

        skipped_prefixes = (
            "/app",
            "/dev",
            "/proc",
            "/run/credentials",
            "/run/user",
            "/snap",
            "/sys",
            "/var/lib/docker",
            "/var/lib/flatpak",
        )
        if mount_point in ("/", "/boot", "/boot/efi"):
            return False
        if mount_point.startswith(skipped_prefixes):
            return False

        if mount_point.startswith(("/run/media/", "/media/", "/mnt/")):
            return True

        if source.startswith(("/dev/", "//")) and mount_point.startswith("/"):
            return True

        return False

    def _append_browse_location(
        self,
        locations: list[BrowseLocation],
        seen_paths: set[str],
        path: str,
        label: str,
        source: str,
    ) -> None:
        normalized_path = self._normalize_selected_path(path)
        if not normalized_path or normalized_path in seen_paths:
            return
        if not os.path.isdir(normalized_path):
            return

        seen_paths.add(normalized_path)
        locations.append({"path": normalized_path, "label": label, "source": source})

    def _get_browse_locations(self) -> list[BrowseLocation]:
        import pwd

        locations: list[BrowseLocation] = []
        seen_paths: set[str] = set()
        home_dir = os.path.expanduser("~")
        downloads_dir = os.path.join(home_dir, "Downloads")

        try:
            username = pwd.getpwuid(os.getuid()).pw_name
        except Exception:
            username = os.environ.get("USER", "")

        self._append_browse_location(locations, seen_paths, home_dir, "Home", "home")
        self._append_browse_location(
            locations, seen_paths, downloads_dir, "Downloads", "downloads"
        )
        self._append_browse_location(
            locations,
            seen_paths,
            os.path.join("/run/media", username) if username else "",
            "Removable Media",
            "common-root",
        )
        self._append_browse_location(
            locations,
            seen_paths,
            os.path.join("/media", username) if username else "",
            "Media",
            "common-root",
        )
        self._append_browse_location(
            locations, seen_paths, "/mnt", "Mounted Drives", "common-root"
        )

        for source, mount_point, fs_type in self._iter_proc_mounts():
            if not self._is_user_relevant_mount(source, mount_point, fs_type):
                continue
            label = os.path.basename(mount_point.rstrip(os.sep)) or mount_point
            self._append_browse_location(
                locations, seen_paths, mount_point, label, "mount"
            )

        return locations

    def get_browse_locations(self) -> BrowseLocationsResponse:
        return {"success": True, "locations": self._get_browse_locations()}

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

        try:
            with cast(
                URLResponseContext,
                urllib.request.urlopen(request, timeout=2),
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

        return get_all_games()

    def add_game(
        self,
        title: str,
        exe_path: str,
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
    ) -> dict[str, object]:
        import sqlite3

        from core.database import add_game

        add_game_fn = cast(
            Callable[..., int | None],
            add_game,
        )

        normalized_url = normalize_thread_url(f95_url)

        try:
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
            )
        except sqlite3.IntegrityError:
            return {
                "success": False,
                "error": "A game with this URL is already in your library",
                "error_code": "duplicate_url",
            }

        needs_metadata_backfill = bool(normalized_url) and (
            self._is_missing_text(engine)
            or self._is_missing_text(cover_image)
            or not self._normalize_tags_csv(tags)
        )

        metadata_updated = 0
        if normalized_url:
            metadata_result = self._coerce_string_key_dict(
                cast(
                    object,
                    self.scraper.get_thread_metadata(normalized_url, headless=True),
                )
            )
            if metadata_result is None:
                metadata_result = {}

            if not metadata_result.get("success") and metadata_result.get("code") in (
                "blocked",
                "login_required",
            ):
                metadata_result = self._coerce_string_key_dict(
                    cast(
                        object,
                        self.scraper.get_thread_metadata(
                            normalized_url,
                            headless=False,
                            timeout_ms=180000,
                            hold_open_seconds=self._get_headed_retry_hold_seconds(),
                        ),
                    )
                )
                if metadata_result is None:
                    metadata_result = {}

            if metadata_result.get("success"):
                _ = self._update_thread_edit_metadata_for_url(
                    normalized_url, metadata_result
                )
                if needs_metadata_backfill:
                    metadata_updated = self._backfill_missing_metadata_for_url(
                        normalized_url,
                        metadata_result,
                    )

        return {"id": game_id, "title": title, "metadata_updated": metadata_updated}

    def delete_game(self, game_id: int) -> dict[str, bool]:
        from core.database import delete_game

        delete_game_fn = cast(Callable[[int], None], delete_game)

        delete_game_fn(game_id)
        return {"success": True}

    def update_game(
        self, game_id: int, fields: Mapping[str, object] | None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import update_game

        update_game_fn = cast(Callable[[int, dict[str, object]], None], update_game)

        updated_fields: dict[str, object] = dict(fields or {})
        if "f95_url" in updated_fields:
            updated_fields["f95_url"] = normalize_thread_url(
                str(updated_fields["f95_url"] or "")
            )

        try:
            update_game_fn(game_id, updated_fields)
        except sqlite3.IntegrityError:
            return {
                "success": False,
                "error": "A game with this URL is already in your library",
                "error_code": "duplicate_url",
            }

        return {"success": True}

    # ==========================
    # Scraper API
    # ==========================
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
        elif isinstance(payload, str) and payload.strip():
            message = payload.strip()

        result = {"success": False, "error": message}
        if code:
            result["error_code"] = code

        if code == "blocked":
            result["error"] = (
                f"{message}. Try checking again once the challenge is cleared, "
                "or log in to F95Zone and retry."
            )
        elif code == "login_required":
            result["error"] = (
                f"{message}. Please log in to F95Zone in the scraper browser session and retry."
            )
        return result

    def _get_bulk_check_delay_seconds(self) -> int:
        raw = os.environ.get("WLIB_UPDATE_CHECK_DELAY_SECONDS", "5")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = 5
        return max(2, min(parsed, 30))

    def _get_headed_retry_hold_seconds(self) -> int:
        raw = os.environ.get("WLIB_HEADED_RETRY_HOLD_SECONDS", "20")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = 20
        return max(5, min(parsed, 300))

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
        if not url.strip():
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
                (url.strip(),),
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

                if engine_value and self._is_missing_text(engine_current):
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
    ) -> int:
        normalized_url = normalize_thread_url(url)
        if not normalized_url:
            return 0

        checked_at_value = str(
            metadata.get("thread_main_post_checked_at") or ""
        ).strip()
        if not checked_at_value:
            return 0

        last_edit_raw = str(metadata.get("thread_main_post_last_edit_at") or "").strip()
        last_edit_value: str | None = last_edit_raw or None

        import sqlite3

        from core.database import get_connection

        updated_rows = 0
        with closing(get_connection()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            _ = cursor.execute(
                "SELECT id, thread_main_post_last_edit_at, thread_main_post_checked_at FROM games WHERE f95_url = ?",
                (normalized_url,),
            )
            games = cast(list[sqlite3.Row], cursor.fetchall())

            for game in games:
                game_id = cast(object, game["id"])
                if not isinstance(game_id, int):
                    continue

                current_last_edit = cast(object, game["thread_main_post_last_edit_at"])
                current_checked_at = cast(object, game["thread_main_post_checked_at"])
                if (
                    current_last_edit == last_edit_value
                    and current_checked_at == checked_at_value
                ):
                    continue

                _ = cursor.execute(
                    "UPDATE games SET thread_main_post_last_edit_at = ?, thread_main_post_checked_at = ? WHERE id = ?",
                    (last_edit_value, checked_at_value, game_id),
                )
                updated_rows += 1

            if updated_rows:
                conn.commit()

        return updated_rows

    def check_for_updates(self, url: str) -> Mapping[str, object]:
        """
        Uses Playwright to hit F95Zone and extract the version string from the thread title.
        Stores it in latest_version for comparison with the current version.
        """
        if not url.strip():
            return {"success": False, "error": "A valid thread URL is required"}

        url = url.strip()
        print(f"Checking for updates for {url}")
        try:
            version_info = self._coerce_string_key_dict(
                cast(
                    object,
                    self.scraper.get_thread_version(
                        url,
                        headless=True,
                        include_metadata=True,
                    ),
                )
            )
            if version_info is None:
                version_info = {}

            # If anti-bot/login blocks headless scraping, retry once in headed mode
            # so users can clear the challenge with the persisted session.
            if not version_info.get("success") and version_info.get("code") in (
                "blocked",
                "login_required",
            ):
                version_info = self._coerce_string_key_dict(
                    cast(
                        object,
                        self.scraper.get_thread_version(
                            url,
                            headless=False,
                            timeout_ms=180000,
                            hold_open_seconds=self._get_headed_retry_hold_seconds(),
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

            _ = self._update_thread_edit_metadata_for_url(url, version_info)

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
                    "SELECT version FROM games WHERE f95_url = ?", (url,)
                )
                row = cast(object, cursor.fetchone())
                current_version = ""
                if isinstance(row, Sequence) and len(row) > 0 and row[0] is not None:
                    current_version = str(row[0]).strip()
                conn.commit()
            finally:
                if conn is not None:
                    conn.close()

            has_update = bool(
                current_version
                and remote_version
                and str(current_version).strip() != str(remote_version).strip()
            )
            metadata_updated = self._backfill_missing_metadata_for_url(
                url, version_info
            )
            return {
                "success": True,
                "version": str(remote_version).strip(),
                "has_update": has_update,
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
        normalized_url = str(url or "").strip()
        if not normalized_url:
            return {"success": False, "error": "URL is required"}

        return self._open_with_system_handler(normalized_url, "No browser opener found")

    def open_scraper_login_session(self):
        """Open headed Playwright session for manual F95 login and wait until user closes it."""
        return self.scraper.open_login_session("https://f95zone.to/login/")

    def reset_scraper_session(self):
        """Clear persisted Playwright session/cookies used by scraper."""
        return self.scraper.reset_browser_session()

    def check_all_updates(self) -> dict[str, object]:
        """Start a background thread to check all games for updates."""
        with self._update_lock:
            if getattr(self, "_update_running", False):
                return {"success": False, "error": "Update check already in progress"}

        from core.database import get_all_games

        all_games_raw = get_all_games()
        games_with_url: list[GameRecord] = []
        for game in all_games_raw:
            game_id = game.get("id")
            url = game.get("f95_url")
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
                }
            )

        with self._update_lock:
            self._update_running = True
            self._update_total = len(games_with_url)
            self._update_checked = 0
            self._update_current = ""
            self._update_results = []
            self._update_delay_seconds = self._get_bulk_check_delay_seconds()

        games_by_url = {g["f95_url"]: g for g in games_with_url}
        current_versions_by_url = {g["f95_url"]: g["version"] for g in games_with_url}

        # Record the check timestamp
        from datetime import datetime

        from core.database import get_connection

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            _ = cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('last_update_check', ?)",
                (datetime.now().isoformat(),),
            )
            conn.commit()
        except Exception as e:
            print(f"[wLib] Failed to update last_update_check setting: {e}")
        finally:
            if conn is not None:
                conn.close()

        def run_checks() -> None:
            try:
                urls = [game["f95_url"] for game in games_with_url]

                def check_callback(url: str, result: object) -> bool:
                    game = games_by_url.get(url)
                    if not game:
                        return True

                    with self._update_lock:
                        if not self._update_running:
                            return False  # Stop checking
                        self._update_current = game["title"]

                    result_dict = self._coerce_string_key_dict(result)
                    if result_dict is None:
                        result_dict = {}

                    has_update = False
                    callback_error = str(result_dict.get("error") or "")
                    callback_error_code = str(result_dict.get("code") or "")

                    if result_dict.get(
                        "success"
                    ) and self._is_actionable_remote_version(
                        result_dict.get("version")
                    ):
                        _ = self._update_thread_edit_metadata_for_url(url, result_dict)
                        remote_version = str(result_dict.get("version") or "").strip()
                        from core.database import get_connection

                        import sqlite3

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
                            current_version = current_versions_by_url.get(url, "")
                            conn.commit()
                            has_update = bool(
                                current_version
                                and remote_version
                                and str(current_version).strip()
                                != str(remote_version).strip()
                            )
                        except Exception as e:
                            callback_error = f"Failed to save update data: {e}"
                            callback_error_code = "db_write_failed"
                        finally:
                            if conn is not None:
                                conn.close()
                    elif result_dict.get("success"):
                        callback_error = "Could not extract a version from thread"
                        callback_error_code = "extract_failed"

                    with self._update_lock:
                        self._update_results.append(
                            {
                                "id": game["id"],
                                "title": game["title"],
                                "current_version": game["version"],
                                "latest_version": str(result_dict.get("version") or ""),
                                "has_update": has_update,
                                "error": callback_error,
                                "error_code": callback_error_code,
                            }
                        )
                        self._update_checked += 1
                    return True

                get_multiple_thread_versions_fn = cast(
                    Callable[
                        [list[str], bool, int, bool, Callable[[str, object], bool]],
                        object,
                    ],
                    self.scraper.get_multiple_thread_versions,
                )

                bulk_results = self._coerce_string_key_dict(
                    get_multiple_thread_versions_fn(
                        urls,
                        True,
                        self._update_delay_seconds,
                        True,
                        check_callback,
                    )
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
                        self._update_checked = len(games_with_url)
            finally:
                with self._update_lock:
                    self._update_checked = self._update_total
                    self._update_current = ""
                    self._update_running = False

        thread = threading.Thread(target=run_checks, daemon=True)
        thread.start()
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
                "total": getattr(self, "_update_total", 0),
                "checked": getattr(self, "_update_checked", 0),
                "current": getattr(self, "_update_current", ""),
                "results": list(getattr(self, "_update_results", [])),
                "delay_seconds": getattr(self, "_update_delay_seconds", 5),
            }

    def cancel_update_check(self) -> dict[str, bool]:
        """Cancel an in-progress bulk update check."""
        with self._update_lock:
            self._update_running = False
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

        return {
            "success": True,
            "modified_at": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
        }

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

    def set_auto_check_setting(self, frequency: str) -> dict[str, bool]:
        """Set auto update check frequency: 'weekly', 'monthly', or 'off'."""
        from core.database import get_connection

        get_connection_fn = get_connection

        conn = get_connection_fn()
        cursor = conn.cursor()
        _ = cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_update_check', ?)",
            (frequency,),
        )
        conn.commit()
        conn.close()
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
            result = self.check_all_updates()
            return {"triggered": True, "result": result}

        return {"triggered": False, "reason": f"Last checked: {last_check_str}"}

    # ==========================
    # Launcher API
    # ==========================
    def get_available_runners(self) -> dict[str, object]:
        """Scan ~/.local/share/wLib/proton/ for available proton versions and return them."""
        import os
        import shutil

        runners: list[dict[str, str]] = []

        # Check system wine
        if shutil.which("wine"):
            runners.append({"name": "System Wine", "path": "wine"})

        proton_dir = os.path.expanduser("~/.local/share/wLib/proton")
        if os.path.exists(proton_dir):
            for entry in os.listdir(proton_dir):
                full_path = os.path.join(proton_dir, entry)
                if os.path.isdir(full_path):
                    proton_exec = os.path.join(full_path, "proton")
                    if os.path.exists(proton_exec):
                        runners.append({"name": entry, "path": proton_exec})

        return {"success": True, "runners": runners}

    def launch_game(
        self,
        game_id: int,
        exe_path: str,
        command_line_args: str = "",
        run_japanese_locale: bool = False,
        run_wayland: bool = False,
        auto_inject_ce: bool = False,
        custom_prefix: str = "",
        proton_version: str = "",
    ) -> Mapping[str, object]:
        """
        Uses the Launcher class to execute the game via Proton/Wine.
        """
        print(
            f"Launching {exe_path} (Args: {command_line_args}, JP Locale: {run_japanese_locale}, Wayland: {run_wayland}, Auto Inject CE: {auto_inject_ce}, Custom Prefix: {custom_prefix}, Proton Version: {proton_version})"
        )

        def on_exit(delta: int, is_final: bool = True) -> None:
            try:
                from core.database import update_playtime

                update_playtime_fn = cast(
                    Callable[[int, int], None], cast(object, update_playtime)
                )

                update_playtime_fn(game_id, delta)
                if self.window:
                    safe_game_id = int(game_id)
                    safe_delta = max(int(delta), 0)
                    self.window.evaluate_js(
                        f"window.dispatchEvent(new CustomEvent('wlib-playtime-tick', {{ detail: {{ gameId: {safe_game_id}, delta: {safe_delta}, isFinal: {str(bool(is_final)).lower()} }} }}))"
                    )
            except Exception as e:
                print(f"Failed to update playtime for game {game_id}: {e}")

        result = self.launcher.launch(
            exe_path,
            command_line_args,
            run_japanese_locale,
            run_wayland,
            auto_inject_ce,
            custom_prefix,
            proton_version,
            on_exit_callback=on_exit,
        )
        return result

    def install_rpgmaker_dependencies(
        self, prefix_path: str | None = None, proton_path: str | None = None
    ) -> dict[str, object]:
        """
        Runs winetricks to install the common dependencies needed for RPGMaker/Unity visual novels.
        """
        import shutil
        import subprocess

        winetricks_path = shutil.which("winetricks")
        if not winetricks_path:
            return {
                "success": False,
                "error": "Winetricks is not installed. Install it first, then try again.",
            }

        env, target = self._build_runtime_install_env(prefix_path, proton_path)

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
                        [winetricks_path, "-q", verb],
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
        import shutil
        import subprocess
        import urllib.request
        import zipfile

        env, target = self._build_runtime_install_env(prefix_path, proton_path)
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
            wine_binary = shutil.which("wine")
            if not wine_binary:
                return {
                    "success": False,
                    "error": "Wine is not installed. Install Wine first, then try again.",
                }
            runner_command = [wine_binary]

        # We run this in a background thread so we don't block the UI returning 'success' immediately
        def _download_and_install():
            rtp_dir = os.path.expanduser("~/.local/share/wLib/rtp")
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
                if not os.path.exists(download_path):
                    try:
                        req = urllib.request.Request(
                            str(rtp["url"]), headers={"User-Agent": "Mozilla/5.0"}
                        )
                        with (
                            self._open_url_with_targeted_tls_fallback(
                                req, timeout=30
                            ) as response,
                            open(download_path, "wb") as out_file,
                        ):
                            _ = out_file.write(response.read())
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
                        try:
                            with zipfile.ZipFile(download_path, "r") as zf:
                                zf.extractall(extract_path)
                        except Exception as e:
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
                    failures.append(f"{rtp_name}: installer executable not found")
                    continue

                print(f"Installing {rtp_name} RTP silently in prefix...")
                setup_exe_str = str(setup_exe)
                command = [*runner_command, setup_exe_str, "/S", "/v/qn"]
                try:
                    _ = subprocess.run(
                        command,
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

        with self._status_lock:
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
            "deps": deps_status,
            "rtps": rtp_status,
            "dlls_installed": self._dlls_installed_in_prefix(target["resolved_prefix"]),
            "rtps_installed": self._rtps_installed_in_prefix(target["resolved_prefix"]),
        }

    def get_system_deps_command(self) -> dict[str, object]:
        """Detects the system package manager and returns the command to install 32-bit GStreamer/multimedia dependencies."""
        import shutil

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
            if shutil.which(pkg_mgr):
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
        import json
        import os
        import tarfile
        import urllib.request

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

            download_url = ""
            release_name = str(data.get("tag_name") or "proton-ge")
            assets = data.get("assets")
            for asset_obj in (
                cast(list[object], assets) if isinstance(assets, list) else []
            ):
                asset = self._coerce_string_key_dict(asset_obj)
                if not asset:
                    continue
                asset_name = str(asset.get("name") or "")
                if asset_name.endswith(".tar.gz"):
                    download_url = str(asset.get("browser_download_url") or "")
                    break

            if not download_url:
                return {
                    "success": False,
                    "error": "Could not find a valid release tarball.",
                }

            wlib_share_dir = os.path.expanduser("~/.local/share/wLib/proton")
            os.makedirs(wlib_share_dir, exist_ok=True)

            tar_path = os.path.join("/tmp", f"{release_name}.tar.gz")

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
                _ = out_file.write(response.read())

            print(f"Extracting {tar_path}...")
            with tarfile.open(tar_path, "r:gz") as tar:
                members = tar.getmembers()
                # Find the root extracted folder name (usually GE-Proton-X)
                root_dir = members[0].name.split("/")[0]
                tar.extractall(path=wlib_share_dir)

            os.remove(tar_path)

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

    def get_settings(self) -> dict[str, object]:
        from core.database import get_setting

        get_setting_fn = cast(Callable[[str], object | None], get_setting)

        playwright_path = get_setting_fn("playwright_browsers_path")
        if not isinstance(playwright_path, str) or not playwright_path.strip():
            playwright_path = DEFAULT_PLAYWRIGHT_BROWSERS_PATH

        return {
            "proton_path": str(get_setting_fn("proton_path") or ""),
            "wine_prefix_path": str(get_setting_fn("wine_prefix_path") or ""),
            "enable_logging": get_setting_fn("enable_logging") == "true",
            "playwright_browsers_path": playwright_path,
        }

    def save_settings(self, settings: Mapping[str, object]) -> dict[str, bool]:
        from core.database import update_setting

        update_setting_fn = cast(Callable[[str, str], None], update_setting)

        raw_playwright_path = settings.get(
            "playwright_browsers_path", DEFAULT_PLAYWRIGHT_BROWSERS_PATH
        )
        if isinstance(raw_playwright_path, str):
            playwright_path = raw_playwright_path.strip()
        else:
            playwright_path = str(raw_playwright_path or "").strip()
        if not playwright_path:
            playwright_path = DEFAULT_PLAYWRIGHT_BROWSERS_PATH

        update_setting_fn("proton_path", str(settings.get("proton_path", "") or ""))
        update_setting_fn(
            "wine_prefix_path", str(settings.get("wine_prefix_path", "") or "")
        )
        update_setting_fn(
            "enable_logging", "true" if settings.get("enable_logging") else "false"
        )
        update_setting_fn("playwright_browsers_path", playwright_path)
        return {"success": True}

    def browse_file(self, start_path: str = "") -> str:
        """Opens a native file dialog to select an executable or HTML game."""
        file_types = (
            "Game Files (*.exe;*.sh;*.AppImage;*.html;*.htm)",
            "All files (*.*)",
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
        Returns a list of {path, type, description} for each found location.
        """
        import glob
        import os

        from core.database import get_setting

        get_setting_fn = cast(Callable[[str], object | None], get_setting)

        _ = engine

        results: list[SaveLocation] = []
        game_dir = os.path.dirname(exe_path) if exe_path else ""
        game_name = os.path.splitext(os.path.basename(exe_path))[0] if exe_path else ""
        title_clean = title.strip() if title else game_name

        # ── 1. Check game directory for common save folders/files ──
        if game_dir and os.path.isdir(game_dir):
            save_patterns = ["save", "saves", "Save", "Saves", "SaveData", "savedata"]
            for pattern in save_patterns:
                candidate = os.path.join(game_dir, pattern)
                if os.path.isdir(candidate):
                    results.append(
                        {
                            "path": candidate,
                            "type": "Game Folder",
                            "description": f'Found "{pattern}/" folder next to executable',
                        }
                    )

            # RPGMaker MV/MZ: www/save/
            www_save = os.path.join(game_dir, "www", "save")
            if os.path.isdir(www_save):
                results.append(
                    {
                        "path": www_save,
                        "type": "RPGMaker MV/MZ",
                        "description": "RPGMaker MV/MZ web save folder",
                    }
                )

            # Check for loose save files
            save_extensions = ["*.sav", "*.rpgsave", "*.save", "*.dat"]
            for ext in save_extensions:
                found = glob.glob(os.path.join(game_dir, ext))
                if found:
                    results.append(
                        {
                            "path": game_dir,
                            "type": "Game Folder",
                            "description": f"Found {len(found)} {ext} file(s) in game directory",
                        }
                    )
                    break  # Don't duplicate the game_dir

        # ── 2. Check Ren'Py native save path (~/.renpy/) ──
        renpy_base = os.path.expanduser("~/.renpy")
        if os.path.isdir(renpy_base):
            # Try to match by game name or title
            for entry in os.listdir(renpy_base):
                entry_lower = entry.lower()
                if (
                    title_clean
                    and title_clean.lower().replace(" ", "").replace("'", "")
                    in entry_lower.replace("-", "").replace("_", "")
                ) or (game_name and game_name.lower() in entry_lower):
                    candidate = os.path.join(renpy_base, entry)
                    if os.path.isdir(candidate):
                        results.append(
                            {
                                "path": candidate,
                                "type": "Ren'Py",
                                "description": f"Ren'Py native save folder: ~/.renpy/{entry}",
                            }
                        )

        # ── 3. Check Wine prefix AppData folders ──
        wine_prefix = (
            custom_prefix
            if custom_prefix
            else str(get_setting_fn("wine_prefix_path") or "")
        )
        if not wine_prefix:
            wine_prefix = os.path.expanduser("~/.local/share/wLib/prefix")

        proton_path = (
            proton_version
            if proton_version
            else str(get_setting_fn("proton_path") or "")
        )
        is_proton = bool(
            proton_path and "proton" in os.path.basename(proton_path).lower()
        )

        if is_proton:
            pfx_path = os.path.join(wine_prefix, "pfx")
            actual_prefix = pfx_path if os.path.exists(pfx_path) else wine_prefix
        else:
            actual_prefix = wine_prefix

        # Search through all user folders in the prefix
        users_dir = os.path.join(actual_prefix, "drive_c", "users")
        if os.path.isdir(users_dir):
            appdata_variants = [
                ("AppData/Roaming", "AppData Roaming"),
                ("AppData/Local", "AppData Local"),
                ("AppData/LocalLow", "AppData LocalLow"),
                ("My Documents", "My Documents"),
                ("Documents", "Documents"),
                ("Saved Games", "Saved Games"),
            ]
            for user_folder in os.listdir(users_dir):
                user_path = os.path.join(users_dir, user_folder)
                if not os.path.isdir(user_path) or user_folder in ("Public",):
                    continue

                for sub_path, label in appdata_variants:
                    appdata_dir = os.path.join(user_path, sub_path)
                    if not os.path.isdir(appdata_dir):
                        continue

                    # Search for folders that fuzzy-match the game title or exe name
                    try:
                        for entry in os.listdir(appdata_dir):
                            entry_lower = entry.lower()
                            title_words = (
                                title_clean.lower().split() if title_clean else []
                            )
                            game_name_lower = game_name.lower() if game_name else ""

                            # Match if any significant title word (3+ chars) appears in folder name
                            matched = False
                            if game_name_lower and game_name_lower in entry_lower:
                                matched = True
                            elif title_words:
                                significant_words = [
                                    w for w in title_words if len(w) >= 3
                                ]
                                if significant_words and any(
                                    w in entry_lower for w in significant_words
                                ):
                                    matched = True

                            if matched:
                                candidate = os.path.join(appdata_dir, entry)
                                if os.path.isdir(candidate):
                                    results.append(
                                        {
                                            "path": candidate,
                                            "type": f"Wine Prefix ({label})",
                                            "description": f"{label}/{entry}",
                                        }
                                    )
                    except PermissionError:
                        continue

        # Deduplicate by path
        seen: set[str] = set()
        unique_results: list[SaveLocation] = []
        for r in results:
            if r["path"] not in seen:
                seen.add(r["path"])
                unique_results.append(r)

        return unique_results

    def open_folder(self, path: str) -> OpenPathResult:
        """Opens a folder in the system file manager."""
        if not os.path.exists(path):
            return {"success": False, "error": f"Path does not exist: {path}"}

        return self._open_path_with_system_handler(path)

    def open_dev_tools(self) -> None:
        if self.window:
            self.window.toggle_inspect()

    def check_app_updates(self) -> dict[str, object]:
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
    def is_cheat_engine_installed(self) -> dict[str, object]:
        """Checks if Cheat Engine (Lunar Engine) is installed in the wLib data directory."""
        import os

        ce_dir = os.path.expanduser("~/.local/share/wLib/CheatEngine")
        # We look for the 64-bit Lunar Engine executable inside the extracted folder
        # The zip extracts into a "Lunar Engine" subfolder usually, or directly containing it.
        # Let's search for "lunarengine-x86_64.exe" or "cheatengine-x86_64.exe"
        extracted_folder = os.path.join(ce_dir, "Lunar Engine")
        if os.path.exists(os.path.join(extracted_folder, "lunarengine-x86_64.exe")):
            return {
                "installed": True,
                "path": os.path.join(extracted_folder, "lunarengine-x86_64.exe"),
            }
        if os.path.exists(os.path.join(ce_dir, "lunarengine-x86_64.exe")):
            return {
                "installed": True,
                "path": os.path.join(ce_dir, "lunarengine-x86_64.exe"),
            }
        return {"installed": False, "path": ""}

    def download_cheat_engine(self) -> dict[str, object]:
        """
        Downloads a safe, portable build of Cheat Engine (Lunar Engine v7.2).
        Lunar Engine is an undetected CE fork that works perfectly in Wine.
        """
        import os
        import shutil
        import urllib.request
        import zipfile

        url = "https://github.com/visibou/lunarengine/releases/download/v.7.2/Lunar.Engine.zip"
        ce_dir = os.path.expanduser("~/.local/share/wLib/CheatEngine")
        zip_path = os.path.join(ce_dir, "ce.zip")

        try:
            # Clean up old directory if exists
            if os.path.exists(ce_dir):
                shutil.rmtree(ce_dir)

            os.makedirs(ce_dir, exist_ok=True)

            print(f"Downloading Cheat Engine from {url}...")

            req = urllib.request.Request(url, headers={"User-Agent": "wLib"})
            with (
                cast(
                    URLResponseContext, urllib.request.urlopen(req, timeout=30)
                ) as response,
                open(zip_path, "wb") as out_file,
            ):
                _ = out_file.write(response.read())

            print(f"Extracting Cheat Engine to {ce_dir}...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(ce_dir)

            os.remove(zip_path)  # cleanup

            # Verify install
            check = self.is_cheat_engine_installed()
            if check["installed"]:
                print("Cheat Engine successfully installed!")
                return {"success": True, "path": check["path"]}
            else:
                return {
                    "success": False,
                    "error": "Extracted successfully but could not find lunarengine-x86_64.exe",
                }

        except Exception as e:
            return {"success": False, "error": str(e)}
