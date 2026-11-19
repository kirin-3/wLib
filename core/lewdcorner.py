# SPDX-License-Identifier: GPL-3.0-or-later
"""LewdCorner thread identity, activation, and update parsing."""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal, NamedTuple, cast, final, override
from urllib.parse import urlsplit

from core.f95zone import ThreadPrefixFlags, normalize_prefix_label, thread_prefix_flags
from core.versions import normalize_version

LC_HOSTS = frozenset({"lewdcorner.com", "www.lewdcorner.com"})
_THREAD_PATH = re.compile(r"^/threads/(?:[^/]+\.)?([0-9]+)(?:/.*)?$", re.IGNORECASE)
LC_ENGINE_PREFIXES = (
    "Flash", "HTML", "Java", "QSP", "RAGS", "RPGM", "Ren'Py", "Unity",
    "Unreal Engine", "WebGL", "Wolf RPG", "Godot", "Other",
)
_ENGINES = {normalize_prefix_label(name): name for name in LC_ENGINE_PREFIXES}
# LC prefix ids that share an engine's name but are not an engine: 29 is the
# render prefix "Other Render Engine", which LC labels "Other" like engine 22.
LC_NON_ENGINE_PREFIX_IDS = frozenset({29})


class LcPrefix(NamedTuple):
    """An LC thread prefix; `id` is None when the source gave only the name."""

    name: str
    id: int | None = None


def lc_prefix(name: object, prefix_id: object = None) -> LcPrefix | None:
    """Build an LcPrefix from untrusted values; None without a usable name."""
    if not isinstance(name, str) or not name.strip():
        return None
    number: int | None = None
    if isinstance(prefix_id, int) and not isinstance(prefix_id, bool):
        number = prefix_id
    elif isinstance(prefix_id, str) and prefix_id.strip().isascii() and prefix_id.strip().isdigit():
        number = int(prefix_id.strip())
    return LcPrefix(name.strip(), number)
LC_SETTINGS = ("lc_enabled", "lc_plus")
GAME_NODES = {"6", "212", "119", "213"}


def hosts_url(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
        return host == "lewdcorner.com" or host.endswith(".lewdcorner.com")
    except ValueError:
        return False


# The only LC image URLs the browser extension fetches (LC_IMAGE_RE in
# extension/background.js): full attachments, not `/data/attachments/`
# thumbnails or `/proxy.php` images.
_IMAGE_URL = re.compile(r"https://lewdcorner\.com/attachments/[^/?#\s]+\.\d+/")


def fetchable_image_url(url: str) -> bool:
    return bool(_IMAGE_URL.fullmatch(url))


def extract_thread_id(url: object) -> str:
    if not isinstance(url, str):
        return ""
    value = url.strip()
    if value.lower().startswith(("lewdcorner.com/", "www.lewdcorner.com/")):
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname not in LC_HOSTS
            or parts.username is not None
            or parts.port not in {None, 80 if parts.scheme == "http" else 443}
        ):
            return ""
    except ValueError:
        return ""
    match = _THREAD_PATH.fullmatch(parts.path)
    return match[1].lstrip("0") if match else ""


def normalize_thread_url(url: object) -> str:
    thread_id = extract_thread_id(url)
    return f"https://lewdcorner.com/threads/{thread_id}/" if thread_id else ""


def thread_urls_match(left: object, right: object) -> bool:
    thread_id = extract_thread_id(left)
    return bool(thread_id and thread_id == extract_thread_id(right))


def validated_thread_url(value: object) -> str:
    url = normalize_thread_url(value)
    if str(value or "").strip() and not url:
        raise ValueError("The URL is not a LewdCorner thread")
    return url


def normalize_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode not in {"", "on", "off"}:
        raise ValueError("Invalid LewdCorner mode")
    return mode


def setting_enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}


def lc_active(game: Mapping[str, object], lc_enabled: bool) -> bool:
    mode = game.get("lc_mode") or ""
    return bool(game.get("lc_url")) and (
        mode == "on" or (mode == "" and (lc_enabled or not game.get("f95_url")))
    )


_TRAILING_GROUP_RE = re.compile(r"\s*\[[^\[\]]*\]\s*$")


def page_title(text: object) -> str:
    """An LC thread heading without its trailing bracket groups.

    LC headings read "Title [v1.2.0] [Developer]"; the catalog stores the
    title alone, and so do F95 games. A heading that is only bracket groups
    is kept as it is."""
    title = str(text or "").strip()
    rest = title
    while (match := _TRAILING_GROUP_RE.search(rest)) is not None and match.start() > 0:
        rest = rest[: match.start()].rstrip()
    return rest or title


