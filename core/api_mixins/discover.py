# SPDX-License-Identifier: GPL-3.0-or-later
"""The Discover page: browse the installed catalog and add games from it."""

from __future__ import annotations

import sqlite3
from contextlib import closing, suppress
from typing import cast

from core import catalog_discover, database, lewdcorner
from core.api_mixins.library import LibraryMixin
from core.catalog_discover import LibraryIdentity

_NOT_READY = "The game catalog is still loading"
_CATALOG_OFF = "The game catalog is turned off"
_UNKNOWN_GAME = "This game is no longer in the catalog"
# Store link kinds that a Discover add copies into the library.
_LINK_KINDS = ("dlsite", "vndb", "steam", "ryuugames", "fanza", "itch")


class DiscoverMixin(LibraryMixin):
    def _discover_unavailable(self) -> dict[str, object] | None:
        if not self._catalog_enabled():
            return {
                "success": False,
                "error": _CATALOG_OFF,
                "error_code": "catalog_off",
            }
        if not self.discover_index.ready():
            return {"success": False, "error": _NOT_READY, "error_code": "not_ready"}
        return None

    @staticmethod
    def _library_identities() -> list[LibraryIdentity]:
        try:
            with closing(database.get_connection()) as conn:
                return catalog_discover.library_identities(conn)
        except sqlite3.Error:
            return []

    def get_discover_status(self) -> dict[str, object]:
        enabled = self._catalog_enabled()
        installed = bool(enabled and self.catalog.installed_sha256())
        index = self.discover_index
        return {
            "success": True,
            "enabled": enabled,
            "installed": installed,
            "ready": installed and index.ready(),
            "building": installed and (index.building or not index.ready()),
            "error": index.error,
            "built_at": self.catalog.status().get("built_at") if installed else None,
        }

    def get_discover_filters(self) -> dict[str, object]:
        if problem := self._discover_unavailable():
            return problem
        try:
            return {
                "success": True,
                **catalog_discover.filters(self.discover_index.path),
            }
        except (sqlite3.Error, OSError) as exc:
            return {"success": False, "error": str(exc) or "Could not read the catalog"}

    def search_discover(self, params: object = None) -> dict[str, object]:
        if problem := self._discover_unavailable():
            return problem
        query = catalog_discover.parse_query(params)
        try:
            return {
                "success": True,
                **catalog_discover.search(
                    self.discover_index.path, query, self._library_identities()
                ),
            }
        except (sqlite3.Error, OSError) as exc:
            return {"success": False, "error": str(exc) or "Could not read the catalog"}

    def _discover_card(self, key: object) -> dict[str, object] | None:
        if not isinstance(key, str) or not key or len(key) > 200:
            return None
        return catalog_discover.game_card(
            self.discover_index.path, key, self._library_identities()
        )

    @staticmethod
    def _download_state(card: dict[str, object]) -> dict[str, object]:
        """Whether Download is offered, and why not when it is disabled."""
        if card.get("f95_id") is not None:
            return {"available": True, "reason": ""}
        if card.get("lc_id") is None:
            return {"available": False, "reason": ""}
        if str(card.get("lc_tier") or "").casefold() == "premium" and not (
            database.lc_plus_effective()
        ):
            return {"available": False, "reason": "Needs LC Plus"}
        return {"available": True, "reason": ""}

    def get_discover_game(self, key: object = "") -> dict[str, object]:
        if problem := self._discover_unavailable():
            return problem
        try:
            card = self._discover_card(key)
            if card is None:
                return {
                    "success": False,
                    "error": _UNKNOWN_GAME,
                    "error_code": "unknown_game",
                }
            details = catalog_discover.entry_details(self.catalog.catalog_path, card)
        except (sqlite3.Error, OSError, ValueError) as exc:
            return {"success": False, "error": str(exc) or "Could not read the catalog"}
        return {
            "success": True,
            "game": {**card, **details, "download": self._download_state(card)},
        }

    def get_discover_screenshots(self, key: object = "") -> dict[str, object]:
        from core.game_covers import LC_IMAGE_ADDRESS
        from core.game_lookup import (
            f95_thread_screenshots,
            lc_thread_screenshots,
            screenshot_urls,
        )

        if problem := self._discover_unavailable():
            return problem
        try:
            card = self._discover_card(key)
        except (sqlite3.Error, OSError) as exc:
            return {"success": False, "error": str(exc) or "Could not read the catalog"}
        if card is None:
            return {
                "success": False,
                "error": _UNKNOWN_GAME,
                "error_code": "unknown_game",
            }
        f95_id, lc_id = card.get("f95_id"), card.get("lc_id")
        urls = f95_thread_screenshots(str(f95_id)) if f95_id is not None else None
        lc_allowed = f95_id is None or lewdcorner.setting_enabled(
            database.get_setting("lc_enabled")
        )
        if not urls and lc_id is not None and lc_allowed:
            urls = lc_thread_screenshots(str(lc_id))
        screenshots: list[dict[str, str]] = []
        for url in urls or []:
            if lewdcorner.hosts_url(url):
                if not lewdcorner.fetchable_image_url(url):
                    continue
                # The window never loads LC URLs; the local address fetches the
                # image through the browser extension, for this thread only.
                local = f"{LC_IMAGE_ADDRESS}{self.lc_image_keys.add(url)}"
                screenshots.append({"thumb": local, "full": local})
            else:
                screenshots.append(screenshot_urls(url))
        return {"success": True, "screenshots": screenshots}

    def _discover_add_arguments(self, card: dict[str, object]) -> dict[str, object]:
        """`add_game` arguments for a Discover card, from catalog data only."""
        from core.game_lookup import (
            combined_thread_entry,
            f95_thread_entries,
            lc_thread_entries,
        )
        from core.store_links import link_from_id

        f95_id = str(card["f95_id"]) if card.get("f95_id") is not None else ""
        lc_id = str(card["lc_id"]) if card.get("lc_id") is not None else ""
        f95_url = f"https://f95zone.to/threads/{f95_id}/" if f95_id else ""
        lc_url = f"https://lewdcorner.com/threads/{lc_id}/" if lc_id else ""
        if lc_url and f95_url:
            # The library's rules for an LC partner of an F95 thread: "Use
            # LewdCorner" on, LC Plus for a Premium thread, not yet claimed.
            premium = str(card.get("lc_tier") or "").casefold() == "premium"
            if (
                not lewdcorner.setting_enabled(database.get_setting("lc_enabled"))
                or (premium and not database.lc_plus_effective())
                or database.find_game_by_lc_url(lc_url)
            ):
                lc_url = ""
        f95_entry = f95_thread_entries([f95_id]).get(f95_id, {}) if f95_id else {}
        lc_entry = lc_thread_entries([lc_id]).get(lc_id, {}) if lc_url else {}
        entry = combined_thread_entry(f95_entry, lc_entry)
        links: list[dict[str, str]] = []
        for item in cast(list[dict[str, str]], card.get("entry_ids") or []):
            if item["kind"] in _LINK_KINDS and (
                link := link_from_id(item["kind"], item["value"])
            ):
                links.append(cast(dict[str, str], cast(object, link)))
        arguments: dict[str, object] = {
            "title": str(entry.get("title") or card.get("title") or ""),
            "exe_path": "",
            "f95_url": f95_url,
            "lc_url": lc_url,
            "version": str(entry.get("version") or card.get("version") or ""),
            "cover_image": str(entry.get("coverImage") or card.get("cover_url") or ""),
            "tags": str(entry.get("tags") or ""),
            "developer": str(entry.get("developer") or card.get("developer") or ""),
            "engine": str(entry.get("engine") or card.get("engine") or ""),
            "links": links,
        }
        for key in ("thread_completed", "thread_onhold", "thread_abandoned"):
            if isinstance(entry.get(key), bool):
                arguments[key] = entry[key]
        if isinstance(entry.get("thread_states_seen_at"), str):
            arguments["thread_states_seen_at"] = entry["thread_states_seen_at"]
        return arguments

    def add_discover_game(self, key: object = "") -> dict[str, object]:
        """Add a catalog game as wishlisted; `{game_id, created}`.

        A game that already matches a library game is not added again.
        Sends no F95 request: the fields come from the catalog.
        """
        if problem := self._discover_unavailable():
            return problem
        try:
            card = self._discover_card(key)
        except (sqlite3.Error, OSError) as exc:
            return {"success": False, "error": str(exc) or "Could not read the catalog"}
        if card is None:
            return {
                "success": False,
                "error": _UNKNOWN_GAME,
                "error_code": "unknown_game",
            }
        library = card.get("library")
        if isinstance(library, dict):
            from core.database import get_game

            game_id = cast(int, cast(dict[str, object], library)["game_id"])
            stored = get_game(game_id)
            # A match by store links alone (no thread) gets the F95 thread
            # attached by the add below; any other match is the game itself.
            if (
                card.get("f95_id") is None
                or not stored
                or stored.get("f95_url")
                or stored.get("lc_url")
            ):
                return {"success": True, "game_id": game_id, "created": False}
        try:
            arguments = self._discover_add_arguments(card)
        except (sqlite3.Error, OSError, KeyError) as exc:
            return {"success": False, "error": str(exc) or "Could not read the catalog"}
        result = self.add_game(**arguments, fetch_metadata=False)  # pyright: ignore[reportArgumentType]
        if not result.get("success"):
            return result
        game_id = cast(int, result["id"])
        created = not result.get("attached")
        if created:
            with self._discover_lock:
                self._discover_created.add(game_id)
        self._fill_discover_game(game_id, arguments, card)
        return {"success": True, "game_id": game_id, "created": created}

    def _fill_discover_game(
        self, game_id: int, arguments: dict[str, object], card: dict[str, object]
    ) -> None:
        """Fill the catalog fields an F95 add would get from the thread page."""
        f95_url = str(arguments.get("f95_url") or "")
        rating, count = card.get("rating"), card.get("rating_count")
        with suppress(Exception):  # catalog details are optional
            completed = arguments.get("thread_completed")
            if isinstance(completed, bool) and not arguments.get("lc_url"):
                # The thread page read would set it; add_game sets it only
                # for a game with an LC thread.
                _ = database.update_game(game_id, {"thread_completed": completed})
            if f95_url and isinstance(rating, float) and isinstance(count, int):
                _ = self._store_f95_rating_for_url(
                    f95_url, {"rating": rating, "rating_count": f"{count} reviews"}
                )
            _ = self._fill_missing_f95_from_catalog([game_id])
            _ = self._fill_thread_details_from_catalog([game_id])
        self.cover_cache.wake()

    def discard_discover_game(self, game_id: object = None) -> dict[str, object]:
        """Remove a game Discover added for a download that never started.

        Removes it only when Discover created it in this session, it is still
        wishlisted and it has no download-queue job; otherwise keeps it.
        """
        from core.database import get_game

        game = self._coerce_db_id(game_id)
        if game is None:
            return {"success": False, "error": "Invalid game id"}
        games = self._discover_created
        with self._discover_lock:
            if game not in games:
                return {"success": True, "removed": False}
            stored = get_game(game)
            queue = self._update_downloads.queue
            queued = queue is not None and any(
                job.game_id == game for job in queue.jobs()
            )
            if stored is None or str(stored.get("exe_path") or "").strip() or queued:
                games.discard(game)
                return {"success": True, "removed": False}
            result = self.delete_game(game)
            if not result.get("success"):
                return result
            games.discard(game)
        return {"success": True, "removed": True}
