# SPDX-License-Identifier: GPL-3.0-or-later
"""Browser extension files and pairing."""
from __future__ import annotations

import os
from contextlib import suppress
from typing import cast

from core.host_platform import (
    get_extension_dir,
)
from core.bridge_exposure import python_only
from core.api_mixins.base import ApiBase
from core.api_mixins.types import (
    ExtensionSyncStatus,
    OpenPathResult,
)


class ExtensionMixin(ApiBase):
    def _get_persistent_extension_dir(self) -> str:
        return get_extension_dir()

    @python_only
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
        self._startup_extension_sync_status: ExtensionSyncStatus = payload

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

    @python_only
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

                with open(chrome_manifest_path, encoding="utf-8") as f:
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

    def open_extension_folder(self) -> ExtensionSyncStatus | OpenPathResult:
        sync_result = self.sync_extension_files()
        if sync_result.get("success") is False:
            return sync_result

        persistent_ext_dir = self._get_persistent_extension_dir()
        return self._open_path_with_system_handler(persistent_ext_dir)

    def get_extension_pairing(self) -> dict[str, object]:
        """Browser extensions that asked to connect, and the ones allowed."""
        from core.extension_pairing import store

        return {"success": True, **store().snapshot()}

    def resolve_extension_pairing(
        self, request_id: object, allow: object = False
    ) -> dict[str, object]:
        from core.extension_pairing import store

        if not isinstance(request_id, str) or not store().resolve(
            request_id, allow is True
        ):
            return {"success": False, "error": "The pairing request was not found"}
        return {"success": True}

    def revoke_extension_pairing(self, pairing_id: object) -> dict[str, object]:
        from core import extension_bridge
        from core.extension_pairing import store

        if not isinstance(pairing_id, str) or not store().revoke(pairing_id):
            return {"success": False, "error": "The browser extension was not found"}
        extension_bridge.close_paired_connections(pairing_id)
        return {"success": True}
