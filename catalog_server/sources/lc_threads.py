# SPDX-License-Identifier: GPL-3.0-or-later
"""Details from the first post of LewdCorner threads: the developer, the
developer links and their IDs, the release date, the languages, the OS list
and the overview.

LC keeps most of these in XenForo custom fields (`dl[data-field]`) above the
post body, so only the overview comes from free text. The queue is the same
as F95's: threads changed since their details were read first, newest first,
then threads never read, most viewed first. Pages are read as a guest, with no
login cookie. A page must be LC's own guest page (`data-logged-in="false"`);
anything else, such as a Cloudflare challenge or a page with a login, stops
LC for the run and stores nothing.

No feed gives a Plus (`Premium`) thread's version and tags to the builder, so
for those threads this step also stores the page's Version field and tags.
"""

import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import cast, override

from ..net import RateLimited
from .base import SourceContext
from .lc import lc_pause
from .post_ids import SECTION_LABELS, external_ids, parse_languages

log = logging.getLogger(__name__)

THREAD_URL = "https://lewdcorner.com/threads/{thread_id}/"
# LC answers "no permission" with 403 and its own page; an unknown thread
# gets nginx's plain 404 page, which has no LC markup.
MISSING_STATUSES = (403, 404)
OVERVIEW_MAX_CHARS = 10_000
QUEUE_CHUNK = 50

_LC_PAGE_RE = re.compile(r'<html[^>]*\sdata-logged-in="(?:true|false)"')
_GUEST_PAGE_RE = re.compile(r'<html[^>]*\sdata-logged-in="false"')
_TAG_LIST_RE = re.compile(r'<dl\b[^>]*\bclass="tagList\b.*?</dl>', re.S)
_TAG_SLUG_RE = re.compile(r'\bclass="tagItem tagItem--tag_([^"\s]+)')
_FIRST_POST_RE = re.compile(r"<article\b[^>]*\bmessage--post\b[^>]*>", re.S)
_FIELD_RE = re.compile(r'<dl\b[^>]*\bdata-field="([^"]+)"[^>]*>(.*?)</dl>', re.S)
_DD_RE = re.compile(r"<dd\b[^>]*>(.*)</dd>", re.S)
_LI_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S)
_BODY_RE = re.compile(r"<article\b[^>]*\bmessage-body\b[^>]*>", re.S)
_HREF_RE = re.compile(r'<a\b[^>]*\shref="([^"]+)"', re.S)

_SKIP_TAGS = frozenset({"noscript", "script", "style", "button", "iframe", "template"})
_BLOCK_TAGS = frozenset(
    {"div", "p", "li", "ul", "ol", "blockquote", "table", "tr", "h1", "h2", "h3", "h4"}
)


class _Text(HTMLParser):
    """Text lines of a fragment. Spoiler blocks are left out: on LC they hold
    the genre, changelog and notes, not the overview."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip: int = 0
        # Depth of open <div>s inside a skipped spoiler, so its end is found.
        self._spoiler_divs: int = 0

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._spoiler_divs:
            if tag == "div":
                self._spoiler_divs += 1
            return
        if tag == "div" and "bbCodeSpoiler" in (dict(attrs).get("class") or "").split():
            self._spoiler_divs = 1
            self.parts.append("\n")
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif not self._skip and (tag == "br" or tag in _BLOCK_TAGS):
            self.parts.append("\n")

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and not self._skip and not self._spoiler_divs:
            self.parts.append("\n")

    @override
    def handle_endtag(self, tag: str) -> None:
        if self._spoiler_divs:
            if tag == "div":
                self._spoiler_divs -= 1
            return
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif not self._skip and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    @override
    def handle_data(self, data: str) -> None:
        if not self._skip and not self._spoiler_divs:
            text = data.replace("​", "").replace("\xa0", " ")
            self.parts.append(re.sub(r"\s+", " ", text))


def _lines(fragment: str) -> list[str]:
    parser = _Text()
    parser.feed(fragment)
    parser.close()
    return [line.strip() for line in "".join(parser.parts).split("\n")]


def _text(fragment: str) -> str:
    return " ".join(line for line in _lines(fragment) if line)


def first_post_html(page: str) -> str:
    match = _FIRST_POST_RE.search(page)
    if match is None:
        raise ValueError("LC thread page has no post")
    return page[match.end() :]


def _fields(post: str) -> dict[str, str]:
    """`data-field` name to the inner HTML of its <dd>; the first of each counts."""
    start = post.find("message-fields--before")
    body = _BODY_RE.search(post)
    if start < 0:
        return {}
    end = body.start() if body is not None and body.start() > start else len(post)
    found: dict[str, str] = {}
    for name, block in cast(list[tuple[str, str]], _FIELD_RE.findall(post[start:end])):
        dd = _DD_RE.search(block)
        if dd is not None and name not in found:
            found[name] = dd.group(1)
    return found


def _release_date(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def _section_label(line: str) -> str:
    return line.rstrip(" :").strip().lower()


def _overview(post: str) -> str | None:
    body = _BODY_RE.search(post)
    if body is None:
        return None
    end = post.find("</article>", body.end())
    lines = _lines(post[body.end() : end if end >= 0 else len(post)])
    # Text after an "Overview:" label, or the whole text when there is none.
    start = 0
    for index, line in enumerate(lines):
        if _section_label(line.split(":", 1)[0]) == "overview" and ":" in line:
            lines[index] = line.split(":", 1)[1].strip()
            start = index
            break
    kept: list[str] = []
    for line in lines[start:]:
        label = (
            _section_label(line.split(":", 1)[0])
            if ":" in line
            else _section_label(line)
        )
        if label in SECTION_LABELS:
            break
        kept.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    if len(text) > OVERVIEW_MAX_CHARS:
        text = text[:OVERVIEW_MAX_CHARS].rstrip() + "…"
    return text or None


def page_tags(page: str) -> list[str]:
    """The thread's tag slugs, the feed's spelling (`tagItem--tag_<slug>`)."""
    found = _TAG_LIST_RE.search(page)
    if found is None:
        return []
    tags: list[str] = []
    for slug in cast(list[str], _TAG_SLUG_RE.findall(found.group(0))):
        if slug not in tags:
            tags.append(slug)
    return tags


def page_version(post: str) -> str | None:
    """The first post's Version field."""
    field = _fields(post).get("version")
    return (_text(field) or None) if field is not None else None


