# SPDX-License-Identifier: GPL-3.0-or-later
"""UMU, Proton, Wine prefix tools, system dependencies and Cheat Engine."""
from __future__ import annotations

import hashlib
import subprocess
from contextlib import suppress
from typing import cast

from core.host_platform import (
    get_cheat_engine_dir,
    get_proton_dir,
    host_command,
    host_which,
    is_linux,
    unsupported_on_host,
)
from core.api_mixins.base import ApiBase
from core.api_mixins.types import (
    URLResponseContext,
)

# Lunar.Engine.zip from visibou/lunarengine v.7.2 (uploaded 2020-11-29, 29738666 bytes).
LUNAR_ENGINE_SHA256 = "c208f52ca1873007df3f7ef71b61f3c2870a58ad0ddadff7944785f6bf36de23"


class RuntimesMixin(ApiBase):
    def open_wine_prefix_tool(
        self,
        tool: str,
        prefix_path: str = "",
        proton_path: str = "",
        game_id: object = None,
    ) -> dict[str, object]:
        if not is_linux():
            return unsupported_on_host("Wine prefix tools")
        if tool not in ("winecfg", "winetricks"):
            return {"success": False, "error": "Unknown Wine prefix tool."}
        try:
            env, target = self._build_runtime_install_env(
                prefix_path, proton_path, game_id
            )
            if tool == "winetricks":
                if not target["is_proton"] and not host_which("wine"):
                    raise ValueError("Wine is not installed. Install Wine first, then try again.")
                if not (host_which("zenity") or host_which("kdialog")):
                    raise ValueError("Winetricks requires zenity or kdialog for its interface.")
                cmd, env = self._winetricks_command(target, env)
                cmd.append("--gui")
            elif target["is_proton"]:
                from core.umu import build_umu_invocation
                cmd, env = build_umu_invocation("winecfg", prefix=target["base_prefix"], runner=target["proton_path"], environment=env)
            else:
                wine = host_which("wine")
                if not wine:
                    raise ValueError("Wine is not installed. Install Wine first, then try again.")
                cmd = [wine, "winecfg"]
            from core.game_sandbox import normalize_prefix
            prefix = normalize_prefix(env["WINEPREFIX"])
            with self._status_lock:
                if self._deps_install_status["running"] or self._rtp_install_status["running"]:
                    return {"success": False, "error": "Another install is running. Wait for it to finish."}
                if self._prefix_tool_running():
                    return {"success": False, "error": "Close the open Wine prefix tool first."}
                title = self.launcher.game_using_prefix(prefix)
                if title:
                    return {"success": False, "error": f"{title} is already running on this Wine prefix."}
                self._prefix_tool: subprocess.Popen[bytes] | None = subprocess.Popen(host_command(cmd, env=env, supervise=True), env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._prefix_tool_prefix: str = prefix
                self._prefix_tool_name: str = "Winecfg" if tool == "winecfg" else "Winetricks"
            return {"success": True}
        except (OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

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

    def get_umu_status(self) -> dict[str, object]:
        if not is_linux():
            return unsupported_on_host("umu-launcher")
        from core.umu import get_umu_status

        return {"success": True, **get_umu_status()}

    def download_umu(self) -> dict[str, object]:
        if not is_linux():
            return unsupported_on_host("umu-launcher")
        return self._umu_download.start()

    def get_umu_download_status(self) -> dict[str, object]:
        if not is_linux():
            return unsupported_on_host("umu-launcher")
        return {"success": True, **self._umu_download.status()}

    def prepare_umu_runtime(self) -> dict[str, object]:
        if not is_linux():
            return unsupported_on_host("umu-launcher")
        target = self._resolve_runtime_install_target()
        return self._umu_prepare.start(target["base_prefix"], target["proton_path"])

    def get_umu_runtime_status(self) -> dict[str, object]:
        if not is_linux():
            return unsupported_on_host("umu-launcher")
        from core.umu import runtime_status

        target = self._resolve_runtime_install_target()
        preparation = self._umu_prepare.status()
        runtime = runtime_status(target["proton_path"])
        if preparation["cancelled"] or preparation["error"]:
            runtime["ready"] = False
        return {"success": True, **runtime, **preparation}

    def cancel_umu_runtime_prepare(self) -> dict[str, object]:
        if not is_linux():
            return unsupported_on_host("umu-launcher")
        return self._umu_prepare.cancel()

    def download_proton_ge(self) -> dict[str, object]:
        """
        Downloads the latest Proton-GE release and extracts it to the local share directory.
        """
        if not is_linux():
            return unsupported_on_host("Proton-GE installation")
        with self._status_lock:
            if self._proton_download_status["running"]:
                return {"success": False, "error": "GE-Proton is already downloading."}
            self._proton_download_status: dict[str, object] = {
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
            tar_fd, tar_path = tempfile.mkstemp(prefix="wlib-proton-", suffix=".tar.gz")
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
