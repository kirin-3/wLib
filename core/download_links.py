# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse ephemeral download mirrors from a signed-in F95 thread."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
from typing import TypedDict, final, override


# Host id -> domains it serves from. Order matters only for readability.
# The in-app hosts (pixeldrain, mega, buzzheavier) list only the domains their
# download adapters accept, or the UI would offer downloads that cannot start.
HOST_DOMAINS: dict[str, tuple[str, ...]] = {
    "pixeldrain": ("pixeldrain.com", "pixeldrain.net", "pixeldra.in"),
    "mega": ("mega.nz", "mega.co.nz"),
    "buzzheavier": ("buzzheavier.com", "bzzhr.to"),
    "gofile": ("gofile.io",),
    "datanodes": ("datanodes.to",),
    "mixdrop": ("mixdrop.ag", "mixdrop.co", "mixdrop.to", "mixdrop.ps", "mixdrop.sx"),
    "workupload": ("workupload.com",),
    "mediafire": ("mediafire.com",),
    "vikingfile": ("vikingfile.com",),
    "akirabox": ("akirabox.com",),
}
# Words a poster uses as link text for a host ("MEGA", "BZZHR").
HOST_WORDS = {name: name for name in HOST_DOMAINS} | {"bzzhr": "buzzheavier"}
DENIED_HOSTS = (
    "f95zone.com", "patreon.com", "subscribestar.com", "subscribestar.adult", "boosty.to",
    "ko-fi.com", "buymeacoffee.com", "itch.io", "steampowered.com", "steamcommunity.com",
    "gog.com", "discord.com", "discord.gg", "discordapp.com", "twitter.com", "x.com",
    "bsky.app", "facebook.com", "instagram.com", "reddit.com", "youtube.com", "youtu.be",
    "twitch.tv", "tumblr.com", "pixiv.net", "deviantart.com", "imgur.com", "postimg.cc",
    "ibb.co", "wikipedia.org",
)
_DOWNLOAD_HEADING = re.compile(r"downloads?\b(?:\s*links?\b)?[\s:]*(.*)", re.I)
_DIVIDER = re.compile(r"(?:extras?|bonus(?:es)?|translations?|mods?)", re.I)
_PART = re.compile(r"part\s*(\d{1,2})\b", re.I)


def classify_build(label: str) -> tuple[str, str]:
    """Return the platform and kind described by a build heading."""
    text = label.lower()
    def has(pattern: str) -> bool:
        return bool(re.search(pattern, text))
    win = has(r"\b(win(?:dows)?|pc)\b")
    linux = has(r"\b(linux|lin)\b")
    if has(r"\bandroid\b"):
        platform = "android"
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
    for name, domains in HOST_DOMAINS.items():
        if any(domain == d or domain.endswith("." + d) for d in domains):
            return name
    return ""


def _host_of_favicon(style: str) -> str:
    """XenForo's has-favicon style names the destination: ``.../ip3/<host>.ico``."""
    match = re.search(r"ip3/([a-z0-9.-]+?)\.ico", style, re.I)
    if not match:
        return ""
    domain = match.group(1).lower().removeprefix("www.")
    return _host_of_domain(domain) or domain


