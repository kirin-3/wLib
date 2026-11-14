# SPDX-License-Identifier: GPL-3.0-or-later
"""Video conversion for the built-in web player (H.264/H.265/AAC to AV1 or VP9 and Opus)."""
from __future__ import annotations

import os
import threading

from core.api_mixins.base import ApiBase
from core.host_platform import is_flatpak

# The job stops when this many files fail before any file converts; the
# ffmpeg build is then likely unable to write Opus in MP4.
EARLY_FAILURE_LIMIT = 3
FAILED_FILES_SHOWN = 20


def _idle_status() -> dict[str, object]:
    return {
        "running": False,
        "finished": False,
        "done": 0,
        "total": 0,
        "current": "",
        "converted": 0,
        "failed": 0,
        "failed_files": [],
        "cancelled": False,
        "error": "",
    }


class WebMediaMixin(ApiBase):
    def _web_media_root(self, game_id: int) -> str:
        """The folder of the last launch check, else the default executable's folder."""
        from core.database import get_game
        from core.web_video import cached_root

        root = cached_root(game_id)
        if root:
            return root
        row = get_game(game_id)
        exe_path = os.path.expanduser(str((row or {}).get("exe_path") or "").strip())
        return os.path.dirname(os.path.abspath(exe_path)) if exe_path else ""

    def start_web_media_conversion(self, game_id: object = None) -> dict[str, object]:
        """Start converting a game's videos in the background; poll get_web_media_conversion_status."""
        from core.database import get_game
        from core.web_video import ffmpeg_info, portal_path_error

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or get_game(coerced_id) is None:
            return {"success": False, "error": "Game was not found"}
        root = self._web_media_root(coerced_id)
        if not root or not os.path.isdir(root):
            return {"success": False, "error": "The game folder was not found."}
        portal_error = portal_path_error(root, is_flatpak())
        if portal_error:
            return {"success": False, "error": portal_error}
        info = ffmpeg_info(refresh=True)
        if not info["available"]:
            return {"success": False, "code": "ffmpeg_unavailable", "error": "Converting videos needs ffmpeg."}
        # _status_lock serializes this with the launcher's reservation.
        with self._status_lock:
            if self._web_media_jobs:
                return {"success": False, "error": "Another game's videos are being converted. Wait for it to finish."}
            if coerced_id in self.get_running_games():
                return {"success": False, "error": "Close the game before converting its videos."}
            busy = self._game_busy_reason(coerced_id)
            if busy:
                return {"success": False, "code": "game_busy", "error": busy}
            status = _idle_status()
            status["running"] = True
            self._web_media_jobs[coerced_id] = status
            _ = self._web_media_results.pop(coerced_id, None)
            self._web_media_cancel.clear()
        thread = threading.Thread(
            target=self._run_web_media_job,
            args=(coerced_id, root),
            name=f"wlib-web-media-{coerced_id}",
            daemon=True,
        )
        thread.start()
        return {"success": True}

    def _web_media_update(self, game_id: int, **fields: object) -> None:
        with self._status_lock:
            status = self._web_media_jobs.get(game_id)
            if status is not None:
                status.update(fields)

    def _run_web_media_job(self, game_id: int, root: str) -> None:
        from core.web_video import (
            ConversionError,
            Converter,
            cleanup_partials,
            ffmpeg_info,
            mark_converted,
            scan_game,
        )

        converted = 0
        failed_files: list[str] = []
        error = ""
        converter = Converter(ffmpeg_info())
        self._set_web_media_converter(converter)
        try:
            _ = cleanup_partials(root)
            needs = scan_game(game_id, root)["needs"]
            self._web_media_update(game_id, total=len(needs))
            real_root = os.path.realpath(root)
            for index, relative in enumerate(needs):
                if self._web_media_cancel.is_set():
                    break
                self._web_media_update(game_id, done=index, current=relative)
                result = converter.convert(os.path.join(real_root, *relative.split("/")))
                if self._web_media_cancel.is_set() and not result["ok"]:
                    # wLib quits or the user cancelled while ffmpeg ran.
                    break
                if result["ok"]:
                    converted += 1
                    mark_converted(game_id, relative, result["size"], result["mtime_ns"])
                else:
                    print(f"[wLib] Could not convert {relative}: {result['error']}")
                    failed_files.append(relative)
                self._web_media_update(
                    game_id,
                    done=index + 1,
                    converted=converted,
                    failed=len(failed_files),
                    failed_files=failed_files[:FAILED_FILES_SHOWN],
                )
                if not converted and len(failed_files) >= EARLY_FAILURE_LIMIT:
                    error = (
                        "ffmpeg could not convert the first videos. Check that ffmpeg is "
                        "version 4.3 or newer, then try again."
                    )
                    break
        except ConversionError as exc:
            error = str(exc)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            print(f"[wLib] Video conversion of game {game_id} failed: {exc}")
        finally:
            self._set_web_media_converter(None)
            with self._status_lock:
                status = self._web_media_jobs.pop(game_id, None) or _idle_status()
                status.update(
                    running=False,
                    finished=True,
                    current="",
                    converted=converted,
                    failed=len(failed_files),
                    failed_files=failed_files[:FAILED_FILES_SHOWN],
                    cancelled=self._web_media_cancel.is_set(),
                    error=error,
                )
                self._web_media_results[game_id] = status

    def cancel_web_media_conversion(self, game_id: object = None) -> dict[str, object]:
        """Stop the job after the current file; converted files stay converted."""
        coerced_id = self._coerce_db_id(game_id)
        with self._status_lock:
            status = self._web_media_jobs.get(coerced_id) if coerced_id is not None else None
            if status is None:
                return {"success": False, "error": "No conversion is running for this game."}
            status["cancelled"] = True
            self._web_media_cancel.set()
        return {"success": True}

    def get_web_media_conversion_status(self, game_id: object = None) -> dict[str, object]:
        coerced_id = self._coerce_db_id(game_id)
        with self._status_lock:
            status = None
            if coerced_id is not None:
                status = self._web_media_jobs.get(coerced_id) or self._web_media_results.get(coerced_id)
            payload = dict(status) if status is not None else _idle_status()
        return {"success": True, **payload}

    def set_web_media_play_anyway(self, game_id: object = None) -> dict[str, object]:
        """Remember "Play anyway" until the set of files that need conversion changes."""
        from core.web_video import cached_root, set_play_anyway

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {"success": False, "error": "Game was not found"}
        root = cached_root(coerced_id)
        if not root:
            return {"success": False, "error": "This game's videos were not checked yet."}
        try:
            _ = set_play_anyway(coerced_id, root)
        except OSError as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True}
