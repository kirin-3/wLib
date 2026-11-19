# SPDX-License-Identifier: GPL-3.0-or-later
"""Game update checks and the automatic check."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import closing
from typing import cast

from core import database, lewdcorner, lewdcorner_updates
from core.f95zone import extract_thread_id
from core.game_lookup import f95_thread_entries, lc_thread_entries
from core.api_mixins.base import ApiBase, normalize_thread_url


class UpdatesMixin(ApiBase):
    def check_for_updates(self, url: str) -> Mapping[str, object]:
        """Check the linked threads and return the winning version."""
        if not url.strip():
            return {"success": False, "error": "A valid thread URL is required"}

        try:
            lc_url = lewdcorner.normalize_thread_url(url)
            match = (
                database.find_game_by_lc_url(lc_url) if lc_url
                else database.find_game_by_f95_url(url)
            )
            game_id = match.get("id") if match else None
            game = database.get_game(game_id) if isinstance(game_id, int) else None
            if lc_url and game is None:
                return {"success": False, "error": "LewdCorner thread is not in the library"}

            current: Mapping[str, object] = game or {}
            lc_result = lewdcorner_updates.CheckResult()
            lc_enabled = lewdcorner.setting_enabled(database.get_setting("lc_enabled"))
            if isinstance(game_id, int) and lewdcorner.lc_active(current, lc_enabled):
                active_url = lewdcorner.normalize_thread_url(current.get("lc_url"))
                thread_id = lewdcorner.extract_thread_id(active_url)
                catalog = lc_thread_entries([thread_id]).get(thread_id, {})
                lc_result = lewdcorner_updates.check_thread(
                    game_id, active_url, current.get("lc_latest_version"), catalog, single=True,
                )
                current = lc_result.game or current

            f95_url = str(current.get("f95_url") or "") if game else normalize_thread_url(url) or url.strip()
            if not f95_url and not lc_result.game and not lc_result.error:
                return {"success": False, "error": "LewdCorner update checks are off for this game"}

            f95_error: Mapping[str, object] | None = None
            f95_finished = False
            f95_abandoned = False
            metadata_updated = 0
            version = str(current.get("latest_version") or "")
            if f95_url:
                version_info = self._coerce_string_key_dict(
                    self.scraper.get_thread_version(f95_url, include_metadata=True)
                ) or {}
                abandoned_ids: list[int] = []
                finished_ids = self._update_thread_edit_metadata_for_url(
                    f95_url, version_info, abandoned_ids
                )
                f95_abandoned = isinstance(game_id, int) and game_id in abandoned_ids
                f95_finished = isinstance(game_id, int) and game_id in finished_ids and not str(
                    current.get("exe_path") or ""
                ).strip()
                _ = self._store_f95_rating_for_url(f95_url, version_info)
                remote = version_info.get("version")
                if not version_info.get("success"):
                    f95_error = self._build_scraper_error_payload(version_info, "Failed to check for updates")
                elif not self._is_actionable_remote_version(remote):
                    f95_error = {
                        "success": False,
                        "error": "Could not extract a version from thread",
                        "error_code": "extract_failed",
                    }
                else:
                    version = str(remote).strip()
                    if isinstance(game_id, int):
                        f95_id = extract_thread_id(f95_url)
                        catalog = f95_thread_entries([f95_id]).get(f95_id, {})
                        stored = lewdcorner_updates.store_f95_version(
                            game_id, f95_url, version, catalog, fallback_states=lc_result.states,
                            page_read=database.thread_states_from(version_info) is not None,
                        )
                        if stored is not None:
                            current, stored_abandoned = stored
                            f95_abandoned = f95_abandoned or stored_abandoned
                            version = str(current.get("latest_version") or "")
                    else:
                        current = {"latest_site": "f95"}
                    metadata_updated = self._backfill_missing_metadata_for_url(f95_url, version_info)

            if f95_error and not lc_result.version_checked:
                return f95_error
            if not f95_url and lc_result.error:
                return {"success": False, "error": str(lc_result.error), "error_code": lc_result.error.code}

            fields = self._version_check_fields(current, version)
            lc_note = lc_result.error or lc_result.note
            note = str(lc_note) if lc_note else str(f95_error["error"]) if f95_error else ""
            return {
                "success": True,
                "version": version,
                "latest_site": fields["latest_site"],
                "has_update": fields["has_update"] if game else self._has_update("", version),
                "skipped": fields["skipped"],
                "archived": fields["archived"],
                "newly_finished": lc_result.newly_finished or f95_finished,
                "newly_abandoned": lc_result.newly_abandoned or f95_abandoned,
                "metadata_updated": metadata_updated,
                **({"note": note} if note else {}),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_code": "check_failed"}

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
                "refresh_info": getattr(self, "_update_refresh_info", False),
            }

    def take_abandoned_notices(self) -> list[dict[str, object]]:
        """Games a catalog fill found newly abandoned since the last call, once each."""
        with self._abandoned_notices_lock:
            notices = list(self._abandoned_notices)
            self._abandoned_notices.clear()
        return notices

    def cancel_update_check(self) -> dict[str, bool]:
        """Cancel an in-progress bulk update check."""
        with self._update_lock:
            self._update_cancelled: bool = True
        return {"success": True}

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
                if last_check.tzinfo is not None:
                    last_check = last_check.astimezone().replace(tzinfo=None)
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