def _host_of_label(text: str) -> str:
    words: list[str] = re.findall(r"[a-z]+", text.lower())
    for word in words:
        if word in HOST_WORDS:
            return HOST_WORDS[word]
    return ""


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
        self.hidden = 0
        self.bold = 0
        self.bold_parts: list[str] = []
        self.anchor: dict[str, str] | None = None
        self.anchor_parts: list[str] = []
        self.events: list[tuple[str, str, dict[str, str]]] = []

    def _flush_bold(self) -> None:
        text = "".join(self.bold_parts).strip()
        self.bold_parts = []
        if text:
            self.events.append(("b", text, {}))

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        classes = a.get("class", "").split()
        if tag == "article" and not self.seen and ("message-threadStarterPost" in classes or "message--post" in classes):
            self.article = 1
            self.seen = True
        elif self.article and tag == "article":
            self.article += 1
        if self.article and tag == "div":
            if self.wrapper:
                self.wrapper += 1
            elif "bbWrapper" in classes:
                self.wrapper = 1
        if not self.wrapper:
            return
        if tag == "span" and (self.hidden or "u-hidden" in classes):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ("b", "strong"):
            if self.anchor is None:
                self.bold += 1
        elif tag == "a":
            if self.anchor is None:
                if self.bold:
                    self._flush_bold()
                self.anchor, self.anchor_parts = a, []
        elif tag == "br":
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
        if self.wrapper:
            if self.hidden:
                if tag == "span":
                    self.hidden -= 1
            elif tag == "a" and self.anchor is not None:
                self.events.append(("a", "".join(self.anchor_parts).strip(), self.anchor))
                self.anchor = None
            elif tag in ("b", "strong") and self.bold and self.anchor is None:
                self.bold -= 1
                if not self.bold:
                    self._flush_bold()
            if tag == "div":
                self.wrapper -= 1
        if tag == "article" and self.article:
            self.article -= 1

    @override
    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        if self.anchor is not None:
            self.anchor_parts.append(data)
        elif self.bold:
            self.bold_parts.append(data)
        elif self.wrapper and data.strip():
            self.events.append(("text", data.strip(), {}))


def _heading_lines(value: str) -> list[str]:
    lines = (line.strip(" :\t") for line in value.splitlines())
    return [line for line in lines if re.search(r"[A-Za-z0-9]", line)]


def parse_download_links(html: str) -> ThreadDownloads:
    """Return first-post builds and ephemeral URLs; callers must not persist them."""
    parser = _Elements()
    parser.feed(html)
    events = parser.events
    heading_present = any(
        t == "b" and any(_DOWNLOAD_HEADING.fullmatch(line) for line in _heading_lines(s))
        for t, s, _ in events
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
    builds: list[Build] = []

    def hold_part(line: str) -> None:
        nonlocal pending_part, part_linked, glue
        pending_part, part_linked, glue = line, False, False

    for typ, value, attrs in events:
        if typ == "break":
            glue = False
        elif typ == "text":
            line = value.rstrip(" :")
            if _PART.fullmatch(line):
                hold_part(line)
            elif value.endswith(":") and len(value) <= 60 and classify_build(line)[0] != "unknown":
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
                    divider, section, label, pending_part, glue = True, line, "", "", False
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
            if parsed.scheme not in ("http", "https") or not domain or attrs.get("_image"):
                continue
            f95 = domain == "f95zone.to" or domain.endswith(".f95zone.to")
            dl = f95 and bool(re.match(r"/dl/\d+/", parsed.path))
            attachment = f95 and (domain == "attachments.f95zone.to" or parsed.path.startswith("/attachments/"))
            if (f95 and not dl and not attachment) or any(domain == d or domain.endswith("." + d) for d in DENIED_HOSTS):
                continue
            known = _host_of_domain(domain)
            if not started:
                if not dl and not known:
                    continue
                started = True
            if dl:
                host = _host_of_favicon(attrs.get("style", "")) or _host_of_label(value) or "unknown"
            else:
                host = known or domain.removeprefix("www.")
            platform, kind = classify_build(label)
            if divider and kind == "full":
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
            build = next((b for b in builds if (b["label"], b["section"], b["platform"], b["kind"]) == key), None)
            if build is None:
                build = Build(label=build_label, section=section, platform=platform, kind=kind, mirrors=[])
                builds.append(build)
            build["mirrors"].append({"url": url, "host": host, "label": value, "part": part, "split": bool(part)})
            glue = False
    title = re.search(r'<h1\b[^>]*class=["\'][^"\']*p-title-value[^"\']*["\'][^>]*>(.*?)</h1>', html, re.I | re.S)
    version = re.search(r"\[((?:v(?:ersion)?\s*)?\d[^\]]*|(?:chapter|ch\.|episode|ep\.|part)\s*\d[^\]]*)\]", re.sub(r"<[^>]*>", "", title.group(1)), re.I) if title else None
    signed = re.search(r'\bdata-logged-in=["\'](true|false)["\']', html[:4096], re.I)
    return {"version": version.group(1) if version else "Unknown", "signed_in": bool(signed and signed.group(1).lower() == "true"), "builds": builds}
