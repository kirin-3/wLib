# SPDX-License-Identifier: GPL-3.0-or-later
"""Read live LewdCorner updates through the browser bridge."""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from core import database, extension_bridge, lewdcorner as lc
from core.versions import actionable_remote_version, normalize_version

logger = logging.getLogger(__name__)
FEED_READ_AT = "lc_feed_read_at"
_FEED_URL = "https://lewdcorner.com/latest-updates/api?page={}&per_page=90&cat=Games&sort=date"


def _fetch(url: str) -> extension_bridge.BridgeResponse:
    try:
        return extension_bridge.fetch(url, site="lc")
    except extension_bridge.BridgeUnavailable as exc:
        raise lc.LcUpdateError("lc_extension_unavailable") from exc
    except extension_bridge.BridgeFailed as exc:
        raise lc.LcUpdateError("lc_extract_failed") from exc


def read_feed(cancelled: Callable[[], bool]) -> dict[str, lc.LcVersion]:
    """Read one newest-first pass and keep its cursor on this device."""
    mark = database.get_setting(FEED_READ_AT) or ""
    newest = mark
    items: dict[str, lc.LcVersion] = {}
    logger.info("Reading LewdCorner update feed through the browser extension")
    for number in range(1, 11):
        if cancelled():
            return {}
        response = _fetch(_FEED_URL.format(number))
        page = lc.parse_feed_page(response.body)
        if response.status != 200:
            raise lc.LcUpdateError("lc_extract_failed")
        for thread_id, item in page.items.items():
            _ = items.setdefault(thread_id, item)
        newest = max(newest, page.newest_at)
        # A page reaching behind the cursor already spans all newer items.
        if not page.has_more or (page.oldest_at and page.oldest_at < mark):
            break
    if cancelled():
        return {}
    if newest > mark:
        database.update_setting(FEED_READ_AT, newest)
    return items


def _checked_at() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def matching_catalog_time(version: str, entry: Mapping[str, object]) -> str:
    if normalize_version(version) == normalize_version(entry.get("version")):
        return lc.timestamp_iso(entry.get("ts"))
    return ""


def store_f95_version(
    game_id: int, url: str, version: str, entry: Mapping[str, object]
) -> dict[str, object] | None:
    at = matching_catalog_time(version, entry) or _checked_at()
    stored = database.store_thread_update(game_id, "f95", url, (version, at))
    return stored[0] if stored is not None else None


@dataclass(frozen=True)
class CheckResult:
    game: dict[str, object] | None = None
    newly_finished: bool = False
    error: lc.LcUpdateError | None = None
    # A failed live read whose catalog fallback still gave a version.
    note: lc.LcUpdateError | None = None

    @property
    def version_checked(self) -> bool:
        return self.error is None and bool(self.game and self.game.get("lc_latest_version"))


def check_thread(
    game_id: int,
    url: str,
    stored_version: object,
    catalog: Mapping[str, object],
    *,
    feed_item: lc.LcVersion | None = None,
    feed_error: lc.LcUpdateError | None = None,
    single: bool = False,
) -> CheckResult:
    """Choose the source and persist one LC observation for an active thread."""
    if not url:
        return CheckResult(error=lc.LcUpdateError("lc_extract_failed"))
    available = extension_bridge.is_available(site="lc")
    catalog_usable = lc.usable_catalog_entry(catalog)
    completed = bool(feed_item and lc.prefix_details(feed_item.prefixes)[1])
    version = ""
    at = ""
    error: lc.LcUpdateError | None = None
    note: lc.LcUpdateError | None = None
    if feed_item and actionable_remote_version(feed_item.version):
        version, at = feed_item.version, feed_item.at or _checked_at()
    elif (single or not catalog_usable and not stored_version) and available:
        try:
            response = _fetch(url)
            premium = bool(feed_item and feed_item.premium) or (
                str(catalog.get("tier") or "").casefold() == "premium"
            )
            page = lc.parse_thread_page(response.body, response.status, premium=premium)
            version = page.version
            completed = completed or lc.prefix_details(page.prefixes)[1]
            if not actionable_remote_version(version):
                raise lc.LcUpdateError("lc_extract_failed")
            if single:
                # A Premium/unknown first read is an undated baseline.
                at = matching_catalog_time(version, catalog) if catalog_usable else ""
                if not at and (catalog_usable or stored_version):
                    at = _checked_at()
        except lc.LcUpdateError as exc:
            if catalog_usable:
                note = exc
                version = str(catalog.get("version") or "").strip()
                at = matching_catalog_time(version, catalog)
                completed = completed or catalog.get("thread_completed") is True
            else:
                version, at, error = "", "", exc
    elif catalog_usable:
        version = str(catalog.get("version") or "").strip()
        at = matching_catalog_time(version, catalog)
        completed = completed or catalog.get("thread_completed") is True
    elif not available:
        error = lc.LcUpdateError("lc_extension_unavailable")
    elif feed_error:
        error = feed_error

    observation = (version, at) if actionable_remote_version(version) else None
    if observation is None and error is None and (single or not stored_version):
        error = feed_error
        if error is None and single:
            error = lc.LcUpdateError("lc_extract_failed")
    saved = database.store_thread_update(
        game_id, "lc", url, observation, completed=completed, keep_newer=True
    )
    if saved is None:
        return CheckResult(error=error or lc.LcUpdateError("lc_extract_failed"))
    current, newly_finished = saved
    return CheckResult(current, newly_finished, error, note)