MAX_PAGE_SCREENSHOTS = 20


def page_screenshots(raw: object) -> list[str]:
    """Screenshot URLs sent from an LC page: ``https://`` strings, in order, once each."""
    if not isinstance(raw, list):
        return []
    urls: list[str] = []
    for item in cast(list[object], raw):
        if not isinstance(item, str):
            continue
        url = item.strip()
        if len(url) <= 2048 and url.lower().startswith("https://") and not any(c.isspace() for c in url) and url not in urls:
            urls.append(url)
            if len(urls) >= MAX_PAGE_SCREENSHOTS:
                break
    return urls


def prefix_details(prefixes: Iterable[LcPrefix | str]) -> tuple[str, ThreadPrefixFlags]:
    """The engine and the status flags of LC prefixes.

    A prefix with a known non-engine id (the render "Other") never sets the
    engine; a prefix without an id is matched by its name alone.
    """
    items = [prefix if isinstance(prefix, LcPrefix) else LcPrefix(str(prefix or "")) for prefix in prefixes]
    engine = next(
        (
            _ENGINES[label]
            for prefix in items
            if prefix.id not in LC_NON_ENGINE_PREFIX_IDS
            and (label := normalize_prefix_label(prefix.name)) in _ENGINES
        ),
        "",
    )
    # The feed badge says "Completed"; the thread page title label says "Complete".
    return engine, thread_prefix_flags(prefix.name for prefix in items)


type Site = Literal["f95", "lc"]
type LatestSite = Literal["", "f95", "lc", "both"]
type SiteVersion = tuple[str, str]


def pick_latest(
    f95: SiteVersion | None, lc: SiteVersion | None
) -> tuple[str, LatestSite]:
    if not f95 or not normalize_version(f95[0]):
        return (lc[0], "lc") if lc and normalize_version(lc[0]) else ("", "")
    if not lc or not normalize_version(lc[0]):
        return f95[0], "f95"
    if normalize_version(f95[0]) == normalize_version(lc[0]):
        return f95[0], "both"
    return (lc[0], "lc") if lc[1] > f95[1] else (f95[0], "f95")


def timestamp_iso(value: object) -> str:
    """Turn a feed or catalog Unix timestamp into sortable UTC text."""
    try:
        seconds = int(str(value))
        if seconds > 0:
            return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        pass
    return ""


def usable_catalog_entry(entry: Mapping[str, object]) -> bool:
    """Whether the catalog's version can stand in for a live LC read: the
    builder listed the thread itself (not an Atlas import) and has a version
    for it. The tier does not count; a Plus thread's version comes from its
    page and is empty until the page for its current date is read."""
    return (
        bool(entry)
        and entry.get("origin") != "import"
        and bool(str(entry.get("version") or "").strip())
    )


LC_UPDATE_ERRORS = {
    "lc_extension_unavailable": "Connect the browser extension to check LewdCorner Plus games",
    "lc_login_required": "Sign in to LewdCorner in the browser with the wLib extension",
    "lc_plus_required": "LewdCorner Plus access is required to check this thread",
    "lc_extract_failed": "Could not read the latest version from LewdCorner",
    "not_found": "LewdCorner thread not found",
    "blocked": "Finish the LewdCorner check in your browser",
}


@final
class LcUpdateError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(LC_UPDATE_ERRORS[code])


@dataclass(frozen=True)
class LcVersion:
    version: str
    at: str = ""
    premium: bool = False
    prefixes: tuple[LcPrefix, ...] = ()


@dataclass(frozen=True)
class FeedPage:
    items: dict[str, LcVersion]
    oldest_at: str
    newest_at: str
    has_more: bool