def parse_first_post(post: str) -> dict[str, object]:
    fields = _fields(post)
    developer = _text(fields["Developer"]) if "Developer" in fields else ""
    release_text = (
        _text(fields["dategamerelease"]) if "dategamerelease" in fields else ""
    )
    language = _text(fields["Language"]) if "Language" in fields else ""
    os_list = [
        _text(item) for item in cast(list[str], _LI_RE.findall(fields.get("OS", "")))
    ]
    links = [
        href.replace("&amp;", "&")
        for href in cast(list[str], _HREF_RE.findall(fields.get("donations", "")))
    ]
    return {
        "overview": _overview(post),
        "developer": developer or None,
        "release_text": release_text or None,
        "released_at": _release_date(release_text),
        "language": language or None,
        "languages": parse_languages(language),
        "os": [name for name in os_list if name],
        "external_ids": external_ids(links),
    }


class LcThreadsSource:
    """Reads LC first posts with its own pacer budget (`lc_threads`)."""

    name: str = "lc_threads"

    def _since(self, ctx: SourceContext) -> int:
        """Threads changed after this feed `ts` count as changed; set on the first run."""
        since = ctx.state.get(self.name, "since_ts")
        if isinstance(since, int):
            return since
        newest = ctx.catalog.lc_newest_ts() or 0
        ctx.state.put(self.name, "since_ts", newest)
        return newest

    def _stop(self, ctx: SourceContext, reason: str) -> ValueError:
        """Pause LC for the run (no guest step goes on) and return the error to raise."""
        ctx.pacer.paused[self.name] = reason
        return ValueError(reason)

    def _read(
        self, ctx: SourceContext, thread_id: int, thread_ts: int, premium: bool
    ) -> None:
        response = ctx.pacer.request(
            self.name,
            THREAD_URL.format(thread_id=thread_id),
            ok_statuses=MISSING_STATUSES,
        )
        html = response.text()
        post: str | None = None
        if response.status == 404:
            details: dict[str, object] = {"status": "missing"}
        elif _LC_PAGE_RE.search(html) is None:
            # Not an LC page, e.g. a Cloudflare challenge: stop, store nothing.
            raise self._stop(
                ctx, f"LC answered HTTP {response.status} without its own page"
            )
        elif _GUEST_PAGE_RE.search(html) is None:
            # A login on a guest request means a cookie was sent; store nothing.
            raise self._stop(ctx, "LC answered a guest request with a logged-in page")
        elif response.status in MISSING_STATUSES:
            # LC's "no permission" page, e.g. a Plus thread for a Standard account.
            details = {"status": "missing"}
        else:
            try:
                post = first_post_html(html)
                details = {"status": "ok", **parse_first_post(post)}
            except ValueError:
                # Kept so one odd page does not block the queue; read again when it changes.
                log.warning("lc_threads: no first post on thread %s", thread_id)
                details = {"status": "unreadable"}
        if premium and post is not None:
            ctx.rows += ctx.catalog.upsert_lc_premium_details(
                thread_id,
                thread_ts,
                ctx.now(),
                details,
                page_version(post),
                page_tags(html),
            )
        else:
            ctx.rows += ctx.catalog.upsert_lc_details(
                thread_id, thread_ts, ctx.now(), details
            )

    def incremental(self, ctx: SourceContext) -> None:
        if (paused := lc_pause(ctx.pacer)) is not None:
            # An LC step was rate-limited or challenged in this run; wait for the next.
            raise RateLimited(self.name, paused)
        since = self._since(ctx)
        while queue := ctx.catalog.lc_details_queue(since, QUEUE_CHUNK):
            premium = ctx.catalog.lc_premium_ids(thread_id for thread_id, _ts in queue)
            for thread_id, thread_ts in queue:
                self._read(ctx, thread_id, thread_ts, thread_id in premium)

    def sweep_due(self, ctx: SourceContext) -> bool:
        del ctx
        return False

    def sweep_step(self, ctx: SourceContext) -> bool:
        del ctx
        return True

    def reset_sweep(self, ctx: SourceContext) -> None:
        del ctx
