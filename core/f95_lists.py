# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the signed-in user's F95 Watched games and Bookmarked games lists.

Pages come only through the paired browser extension (``extension_bridge.
fetch_list_page``), one request at a time in the shared F95 slot. wLib never
sees F95 credentials or cookies. This module parses the list pages, walks
both lists and builds the import preview; ``apply_import`` writes the
preview to the library in one transaction.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from typing import Literal, cast
from urllib.parse import urlsplit

from core import extension_bridge
from core.f95zone import (
    NON_ENGINE_PREFIXES,
    extract_thread_id,
    normalize_prefix_label,
    split_thread_title,
)
from core.versions import actionable_remote_version

ListName = Literal["watched", "bookmarks"]
LIST_NAMES: tuple[ListName, ...] = ("watched", "bookmarks")
LIST_LABELS: dict[ListName, str] = {
    "watched": "Watched games",
    "bookmarks": "Bookmarks",
}
LIST_TAGS: dict[ListName, str] = {
    "watched": "Watched on F95",
    "bookmarks": "Bookmarked on F95",
}
LIST_TAG_COLORS: dict[str, str] = {
    "Watched on F95": "orange",
    "Bookmarked on F95": "purple",
}
LIST_TAG_COLORS_SEEDED_KEY = "f95_list_tag_colors_seeded"
_LIST_URLS: dict[ListName, str] = {
    "watched": "https://f95zone.to/watched/threads?tab=games&unread=0&sort=title&page={page}",
    "bookmarks": "https://f95zone.to/watched/threads?tab=bookmarks&bm=games&sort=title&page={page}",
}
MAX_PAGES = 200
LIST_TEMPLATE = "f95wg_watched"

ERROR_MESSAGES: dict[str, str] = {
    "no_extension": "No paired wLib browser extension is connected.",
    "extension_outdated": "The wLib browser extension is too old to read F95 lists.",
    "login_required": "The browser is not signed in to F95Zone.",
    "blocked": "F95Zone blocked the request with an anti-bot check.",
    "not_a_list": "F95Zone did not return a list page.",
    "rate_limited": "F95Zone asked wLib to slow down.",
    "timeout": "The browser extension did not answer in time.",
    "fetch_failed": "The browser extension could not read the page.",
    "cancelled": "Reading the F95 lists was cancelled.",
}


class ListReadError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or ERROR_MESSAGES.get(code, code))
        self.code: str = code


@dataclass
class ListRow:
    thread_id: str
    title: str
    prefix_ids: list[int] = field(default_factory=list)
    prefix_names: list[str] = field(default_factory=list)
    list_version: str = ""


@dataclass
class ListEntry:
    thread_id: str
    raw_title: str
    title: str
    version: str
    developer: str
    engine: str
    prefixes: list[str]
    lists: list[ListName]

    @property
    def url(self) -> str:
        return f"https://f95zone.to/threads/{self.thread_id}/"

    def to_json(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "title": self.title,
            "version": self.version,
            "developer": self.developer,
            "engine": self.engine,
            "prefixes": list(self.prefixes),
            "lists": list(self.lists),
        }


@dataclass
class ListReadResult:
    entries: list[ListEntry]
    counts: dict[ListName, int]


# --- small HTML helpers (same regex style as core/scraper.py) -------------


def _attribute(opening: str, name: str) -> str:
    from html import unescape

    match = re.search(
        rf"(?<![\w:-]){re.escape(name)}\s*=\s*([\"'])(.*?)\1", opening, re.S | re.I
    )
    return unescape(match.group(2)) if match else ""


def _text(html: str) -> str:
    from html import unescape

    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _classes(opening: str) -> list[str]:
    return _attribute(opening, "class").split()


def _element_blocks(html: str, class_name: str) -> list[tuple[str, str]]:
    """``(opening tag, inner HTML)`` of each element whose class list has ``class_name``.

    Nested elements with the class are skipped (the outer one wins)."""
    blocks: list[tuple[str, str]] = []
    position = 0
    opening_re = re.compile(r"<([a-zA-Z][\w-]*)\b[^>]*>")
    while True:
        match = opening_re.search(html, position)
        if match is None:
            return blocks
        if class_name not in _classes(match.group()):
            position = match.end()
            continue
        tag = match.group(1)
        if match.group().endswith("/>"):
            blocks.append((match.group(), ""))
            position = match.end()
            continue
        tokens = re.compile(rf"</?{tag}\b[^>]*>", re.I)
        depth = 1
        end = len(html)
        inner_end = len(html)
        for token in tokens.finditer(html, match.end()):
            if token.group().endswith("/>"):
                continue
            depth += -1 if token.group().startswith("</") else 1
            if depth == 0:
                inner_end = token.start()
                end = token.end()
                break
        blocks.append((match.group(), html[match.end() : inner_end]))
        position = end


