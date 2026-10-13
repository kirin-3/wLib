# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import re
import socket
import ssl
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from html import unescape
from http.client import HTTPResponse
from typing import Protocol, TypeAlias, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .f95zone import NON_ENGINE_PREFIXES, extract_thread_id

ScraperResultValue: TypeAlias = bool | str | list[str]
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
# if we ever add another host.
_F95_REQUEST_LOCK = threading.Lock()
_f95_last_request_finished: float | None = None


def get_request_interval_seconds() -> int:
    try:
        value = int(os.environ.get("WLIB_UPDATE_CHECK_DELAY_SECONDS", "2"))
    except ValueError:
        value = 2
    return max(1, min(value, 30))


def _f95_get(url: str) -> tuple[int, str]:
    global _f95_last_request_finished
    with _F95_REQUEST_LOCK:
        if _f95_last_request_finished is not None:
            remaining = get_request_interval_seconds() - (
                time.monotonic() - _f95_last_request_finished
            )
            if remaining > 0:
                time.sleep(remaining)
        try:
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
        except (OSError, ValueError) as exc:
            raise _F95RequestError("scraper_error", str(exc)) from exc
        finally:
            _f95_last_request_finished = time.monotonic()


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


class Scraper:
    def _extract_version_from_title(self, title: object) -> str:
        # Keep these rules aligned with extractGameInfo() in extension/content.js.
        if not isinstance(title, str):
            return "Unknown"
        title = re.sub(r"^\[[^\]]+\]\s*", "", title.strip())
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
                raw = unescape(match.group(1)).strip()
                version = raw.split()[0] if raw else ""
                if re.match(r"^\d", version):
                    return version
        text_patterns = [
            r"(?:Current\s+)?Version\s*:?\s*v?([^\s,;\n]+)",
            r"Ver(?:\.|sion)?\s*:?\s*v?([^\s,;\n]+)",
            r"Release\s*:?\s*v?(\d[\d.]*[a-zA-Z]?)",
        ]
        for pattern in text_patterns:
            match = re.search(pattern, _text(html), re.I)
            if match and re.match(r"^\d", match.group(1)):
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

    def _error(self, code: str, message: str) -> ScraperResult:
        return {"success": False, "code": code, "error": message}

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
        combined = f"{page_title}\n{page_html}"
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
        if any(
            marker in combined
            for marker in (
                "just a moment",
                "checking your browser",
                "verify you are human",
                "attention required",
                "cf-challenge",
                "challenge-platform",
            )
        ):
            return {
                "code": "blocked",
                "error": "Blocked by anti-bot challenge while loading thread",
            }
        if any(
            marker in page_title for marker in ("log in", "login", "sign in")
        ) or any(
            marker in combined
            for marker in (
                "you must be logged-in",
                "you must be logged in",
                'action="/login/login"',
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
        appended_labels = [_text(content) for _, content in _blocks(h1, "span", "label-append")]
        engine = next(
            (label for label in labels if label.casefold() not in NON_ENGINE_PREFIXES),
            "",
        )
        thread_completed = any(
            label.casefold() == "completed" for label in (*labels, *appended_labels)
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
            "cover_image": self._normalize_cover_image_url(cover),
            "thread_main_post_last_edit_at": last_edit,
            "thread_main_post_checked_at": datetime.now().isoformat(),
        }

    def _get_page(self, thread_id: str) -> ScraperResult:
        try:
            status, html = _f95_get(f"https://f95zone.to/threads/{thread_id}/")
            issue = self._classify_page_issue(status, self._page_title(html), html)
            if issue:
                return self._error(issue["code"], issue["error"])
            if status != 200:
                return self._error("scraper_error", f"F95Zone returned HTTP {status}")
            return self._extract_page(html)
        except _F95RequestError as exc:
            return self._error(exc.code, str(exc))
        except Exception as exc:
            return self._error("scraper_error", str(exc))

    def _check_chunk(self, ids: list[str]) -> dict[str, ScraperResult | None]:
        versions: dict[str, object] = {}
        try:
            status, text = _f95_get(
                f"https://f95zone.to/sam/checker.php?threads={','.join(ids)}"
            )
            issue = self._classify_page_issue(status, self._page_title(text), text)
            if issue and issue["code"] == "blocked":
                return {
                    thread_id: self._error(issue["code"], issue["error"])
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
                        page_result = self._error(
                            "extract_failed", "Could not extract a version from thread"
                        )
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
