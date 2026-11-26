# SPDX-License-Identifier: GPL-3.0-or-later
"""First-run onboarding state and startup catalog hold."""

from __future__ import annotations

import sqlite3

from core.api_mixins.base import ApiBase
from core.database import (
    ONBOARDING_FINISHED_STATES,
    ONBOARDING_STEPS,
    get_setting,
    update_setting,
    update_settings,
)


class OnboardingMixin(ApiBase):
    def get_onboarding_state(self) -> dict[str, object]:
        try:
            raw_status = str(get_setting("onboarding") or "").strip().lower()
            status = raw_status if raw_status in ONBOARDING_FINISHED_STATES else ""
            raw_step = str(get_setting("onboarding_step") or "").strip().lower()
            step = raw_step if raw_step in ONBOARDING_STEPS else ""
            with self._onboarding_lock:
                held = self._catalog_held
            return {
                "success": True,
                "status": status,
                "step": step,
                "catalog_held": held,
            }
        except sqlite3.Error as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not read onboarding state",
            }

    def set_onboarding_step(self, step: object = None) -> dict[str, object]:
        if not isinstance(step, str) or step not in ONBOARDING_STEPS:
            return {"success": False, "error": "Invalid onboarding step"}
        try:
            update_setting("onboarding_step", step)
            return {"success": True, "step": step}
        except sqlite3.Error as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not save onboarding step",
            }

    def finish_onboarding(self, status: object = None) -> dict[str, object]:
        if not isinstance(status, str) or status not in ONBOARDING_FINISHED_STATES:
            return {"success": False, "error": "Invalid onboarding status"}
        try:
            update_settings({"onboarding": status, "onboarding_step": ""})
            _, started = self._release_catalog_hold()
            return {
                "success": True,
                "status": status,
                "catalog_started": started,
            }
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not finish onboarding",
            }

    def release_catalog_hold(self) -> dict[str, object]:
        try:
            released, started = self._release_catalog_hold()
            return {
                "success": True,
                "released": released,
                "started": started,
            }
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not release catalog hold",
            }
