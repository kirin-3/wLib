# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse ephemeral download mirrors from a signed-in forum thread."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import SplitResult, urlsplit

from core import lewdcorner
from core.versions import normalize_version
from typing import TypedDict, final, override


# Hosts with an in-app download adapter. Their domains in HOST_DOMAINS must
# match only what the adapters accept, or the UI would offer downloads that
# cannot start.
IN_APP_HOSTS: frozenset[str] = frozenset({"pixeldrain", "mega", "buzzheavier"})

# Host id -> (display name, domains it serves from). Order matters only for readability.
HOST_DOMAINS: dict[str, tuple[str, tuple[str, ...]]] = {
    "pixeldrain": ("Pixeldrain", ("pixeldrain.com", "pixeldrain.net", "pixeldra.in")),
    "mega": ("MEGA", ("mega.nz", "mega.co.nz")),
    "buzzheavier": ("BuzzHeavier", ("buzzheavier.com", "bzzhr.to")),
    "gofile": ("GoFile", ("gofile.io",)),
    "datanodes": ("DataNodes", ("datanodes.to",)),
    "mixdrop": (
        "Mixdrop",
        (
            "mixdrop.ag",
            "mixdrop.co",
            "mixdrop.to",
            "mixdrop.ps",
            "mixdrop.sx",
            "mxdrop.sx",
            "mixdrop.ch",
            "mixdrop.club",
            "mixdrop.gl",
            "mixdrop.is",
            "mixdrop.my",
            "mixdrop.nu",
            "mixdrop.sb",
            "mixdrop.si",
            "mixdrop.vc",
            "mixdrop21.net",
            "mxdrop.to",
        ),
    ),
    "workupload": ("Workupload", ("workupload.com",)),
    "mediafire": ("MediaFire", ("mediafire.com",)),
    "vikingfile": ("VikingFile", ("vikingfile.com", "vik1ngfile.site")),
    "akirabox": ("AkiraBox", ("akirabox.com", "akirabox.to")),
    "1fichier": (
        "1fichier",
        (
            "1fichier.com",
            "alterupload.com",
            "cjoint.net",
            "desfichiers.com",
            "desfichiers.net",
            "dfichiers.com",
            "dl4free.com",
            "megadl.fr",
            "mesfichiers.net",
            "mesfichiers.org",
            "piecejointe.net",
            "pjointe.com",
            "tenvoi.com",
        ),
    ),
    "krakenfiles": ("KrakenFiles", ("krakenfiles.com",)),
    "uploadhaven": ("UploadHaven", ("uploadhaven.com",)),
    "filesfm": ("Files.fm", ("files.fm",)),
    "catbox": ("Catbox", ("catbox.moe",)),
    "dropbox": ("Dropbox", ("dropbox.com", "dropboxusercontent.com")),
    "onedrive": ("OneDrive", ("onedrive.live.com", "1drv.ms")),
    "gdrive": ("Google Drive", ("drive.google.com", "drive.usercontent.google.com")),
    "nopy": ("Nopy", ("nopy.to",)),
    "racaty": ("Racaty", ("racaty.io", "racaty.net")),
    "bowfile": ("Bowfile", ("bowfile.com",)),
}


def _norm_host_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _build_host_words() -> dict[str, str]:
    words: dict[str, str] = {}
    for host_id, (display_name, domains) in HOST_DOMAINS.items():
        for candidate in (host_id, display_name):
            norm = _norm_host_token(candidate)
            if norm:
                _ = words.setdefault(norm, host_id)
        for domain in domains:
            first = _norm_host_token(domain.split(".", 1)[0])
            # Skip generic first labels like "files" (from files.fm); "Files.fm"
            # still matches through the normalized display name "filesfm".
            if first and first not in ("files",):
                _ = words.setdefault(first, host_id)
    return words


