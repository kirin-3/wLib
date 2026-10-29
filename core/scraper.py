# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPException, HTTPResponse
from typing import Protocol, TypeAlias, cast, override
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core import extension_bridge
from core.store_links import StoreLink, parse_store_link
from .f95zone import NON_ENGINE_PREFIXES, extract_thread_id, normalize_prefix_label

ScraperResultValue: TypeAlias = bool | str | list[str] | list[StoreLink]
ScraperResult: TypeAlias = Mapping[str, ScraperResultValue]
ScraperResultDict: TypeAlias = dict[str, ScraperResultValue]
ThreadMetadata: TypeAlias = dict[str, str | list[str]]


class BatchResultCallback(Protocol):
    def __call__(self, url: str, result: ScraperResult) -> bool: ...


class _F95RequestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code: str = code


_BRACKETED_VERSION_RE = re.compile(
    r"^v?(?:er(?:sion)?\.?\s*)?(\d+[\d.]*[a-zA-Z]?(?:\s*(?:beta|alpha|final|fix\d*|hotfix))?)$",
    re.I,
)
_CHAPTER_VERSION_RE = re.compile(
    r"^(Ch(?:apter|\.)?\s*\d+(?:\.\d+)?(?:\s*v[\d.]+[a-zA-Z]?)?|Ep(?:isode|\.)?\s*\d+(?:\.\d+)?|S\d+\s*E\d+|Part\s*\d+)",
    re.I,
)
_BARE_VERSION_RE = re.compile(r"\bv(\d+[\d.]*[a-zA-Z]?)(?:\s|$|\[)", re.I)

# ponytail: global lock serializes all F95 traffic; use a per-host token bucket
# if we ever add another host. Re-entrant because _fetch_with_fallback holds it
# across the paced request so a queued caller chooses its transport after the
# request in front of it has finished, and _f95_get takes it again from there.
_F95_REQUEST_LOCK = threading.RLock()
_f95_last_request_finished: float | None = None
# Extension-first window: set only by a plain-HTTP `blocked` result, kept in
# memory only, 15 minutes. While active and an eligible extension is connected,
# F95 requests go straight to the extension without trying plain HTTP first.
_prefer_extension_until: float | None = None
_PREFER_EXTENSION_WINDOW_SECONDS = 15 * 60


def get_request_interval_seconds() -> int:
    try:
        value = int(os.environ.get("WLIB_UPDATE_CHECK_DELAY_SECONDS", "2"))
    except ValueError:
        value = 2
    return max(1, min(value, 30))


# Version token plus an optional space-separated suffix ("1.0 Beta"); attached
# suffixes ("1.0.0-beta", "0.5_public") stay in the token. Mirrored in
# extension/content.js.
_POST_VERSION_RE = re.compile(
    r"\d[^\s,;<]*(?:\s+(?:beta|alpha|final|fix\d*|hotfix)\b)?", re.I
)


def _run_paced_f95_request(
    transport: Callable[[], tuple[int, str]],
) -> tuple[int, str]:
    """Run one F95 transport inside the global lock and minimum interval.

    Both plain HTTP and extension fetches go through here, so a urllib
    request followed by an extension request never overlap and stay spaced.
    """
    global _f95_last_request_finished
    with _F95_REQUEST_LOCK:
        if _f95_last_request_finished is not None:
            remaining = get_request_interval_seconds() - (
                time.monotonic() - _f95_last_request_finished
            )
            if remaining > 0:
                time.sleep(remaining)
        try:
            return transport()
        finally:
            _f95_last_request_finished = time.monotonic()


def _urllib_f95_get(url: str) -> tuple[int, str]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with cast(
            HTTPResponse,
            urlopen(request, timeout=30, context=ssl.create_default_context()),
        ) as response:
            encoding = response.headers.get_content_charset() or "utf-8"
            return response.status, response.read().decode(
                encoding, errors="replace"
            )
    except HTTPError as exc:
        with exc:
            encoding = exc.headers.get_content_charset() or "utf-8"
            return exc.code, exc.read().decode(encoding, errors="replace")
    except (TimeoutError, socket.timeout) as exc:
        raise _F95RequestError(
            "navigation_timeout", "F95Zone request timed out"
        ) from exc
    except URLError as exc:
        code = (
            "navigation_timeout"
            if isinstance(exc.reason, TimeoutError)
            else "scraper_error"
        )
        raise _F95RequestError(code, str(exc.reason)) from exc
    except (HTTPException, OSError, ValueError) as exc:
        raise _F95RequestError("scraper_error", str(exc)) from exc