def _first_block(html: str, class_name: str) -> tuple[str, str] | None:
    blocks = _element_blocks(html, class_name)
    return blocks[0] if blocks else None


def _anchors(html: str) -> list[tuple[str, str]]:
    return [
        (m.group(1), m.group(2))
        for m in re.finditer(r"(<a\b[^>]*>)(.*?)</a>", html, re.S | re.I)
    ]


# --- page parsing ---------------------------------------------------------


def _html_opening(html: str) -> str:
    match = re.search(r"<html\b[^>]*>", html[:8192], re.I)
    return match.group() if match else ""


def page_content(html: str) -> str | None:
    """Inner HTML of ``.p-body-pageContent``; ``None`` when the page has none."""
    block = _first_block(html, "p-body-pageContent")
    return block[1] if block else None


def _row_thread_id(opening: str, inner: str) -> str:
    for name in _classes(opening):
        match = re.fullmatch(r"js-threadListItem-(\d+)", name)
        if match:
            return match.group(1)
    title_block = _first_block(inner, "structItem-title")
    if title_block:
        thread_id = extract_thread_id(_attribute(title_block[0], "uix-href"))
        if thread_id:
            return thread_id
    for anchor, _ in _anchors(inner):
        if _attribute(anchor, "data-tp-primary") == "on":
            thread_id = extract_thread_id(_attribute(anchor, "href"))
            if thread_id:
                return thread_id
    return ""


def _row_title(inner: str) -> str:
    title_block = _first_block(inner, "structItem-title")
    scope = title_block[1] if title_block else inner
    anchors = _anchors(scope)
    for anchor, content in anchors:
        if _attribute(anchor, "data-tp-primary") == "on":
            return _text(content)
    for anchor, content in anchors:
        if "labelLink" not in _classes(anchor) and extract_thread_id(
            _attribute(anchor, "href")
        ):
            return _text(content)
    return ""


def parse_rows(html: str) -> list[ListRow]:
    """Game rows of one list page, read only inside ``.p-body-pageContent``."""
    content = page_content(html)
    if content is None:
        return []
    rows: list[ListRow] = []
    for opening, inner in _element_blocks(content, "structItem"):
        thread_id = _row_thread_id(opening, inner)
        if not thread_id:
            continue
        prefix_ids = [
            int(match.group(1))
            for name in _classes(opening)
            if (match := re.fullmatch(r"is-prefix(\d+)", name))
        ]
        title_block = _first_block(inner, "structItem-title")
        prefix_names: list[str] = []
        for anchor, label in _anchors(title_block[1] if title_block else ""):
            if "labelLink" in _classes(anchor):
                name = _text(label)
                if name:
                    prefix_names.append(name)
        version_block = _first_block(inner, "f95wg-ver")
        rows.append(
            ListRow(
                thread_id=thread_id,
                title=_row_title(inner),
                prefix_ids=prefix_ids,
                prefix_names=prefix_names,
                list_version=_text(version_block[1]) if version_block else "",
            )
        )
    return rows


def read_tab_counts(html: str) -> dict[ListName, int | None]:
    """The Watched games and Bookmarks totals from the tab headers."""
    counts: dict[ListName, int | None] = {"watched": None, "bookmarks": None}
    for opening, inner in _element_blocks(html, "tabs-tab"):
        count_block = _first_block(inner, "f95wg-count")
        if count_block is None:
            continue
        digits = re.sub(r"[^\d]", "", _text(count_block[1]))
        if not digits:
            continue
        href = _attribute(opening, "href")
        label = _text(inner.replace(count_block[1], "")).casefold()
        name: ListName | None = None
        if "tab=games" in href or label.startswith("watched games"):
            name = "watched"
        elif "tab=bookmarks" in href or label.startswith("bookmarks"):
            name = "bookmarks"
        if name is not None:
            counts[name] = int(digits)
    return counts


def classify_page(status: int, final_url: str, html: str) -> str | None:
    """``None`` for a list page, else ``login_required``, ``blocked``,
    ``rate_limited`` or ``not_a_list``."""
    from core import scraper

    path = ""
    try:
        path = urlsplit(final_url).path
    except ValueError:
        pass
    if path.startswith("/login") or scraper.xenforo_signed_in(html) is False:
        return "login_required"
    if status == 429:
        return "rate_limited"
    is_list = (
        _attribute(_html_opening(html), "data-template") == LIST_TEMPLATE
        and page_content(html) is not None
    )
    if not is_list:
        issue = scraper.classify_f95_page_issue(status, html)
        if issue and issue.get("code") in ("blocked", "login_required", "rate_limited"):
            return issue["code"]
        return "not_a_list"
    return None


