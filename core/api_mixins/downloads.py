# SPDX-License-Identifier: GPL-3.0-or-later
"""File hosts, torrents, RPDL, download offers and archive installs."""
from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Mapping
from typing import cast

from core.api_mixins.base import ApiBase, normalize_thread_url


_NOT_FOUND = "The download was not found"


class DownloadsMixin(ApiBase):
    def browse_archive_file(self, start_path: str = "") -> str:
        """Opens a native file dialog to select a game archive (.zip/.7z/.rar)."""
        file_types = (
            "Game Archives (*.zip;*.7z;*.rar;*.001)",
            "All files (*.*)",
        )

        if sys.platform.startswith("linux"):
            return self._browse_linux_dialog(
                "file", directory=start_path, file_types=file_types
            )

        return self._browse_qt_dialog(
            "file", directory=start_path, file_types=file_types
        )

    def list_library_folders(self) -> list[dict[str, object]]:
        from core.archive_install import library_folder_status
        from core.database import get_library_folders

        folders, default = get_library_folders()
        return [library_folder_status(path, path == default) for path in folders]

    def preview_install_destination(
        self, options: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Recompute the install path without touching the filesystem."""
        from core.archive_install import build_destination
        from core.database import DEFAULT_LIBRARY_FOLDER_PATTERN, get_setting

        payload = dict(options or {})
        folder = str(
            payload.get("library_folder") or get_setting("library_folder") or ""
        )
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

    def get_update_download_links(self, game_id: int) -> dict[str, object]:
        return self._update_downloads.get_links(game_id)

    def close_update_download_links(self, game_id: int) -> dict[str, object]:
        self._update_downloads.clear(game_id)
        return {"success": True}

    def start_update_download(
        self, game_id: int, link_id: str, replaces: object = None
    ) -> dict[str, object]:
        """Add the chosen mirror to the download queue (`replaces`: a failed job's id)."""
        from core.downloads.queue import QueueError
        from core.downloads.transfer import DownloadError

        if game_id in self.get_running_games():
            return {"success": False, "error": "Close the game before downloading an update"}
        replaced = replaces if isinstance(replaces, str) and replaces else None
        try:
            job_id = self._update_downloads.start(game_id, link_id, replaced)
        except (DownloadError, QueueError) as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "job_id": job_id}

    # --- download queue -----------------------------------------------------

    def _queue_job_id(self, job_id: object) -> str | None:
        if not isinstance(job_id, str) or self._download_queue.get(job_id) is None:
            return None
        return job_id

    def get_download_queue(self, include_history: object = False) -> dict[str, object]:
        """Every queued download, the queue summary and the seeding torrents."""
        from core.downloads.torrents import supervisor

        status = self._download_queue.status(include_history is True)
        seeding = [
            job for job in supervisor.status()
            if job.get("stage") == "done" and job.get("seed_state") == "seeding"
        ]
        return {"success": True, **status, "seeding": seeding}

    def reorder_downloads(self, job_ids: object = None) -> dict[str, object]:
        if not isinstance(job_ids, list) or not all(
            isinstance(item, str) for item in cast(list[object], job_ids)
        ):
            return {"success": False, "error": "The order must be a list of downloads"}
        self._download_queue.reorder(cast(list[str], job_ids))
        return {"success": True}

    def start_download_now(self, job_id: object = None) -> dict[str, object]:
        from core.downloads.queue import QueueError

        wanted = self._queue_job_id(job_id)
        if wanted is None:
            return {"success": False, "error": _NOT_FOUND}
        try:
            outcome = self._download_queue.start_now(wanted)
        except QueueError as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "outcome": outcome}

    def pause_download(self, job_id: object = None) -> dict[str, object]:
        wanted = self._queue_job_id(job_id)
        if wanted is None:
            return {"success": False, "error": _NOT_FOUND}
        if not self._download_queue.pause(wanted):
            return {"success": False, "error": "This download cannot be paused"}
        return {"success": True}

    def resume_download(self, job_id: object = None) -> dict[str, object]:
        wanted = self._queue_job_id(job_id)
        if wanted is None:
            return {"success": False, "error": _NOT_FOUND}
        if not self._download_queue.resume(wanted):
            return {"success": False, "error": "This download is not paused"}
        return {"success": True}

    def pause_all_downloads(self) -> dict[str, object]:
        return {"success": True, "count": self._download_queue.pause_all()}

    def resume_all_downloads(self) -> dict[str, object]:
        return {"success": True, "count": self._download_queue.resume_all()}

    def retry_download(self, job_id: object = None) -> dict[str, object]:
        wanted = self._queue_job_id(job_id)
        if wanted is None:
            return {"success": False, "error": _NOT_FOUND}
        if not self._download_queue.retry(wanted):
            return {"success": False, "error": "This download has not failed"}
        return {"success": True}

    def remove_download(self, job_id: object = None) -> dict[str, object]:
        wanted = self._queue_job_id(job_id)
        if wanted is None or not self._download_queue.remove(wanted):
            return {"success": False, "error": _NOT_FOUND}
        return {"success": True}

    def resume_downloads_now(self) -> dict[str, object]:
        """Let downloads run until the current hold (game or battery) ends."""
        if not self._download_queue.resume_now():
            return {"success": False, "error": "Downloads are not waiting"}
        return {"success": True}

    def get_download_accounts(self) -> dict[str, object]:
        from core.downloads import accounts

        return {"success": True, **accounts.status()}

    def connect_pixeldrain_account(self, api_key: object = None) -> dict[str, object]:
        import sqlite3

        from core.downloads import accounts
        from core.downloads.transfer import DownloadError

        if not isinstance(api_key, str):
            return {"success": False, "error": "Enter a valid Pixeldrain API key"}
        try:
            username = accounts.connect_pixeldrain(api_key)
        except DownloadError as exc:
            return {"success": False, "error": str(exc)}
        except (OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc) or "Could not save the API key"}
        return {"success": True, "username": username}

    def disconnect_pixeldrain_account(self) -> dict[str, object]:
        import sqlite3

        from core.downloads import accounts

        try:
            accounts.disconnect_pixeldrain()
        except (OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc) or "Could not remove the API key"}
        return {"success": True}

    def connect_mega_account(
        self, email: object = None, password: object = None, code: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.downloads import accounts
        from core.downloads.transfer import DownloadError

        if not isinstance(email, str) or not isinstance(password, str):
            return {"success": False, "error": "Enter your MEGA email and password"}
        try:
            connected = accounts.connect_mega(
                email, password, code if isinstance(code, str) else ""
            )
        except DownloadError as exc:
            return {
                "success": False,
                "error": str(exc),
                "needs_code": str(exc) == "MEGA requires your two-factor code",
            }
        except (OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc) or "Could not save the MEGA session"}
        return {"success": True, "email": connected}

    def disconnect_mega_account(self) -> dict[str, object]:
        import sqlite3

        from core.downloads import accounts

        try:
            accounts.disconnect_mega()
        except (OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc) or "Could not sign out of MEGA"}
        return {"success": True}

    def connect_rpdl_account(
        self, username: object = None, password: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.downloads import accounts
        from core.downloads.transfer import DownloadError

        if not isinstance(username, str) or not isinstance(password, str):
            return {"success": False, "error": "Enter your RPDL username and password"}
        try:
            name = accounts.connect_rpdl(username, password)
        except DownloadError as exc:
            return {"success": False, "error": str(exc)}
        except (OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc) or "Could not save the RPDL account"}
        return {"success": True, "username": name}

    def disconnect_rpdl_account(self) -> dict[str, object]:
        import sqlite3

        from core.downloads import accounts

        try:
            accounts.disconnect_rpdl()
        except (OSError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc) or "Could not sign out of RPDL"}
        return {"success": True}

    def find_rpdl_torrents(
        self, game_id: object = None, version: object = None, refresh: object = None
    ) -> dict[str, object]:
        """Search RPDL for a game and score the results; works without an account."""
        from core import database
        from core.downloads import accounts, rpdl_match
        from core.downloads.hosts import rpdl
        from core.downloads.transfer import DownloadError

        coerced_id = self._coerce_db_id(game_id)
        game = database.get_game(coerced_id) if coerced_id is not None else None
        if not game:
            return {"success": False, "error": "Game not found"}
        wanted = refresh is True
        try:
            found = rpdl_match.find(
                lambda query: rpdl.search(query, refresh=wanted),
                str(game.get("title") or ""),
                str(game.get("developer") or ""),
                version if isinstance(version, str) else None,
            )
        except rpdl.RpdlError as exc:
            return {"success": False, "error": str(exc), "error_code": exc.kind}
        except DownloadError as exc:
            return {"success": False, "error": str(exc)}
        signed_in = accounts.rpdl_connected()
        return {"success": True, "signed_in": signed_in, **found}

    def start_rpdl_download(
        self,
        game_id: object = None,
        torrent_id: object = None,
        version: object = None,
        replaces: object = None,
    ) -> dict[str, object]:
        """Add an RPDL torrent to the download queue (`replaces`: a failed job's id)."""
        from core import database
        from core.downloads import credentials
        from core.downloads.hosts import rpdl
        from core.downloads.queue import QueueError
        from core.downloads.workers import TorrentContext

        coerced_id = self._coerce_db_id(game_id)
        game = database.get_game(coerced_id) if coerced_id is not None else None
        if coerced_id is None or not game:
            return {"success": False, "error": "Game not found"}
        if isinstance(torrent_id, bool) or not isinstance(torrent_id, int) or torrent_id <= 0:
            return {"success": False, "error": "Choose a torrent first"}
        if coerced_id in self.get_running_games():
            return {"success": False, "error": "Close the game before downloading an update"}
        if not (credentials.get(credentials.RPDL_TOKEN) or credentials.get(credentials.RPDL_USERNAME)):
            return {"success": False, "error": rpdl.SIGN_IN_MESSAGE, "error_code": rpdl.KIND_SIGN_IN}
        wanted_id = torrent_id

        def fetch() -> bytes:
            return self._fetch_checked_rpdl_torrent(wanted_id)

        try:
            job = self._download_queue.add(
                "torrent",
                coerced_id,
                str(game.get("title") or ""),
                version.strip() if isinstance(version, str) else "",
                {"rpdl_id": wanted_id},
                TorrentContext(fetch),
                replaces=replaces if isinstance(replaces, str) and replaces else None,
            )
        except QueueError as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "job_id": job.id}

    def _export_rpdl_torrent(
        self,
        torrent_id: object,
        kind: str,
        export: Callable[[bytes], dict[str, object]],
        folder: str = "",
    ) -> dict[str, object]:
        """Shared sign-in check, checked fetch and error mapping for the RPDL exports.

        Logs only the torrent id and the result kind: never the bytes, magnet or trackers.
        """
        import logging

        from core.downloads import credentials
        from core.downloads.hosts import rpdl
        from core.downloads.transfer import DownloadError

        log = logging.getLogger("wlib.torrent_export")
        if isinstance(torrent_id, bool) or not isinstance(torrent_id, int) or torrent_id <= 0:
            return {"success": False, "error": "Choose a torrent first"}
        if not (credentials.get(credentials.RPDL_TOKEN) or credentials.get(credentials.RPDL_USERNAME)):
            return {"success": False, "error": rpdl.SIGN_IN_MESSAGE, "error_code": rpdl.KIND_SIGN_IN}
        try:
            data = self._fetch_checked_rpdl_torrent(torrent_id)
            result = export(data)
        except rpdl.RpdlError as exc:
            log.info("RPDL torrent %s %s failed: %s", torrent_id, kind, exc.kind)
            return {"success": False, "error": str(exc), "error_code": exc.kind}
        except DownloadError as exc:
            log.info("RPDL torrent %s %s failed", torrent_id, kind)
            return {"success": False, "error": str(exc)}
        except ValueError:
            log.info("RPDL torrent %s %s failed: invalid torrent", torrent_id, kind)
            return {
                "success": False,
                "error": "RPDL sent an invalid response",
                "error_code": rpdl.KIND_INVALID,
            }
        except OSError as exc:
            log.info("RPDL torrent %s %s failed: %s", torrent_id, kind, type(exc).__name__)
            return {
                "success": False,
                "error": f"Could not write the .torrent file to {folder}: {exc.strerror or exc}",
            }
        log.info("RPDL torrent %s %s done", torrent_id, kind)
        return result

    @staticmethod
    def _write_rpdl_torrent(folder: str, torrent_id: int, data: bytes) -> dict[str, object]:
        from core.downloads.bencode import parse_torrent
        from core.downloads.torrent_export import safe_file_name, write_torrent

        name = safe_file_name(parse_torrent(data).name, f"rpdl-{torrent_id}")
        return {"success": True, "path": write_torrent(folder, name, data)}

    def save_rpdl_torrent(self, torrent_id: object = None, folder: object = None) -> dict[str, object]:
        """Write the chosen RPDL torrent's `.torrent` file into a folder the user picked."""
        if not isinstance(folder, str) or not os.path.isabs(folder) or not os.path.isdir(folder):
            return {"success": False, "error": "Choose an existing folder"}
        wanted = cast(int, torrent_id)
        return self._export_rpdl_torrent(
            torrent_id, "save", lambda data: self._write_rpdl_torrent(folder, wanted, data), folder
        )

    def get_rpdl_magnet(self, torrent_id: object = None) -> dict[str, object]:
        """A magnet link for the chosen RPDL torrent. It holds the passkey: never log it."""
        from core.downloads.torrent_export import magnet_link

        return self._export_rpdl_torrent(
            torrent_id, "magnet", lambda data: {"success": True, "magnet": magnet_link(data)}
        )

    def send_rpdl_torrent_to_watch_folder(self, torrent_id: object = None) -> dict[str, object]:
        """Write the `.torrent` file into the watch folder. Never starts an in-app download."""
        from core.downloads import torrent_settings

        folder = torrent_settings.watch_folder()
        if not folder:
            return {"success": False, "error": "Set a watch folder in Settings first"}
        if not os.path.isabs(folder) or not os.path.isdir(folder):
            return {"success": False, "error": f"Watch folder not found: {folder}"}
        if not os.access(folder, os.W_OK | os.X_OK):
            return {"success": False, "error": f"Cannot write to the watch folder: {folder}"}
        wanted = cast(int, torrent_id)
        result = self._export_rpdl_torrent(
            torrent_id, "send", lambda data: self._write_rpdl_torrent(folder, wanted, data), folder
        )
        if result.get("success"):
            result["folder"] = folder
        return result

    def answer_seed_prompt(
        self, job_id: object = None, seed: object = None, remember: object = None
    ) -> dict[str, object]:
        from core.downloads.torrents import supervisor

        if not isinstance(job_id, str) or not isinstance(seed, bool):
            return {"success": False, "error": "Choose Seed or Don't seed"}
        if not supervisor.answer_seed(job_id, seed, remember is True):
            return {"success": False, "error": "That question was already answered"}
        return {"success": True}

    def stop_seeding(self, job_id: object = None) -> dict[str, object]:
        from core.downloads.torrents import supervisor

        if not isinstance(job_id, str) or not supervisor.stop_seeding(job_id):
            return {"success": False, "error": "The torrent is not seeding"}
        return {"success": True}

    def open_update_download_in_browser(self, game_id: int, link_id: str) -> dict[str, object]:
        from core.downloads.transfer import DownloadError

        try:
            url = self._update_downloads.browser_link(game_id, link_id)
        except DownloadError as exc:
            return {"success": False, "error": str(exc)}
        return dict(self.open_in_browser(url))

    def inspect_archive(
        self,
        path: str,
        game_id: int | None = None,
        target_id: int | None = None,
        library_folder: str | None = None,
    ) -> dict[str, object]:
        """Read-only preview for the install-from-archive dialog."""
        from core.archive_install import (
            archive_format,
            archive_stem,
            build_destination,
            can_remove_old_folder,
            detect_archive_tool,
        )
        from core.database import DEFAULT_LIBRARY_FOLDER_PATTERN, get_setting
        from core.game_scan import guess_title_and_version

        get_setting_fn = cast(Callable[[str], object | None], get_setting)

        from core.archive_install import in_torrent_area

        archive_path = os.path.expanduser((path or "").strip())
        folder_source = bool(archive_path) and os.path.isdir(archive_path)
        if folder_source:
            if not in_torrent_area(archive_path):
                return {
                    "success": False,
                    "error": "Only folders downloaded by wLib's torrent downloads can be installed this way.",
                }
            guessed_title, guessed_version = guess_title_and_version(
                os.path.basename(archive_path.rstrip("/\\"))
            )
        else:
            if not archive_path or not os.path.isfile(archive_path):
                return {"success": False, "error": "Archive file was not found."}
            if archive_format(archive_path) not in ("zip", "7z", "rar"):
                return {
                    "success": False,
                    "error": "Unsupported archive format. Choose a .zip, .7z, or .rar file.",
                }
            from core.archive_install import ArchiveInstallError, check_volume_set, volume_set

            volumes = volume_set(archive_path)
            if volumes is not None:
                try:
                    check_volume_set(volumes)
                except ArchiveInstallError as exc:
                    return {"success": False, "error": str(exc), "error_code": "parts_missing"}

            guessed_title, guessed_version = guess_title_and_version(
                archive_stem(archive_path)
            )

        from core.database import get_library_folders, list_game_launch_targets
        from core.game_saves import is_within

        folders, default = get_library_folders()
        requested_folder = library_folder
        library_folder = library_folder or default
        pattern = str(
            get_setting_fn("library_folder_pattern") or DEFAULT_LIBRARY_FOLDER_PATTERN
        )

        title = guessed_title
        version = guessed_version
        creator = ""
        engine = ""
        old_folder = ""
        old_exe = ""
        old_title = old_version = ""
        if game_id is not None:
            from core.database import get_all_games

            for game in get_all_games():
                try:
                    if int(str(game.get("id"))) != int(game_id):
                        continue
                except (TypeError, ValueError):
                    continue
                old_exe = str(game.get("exe_path") or "").strip()
                if old_exe:
                    old_exe = os.path.abspath(os.path.expanduser(old_exe))
                if not title.strip():
                    title = str(game.get("title") or "")
                latest = str(game.get("latest_version") or "").strip()
                if latest:
                    version = latest
                creator = str(game.get("developer") or "")
                engine = str(game.get("engine") or "")
                old_title = str(game.get("title") or "")
                old_version = str(game.get("version") or "")
                break
            if old_exe:
                old_folder = os.path.dirname(os.path.abspath(old_exe))

        targets = list_game_launch_targets(game_id) if game_id is not None else []
        if target_id is not None:
            chosen = next((t for t in targets if t["id"] == target_id), None)
            if chosen is None:
                return {
                    "success": False,
                    "error": "The launch target does not belong to this game.",
                }
            old_exe = os.path.abspath(os.path.expanduser(str(chosen["exe_path"])))
            old_folder = os.path.dirname(old_exe)
        if not requested_folder:
            matches = [
                p
                for p in folders
                if old_folder and is_within(old_folder, os.path.expanduser(p))
            ]
            library_folder = max(
                matches,
                key=lambda p: len(os.path.realpath(os.path.expanduser(p))),
                default=default,
            )
        own_targets: list[dict[str, str]] = []
        if target_id is not None and game_id is not None:
            from core.database import get_game

            row = get_game(game_id)
            own_targets = (
                [
                    {
                        "label": str(row.get("default_target_label") or "").strip()
                        or "Default",
                        "exe_path": str(row.get("exe_path") or ""),
                    }
                ]
                if row
                else []
            )
        own_targets.extend(
            {"label": t["label"], "exe_path": t["exe_path"]}
            for t in targets
            if t["id"] != target_id
        )
        own_inside = [
            t["label"]
            for t in own_targets
            if t["exe_path"]
            and old_folder
            and is_within(os.path.expanduser(str(t["exe_path"])), old_folder)
        ]
        tool_error: str | None = None
        if not folder_source:
            _tool, tool_error = detect_archive_tool(
                archive_path, library_folder=os.path.expanduser(library_folder)
            )
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
                folders,
                downloads_folder,
                other_game_paths(int(game_id) if game_id is not None else None),
                old_exe,
            )

        from pathlib import Path
        from core.downloads.transfer import staging_dir

        downloaded_by_wlib = Path(archive_path).resolve().is_relative_to(staging_dir().resolve())
        from core.downloads.torrents import supervisor as torrent_supervisor

        in_torrents = in_torrent_area(archive_path)
        if downloaded_by_wlib:
            self._update_downloads.manager.mark_open(archive_path)
        if in_torrents:
            torrent_supervisor.mark_open(archive_path)
        outside_library_folders = bool(old_folder) and not any(
            is_within(old_folder, os.path.expanduser(p)) for p in folders
        )
        suggested_library_folder = ""
        if outside_library_folders:
            from core.archive_install import suggest_library_folder

            suggested_library_folder = suggest_library_folder(
                old_exe,
                pattern,
                creator,
                old_title or title,
                old_version,
                engine,
                str(get_setting_fn("downloads_folder") or ""),
            )
        return {
            "success": True,
            "source_kind": "folder" if folder_source else "archive",
            "outside_library_folders": outside_library_folders,
            "suggested_library_folder": suggested_library_folder,
            "torrent_source": in_torrents,
            "torrent_seeding": in_torrents and torrent_supervisor.is_seeding_path(archive_path),
            "downloaded_by_wlib": downloaded_by_wlib,
            "title": title,
            "version": version,
            "creator": creator,
            "engine": engine,
            "engine_pending": "{engine}" in pattern and not engine.strip(),
            "destination": destination,
            "tool_error": tool_error or "",
            "old_folder": old_folder,
            "library_folder": library_folder,
            "own_targets_inside": own_inside,
            "removable": guard.get("removable", False),
            "remove_reason": guard.get("reason", ""),
        }

    def release_download_archive(self, path: str) -> dict[str, object]:
        from core.downloads.torrents import supervisor as torrent_supervisor

        self._update_downloads.manager.release(path)
        torrent_supervisor.release(path)
        return {"success": True}

    def get_archive_install_status(self) -> dict[str, object]:
        with self._status_lock:
            return dict(self._archive_install_status)

    # Stages before the library changes; the install can still be cancelled.
    _CANCELLABLE_INSTALL_STAGES: frozenset[str] = frozenset(
        {"starting", "preparing", "extracting", "copying", "fixing permissions", "carrying saves"}
    )

    def _set_archive_install_status(
        self,
        *,
        running: bool,
        stage: str = "",
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        """Update the polled status; bytes left out keep their last value."""
        with self._status_lock:
            if not running:
                self._archive_install_game_id: int | None = None
            previous = self._archive_install_status
            if running and self._archive_install_cancel.is_set() and stage in self._CANCELLABLE_INSTALL_STAGES:
                stage = "cancelling"
            self._archive_install_status: dict[str, object] = {
                "running": running,
                "stage": stage,
                "done": previous.get("done", 0) if done is None else done,
                "total": previous.get("total", 0) if total is None else total,
                "cancellable": running
                and stage in self._CANCELLABLE_INSTALL_STAGES
                and not self._archive_install_cancel.is_set(),
            }

    def cancel_archive_install(self) -> dict[str, object]:
        """Stop the running install if the new version has not moved yet."""
        with self._status_lock:
            status = self._archive_install_status
            if not status.get("running"):
                return {"success": False, "error": "No install is running.", "error_code": "not_running"}
            if not status.get("cancellable"):
                return {
                    "success": False,
                    "error": "The new version is already being moved into place; it can no longer be cancelled.",
                    "error_code": "not_cancellable",
                }
            self._archive_install_cancel.set()
            self._archive_install_status = {**status, "cancellable": False, "stage": "cancelling"}
        return {"success": True}

    def install_archive(
        self, options: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Blocking install from an archive; the dialog polls status meanwhile."""
        import sqlite3

        from core.archive_install import (
            ArchiveInstallError,
            InstallCancelled,
            NoLauncherChosenError,
            archive_stem,
        )
        from core.archive_install import install_archive as run_install
        from core.database import (
            DEFAULT_LIBRARY_FOLDER_PATTERN,
            find_game_by_f95_url,
            get_setting,
        )

        payload = dict(options or {})
        archive_path = os.path.expanduser(
            str(payload.get("archive_path") or payload.get("archive") or "").strip()
        )
        source_kind = "folder" if str(payload.get("source_kind") or "") == "folder" else "archive"
        from core.archive_install import in_torrent_area
        from core.downloads.torrents import supervisor as torrent_supervisor

        torrent_source = bool(archive_path) and in_torrent_area(archive_path)
        self._update_downloads.manager.release(archive_path)
        if torrent_source:
            # Held for the whole install, so seeding ending now cannot delete the files.
            torrent_supervisor.mark_open(archive_path)
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
        from core.database import get_library_folders

        folders, default = get_library_folders()
        library_folder = str(payload.get("library_folder") or "").strip() or default
        if not library_folder:
            return {"success": False, "error": "Choose a library folder first."}
        if not any(
            os.path.realpath(os.path.expanduser(p))
            == os.path.realpath(os.path.expanduser(library_folder))
            for p in folders
        ):
            return {
                "success": False,
                "error": "Choose a listed library folder. The selected folder is no longer configured.",
            }
        target_id_raw = payload.get("target_id")
        try:
            target_id = int(str(target_id_raw)) if target_id_raw is not None else None
        except ValueError:
            return {"success": False, "error": "Invalid launch target id."}
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

            guessed_title, guessed_version = _guess(archive_stem(archive_path))
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
            if game_id is not None:
                if game_id in self.launcher.get_running_games():
                    return {
                        "success": False,
                        "error": "Close the game before installing an update.",
                        "error_code": "game_running",
                    }
                busy = self._game_busy_reason(game_id)
                if busy:
                    return {"success": False, "error": busy, "error_code": "game_busy"}
            self._archive_install_game_id = game_id
            self._archive_install_cancel.clear()
            self._archive_install_status = {
                "running": True,
                "stage": "starting",
                "done": 0,
                "total": 0,
                "cancellable": True,
            }

        def _on_stage(stage: str) -> None:
            self._set_archive_install_status(running=True, stage=stage)

        def _on_bytes(done: int, total: int) -> None:
            with self._status_lock:
                if self._archive_install_status.get("running"):
                    self._archive_install_status = {
                        **self._archive_install_status,
                        "done": done,
                        "total": total,
                    }

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
                target_id=target_id,
                remove_old=remove_old,
                delete_archive=delete_archive,
                progress_cb=_on_stage,
                bytes_cb=_on_bytes,
                cancel=self._archive_install_cancel,
                source_kind=source_kind,
                exe_relpath=str(payload.get("exe_relpath") or ""),
            )
        except InstallCancelled as exc:
            self._set_archive_install_status(running=False, stage="cancelled")
            return {"success": False, "error": str(exc), "error_code": "install_cancelled"}
        except NoLauncherChosenError as exc:
            self._set_archive_install_status(running=False, stage="failed")
            return {
                "success": False,
                "error": str(exc),
                "error_code": "no_launcher_chosen",
                "launch_candidates": exc.candidates,
            }
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
        if torrent_source:
            torrent_supervisor.release(archive_path, installed=True)
        # An installed download leaves "Ready to install".
        _ = self._download_queue.installed(archive_path)
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
