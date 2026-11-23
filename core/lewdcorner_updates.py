# SPDX-License-Identifier: GPL-3.0-or-later
"""Read live LewdCorner updates through the browser bridge."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from core import database, extension_bridge, lewdcorner as lc, lewdcorner_plus
from core.versions import actionable_remote_version, normalize_version

logger = logging.getLogger(__name__)
FEED_READ_AT = "lc_feed_read_at"
_FEED_URL = (
    "https://lewdcorner.com/latest-updates/api?page={}&per_page=90&cat=Games&sort=date"
)


def _fetch(url: str) -> extension_bridge.BridgeResponse:
    try:
        return extension_bridge.fetch(url, site="lc")
    except extension_bridge.BridgeUnavailable as exc:
        raise lc.LcUpdateError("lc_extension_unavailable") from exc
    except extension_bridge.BridgeFailed as exc:
        raise lc.LcUpdateError("lc_extract_failed") from exc


@dataclass(frozen=True)
class FeedRead:
    items: dict[str, lc.LcVersion]
    # The new cursor; empty when the read must not move it.
    cursor: str = ""


def read_feed(cancelled: Callable[[], bool]) -> FeedRead:
    """Read one newest-first pass and find its new cursor.

    The caller saves `cursor` with `save_feed_cursor` only after a check of
    every LC game applied `items`; a cursor saved before that would hide the
    feed items of the games it did not check.
    """
    mark = database.get_setting(FEED_READ_AT) or ""
    newest = mark
    # Without a cursor the first read is only a baseline.
    reached = not mark
    items: dict[str, lc.LcVersion] = {}
    logger.info("Reading LewdCorner update feed through the browser extension")
    for number in range(1, 11):
        if cancelled():
            return FeedRead({})
        response = _fetch(_FEED_URL.format(number))
        page = lc.parse_feed_page(response.body)
        if response.status != 200:
            raise lc.LcUpdateError("lc_extract_failed")
        for thread_id, item in page.items.items():
            _ = items.setdefault(thread_id, item)
        newest = max(newest, page.newest_at)
        # A page reaching behind the cursor already spans all newer items.
        if not page.has_more or (page.oldest_at and page.oldest_at < mark):
            reached = True
            break
    if cancelled():
        return FeedRead({})
    # A read the page limit stopped early keeps the old cursor, so the
    # unread gap is not marked as read.
    return FeedRead(items, newest if reached and newest > mark else "")


def save_feed_cursor(cursor: str) -> None:
    if cursor:
        database.update_setting(FEED_READ_AT, cursor)


def matching_catalog_time(version: str, entry: Mapping[str, object]) -> str:
    if normalize_version(version) == normalize_version(entry.get("version")):
        return lc.timestamp_iso(entry.get("ts"))
    return ""


def store_f95_version(
    game_id: int,
    url: str,
    version: str,
    entry: Mapping[str, object],
    *,
    fallback_states: database.ThreadStates | None = None,
    page_read: bool = False,
) -> tuple[dict[str, object], bool] | None:
    """Store an F95 version and the thread states of its catalog entry.

    F95 prefix data decides; without it, `fallback_states` (the LC read of
    the same game) applies only while the game's states are unknown. Returns the stored game and whether it is newly
    abandoned. With `page_read` the states stay as the page read left them.
    """
    at = matching_catalog_time(version, entry) or database.utc_now_iso()
    # A thread page read is newer than the catalog and already stored its states.
    if page_read:
        states, lc_states = None, None
    else:
        states, lc_states = database.thread_states_from(entry), fallback_states
    stored = database.store_thread_update(
        game_id, "f95", url, (version, at), states=states, lc_states=lc_states
    )
    return (stored[0], stored[2]) if stored is not None else None


@dataclass(frozen=True)
class CheckResult:
    game: dict[str, object] | None = None
    newly_finished: bool = False
    error: lc.LcUpdateError | None = None
    # A failed live read whose catalog fallback still gave a version.
    note: lc.LcUpdateError | None = None
    newly_abandoned: bool = False
    # The On Hold and Abandoned prefixes this read found; None without prefix data.
    states: database.ThreadStates | None = None

    @property
    def version_checked(self) -> bool:
        return self.error is None and bool(
            self.game and self.game.get("lc_latest_version")
        )


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
    flags = (
        lc.prefix_details(feed_item.prefixes)[1]
        if feed_item and feed_item.prefixes
        else None
    )
    completed = bool(flags and flags.completed)
    # The catalog's Completed flag is as old as its version.
    catalog_completed = False
    version = ""
    at = ""
    error: lc.LcUpdateError | None = None
    note: lc.LcUpdateError | None = None
    lc_completed_from_feed = False
    if feed_item and actionable_remote_version(feed_item.version):
        version, at = feed_item.version, feed_item.at or database.utc_now_iso()
        lc_completed_from_feed = bool(feed_item.at)
    elif (single or not catalog_usable and not stored_version) and available:
        try:
            response = _fetch(url)
            lewdcorner_plus.note_thread_page(
                lc.extract_thread_id(url), response.body, response.status
            )
            premium = bool(feed_item and feed_item.premium) or (
                str(catalog.get("tier") or "").casefold() == "premium"
            )
            page = lc.parse_thread_page(response.body, response.status, premium=premium)
            version = page.version
            if page.prefixes:
                page_flags = lc.prefix_details(page.prefixes)[1]
                completed = completed or page_flags.completed
                flags = flags or page_flags
            if not actionable_remote_version(version):
                raise lc.LcUpdateError("lc_extract_failed")
            if single:
                # A Premium/unknown first read is an undated baseline.
                at = matching_catalog_time(version, catalog) if catalog_usable else ""
                if not at and (catalog_usable or stored_version):
                    at = database.utc_now_iso()
        except lc.LcUpdateError as exc:
            if catalog_usable:
                note = exc
                version = str(catalog.get("version") or "").strip()
                at = matching_catalog_time(version, catalog)
                catalog_completed = catalog.get("thread_completed") is True
            else:
                version, at, error = "", "", exc
    elif catalog_usable:
        version = str(catalog.get("version") or "").strip()
        at = matching_catalog_time(version, catalog)
        catalog_completed = catalog.get("thread_completed") is True
    elif not available:
        error = lc.LcUpdateError("lc_extension_unavailable")
    elif feed_error:
        error = feed_error

    states = (
        database.ThreadStates(flags.onhold, flags.abandoned)
        if flags is not None
        else database.thread_states_from(catalog)
    )
    observation = (version, at) if actionable_remote_version(version) else None
    if observation is None and error is None and (single or not stored_version):
        error = feed_error
        if error is None and single:
            error = lc.LcUpdateError("lc_extract_failed")
    # Only live prefix data (feed or page) can say the thread is not Completed;
    # the catalog can lag behind the feed.
    lc_completed = True if completed else (False if flags is not None else None)
    saved = database.store_thread_update(
        game_id,
        "lc",
        url,
        observation,
        completed=completed,
        keep_newer=True,
        states=states,
        lc_completed=lc_completed,
        lc_completed_from_feed=lc_completed_from_feed,
        catalog_completed=catalog_completed,
    )
    if saved is None:
        return CheckResult(error=error or lc.LcUpdateError("lc_extract_failed"))
    current, newly_finished, newly_abandoned = saved
    return CheckResult(current, newly_finished, error, note, newly_abandoned, states)


def store_feed_item(
    game_id: int, url: str, stored_version: object, item: lc.LcVersion
) -> None:
    """Store a feed item of a game the check leaves out, without a request.

    The feed read already paid for the item; an item without a usable
    version is skipped, because reading the thread page would be a request.
    """
    if actionable_remote_version(item.version):
        _ = check_thread(game_id, url, stored_version, {}, feed_item=item)