def _version_from_list(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    return split_thread_title(f"_ [{text}] [_]")[1]


def _first_actionable_version(*versions: str) -> str:
    """The first usable version; else the first nonempty placeholder ("Unknown")."""
    return next(
        (value for value in versions if actionable_remote_version(value)), ""
    ) or next((value for value in versions if value), "")


def entry_from_row(row: ListRow, list_name: ListName) -> ListEntry:
    """Title, version and developer from the row title, engine from the prefix labels."""
    title, version, developer = split_thread_title(row.title)
    engine = next(
        (
            name
            for name in row.prefix_names
            if normalize_prefix_label(name) not in NON_ENGINE_PREFIXES
        ),
        "",
    )
    return ListEntry(
        thread_id=row.thread_id,
        raw_title=row.title,
        title=title or row.title.strip(),
        version=_first_actionable_version(
            version, _version_from_list(row.list_version)
        ),
        developer=developer,
        engine=engine,
        prefixes=list(row.prefix_names),
        lists=[list_name],
    )


# --- the walk -------------------------------------------------------------

type PageFetch = Callable[[str], tuple[int, str, str]]
type Progress = Callable[[Mapping[str, object]], None]


def list_url(list_name: ListName, page: int) -> str:
    return _LIST_URLS[list_name].format(page=page)


def fetch_page_via_extension(url: str) -> tuple[int, str, str]:
    """One list page through the extension, in the shared F95 slot.

    Returns ``(status, final_url, body)``; raises ``ListReadError``."""
    from core import scraper

    reason = extension_bridge.list_fetch_unavailable_reason()
    if reason:
        raise ListReadError(reason)

    def transport() -> extension_bridge.BridgeResponse:
        response = extension_bridge.fetch_list_page(url, timeout=30)
        # Inside the slot, so the next queued request sees the cooldown.
        scraper.note_f95_status(response.status)
        return response

    try:
        response = scraper.run_paced_f95_request(transport)
    except extension_bridge.BridgeUnavailable as exc:
        raise ListReadError(exc.reason) from exc
    except extension_bridge.BridgeFailed as exc:
        text = str(exc)
        raise ListReadError("timeout" if "in time" in text else "fetch_failed") from exc
    except scraper.F95RequestError as exc:
        raise ListReadError(
            exc.code if exc.code in ERROR_MESSAGES else "fetch_failed", str(exc)
        ) from exc
    return response.status, response.final_url, response.body


def read_lists(
    fetch: PageFetch,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Progress | None = None,
) -> ListReadResult:
    """Walk Watched games, then Bookmarks, page by page, sorted by title.

    A list stops at a page with no rows, a page whose rows were all seen in
    this list already, or after ``MAX_PAGES``. Raises ``ListReadError`` on the
    first failed page (nothing is applied) and on cancel."""
    entries: dict[str, ListEntry] = {}
    counts: dict[ListName, int] = {"watched": 0, "bookmarks": 0}
    totals: dict[ListName, int | None] = {"watched": None, "bookmarks": None}

    def report(list_name: ListName, page: int) -> None:
        if progress is None:
            return
        known = [value for value in totals.values() if value is not None]
        progress(
            {
                "list": list_name,
                "list_label": LIST_LABELS[list_name],
                "page": page,
                "found": counts["watched"] + counts["bookmarks"],
                "total": sum(known) if known else None,
            }
        )

    for list_name in LIST_NAMES:
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            if cancelled():
                raise ListReadError("cancelled")
            report(list_name, page)
            status, final_url, body = fetch(list_url(list_name, page))
            if cancelled():
                raise ListReadError("cancelled")
            issue = classify_page(status, final_url, body)
            if issue:
                raise ListReadError(issue)
            if page == 1 and list_name == "watched":
                totals.update(read_tab_counts(body))
            rows = parse_rows(body)
            fresh = [row for row in rows if row.thread_id not in seen]
            if not fresh:
                break
            for row in fresh:
                seen.add(row.thread_id)
                counts[list_name] += 1
                existing = entries.get(row.thread_id)
                if existing is None:
                    entries[row.thread_id] = entry_from_row(row, list_name)
                elif list_name not in existing.lists:
                    existing.lists.append(list_name)
            report(list_name, page)
    return ListReadResult(entries=list(entries.values()), counts=counts)


# --- preview and apply ----------------------------------------------------


def build_preview(result: ListReadResult) -> dict[str, object]:
    """Counts per list and in total, with new and existing games."""
    from core.database import find_matching_game_row, get_connection

    existing_ids: set[str] = set()
    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        for entry in result.entries:
            if find_matching_game_row(cursor, entry.url) is not None:
                existing_ids.add(entry.thread_id)
    per_list: dict[str, dict[str, int]] = {}
    for list_name in LIST_NAMES:
        on_list = [entry for entry in result.entries if list_name in entry.lists]
        existing = sum(1 for entry in on_list if entry.thread_id in existing_ids)
        per_list[list_name] = {
            "found": len(on_list),
            "new": len(on_list) - existing,
            "existing": existing,
        }
    existing_total = sum(
        1 for entry in result.entries if entry.thread_id in existing_ids
    )
    return {
        "lists": per_list,
        "total": len(result.entries),
        "new": len(result.entries) - existing_total,
        "existing": existing_total,
        "entries": [
            {**entry.to_json(), "in_library": entry.thread_id in existing_ids}
            for entry in result.entries
        ],
    }


def _catalog_values(
    entry: ListEntry, catalog: Mapping[str, object]
) -> dict[str, object]:
    def text(key: str) -> str:
        value = catalog.get(key)
        return str(value).strip() if isinstance(value, (str, int, float)) else ""

    completed = catalog.get("thread_completed")
    return {
        "title": text("title") or entry.title,
        "version": _first_actionable_version(text("version"), entry.version),
        "developer": text("developer") or entry.developer,
        "engine": text("engine") or entry.engine,
        "cover_image": text("coverImage"),
        "tags": text("tags"),
        "rating": text("rating"),
        "thread_completed": completed if isinstance(completed, bool) else None,
    }


def apply_import(
    entries: Sequence[ListEntry],
    *,
    catalog_lookup: Callable[[Sequence[str]], Mapping[str, Mapping[str, object]]]
    | None = None,
) -> dict[str, object]:
    """Add new games as wishlisted, tag every game with its lists and seed
    the two tag colors once, all in one transaction (rolled back on error)."""
    from core import database

    if catalog_lookup is None:
        from core.game_lookup import f95_thread_entries

        catalog_lookup = f95_thread_entries
    catalog = catalog_lookup([entry.thread_id for entry in entries]) if entries else {}

    added_ids: list[int] = []
    tagged_ids: list[int] = []
    with closing(database.get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ = conn.execute("BEGIN IMMEDIATE")
        try:
            for entry in entries:
                list_tags = [
                    LIST_TAGS[name] for name in LIST_NAMES if name in entry.lists
                ]
                row = database.find_matching_game_row(cursor, entry.url)
                if row is not None:
                    if database.add_game_tags(
                        cursor, int(cast(int, row["id"])), list_tags
                    ):
                        tagged_ids.append(int(cast(int, row["id"])))
                    continue
                values = _catalog_values(entry, catalog.get(entry.thread_id, {}))
                game_id = database.insert_wishlist_game(
                    cursor,
                    title=str(values["title"]),
                    f95_url=entry.url,
                    version=str(values["version"]),
                    developer=str(values["developer"]),
                    engine=str(values["engine"]),
                    cover_image=str(values["cover_image"]),
                    f95_tags=str(values["tags"]),
                    extra_tags=list_tags,
                    rating=str(values["rating"]),
                    thread_completed=cast(bool | None, values["thread_completed"]),
                )
                added_ids.append(game_id)
            colors_seeded = database.seed_tag_colors_once(
                cursor, LIST_TAG_COLORS_SEEDED_KEY, LIST_TAG_COLORS
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return {
        "added": len(added_ids),
        "tagged": len(tagged_ids),
        "added_ids": added_ids,
        "tagged_ids": tagged_ids,
        "colors_seeded": colors_seeded,
    }


# --- background read with a held preview ----------------------------------

PREVIEW_TTL_SECONDS = 2 * 60 * 60


def _error(code: str, message: str = "") -> dict[str, object]:
    return {
        "success": False,
        "error": message or ERROR_MESSAGES.get(code, code),
        "error_code": code,
    }


class AccountReader:
    """One F95 list read at a time, in a background thread.

    The finished read's preview is held in memory for two hours, or until a
    new read starts, so Import applies exactly what the user saw."""

    def __init__(
        self,
        fetch: PageFetch = fetch_page_via_extension,
        preflight: Callable[
            [], str | None
        ] = extension_bridge.list_fetch_unavailable_reason,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import threading
        import time

        self._fetch: PageFetch = fetch
        self._preflight: Callable[[], str | None] = preflight
        self._clock: Callable[[], float] = clock or time.monotonic
        self._lock: threading.Lock = threading.Lock()
        self._cancel: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        self._read_id: str = ""
        self._state: str = "idle"
        self._progress: dict[str, object] = {}
        self._error: dict[str, object] | None = None
        self._result: ListReadResult | None = None
        self._preview: dict[str, object] | None = None
        self._finished_at: float = 0.0

    def start(self) -> dict[str, object]:
        import threading
        import uuid

        with self._lock:
            if self._state == "running":
                return _error("busy", "wLib is already reading your F95 lists.")
            reason = self._preflight()
            if reason:
                return _error(reason)
            self._read_id = uuid.uuid4().hex
            self._state = "running"
            self._progress = {}
            self._error = None
            self._result = None
            self._preview = None
            self._cancel.clear()
            read_id = self._read_id
            self._thread = threading.Thread(
                target=self._run, args=(read_id,), name="f95-list-read", daemon=True
            )
            self._thread.start()
        return {"success": True, "read_id": read_id}

    def _on_progress(self, read_id: str, item: Mapping[str, object]) -> None:
        with self._lock:
            if self._read_id == read_id:
                self._progress = dict(item)

    def _run(self, read_id: str) -> None:
        import logging

        try:
            result = read_lists(
                self._fetch,
                cancelled=self._cancel.is_set,
                progress=lambda item: self._on_progress(read_id, item),
            )
            preview = build_preview(result)
        except ListReadError as exc:
            self._finish(
                read_id,
                "cancelled" if exc.code == "cancelled" else "error",
                error=_error(exc.code, str(exc)),
            )
            return
        except Exception as exc:
            logging.getLogger("wlib.f95_lists").exception("F95 list read failed")
            self._finish(
                read_id,
                "error",
                error=_error(
                    "fetch_failed", str(exc) or ERROR_MESSAGES["fetch_failed"]
                ),
            )
            return
        self._finish(read_id, "done", result=result, preview=preview)

    def _finish(
        self,
        read_id: str,
        state: str,
        *,
        error: dict[str, object] | None = None,
        result: ListReadResult | None = None,
        preview: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            if self._read_id != read_id:
                return
            self._state = state
            self._error = error
            self._result = result
            self._preview = preview
            self._finished_at = self._clock()

    def _expire_locked(self) -> None:
        if (
            self._state == "done"
            and self._clock() - self._finished_at > PREVIEW_TTL_SECONDS
        ):
            self._state = "expired"
            self._result = None
            self._preview = None

    def status(self) -> dict[str, object]:
        with self._lock:
            self._expire_locked()
            payload: dict[str, object] = {
                "success": True,
                "read_id": self._read_id,
                "state": self._state,
                "progress": dict(self._progress),
            }
            if self._state == "done" and self._preview is not None:
                payload["preview"] = self._preview
            if self._state in ("error", "cancelled") and self._error is not None:
                payload.update(self._error)
            if self._state == "expired":
                payload.update(
                    _error("expired", "The preview expired. Read your F95 lists again.")
                )
            return payload

    def cancel(self) -> dict[str, object]:
        with self._lock:
            if self._state != "running":
                return _error("not_running", "wLib is not reading your F95 lists.")
            self._cancel.set()
        return {"success": True}

    def take_result(self, read_id: str) -> ListReadResult | dict[str, object]:
        """The finished read's entries, once; an error payload otherwise."""
        with self._lock:
            self._expire_locked()
            if self._state == "running":
                return _error("busy", "wLib is still reading your F95 lists.")
            if not read_id or read_id != self._read_id:
                return _error(
                    "not_found",
                    "This preview is no longer available. Read your F95 lists again.",
                )
            if self._state == "expired":
                return _error(
                    "expired", "The preview expired. Read your F95 lists again."
                )
            if self._state != "done" or self._result is None:
                return _error(
                    "not_found",
                    "This preview is no longer available. Read your F95 lists again.",
                )
            if not self._result.entries:
                return _error("empty", "No watched or bookmarked games were found.")
            result = self._result
            self._state = "imported"
            self._result = None
            return result

    def restore_result(self, read_id: str, result: ListReadResult) -> None:
        """Hand back a taken result after a failed apply, so Import can be retried."""
        with self._lock:
            if self._read_id == read_id and self._state == "imported":
                self._state = "done"
                self._result = result

    def finish_import(self, read_id: str) -> None:
        """Drop the preview once its import succeeded."""
        with self._lock:
            if self._read_id == read_id and self._state == "imported":
                self._preview = None

    def join(self, timeout: float | None = None) -> None:
        """Wait for the running read (tests only)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
