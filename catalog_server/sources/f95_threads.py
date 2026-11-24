# SPDX-License-Identifier: GPL-3.0-or-later
"""Details from the first post of F95 threads: overview, original title,
aliases, language (as written and as a list of names) and the developer's
external IDs with their links.

Threads whose feed `ts` is newer than their stored details are read first,
newest first. Spare budget then fills in threads that were never read, most
viewed first, along with older details needing a count backfill.
Only logged-in pages are used: F95 hides links from guests.
"""

import logging
import re
from html.parser import HTMLParser
from typing import override

from core.f95_rating import extract_f95_review_count

from ..net import RateLimited
from .base import SourceContext
from .post_ids import SECTION_LABELS, external_ids, parse_languages

log = logging.getLogger(__name__)

# Run reports look for this text to flag a lost F95 login.
NO_LOGIN = "F95 sees no login; check the cookie with check-f95-login"

THREAD_URL = "https://f95zone.to/threads/{thread_id}/"
# Statuses kept for a thread page that does not exist (any more) or is closed to us.
MISSING_STATUSES = (403, 404)
OVERVIEW_MAX_CHARS = 10_000
QUEUE_CHUNK = 50

_LOGGED_IN_RE = re.compile(r'<html[^>]*\sdata-logged-in="(true|false)"')
_FIRST_POST_RE = re.compile(r"<article\b[^>]*\bmessage--post\b[^>]*>", re.S)
_LABEL_RE = re.compile(r"^([A-Za-z][A-Za-z ]{0,30}?)\s*:\s*(.*)$")

_ORIGINAL_TITLE_LABELS = frozenset(
    {"original title", "original name", "japanese title", "native title"}
)
_ALIAS_LABELS = frozenset(
    {
        "aliases",
        "alias",
        "also known as",
        "aka",
        "alternative title",
        "alternative titles",
    }
)
_LANGUAGE_LABELS = frozenset({"language", "languages"})
# Lines whose links belong to the game or its developer, not to a translator.
_ID_LINK_LABELS = frozenset({"developer", "developers", "publisher", "store", "stores"})
_ALIAS_SPLIT_RE = re.compile(r"\s*(?:,|;|\||\s/\s)\s*")

_SKIP_TAGS = frozenset({"noscript", "script", "style", "button", "iframe", "template"})
_BLOCK_TAGS = frozenset(
    {"div", "p", "li", "ul", "ol", "blockquote", "table", "tr", "h1", "h2", "h3", "h4"}
)