def parse_feed_page(body: str) -> FeedPage:
    body = body.lstrip("\ufeff \t\r\n")
    if body.startswith("<"):
        raise LcUpdateError("lc_login_required")
    try:
        payload = cast(object, json.loads(body))
    except ValueError as exc:
        raise LcUpdateError("lc_extract_failed") from exc
    if isinstance(payload, str) and payload:
        raise LcUpdateError("lc_login_required")
    if not isinstance(payload, dict):
        raise LcUpdateError("lc_extract_failed")
    envelope = cast(dict[str, object], payload)
    if isinstance(envelope.get("error"), str) and envelope["error"]:
        raise LcUpdateError("lc_login_required")
    result = envelope.get("result")
    if envelope.get("status") != "ok" or not isinstance(result, dict):
        raise LcUpdateError("lc_extract_failed")
    page = cast(dict[str, object], result)
    raw_items = page.get("items")
    if not isinstance(raw_items, list):
        raise LcUpdateError("lc_extract_failed")
    versions: dict[str, LcVersion] = {}
    dates: list[str] = []
    for raw_item in cast(list[object], raw_items):
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, object], raw_item)
        at = timestamp_iso(item.get("timestamp"))
        if at:
            dates.append(at)
        thread_id = str(item.get("id") or "").lstrip("0")
        if str(item.get("node_id")) not in GAME_NODES or not re.fullmatch(r"[0-9]+", thread_id):
            continue
        badges = item.get("badges")
        prefixes: list[LcPrefix] = []
        if isinstance(badges, list):
            for badge in cast(list[object], badges):
                if isinstance(badge, dict):
                    entry = cast(dict[str, object], badge)
                    if prefix := lc_prefix(entry.get("name"), entry.get("id")):
                        prefixes.append(prefix)
        _ = versions.setdefault(thread_id, LcVersion(
            version=str(item.get("version") or "").strip(),
            at=at,
            premium=item.get("is_premium") is True,
            prefixes=tuple(prefixes),
        ))
    return FeedPage(versions, min(dates, default=""), max(dates, default=""), page.get("hasMore") is True)


@final
class _ThreadPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.template = ""
        self.logged_in: bool | None = None
        self.in_title = False
        self.in_version_field = False
        self.in_version = False
        self.version: list[str] = []
        self.version_read = False
        self.prefixes: list[LcPrefix] = []
        self._prefix_tag = ""
        self._prefix_id: int | None = None
        self._prefix_depth = 0
        self._prefix_text: list[str] = []
        self.challenge = False

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if tag == "html":
            self.template = attr.get("data-template") or ""
            match attr.get("data-logged-in"):
                case "true":
                    self.logged_in = True
                case "false":
                    self.logged_in = False
                case _:
                    pass
        if attr.get("id") in {"challenge-form", "cf-challenge-running"}:
            self.challenge = True
        if tag == "h1" and "p-title-value" in classes:
            self.in_title = True
        if self.in_title:
            if tag == self._prefix_tag:
                self._prefix_depth += 1
            elif not self._prefix_tag and "label" in classes:
                self._prefix_tag = tag
                self._prefix_depth = 1
            if tag == "a" and "labelLink" in classes:
                # The label link ends in "?prefix_id=<id>".
                match = re.search(r"[?&]prefix_id=([0-9]+)", attr.get("href") or "")
                if match:
                    self._prefix_id = int(match.group(1))
        if tag == "dl" and (attr.get("data-field") or "").casefold() == "version":
            self.in_version_field = not self.version_read
        if tag == "dd" and self.in_version_field:
            self.in_version = True

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == self._prefix_tag:
            self._prefix_depth -= 1
            if not self._prefix_depth:
                name = "".join(self._prefix_text).strip()
                self.prefixes.append(LcPrefix(name, self._prefix_id))
                self._prefix_tag = ""
                self._prefix_id = None
                self._prefix_text.clear()
        if tag == "h1":
            self.in_title = False
        if tag == "dd" and self.in_version:
            self.in_version = False
            self.version_read = True
        if tag == "dl":
            self.in_version_field = False

    @override
    def handle_data(self, data: str) -> None:
        if self.in_version:
            self.version.append(data)
        if self._prefix_tag:
            self._prefix_text.append(data)


def parse_thread_page(
    body: str, status: int, *, premium: bool = False, for_downloads: bool = False
) -> LcVersion:
    page = _ThreadPage()
    page.feed(body)
    page.close()
    if status == 404:
        raise LcUpdateError("not_found" if for_downloads else "lc_extract_failed")
    if for_downloads and page.challenge:
        raise LcUpdateError("blocked")
    if for_downloads and page.logged_in is False:
        raise LcUpdateError("lc_login_required")
    if status == 401 or page.template.startswith("login") or page.challenge:
        raise LcUpdateError("lc_login_required")
    if status == 403 or page.template == "error":
        if page.logged_in is False or (status == 403 and page.logged_in is None):
            raise LcUpdateError("lc_login_required")
        # A signed-in account without LC Plus gets a 403 error page for Plus threads.
        if (premium or status == 403) and page.logged_in is True:
            raise LcUpdateError("lc_plus_required")
        raise LcUpdateError("lc_extract_failed")
    if status != 200 or (not page.version_read and page.template != "thread_view"):
        raise LcUpdateError("lc_extract_failed")
    return LcVersion("".join(page.version).strip(), premium=premium, prefixes=tuple(page.prefixes))