# Normalized words a poster uses as link text for a host ("MEGA", "BZZHR", "1fichier").
HOST_WORDS: dict[str, str] = _build_host_words()
DENIED_HOSTS = (
    "f95zone.com",
    "patreon.com",
    "subscribestar.com",
    "subscribestar.adult",
    "boosty.to",
    "ko-fi.com",
    "buymeacoffee.com",
    "itch.io",
    "steampowered.com",
    "steamcommunity.com",
    "gog.com",
    "discord.com",
    "discord.gg",
    "discordapp.com",
    "twitter.com",
    "x.com",
    "bsky.app",
    "facebook.com",
    "instagram.com",
    "reddit.com",
    "youtube.com",
    "youtu.be",
    "twitch.tv",
    "tumblr.com",
    "pixiv.net",
    "deviantart.com",
    "imgur.com",
    "postimg.cc",
    "ibb.co",
    "wikipedia.org",
)
_DOWNLOAD_HEADING = re.compile(r"downloads?\b(?:\s*links?\b)?[\s:]*(.*)", re.I)
_DIVIDER = re.compile(r"(?:extras?|bonus(?:es)?|translations?|mods?)", re.I)
_PART = re.compile(r"part\s*(\d{1,2})\b", re.I)
_SPOILER_PREFIX = re.compile(r"^\s*spoiler\s*:?\s*", re.I)
# A file size ("Win (1.5 GB)") is not a version.
_VERSION_IN_TEXT = re.compile(
    r"\bv(?:er(?:sion)?)?\.?\s*(\d+(?:\.\d+)*[a-z]?)\b|\b(\d+(?:\.\d+)+[a-z]?)\b(?!\s*[KMGT]i?B\b)",
    re.I,
)
_DATE_IN_TEXT = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b"
)
_OLD_WORDS = re.compile(r"\b(?:old|older|previous|prior|legacy|archived?|past)\b", re.I)
# A bare "Pass"/"PW" counts only at the start of a line, so "Season Pass:" or
# "Battle pass -" is not read as a password label.
_PASSWORD_LABEL = re.compile(
    r"(?:"
    + r"\b(?:(?:archive|rar|zip|7z|extract(?:ion)?|file)\s+)?(?:password|passwd|pwd)"
    + r"|\b(?:archive|rar|zip|7z|extract(?:ion)?|file)\s+(?:pass|pw)"
    + r"|^[\W_]*(?:pass|pw)"
    + r"|\bпароль(?:\s+(?:на|от|к|для)\s+(?:архив\w*|файл\w*))?"
    + r")\s*(?:[:=]|[–—]|-\s+|\s+-\s*)(.*)$",
    re.I,
)
# A "Win: MEGA" row after a bare "Password:" line is not its value.
_PASSWORD_ROW_LABEL = re.compile(r"^[^\s:][^:]{0,39}:\s+\S")
_PASSWORD_CHROME = re.compile(
    r"^(?:spoiler\b.*|code\s*:?|click\s+to\s+expand\b.*)$",
    re.I,
)
_PASSWORD_DENY = frozenset(
    {
        "none",
        "no",
        "n/a",
        "na",
        "nil",
        "null",
        "empty",
        "nothing",
        "no password",
        "no pass",
        "no pwd",
        "not required",
        "not needed",
        "unprotected",
        "see below",
        "see above",
        "below",
        "above",
        "in spoiler",
        "spoiler",
        "check spoiler",
        "check below",
        "in thread",
        "in comments",
        "in description",
        "in post",
        "tbd",
        "unknown",
        "нет",
        "не требуется",
        "не нужен",
        "без пароля",
    }
)
_PASSWORD_DENY_PREFIX = re.compile(
    r"^(?:none|no\s+(?:password|pass|pwd)|not\s+(?:required|needed)|n/?a|"
    + r"see\s+(?:below|above)|in\s+(?:the\s+)?(?:spoiler|thread|comments|description|post)|"
    + r"check\s+(?:the\s+)?(?:spoiler|below)|без\s+пароля|не\s+(?:требуется|нужен))\b",
    re.I,
)
_QUOTE_PAIRS = (
    ('"', '"'),
    ("'", "'"),
    ("`", "`"),
    ("“", "”"),
    ("«", "»"),
    ("[", "]"),
    ("(", ")"),
)
_MAX_PASSWORD_LEN = 128