class _PostText(HTMLParser):
    """Text lines of one post, with the links found on each line."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.line: int = 0
        self.links: list[tuple[int, str]] = []
        self._skip: int = 0

    def _newline(self) -> None:
        self.parts.append("\n")
        self.line += 1

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif self._skip:
            return
        elif tag == "br" or tag in _BLOCK_TAGS:
            self._newline()
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append((self.line, href))

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and not self._skip:
            self._newline()

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif not self._skip and tag in _BLOCK_TAGS:
            self._newline()

    @override
    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.replace("​", "").replace("\xa0", " ")
            # Source line breaks are not post line breaks; <br> makes those.
            self.parts.append(re.sub(r"\s+", " ", text))


def first_post_html(page: str) -> str:
    match = _FIRST_POST_RE.search(page)
    if match is None:
        raise ValueError("F95 thread page has no post")
    start = page.find('<div class="bbWrapper">', match.end())
    end = page.find("</article>", match.end())
    if start < 0 or end < 0 or start > end:
        raise ValueError("F95 thread page has no first post body")
    return page[start:end]


def _label(line: str) -> tuple[str, str] | None:
    match = _LABEL_RE.match(line)
    if match is None:
        return None
    return match.group(1).strip().lower(), match.group(2).strip()


def _tidy(lines: list[str]) -> str:
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(text) > OVERVIEW_MAX_CHARS:
        text = text[:OVERVIEW_MAX_CHARS].rstrip() + "…"
    return text


def parse_first_post(post_html: str) -> dict[str, object]:
    parser = _PostText()
    parser.feed(post_html)
    parser.close()
    lines = [line.strip() for line in "".join(parser.parts).split("\n")]
    links: dict[int, list[str]] = {}
    for line, href in parser.links:
        links.setdefault(line, []).append(href)

    overview: list[str] = []
    in_overview = False
    original_title: str | None = None
    aliases: list[str] = []
    language: str | None = None
    id_urls: list[str] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        labelled = _label(line)
        name, value = labelled if labelled else ("", "")
        if in_overview:
            if name not in SECTION_LABELS:
                overview.append(line)
                continue
            in_overview = False
        # The first of each label counts; later ones are in changelogs or notes.
        if not name or name in seen:
            continue
        seen.add(name)
        if name == "overview":
            in_overview = True
            overview.append(value)
        elif name in _ORIGINAL_TITLE_LABELS and value and original_title is None:
            original_title = value
        elif name in _ALIAS_LABELS and value and not aliases:
            aliases = [alias for alias in _ALIAS_SPLIT_RE.split(value) if alias]
        elif name in _LANGUAGE_LABELS and value and language is None:
            language = value
        if name in _ID_LINK_LABELS:
            id_urls.extend(links.get(index, []))

    return {
        "overview": _tidy(overview) or None,
        "original_title": original_title,
        "aliases": aliases,
        "language": language,
        "languages": parse_languages(language),
        "external_ids": external_ids(id_urls),
    }


class F95ThreadsSource:
    """Reads first posts with its own pacer budget (`f95_threads`)."""

    name: str = "f95_threads"

    def _since(self, ctx: SourceContext) -> int:
        """Threads changed after this feed `ts` count as changed; set on the first run."""
        since = ctx.state.get(self.name, "since_ts")
        if isinstance(since, int):
            return since
        newest = ctx.catalog.f95_newest_ts() or 0
        ctx.state.put(self.name, "since_ts", newest)
        return newest

    def _read(self, ctx: SourceContext, thread_id: int, thread_ts: int) -> None:
        response = ctx.pacer.request(
            self.name,
            THREAD_URL.format(thread_id=thread_id),
            ok_statuses=MISSING_STATUSES,
        )
        login = _LOGGED_IN_RE.search(response.text())
        if login is None:
            # Not an F95 page at all, e.g. a Cloudflare challenge: stop, store nothing.
            raise ValueError(
                f"F95 answered HTTP {response.status} without its own page"
            )
        if login.group(1) != "true":
            # Without the login F95 hides the links; never store such a page.
            raise ValueError(NO_LOGIN)
        if response.status in MISSING_STATUSES:
            # F95's own "not found" or "no permission" page.
            details: dict[str, object] = {"status": "missing"}
        else:
            try:
                details = {
                    "status": "ok",
                    **parse_first_post(first_post_html(response.text())),
                }
                details["rating_count"] = extract_f95_review_count(response.text())
            except ValueError:
                # Kept so one odd page does not block the queue; read again when it changes.
                log.warning("f95_threads: no first post on thread %s", thread_id)
                details = {"status": "unreadable"}
        ctx.rows += ctx.catalog.upsert_f95_details(
            thread_id, thread_ts, ctx.now(), details
        )

    def incremental(self, ctx: SourceContext) -> None:
        if "f95" in ctx.pacer.paused:
            # F95 rate-limited the feed in this run; leave it alone until the next one.
            raise RateLimited(self.name, f"f95 is paused: {ctx.pacer.paused['f95']}")
        since = self._since(ctx)
        while queue := ctx.catalog.f95_details_queue(since, QUEUE_CHUNK):
            for thread_id, thread_ts in queue:
                self._read(ctx, thread_id, thread_ts)

    def sweep_due(self, ctx: SourceContext) -> bool:
        del ctx
        return False

    def sweep_step(self, ctx: SourceContext) -> bool:
        del ctx
        return True

    def reset_sweep(self, ctx: SourceContext) -> None:
        del ctx