def _f95_get(url: str) -> tuple[int, str]:
    return _run_paced_f95_request(lambda: _urllib_f95_get(url))


def _f95_get_via_extension(url: str) -> tuple[int, str]:
    """Fetch one F95 URL through the connected extension, paced like HTTP."""

    def _transport() -> tuple[int, str]:
        response = extension_bridge.fetch(url, timeout=30)
        return response.status, response.body

    return _run_paced_f95_request(_transport)


def _attribute(opening: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*([\"'])(.*?)\1", opening, re.S | re.I
    )
    return unescape(match.group(2)) if match else ""


def _text(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", html)).strip()


def _blocks(html: str, tag: str, class_name: str = "") -> Iterator[tuple[str, str]]:
    # A spoiler/div inside bbWrapper must not truncate the starter post.
    tokens = re.compile(rf"</?{tag}\b[^>]*>", re.I)
    for opening in re.finditer(rf"<{tag}\b[^>]*>", html, re.I):
        if (
            class_name
            and class_name not in _attribute(opening.group(), "class").split()
        ):
            continue
        depth = 1
        for token in tokens.finditer(html, opening.end()):
            depth += -1 if token.group().startswith("</") else 1
            if depth == 0:
                yield opening.group(), html[opening.end() : token.start()]
                break


class _StoreHeaderParser(HTMLParser):
    """Read bold labelled header lines while skipping entire spoiler trees."""

    _VOID: frozenset[str] = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: dict[tuple[str, str], StoreLink] = {}
        self._hidden_depth: int = 0
        self._text: str = ""
        self._bold_depth: int = 0
        self._bold_text: str = ""
        self._labels: list[str] = []
        self._urls: list[str] = []

    def _finish_line(self) -> None:
        match = re.match(r"\s*(Developer|Publisher|Stores?)\s*:", self._text, re.I)
        if match and match[1].lower() in self._labels:
            for url in self._urls:
                link = parse_store_link(url)
                if link:
                    _ = self.links.setdefault((link["source"], link["id"]), link)
        self._text = ""
        self._labels = []
        self._urls = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if self._hidden_depth:
            if tag not in self._VOID:
                self._hidden_depth += 1
            return
        classes = (attributes.get("class") or "").split()
        if any(
            name.startswith("bbCodeSpoiler") or name == "bbCodeBlock--spoiler"
            for name in classes
        ):
            self._finish_line()
            self._hidden_depth = 1
            return
        if tag in {"br", "div", "p", "hr"}:
            self._finish_line()
        if tag in {"b", "strong"}:
            if not self._bold_depth:
                self._bold_text = ""
            self._bold_depth += 1
        if tag == "a" and (url := attributes.get("href")):
            self._urls.append(url)

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    @override
    def handle_endtag(self, tag: str) -> None:
        if self._hidden_depth:
            if tag not in self._VOID:
                self._hidden_depth -= 1
            return
        if tag in {"b", "strong"} and self._bold_depth:
            self._bold_depth -= 1
            if not self._bold_depth:
                label = self._bold_text.strip().rstrip(":").strip().lower()
                self._labels.append(label)
        if tag in {"div", "p"}:
            self._finish_line()

    @override
    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self._text += data
            if self._bold_depth:
                self._bold_text += data

    def collect(self, html: str) -> list[StoreLink]:
        self.feed(html)
        self._finish_line()
        return list(self.links.values())


class Scraper:
    def _extract_version_from_title(self, title: object) -> str:
        # Keep these rules aligned with extractGameInfo() in extension/content.js.
        if not isinstance(title, str):
            return "Unknown"
        stripped = title.strip()
        while True:
            leading = re.match(r"^\[([^\]]+)\]\s*", stripped)
            if not leading:
                break
            inner = leading.group(1).strip()
            if _BRACKETED_VERSION_RE.match(inner) or _CHAPTER_VERSION_RE.match(inner):
                break
            stripped = stripped[leading.end() :]
        title = stripped
        brackets = cast(list[str], re.findall(r"\[([^\]]*)\]", title))
        for bracket in brackets:
            inner = bracket.strip()
            match = _BRACKETED_VERSION_RE.match(inner)
            if match:
                return match.group(1).strip()
            if _CHAPTER_VERSION_RE.match(inner):
                return inner
        match = _BARE_VERSION_RE.search(title)
        if match:
            return match.group(1)
        if len(brackets) >= 2:
            return brackets[0].strip() or "Unknown"
        return "Unknown"

    def _normalize_checker_version(self, raw: object) -> str:
        if not isinstance(raw, str):
            return "Unknown"
        return self._extract_version_from_title(f"_ [{raw.strip()}] [_]")

    def _extract_version_from_post(self, html: str) -> str:
        html_patterns = [
            r"<b>\s*(?:Current\s+)?Version\s*</b>\s*:?\s*v?([^<\n]+)",
            r"<b>\s*Ver(?:\.|sion)?\s*</b>\s*:?\s*v?([^<\n]+)",
        ]
        for pattern in html_patterns:
            match = re.search(pattern, html, re.I)
            if match:
                version = _POST_VERSION_RE.match(unescape(match.group(1)).strip())
                if version:
                    return version.group()
        version_re = _POST_VERSION_RE.pattern
        text_patterns = [
            rf"(?:Current\s+)?Version\s*:?\s*v?({version_re})",
            rf"Ver(?:\.|sion)?\s*:?\s*v?({version_re})",
            r"Release\s*:?\s*v?(\d[\d.]*[a-zA-Z]?)",
        ]
        for pattern in text_patterns:
            match = re.search(pattern, _text(html), re.I)
            if match:
                return match.group(1).strip()
        return "Unknown"

    def _normalize_cover_image_url(self, url: object) -> str:
        if not isinstance(url, str):
            return ""
        normalized = url.strip()
        if "attachments.f95zone.to/" in normalized:
            while "/thumb/" in normalized:
                normalized = normalized.replace("/thumb/", "/", 1)
        return normalized

    def _collect_screenshots(self, wrapper: str, normalized_cover: str) -> list[str]:
        """Collect starter-post screenshot URLs in post order.

        Scans the starter ``bbWrapper`` for lbContainer-zoomer data-src values,
        bbImage data-src/src values, and hrefs of anchors wrapping a bbImage
        that point at attachments.f95zone.to. Each value is normalized like the
        cover, the cover is dropped, duplicates collapse keeping first-seen
        order, and at most 20 URLs are kept.
        """
        if not wrapper:
            return []
        candidates: list[tuple[int, str]] = []
        for match in re.finditer(r"<[^/!][^>]*>", wrapper):
            opening = match.group()
            classes = _attribute(opening, "class").split()
            if "lbContainer-zoomer" in classes:
                value = _attribute(opening, "data-src")
                if value:
                    candidates.append((match.start(), value))
            tag_name = re.match(r"<\s*([a-zA-Z0-9]+)", opening)
            if (
                tag_name
                and tag_name.group(1).lower() == "img"
                and "bbImage" in classes
            ):
                value = _attribute(opening, "data-src") or _attribute(
                    opening, "src"
                )
                if value:
                    candidates.append((match.start(), value))
        for match in re.finditer(
            r"<a\b[^>]*>.*?</a>", wrapper, re.I | re.S
        ):
            opening_end = match.group().find(">")
            opening = match.group()[: opening_end + 1] if opening_end != -1 else ""
            href = _attribute(opening, "href")
            if not href or "attachments.f95zone.to" not in href:
                continue
            inner = match.group()[opening_end + 1 :] if opening_end != -1 else ""
            has_bb_image = False
            for img_match in re.finditer(r"<img\b[^>]*>", inner, re.I):
                if "bbImage" in _attribute(img_match.group(), "class").split():
                    has_bb_image = True
                    break
            if has_bb_image:
                candidates.append((match.start(), href))
        candidates.sort(key=lambda item: item[0])
        seen: set[str] = set()
        screenshots: list[str] = []
        if normalized_cover:
            seen.add(normalized_cover)
        for _, raw in candidates:
            normalized = self._normalize_cover_image_url(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            screenshots.append(normalized)
            if len(screenshots) >= 20:
                break
        return screenshots

    def _error(
        self, code: str, message: str, browser_fallback: str | None = None
    ) -> ScraperResult:
        result: ScraperResultDict = {
            "success": False,
            "code": code,
            "error": message,
        }
        if browser_fallback in ("unavailable", "failed"):
            result["browser_fallback"] = browser_fallback
        return result

    def _fetch_with_fallback(self, url: str) -> tuple[int, str, str | None]:
        """Fetch one F95 URL with a single extension retry.

        Holds the shared F95 lock across the transport choice and the request,
        so a caller that waited for its turn sees a challenge that another
        caller hit and goes straight to the extension instead of trying plain
        HTTP once more.

        Returns ``(status, body, browser_fallback)`` where the flag is
        ``None`` on success or when no fallback applied, ``"unavailable"``
        when a blocked/login page had no eligible extension, and ``"failed"``
        when the extension was tried but did not recover the page. Only
        plain-HTTP ``blocked`` opens the 15-minute extension-first window.
        Timeouts and network errors propagate without fallback.
        """
        with _F95_REQUEST_LOCK:
            return self._fetch_with_fallback_locked(url)

    def _fetch_with_fallback_locked(
        self, url: str
    ) -> tuple[int, str, str | None]:
        global _prefer_extension_until
        now = time.monotonic()
        prefer_until = _prefer_extension_until
        if prefer_until is not None and now < prefer_until:
            try:
                window_available = extension_bridge.is_available()
            except Exception:
                window_available = False
            if window_available:
                try:
                    ext_status, ext_body = _f95_get_via_extension(url)
                except (
                    extension_bridge.BridgeUnavailable,
                    extension_bridge.BridgeFailed,
                ):
                    pass
                else:
                    ext_issue = self._classify_page_issue(
                        ext_status, self._page_title(ext_body), ext_body
                    )
                    if ext_issue and ext_issue["code"] in (
                        "blocked",
                        "login_required",
                    ):
                        return (ext_status, ext_body, "failed")
                    return (ext_status, ext_body, None)
                # Extension failed inside the window: use plain HTTP once,
                # without a second fallback attempt.
                try:
                    status, body = _f95_get(url)
                except _F95RequestError:
                    raise
                issue = self._classify_page_issue(
                    status, self._page_title(body), body
                )
                if issue and issue["code"] == "blocked":
                    _prefer_extension_until = (
                        time.monotonic() + _PREFER_EXTENSION_WINDOW_SECONDS
                    )
                    return (status, body, "failed")
                if issue and issue["code"] == "login_required":
                    return (status, body, "failed")
                return (status, body, None)
            # No eligible extension: fall through to the normal path.

        try:
            status, body = _f95_get(url)
        except _F95RequestError:
            raise
        issue = self._classify_page_issue(status, self._page_title(body), body)
        if issue and issue["code"] == "blocked":
            _prefer_extension_until = (
                time.monotonic() + _PREFER_EXTENSION_WINDOW_SECONDS
            )
        if not issue or issue["code"] not in ("blocked", "login_required"):
            return (status, body, None)
        try:
            window_available = extension_bridge.is_available()
        except Exception:
            window_available = False
        if not window_available:
            return (status, body, "unavailable")
        try:
            ext_status, ext_body = _f95_get_via_extension(url)
        except extension_bridge.BridgeUnavailable:
            return (status, body, "unavailable")
        except extension_bridge.BridgeFailed:
            return (status, body, "failed")
        ext_issue = self._classify_page_issue(
            ext_status, self._page_title(ext_body), ext_body
        )
        if ext_issue and ext_issue["code"] in ("blocked", "login_required"):
            return (ext_status, ext_body, "failed")
        return (ext_status, ext_body, None)

    def _is_non_actionable_version(self, version: object) -> bool:
        return str(version or "").strip().lower() in (
            "",
            "unknown",
            "n/a",
            "na",
            "none",
            "null",
        )

    def _classify_page_issue(
        self, status: int, title: str, html: str
    ) -> dict[str, str] | None:
        page_title = title.lower()
        # Guest threads include Cloudflare's passive JS detection script even
        # after the server has delivered the complete page.
        page_html = re.sub(
            r"/cdn-cgi/challenge-platform/scripts/jsd/[^\s\"'<>]+",
            "",
            html.lower(),
        )
        # A real thread page always has its title heading. Words such as
        # "login" or "just a moment" in a game title must not classify it.
        has_thread_heading = bool(
            _text(next(_blocks(html, "h1", "p-title-value"), ("", ""))[1])
        )
        # Check body markers separately from the thread title and heading.
        page_html = re.sub(r"<title\b[^>]*>.*?</title>", "", page_html, flags=re.S)
        page_html = re.sub(
            r"<h1\b[^>]*\bp-title-value\b[^>]*>.*?</h1>",
            "",
            page_html,
            flags=re.S,
        )
        combined = f"{page_title}\n{page_html}"
        if status == 429:
            return {"code": "rate_limited", "error": "Rate limited by F95Zone"}
        if status == 404 or any(
            marker in combined
            for marker in (
                "requested thread could not be found",
                "requested page could not be found",
            )
        ):
            return {
                "code": "not_found",
                "error": "Thread not found; it may have been deleted or moved",
            }
        challenge_markers = (
            "just a moment",
            "checking your browser",
            "verify you are human",
            "attention required",
            "cf-challenge",
            "challenge-platform",
        )
        # Challenge pages replace the whole forum page. Once XenForo rendered
        # the thread heading, the game title also repeats in og:title and
        # JSON-LD, and post text may use the same phrases.
        if not has_thread_heading and any(
            marker in combined for marker in challenge_markers
        ):
            return {
                "code": "blocked",
                "error": "Blocked by anti-bot challenge while loading thread",
            }
        login_title_markers = ("log in", "login", "sign in")
        login_body_markers = (
            "you must be logged-in",
            "you must be logged in",
            'action="/login/login"',
        )
        # Login walls render outside the starter post, and their page shell can
        # carry an h1.p-title-value. A rendered starter post, though, may
        # mention a login requirement in its download notes without gating the
        # page; ignore phrases that only occur in the head or starter post.
        login_scope = re.sub(
            r"<head\b[^>]*>.*?</head>", "", page_html, flags=re.S
        )
        starter_html = next(
            _blocks(html, "article", "message-threadStarterPost"), ("", "")
        )[1]
        if starter_html:
            login_scope = login_scope.replace(starter_html.lower(), "", 1)
        if any(marker in login_scope for marker in login_body_markers) or (
            not has_thread_heading
            and any(
                marker in page_title
                for marker in (*login_title_markers, *login_body_markers)
            )
        ):
            return {
                "code": "login_required",
                "error": "Login is required to access this thread",
            }
        return None

    def _page_title(self, html: str) -> str:
        return _text(next(_blocks(html, "title"), ("", ""))[1])

    def _extract_page(self, html: str) -> ScraperResult:
        h1 = next(_blocks(html, "h1", "p-title-value"), ("", ""))[1]
        labels = [_text(content) for _, content in _blocks(h1, "a", "labelLink")]
        appended_labels = [
            _text(content) for _, content in _blocks(h1, "span", "label-append")
        ]
        engine = next(
            (
                label
                for label in labels
                if normalize_prefix_label(label) not in NON_ENGINE_PREFIXES
            ),
            "",
        )
        thread_completed = any(
            normalize_prefix_label(label) == "completed"
            for label in (*labels, *appended_labels)
        )
        clean_h1 = re.sub(
            r'<a\b[^>]*class=["\'][^"\']*\blabelLink\b[^"\']*["\'][^>]*>.*?</a>',
            "",
            h1,
            flags=re.I | re.S,
        )
        clean_h1 = re.sub(
            r'<span\b[^>]*class=["\'][^"\']*\blabel-append\b[^"\']*["\'][^>]*>.*?</span>',
            "",
            clean_h1,
            flags=re.I | re.S,
        )
        title = re.sub(r"\s+", " ", _text(clean_h1)) or self._page_title(html)
        if not title:
            return self._error("title_not_found", "Could not find thread title")
        starter = next(_blocks(html, "article", "message-threadStarterPost"), ("", ""))[
            1
        ]
        if not starter:
            starter = next(_blocks(html, "article", "message--post"), ("", ""))[1]
        wrapper = next(_blocks(starter, "div", "bbWrapper"), ("", ""))[1]
        version = self._extract_version_from_title(title)
        if self._is_non_actionable_version(version):
            version = self._extract_version_from_post(wrapper)
        tags: list[str] = []
        for tag in ("a", "span"):
            for _, content in _blocks(html, tag, "tagItem"):
                value = _text(content)
                if value and value not in tags:
                    tags.append(value)
        cover = ""
        for match in re.finditer(r"<[^/!][^>]*>", wrapper):
            opening = match.group()
            if "lbContainer-zoomer" in _attribute(opening, "class").split():
                cover = _attribute(opening, "data-src")
                if cover:
                    break
        if not cover:
            for match in re.finditer(r"<img\b[^>]*>", wrapper, re.I):
                opening = match.group()
                if "bbImage" in _attribute(opening, "class").split():
                    cover = _attribute(opening, "data-src") or _attribute(
                        opening, "src"
                    )
                    if cover:
                        break
        normalized_cover = self._normalize_cover_image_url(cover)
        screenshots = self._collect_screenshots(wrapper, normalized_cover)
        last_edit = ""
        for tag in ("div", "span"):
            edit = next(_blocks(starter, tag, "message-lastEdit"), ("", ""))[1]
            edit_time = re.search(r"<time\b[^>]*>", edit, re.I)
            if edit_time:
                last_edit = _attribute(edit_time.group(), "datetime")
                break
        return {
            "success": True,
            "title": title,
            "version": version,
            "engine": engine,
            "thread_completed": thread_completed,
            "tags": tags,
            "cover_image": normalized_cover,
            "screenshots": screenshots,
            "store_links": _StoreHeaderParser().collect(wrapper),
            "thread_main_post_last_edit_at": last_edit,
            "thread_main_post_checked_at": datetime.now().isoformat(),
        }

    def _starter_hides_attachments(self, html: str) -> bool:
        """True when the starter post hides attachments from guests."""
        starter = next(
            _blocks(html, "article", "message-threadStarterPost"), ("", "")
        )[1]
        return "messageHide--attach" in starter

    def _starter_hides_links(self, html: str) -> bool:
        starter = next(_blocks(html, "article", "message-threadStarterPost"), ("", ""))[
            1
        ]
        return "messageHide--link" in starter

    def _reload_signed_in(self, url: str, guest_html: str) -> str:
        """Re-fetch a guest page through the extension to reveal links and attachments.

        Plain HTTP is always a guest session, so screenshots posted as
        attachments are replaced by a login prompt. The extension runs with
        the browser's F95 login. Any failure keeps the guest page.
        """
        try:
            if not extension_bridge.is_available():
                return guest_html
            ext_status, ext_body = _f95_get_via_extension(url)
        except Exception:
            return guest_html
        if ext_status != 200 or self._classify_page_issue(
            ext_status, self._page_title(ext_body), ext_body
        ):
            return guest_html
        return ext_body

    def _get_page(self, thread_id: str) -> ScraperResult:
        try:
            status, html, fallback = self._fetch_with_fallback(
                f"https://f95zone.to/threads/{thread_id}/"
            )
            issue = self._classify_page_issue(status, self._page_title(html), html)
            if issue:
                return self._error(
                    issue["code"], issue["error"], browser_fallback=fallback
                )
            if status != 200:
                return self._error(
                    "scraper_error", f"F95Zone returned HTTP {status}"
                )
            if fallback is None and (
                self._starter_hides_attachments(html) or self._starter_hides_links(html)
            ):
                html = self._reload_signed_in(
                    f"https://f95zone.to/threads/{thread_id}/", html
                )
            return self._extract_page(html)
        except _F95RequestError as exc:
            return self._error(exc.code, str(exc))
        except Exception as exc:
            return self._error("scraper_error", str(exc))

    def _check_chunk(self, ids: list[str]) -> dict[str, ScraperResult | None]:
        versions: dict[str, object] = {}
        try:
            status, text, fallback = self._fetch_with_fallback(
                f"https://f95zone.to/sam/checker.php?threads={','.join(ids)}"
            )
            issue = self._classify_page_issue(status, self._page_title(text), text)
            # Falling back to 100 page requests would only hammer F95 harder.
            if issue and issue["code"] == "blocked":
                return {
                    thread_id: self._error(
                        issue["code"], issue["error"], browser_fallback=fallback
                    )
                    for thread_id in ids
                }
            if issue and issue["code"] == "rate_limited":
                return {
                    thread_id: self._error(issue["code"], issue["error"])
                    for thread_id in ids
                }
            if issue and issue["code"] == "login_required" and fallback is not None:
                # Extension was tried for the batch and still shows login:
                # fail directly instead of fanning out to 100 page requests.
                return {
                    thread_id: self._error(
                        issue["code"], issue["error"], browser_fallback=fallback
                    )
                    for thread_id in ids
                }
            if status == 200:
                payload = cast(object, json.loads(text))
                if isinstance(payload, dict):
                    data = cast(dict[str, object], payload)
                    if data.get("status") == "ok" and isinstance(data.get("msg"), dict):
                        versions = cast(dict[str, object], data["msg"])
        except (_F95RequestError, ValueError):
            pass
        results: dict[str, ScraperResult | None] = {}
        for thread_id in ids:
            version = self._normalize_checker_version(versions.get(thread_id))
            if self._is_non_actionable_version(version):
                results[thread_id] = None
            else:
                results[thread_id] = {"success": True, "version": version}
        return results

    def check_versions(
        self, urls: Sequence[str], callback: BatchResultCallback | None = None
    ) -> dict[str, ScraperResult]:
        ids = list(
            dict.fromkeys(
                thread_id for url in urls if (thread_id := extract_thread_id(url))
            )
        )
        chunk_by_id: dict[str, list[str]] = {}
        for offset in range(0, len(ids), 100):
            chunk = ids[offset : offset + 100]
            for thread_id in chunk:
                chunk_by_id[thread_id] = chunk
        by_id: dict[str, ScraperResult | None] = {}
        results: dict[str, ScraperResult] = {}
        for url in urls:
            thread_id = extract_thread_id(url)
            if not thread_id:
                result = self._error(
                    "invalid_url", "A valid F95Zone thread URL is required"
                )
            else:
                if thread_id not in by_id:
                    by_id.update(self._check_chunk(chunk_by_id[thread_id]))
                page_result = by_id[thread_id]
                if page_result is None:
                    page_result = self._get_page(thread_id)
                    if page_result.get("success") and self._is_non_actionable_version(
                        page_result.get("version")
                    ):
                        # The page itself was read, so its Completed flag,
                        # screenshots, links and edit metadata are still valid
                        # even though no version could be extracted.
                        page_result = {
                            **page_result,
                            "success": False,
                            "code": "extract_failed",
                            "error": "Could not extract a version from thread",
                            "version": "",
                        }
                    by_id[thread_id] = page_result
                result = page_result
            results[url] = result
            if callback is not None and callback(url, result) is False:
                break
        return results

    def get_thread_version(
        self, url: str, include_metadata: bool = False
    ) -> ScraperResult:
        result = self.check_versions([url])[url]
        if include_metadata and result.get("success"):
            # A page fallback already has metadata; do not request it twice.
            if "thread_main_post_checked_at" not in result:
                metadata = self.get_thread_metadata(url)
                if not metadata.get("success"):
                    return metadata
                result = {**metadata, **result}
        return result

    def get_thread_metadata(self, url: str) -> ScraperResult:
        thread_id = extract_thread_id(url)
        if not thread_id:
            return self._error("invalid_url", "A valid F95Zone thread URL is required")
        return self._get_page(thread_id)