def _clean_password_value(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if re.match(r"^https?://", text, re.I) or text.lower().startswith("www."):
        return ""
    lowered = " ".join(text.lower().split()).strip(".,;:!?\"'`()[]")
    if not lowered or lowered in _PASSWORD_DENY or _PASSWORD_DENY_PREFIX.match(lowered):
        return ""
    quoted = False
    for opener, closer in _QUOTE_PAIRS:
        if text.startswith(opener):
            end = text.find(closer, len(opener))
            if end > len(opener):
                text = text[len(opener) : end].strip()
                quoted = True
                break
    if not quoted:
        text = text.split(None, 1)[0].rstrip(".,;:)]}'\"")
    if not text or len(text) > _MAX_PASSWORD_LEN:
        return ""
    if re.match(r"^https?://", text, re.I) or text.lower().startswith("www."):
        return ""
    if text.lower() in _PASSWORD_DENY:
        return ""
    return text


def find_archive_password(lines: Sequence[str], start: int | None = None) -> str:
    """Find an archive password in plain-text first-post lines.

    Starts at ``start`` (or at the first DOWNLOAD heading when omitted) and
    wraps to the start of the post.
    """
    if not lines:
        return ""
    if start is None:
        start = next(
            (
                i
                for i, line in enumerate(lines)
                if _DOWNLOAD_HEADING.fullmatch(line.strip(" :\t"))
            ),
            0,
        )
    else:
        start = max(0, min(start, len(lines)))
    order = [*range(start, len(lines)), *range(0, start)]
    for index in order:
        match = _PASSWORD_LABEL.search(lines[index])
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            value = _clean_password_value(inline)
            if value:
                return value
            continue
        # The value is the next line that is not spoiler or code chrome; at
        # most three chrome lines are skipped.
        chrome = 0
        for follow in lines[index + 1 :]:
            candidate = follow.strip()
            if not candidate:
                continue
            if _PASSWORD_CHROME.match(candidate):
                chrome += 1
                if chrome > 3:
                    break
                continue
            if not (
                _PASSWORD_LABEL.search(candidate)
                or _PASSWORD_ROW_LABEL.match(candidate)
                or _DOWNLOAD_HEADING.fullmatch(candidate.strip(" :\t"))
            ):
                value = _clean_password_value(candidate)
                if value:
                    return value
            break
    return ""


def _versions_in(text: str) -> set[str]:
    return {
        normalize_version(m.group(1) or m.group(2))
        for m in _VERSION_IN_TEXT.finditer(text)
    }


def spoiler_title_kind(title: str, thread_version: str) -> str:
    """Return how a spoiler title scopes its content: download, part, old or ignore.

    A title names an older version when it has a date, an old word, or a
    version that the thread's version does not have.
    """
    line = title.strip().rstrip(" :")
    if not line:
        return "ignore"
    if _DOWNLOAD_HEADING.fullmatch(line):
        return "download"
    if _PART.fullmatch(line):
        return "part"
    if _DATE_IN_TEXT.search(line) or _OLD_WORDS.search(line):
        return "old"
    versions = _versions_in(line)
    if versions:
        current = _versions_in(thread_version) or {normalize_version(thread_version)}
        if versions - current:
            return "old"
    return "ignore"


def classify_build(label: str) -> tuple[str, str]:
    """Return the platform and kind described by a build heading."""
    text = label.lower()

    def has(pattern: str) -> bool:
        return bool(re.search(pattern, text))

    win = has(r"\b(win(?:dows)?|pc)\b")
    linux = has(r"\b(linux|lin)\b")
    if has(r"\b(android|apk)\b"):
        platform = "android"
    elif has(r"\b(ios|iphone|ipad|ipa)\b"):
        platform = "ios"
    elif win and linux:
        platform = "windows/linux"
    elif linux:
        platform = "linux"
    elif win:
        platform = "windows"
    elif has(r"\b(mac(?:os)?|osx)\b"):
        platform = "mac"
    elif has(r"\b(all|multi[ -]?platform)\b"):
        platform = "all"
    else:
        platform = "unknown"
    if has(r"\b(patch|hotfix|incremental|update[ -]?only)\b"):
        kind = "patch"
    elif has(r"\b(extra|extras|bonus|walkthrough|cheat)\b"):
        kind = "extra"
    else:
        kind = "full"
    return platform, kind


def _host_of_domain(domain: str) -> str:
    domain = domain.lower().removeprefix("www.")
    for name, (_, domains) in HOST_DOMAINS.items():
        if any(domain == d or domain.endswith("." + d) for d in domains):
            return name
    return ""


def _host_of_f95_favicon(style: str) -> str:
    """XenForo's has-favicon style names the destination: ``.../ip3/<host>.ico``."""
    match = re.search(r"ip3/([a-z0-9.-]+?)\.ico", style, re.I)
    if not match:
        return ""
    domain = match.group(1).lower().removeprefix("www.")
    return _host_of_domain(domain) or domain


def _host_of_lc_favicon(style: str) -> str:
    match = re.search(r"s2/favicons\?domain=([a-z0-9.-]+)", style, re.I)
    if not match:
        return ""
    domain = match.group(1).lower().removeprefix("www.")
    return _host_of_domain(domain) or domain


def _title_version(html: str) -> str:
    title = re.search(
        r"""<h1\b[^>]*class=["'][^"']*p-title-value[^"']*["'][^>]*>(.*?)</h1>""",
        html,
        re.I | re.S,
    )
    version = (
        re.search(
            r"\[((?:v(?:ersion)?\s*)?\d[^\]]*|(?:chapter|ch\.|episode|ep\.|part)\s*\d[^\]]*)\]",
            re.sub(r"<[^>]*>", "", title.group(1)),
            re.I,
        )
        if title
        else None
    )
    return version.group(1) if version else "Unknown"


def _lc_version(html: str) -> str:
    try:
        return lewdcorner.parse_thread_page(html, 200).version or _title_version(html)
    except lewdcorner.LcUpdateError:
        return _title_version(html)


@dataclass(frozen=True)
class _SiteProfile:
    domain: str
    masked: Callable[[SplitResult], bool]
    keep_own_link: Callable[[SplitResult], bool]
    favicon_host: Callable[[str], str]
    version: Callable[[str], str]


def _f95_masked(url: SplitResult) -> bool:
    return bool(re.match(r"/dl/\d+/", url.path))


def _lc_masked(url: SplitResult) -> bool:
    return (
        url.scheme == "https"
        and url.netloc == "lewdcorner.com"
        and url.path == "/masked/out"
        and bool(url.query)
        and not url.fragment
    )


def _f95_attachment(url: SplitResult) -> bool:
    return url.hostname == "attachments.f95zone.to" or url.path.startswith(
        "/attachments/"
    )


_PROFILES = {
    "f95": _SiteProfile(
        "f95zone.to", _f95_masked, _f95_attachment, _host_of_f95_favicon, _title_version
    ),
    "lc": _SiteProfile(
        "lewdcorner.com", _lc_masked, lambda _: False, _host_of_lc_favicon, _lc_version
    ),
}


def masked_download_site(url: str) -> lewdcorner.Site | None:
    if url.startswith("https://f95zone.to/dl/"):
        return "f95"
    try:
        return "lc" if _lc_masked(urlsplit(url)) else None
    except ValueError:
        return None


def _host_of_label(text: str) -> str:
    for token in re.split(r"[\s/|,+:\-()\[\]]+", text.lower()):
        if not token:
            continue
        norm = _norm_host_token(token)
        if norm in HOST_WORDS:
            return HOST_WORDS[norm]
        if "." in token:
            domain_host = _host_of_domain(token.strip("."))
            if domain_host:
                return domain_host
    return HOST_WORDS.get(_norm_host_token(text), "")


class Mirror(TypedDict):
    url: str
    host: str
    label: str
    part: str
    split: bool


class Build(TypedDict):
    label: str
    section: str
    platform: str
    kind: str
    mirrors: list[Mirror]


class ThreadDownloads(TypedDict):
    version: str
    signed_in: bool
    builds: list[Build]
    archive_password: str


_VOID_TAGS = frozenset(
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


_LINE_BREAK_START_TAGS = frozenset({"div", "p", "li", "pre"})
_LINE_BREAK_END_TAGS = frozenset({"div", "p", "li", "pre", "code", "button"})


@final
class _Elements(HTMLParser):
    """Emit first-post bold text, links, text and line breaks in document order.

    A link inside a bold is emitted on its own, after the bold text before it,
    so ``<b>Win: <a>MEGA</a></b>`` reads as the heading "Win" followed by a link.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.article = 0
        self.wrapper = 0
        self.seen = False
        self.hidden_tag = ""
        self.hidden_depth = 0
        self.bold = 0
        self.bold_parts: list[str] = []
        self.anchor: dict[str, str] | None = None
        self.anchor_parts: list[str] = []
        self.spoilers: list[int] = []
        self.button = 0
        self.button_parts: list[str] = []
        self.pre = 0
        self._lines: list[str] = []
        self._line_parts: list[str] = []
        self.events: list[tuple[str, str, dict[str, str]]] = []

    def _break_line(self) -> None:
        line = " ".join("".join(self._line_parts).split())
        self._line_parts = []
        if line:
            self._lines.append(line)

    @property
    def lines(self) -> list[str]:
        tail = " ".join("".join(self._line_parts).split())
        return [*self._lines, tail] if tail else list(self._lines)

    def _flush_bold(self) -> None:
        text = "".join(self.bold_parts).strip()
        self.bold_parts = []
        if text:
            self.events.append(("b", text, {}))

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {
            k: v or ""
            for k, v in attrs
            if k in ("class", "href", "style", "aria-hidden", "tabindex")
        }
        classes = a.get("class", "").split()
        is_hidden = (
            "u-hidden" in classes or a.get("aria-hidden", "").strip().lower() == "true"
        )
        if (
            tag == "article"
            and not self.seen
            and ("message-threadStarterPost" in classes or "message--post" in classes)
        ):
            self.article = 1
            self.seen = True
        elif self.article and tag == "article":
            self.article += 1
        if self.article and tag == "div":
            if self.wrapper:
                self.wrapper += 1
                # A spoiler inside a hidden region is not read, like the rest of that region.
                if (
                    "bbCodeSpoiler" in classes
                    and not self.hidden_depth
                    and not is_hidden
                ):
                    self.spoilers.append(self.wrapper)
            elif "bbWrapper" in classes:
                self.wrapper = 1
        if not self.wrapper:
            return
        if tag == "button" and (
            self.button
            or (
                "bbCodeSpoiler-button" in classes
                and not self.hidden_depth
                and not is_hidden
            )
        ):
            # The button's own text ("Spoiler: <title>") is not post text.
            if not self.button:
                self.button_parts = []
            self.button += 1
            return
        if self.button:
            return
        if self.hidden_depth:
            if tag == self.hidden_tag:
                self.hidden_depth += 1
            return
        if is_hidden:
            if tag not in _VOID_TAGS:
                self.hidden_tag = tag
                self.hidden_depth = 1
            return
        if tag in _LINE_BREAK_START_TAGS:
            self._break_line()
        if tag == "pre":
            self.pre += 1
        if "messageHide--link" in classes:
            self.events.append(("hidden_link", "", {}))
        if tag in ("b", "strong"):
            if self.anchor is None:
                self.bold += 1
        elif tag == "a":
            if self.anchor is None:
                if self.bold:
                    self._flush_bold()
                self.anchor, self.anchor_parts = a, []
        elif tag == "br":
            self._break_line()
            if self.anchor is not None:
                self.anchor_parts.append(" ")
            elif self.bold:
                self.bold_parts.append("\n")
            else:
                self.events.append(("break", "", {}))
        elif tag == "img" and self.anchor is not None:
            self.anchor["_image"] = "1"

    @override
    def handle_endtag(self, tag: str) -> None:
        if self.wrapper and self.button:
            if tag == "button":
                self.button -= 1
                if not self.button:
                    self._break_line()
                    title = _SPOILER_PREFIX.sub(
                        "", " ".join("".join(self.button_parts).split())
                    )
                    self.events.append(("spoiler_open", title, {}))
            return
        if self.wrapper:
            if self.hidden_depth:
                if tag == self.hidden_tag:
                    self.hidden_depth -= 1
                    if not self.hidden_depth:
                        self.hidden_tag = ""
            else:
                if tag == "pre" and self.pre:
                    self.pre -= 1
                if tag in _LINE_BREAK_END_TAGS:
                    self._break_line()
                if tag == "a" and self.anchor is not None:
                    if self.anchor.get("tabindex", "").strip() != "-1":
                        self.events.append(
                            ("a", "".join(self.anchor_parts).strip(), self.anchor)
                        )
                    self.anchor = None
                elif tag in ("b", "strong") and self.bold and self.anchor is None:
                    self.bold -= 1
                    if not self.bold:
                        self._flush_bold()
            if tag == "div":
                if self.spoilers and self.spoilers[-1] == self.wrapper:
                    _ = self.spoilers.pop()
                    if self.bold:
                        self._flush_bold()
                    self.events.append(("spoiler_close", "", {}))
                self.wrapper -= 1
        if tag == "article" and self.article:
            self.article -= 1

    @override
    def handle_data(self, data: str) -> None:
        if self.hidden_depth:
            return
        if self.button:
            self.button_parts.append(data)
            return
        if self.wrapper and (
            self.anchor is None or self.anchor.get("tabindex", "").strip() != "-1"
        ):
            if self.pre:
                for part in data.splitlines(keepends=True):
                    if part.endswith(("\n", "\r")):
                        self._line_parts.append(part.rstrip("\r\n"))
                        self._break_line()
                    else:
                        self._line_parts.append(part)
            else:
                self._line_parts.append(data)
        if self.anchor is not None:
            self.anchor_parts.append(data)
        elif self.bold:
            self.bold_parts.append(data)
        elif self.wrapper and data.strip():
            self.events.append(("text", data.strip(), {}))


def _heading_lines(value: str) -> list[str]:
    lines = (line.strip(" :\t") for line in value.splitlines())
    return [line for line in lines if re.search(r"[A-Za-z0-9]", line)]


def parse_download_links(
    html: str,
    site: lewdcorner.Site = "f95",
    *,
    page_version: str | None = None,
) -> ThreadDownloads:
    """Return first-post builds and ephemeral URLs; callers must not persist them.

    ``page_version`` is the version an earlier parse of the same page read, so
    the page is not parsed again for it; an empty one falls back to the title.
    """
    profile = _PROFILES[site]
    parser = _Elements()
    parser.feed(html)
    events = parser.events
    signed = re.search(r'\bdata-logged-in=["\'](true|false)["\']', html[:4096], re.I)
    signed_in = bool(signed and signed.group(1).lower() == "true")
    version = (
        profile.version(html)
        if page_version is None
        else page_version or _title_version(html)
    )
    spoiler_kinds = {
        i: spoiler_title_kind(s, version)
        for i, (t, s, _) in enumerate(events)
        if t == "spoiler_open"
    }
    heading_present = (
        any(
            t == "b"
            and any(_DOWNLOAD_HEADING.fullmatch(line) for line in _heading_lines(s))
            for t, s, _ in events
        )
        or "download" in spoiler_kinds.values()
    )
    active = not heading_present
    started = False
    section = ""
    label = ""
    divider = False
    pending_part = ""
    # A part line that already labelled links is a fragment marker, not a build.
    part_linked = False
    glue = False
    # Builds inside a spoiler that names an older version are kind "old".
    old = False
    saved: list[tuple[str, str, bool, str, bool, bool, bool]] = []
    builds: list[Build] = []

    def hold_part(line: str) -> None:
        nonlocal pending_part, part_linked, glue
        pending_part, part_linked, glue = line, False, False

    for index, (typ, value, attrs) in enumerate(events):
        if typ == "hidden_link" and active and site == "lc":
            return {
                "version": "Unknown",
                "signed_in": False,
                "builds": [],
                "archive_password": "",
            }
        if typ == "spoiler_open":
            # A spoiler is its own scope: what it sets ends where it ends.
            saved.append((section, label, divider, pending_part, glue, old, active))
            glue = False
            spoiler_kind = spoiler_kinds[index]
            if spoiler_kind == "download":
                active, started, divider = True, True, False
                section = label = pending_part = ""
            elif spoiler_kind == "part":
                hold_part(value.strip().rstrip(" :"))
            elif spoiler_kind == "old":
                section, label, divider, pending_part, old = (
                    value.strip().rstrip(" :"),
                    "",
                    False,
                    "",
                    True,
                )
        elif typ == "spoiler_close":
            if saved:
                # In a headingless post, a known mirror can start the fallback
                # download list even when that mirror was inside a spoiler.
                section, label, divider, pending_part, glue, old, active = saved.pop()
        elif typ == "break":
            glue = False
        elif typ == "text":
            line = value.rstrip(" :")
            if _PART.fullmatch(line):
                hold_part(line)
            elif (
                value.endswith(":")
                and len(value) <= 60
                and classify_build(line)[0] != "unknown"
            ):
                # A plain "Win:" row label works like a bold platform heading.
                label, pending_part, glue = line, "", False
            else:
                glue = bool(re.fullmatch(r"\s*[/&+|\-:,]\s*", value)) and glue
        elif typ == "b":
            for line in _heading_lines(value):
                heading = _DOWNLOAD_HEADING.fullmatch(line)
                if heading:
                    active, started, divider, glue = True, True, False, False
                    section = label = pending_part = ""
                    line = heading.group(1).strip(" :")
                    if not line:
                        continue
                if _DIVIDER.fullmatch(line):
                    divider, section, label, pending_part, glue = (
                        True,
                        line,
                        "",
                        "",
                        False,
                    )
                    continue
                if _PART.fullmatch(line):
                    hold_part(line)
                    continue
                platform, kind = classify_build(line)
                if platform != "unknown" or kind != "full":
                    # A platform right after an unused part line makes that part a build name.
                    if pending_part:
                        if not part_linked:
                            section = pending_part
                        pending_part = ""
                    label = f"{label}/{line}" if glue and label else line
                else:
                    pending_part = ""
                    section = line
                    label = ""
                glue = True
        elif typ == "a" and active:
            url = attrs.get("href", "")
            try:
                parsed = urlsplit(url)
                domain = (parsed.hostname or "").lower()
            except ValueError:
                continue
            if (
                parsed.scheme not in ("http", "https")
                or not domain
                or attrs.get("_image")
            ):
                continue
            own = domain == profile.domain or domain.endswith("." + profile.domain)
            masked = own and profile.masked(parsed)
            if (own and not masked and not profile.keep_own_link(parsed)) or any(
                domain == d or domain.endswith("." + d) for d in DENIED_HOSTS
            ):
                continue
            known = _host_of_domain(domain)
            if not started:
                if not masked and not known:
                    continue
                started = True
            if masked:
                host = (
                    profile.favicon_host(attrs.get("style", ""))
                    or _host_of_label(value)
                    or "unknown"
                )
            else:
                host = known or domain.removeprefix("www.")
            platform, kind = classify_build(label)
            if old:
                kind = "old"
            elif divider and kind == "full":
                kind = "extra"
            anchor_part = _PART.search(value)
            if re.search(r"\b(full|complete|single)\b", value, re.I):
                part = ""
            elif pending_part:
                part = pending_part
                part_linked = True
            elif anchor_part:
                part = f"Part {int(anchor_part.group(1))}"
            else:
                part = ""
            build_label = label or section or "Unknown"
            key = (build_label, section, platform, kind)
            build = next(
                (
                    b
                    for b in builds
                    if (b["label"], b["section"], b["platform"], b["kind"]) == key
                ),
                None,
            )
            if build is None:
                build = Build(
                    label=build_label,
                    section=section,
                    platform=platform,
                    kind=kind,
                    mirrors=[],
                )
                builds.append(build)
            build["mirrors"].append(
                {
                    "url": url,
                    "host": host,
                    "label": value,
                    "part": part,
                    "split": bool(part),
                }
            )
            glue = False
    return {
        "version": version,
        "signed_in": signed_in,
        "builds": builds,
        "archive_password": find_archive_password(parser.lines),
    }
