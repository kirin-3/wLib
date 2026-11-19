# SPDX-License-Identifier: GPL-3.0-or-later
"""Detect whether the user's LewdCorner account has LC Plus.

A detection reads the page of an LC thread that the catalog marks Premium,
through the browser extension, as a background LC request. A signed-in page
in a Plus forum means Plus; LC's signed-in 403 error page means Standard; any
other page says nothing. The result and its time stay on this device.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict

from core import database, extension_bridge, game_lookup, lewdcorner

logger = logging.getLogger(__name__)

Trigger = Literal["settings", "connect", "check_now"]
DETECT_INTERVAL = timedelta(hours=24)
MAX_CANDIDATES = 3

_single_flight = threading.Lock()
_store_lock = threading.Lock()
_on_turned_on: Callable[[], None] | None = None


class Detection(TypedDict):
    # "plus", "standard", "" (every page said nothing) or None (nothing read).
    result: str | None
    skipped: str


def set_turned_on_callback(callback: Callable[[], None] | None) -> None:
    """Run ``callback`` when a detected tier turns the effective LC Plus state on."""
    global _on_turned_on
    _on_turned_on = callback


def on_lc_fetch_available() -> None:
    """Bridge listener: an extension can now fetch LC pages."""
    start("connect")


def _parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def skip_reason(trigger: Trigger) -> str:
    """Why a detection would not run now, or "" when it can run."""
    if not lewdcorner.setting_enabled(database.get_setting("lc_enabled")):
        return "lc_off"
    if lewdcorner.plus_mode(database.get_setting(lewdcorner.LC_PLUS_MODE)) != "auto":
        return "mode"
    if not game_lookup.catalog_installed():
        return "no_catalog"
    if not extension_bridge.is_available(site="lc"):
        return "no_extension"
    if trigger == "connect":
        last = _parse_time(database.get_setting(lewdcorner.LC_PLUS_DETECTED_AT))
        if last is not None and datetime.now(UTC) - last < DETECT_INTERVAL:
            return "recent"
    return ""


def _store(signal: str | None) -> None:
    """Keep the detected tier (when known) and the time; start the Plus fill when it turns on."""
    with _store_lock:
        before = database.lc_plus_effective()
        values = {lewdcorner.LC_PLUS_DETECTED_AT: database.utc_now_iso()}
        if signal in ("plus", "standard"):
            values[lewdcorner.LC_PLUS_DETECTED] = signal
        database.update_settings(values)
        turned_on = database.lc_plus_effective() and not before
    callback = _on_turned_on
    if turned_on and callback is not None:
        try:
            callback()
        except Exception:
            logger.exception("LC Plus listener failed")


def detect(trigger: Trigger) -> Detection:
    """Run one detection unless another runs or a skip rule applies."""
    if not _single_flight.acquire(blocking=False):
        return {"result": None, "skipped": "running"}
    try:
        reason = skip_reason(trigger)
        if reason:
            return {"result": None, "skipped": reason}
        candidates = game_lookup.premium_lc_threads(MAX_CANDIDATES)
        if not candidates:
            return {"result": None, "skipped": "no_candidates"}
        signal: str | None = None
        # Only a page that came back counts as a check. A timeout or a failed
        # fetch reads nothing, so the time stays as it was and the next
        # connect tries again.
        read = False
        failed = False
        for thread_id in candidates:
            try:
                response = extension_bridge.fetch(
                    f"https://lewdcorner.com/threads/{thread_id}/", site="lc", background=True
                )
            except extension_bridge.BridgeUnavailable:
                logger.info("LC Plus check stopped: no extension can fetch from LewdCorner")
                break
            except extension_bridge.BridgeFailed as exc:
                failed = True
                logger.info("LC Plus check: thread %s failed (%s)", thread_id, exc)
                continue
            read = True
            signal = lewdcorner.tier_signal(response.body, response.status)
            # Never log the page body: it is the user's signed-in page.
            logger.info("LC Plus check (%s): thread %s, HTTP %s, %s",
                        trigger, thread_id, response.status, signal or "unknown")
            if signal:
                break
        if not read:
            return {"result": None, "skipped": "failed" if failed else "no_extension"}
        _store(signal)
        return {"result": signal or "", "skipped": ""}
    finally:
        _single_flight.release()


def start(trigger: Trigger) -> None:
    """Run a detection in a background thread."""
    threading.Thread(target=detect, kwargs={"trigger": trigger}, name="lc-plus-detect", daemon=True).start()


def note_thread_page(thread_id: str, body: str, status: int) -> None:
    """Update the detected tier from an LC thread page wLib already read.

    Plus needs the page to prove its forum. Standard (a 403 page, which does
    not show the forum) counts only for a thread the catalog marks Premium.
    """
    try:
        if lewdcorner.plus_mode(database.get_setting(lewdcorner.LC_PLUS_MODE)) != "auto":
            return
        signal = lewdcorner.tier_signal(body, status)
        if signal == "standard":
            tier = game_lookup.lc_thread_tiers([thread_id]).get(thread_id, "")
            if tier.casefold() != "premium":
                return
        if signal:
            logger.info("LC Plus: thread %s read gives %s", thread_id, signal)
            _store(signal)
    except Exception:
        logger.exception("LC Plus: could not use a thread page")
