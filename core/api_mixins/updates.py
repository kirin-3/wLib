# SPDX-License-Identifier: GPL-3.0-or-later
"""Game update checks and the automatic check."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import closing
from typing import cast

from core.versions import normalize_version
from core.api_mixins.base import ApiBase, normalize_thread_url


class UpdatesMixin(ApiBase):
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

            # A thread page can be read successfully even when no version is
            # extractable; its Completed flag, screenshots, links and edit
            # metadata are still valid and must be stored either way.
            newly_finished_ids = self._update_thread_edit_metadata_for_url(
                url, version_info
            )

            if not version_info.get("success"):
                return self._build_scraper_error_payload(
                    version_info, "Failed to check for updates"
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
                "refresh_covers": getattr(self, "_update_refresh_covers", False),
            }

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
