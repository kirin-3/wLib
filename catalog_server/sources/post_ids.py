# SPDX-License-Identifier: GPL-3.0-or-later
"""Developer IDs from links and language names from free text, shared by the
F95 and LewdCorner first-post readers."""

import re
from collections.abc import Callable

# Labels that end the overview; a colon inside the overview text is not one.
SECTION_LABELS = frozenset(
    {
        "thread updated",
        "release date",
        "developer",
        "developers",
        "publisher",
        "original title",
        "original name",
        "aliases",
        "alias",
        "also known as",
        "aka",
        "translator",
        "translators",
        "censored",
        "censorship",
        "version",
        "os",
        "platform",
        "platforms",
        "language",
        "languages",
        "store",
        "stores",
        "genre",
        "genres",
        "installation",
        "changelog",
        "download",
        "downloads",
        "trailer",
        "developer notes",
        "translator notes",
        "other games",
    }
)
_LANGUAGE_SPLIT_RE = re.compile(r",|;|/|&|\+|\band\b|\|", re.I)
_LANGUAGE_NAME_RE = re.compile(r"[A-Za-z][A-Za-z -]{1,24}")
_NOT_LANGUAGES = frozenset(
    {"MTL", "AI", "LLM", "TL", "OTHER", "OTHERS", "NONE", "UNKNOWN"}
)

_ID_PATTERNS: tuple[
    tuple[str, re.Pattern[str], Callable[[re.Match[str]], str | None]], ...
] = (
    (
        "patreon",
        re.compile(
            r"^https?://(?:www\.)?patreon\.com/(?:c/|cw/|join/)?([A-Za-z0-9_-]+)/?(?:[?#/].*)?$"
        ),
        lambda m: (
            None
            if m.group(1).lower()
            in {"posts", "user", "home", "login", "checkout", "bepatron"}
            else m.group(1).lower()
        ),
    ),
    (
        "patreon",
        re.compile(r"^https?://(?:www\.)?patreon\.com/user\?u=(\d+)"),
        lambda m: f"u:{m.group(1)}",
    ),
    (
        "itch",
        re.compile(
            r"^https?://([a-z0-9-]+)\.itch\.io(?:/([A-Za-z0-9_-]+))?/?(?:[?#].*)?$",
            re.I,
        ),
        lambda m: m.group(1).lower() + (f"/{m.group(2).lower()}" if m.group(2) else ""),
    ),
    (
        "itch",
        re.compile(r"^https?://(?:www\.)?itch\.io/profile/([A-Za-z0-9_-]+)", re.I),
        lambda m: m.group(1).lower(),
    ),
    (
        "subscribestar",
        re.compile(
            r"^https?://(?:www\.)?subscribestar\.(?:com|adult)/([A-Za-z0-9_-]+)", re.I
        ),
        lambda m: None if m.group(1).lower() == "posts" else m.group(1).lower(),
    ),
    (
        "kofi",
        re.compile(r"^https?://(?:www\.)?ko-fi\.com/([A-Za-z0-9_]+)", re.I),
        lambda m: (
            None
            if m.group(1).lower() in {"s", "i", "post", "home", "album", "shop"}
            else m.group(1).lower()
        ),
    ),
    (
        "discord",
        re.compile(
            r"^https?://(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9-]+)",
            re.I,
        ),
        lambda m: m.group(1),
    ),
    (
        "steam",
        re.compile(
            r"^https?://(?:store\.steampowered\.com|steamcommunity\.com)/app/(\d+)",
            re.I,
        ),
        lambda m: m.group(1),
    ),
    (
        # A work page on any DLsite site path (maniax, pro, home, ecchi-eng, ...);
        # ci-en and other subdomains are not works.
        "dlsite",
        re.compile(
            r"^https?://(?:www\.)?dlsite\.com/[^?#]*?/(?:product_id|id)/((?:RJ|VJ|BJ)\d{6,8})(?:\.html)?/?(?:[?#].*)?$",
            re.I,
        ),
        lambda m: m.group(1).upper(),
    ),
    (
        # A circle page names a circle, never a work.
        "dlsite_circle",
        re.compile(
            r"^https?://(?:www\.)?dlsite\.com/[^?#]*?/maker_id/((?:RG|VG|BG)\d+)(?:\.html)?/?(?:[?#].*)?$",
            re.I,
        ),
        lambda m: m.group(1).upper(),
    ),
    (
        "fanza",
        re.compile(
            r"^https?://(?:[a-z]+\.)?dmm\.co\.jp/[^?#]*?[/?&]cid=([a-z0-9_]+)",
            re.I,
        ),
        lambda m: m.group(1).lower(),
    ),
)


def external_ids(urls: list[str]) -> dict[str, list[dict[str, str]]]:
    """`{kind: [{"id": ..., "url": ...}]}`; the URL is the link as the post has it."""
    found: dict[str, list[dict[str, str]]] = {}
    for raw in urls:
        url = raw.strip()
        for kind, pattern, pick in _ID_PATTERNS:
            match = pattern.match(url)
            if match is None:
                continue
            value = pick(match)
            if value is None:
                # Not an ID form of this pattern (e.g. patreon.com/user?u=); try the next.
                continue
            entries = found.setdefault(kind, [])
            if all(entry["id"] != value for entry in entries):
                entries.append({"id": value, "url": url})
            break
    return found


def parse_languages(text: str | None) -> list[str]:
    """Language names from the free text: `English (Sonnet 5.5 & Opus 5.5), Japanese`
    gives `["English", "Japanese"]`. Notes in brackets are not languages."""
    if not text:
        return []
    plain = re.sub(r"\([^)]*\)|\[[^\]]*\]", ",", text)
    names: list[str] = []
    for part in _LANGUAGE_SPLIT_RE.split(plain):
        # "English MTL": a note word after the name is not part of it.
        words = [word for word in part.split() if word.upper() not in _NOT_LANGUAGES]
        name = " ".join(words).strip(" .-:")
        # Words only: version numbers and long phrases are not names.
        if not _LANGUAGE_NAME_RE.fullmatch(name):
            continue
        name = " ".join(word[:1].upper() + word[1:].lower() for word in name.split())
        if name not in names:
            names.append(name)
    return names
