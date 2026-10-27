# SPDX-License-Identifier: GPL-3.0-or-later
import re
from urllib.parse import urlparse, urlunparse


THREAD_PATH_RE = re.compile(
    r"^/threads/(?:(?P<slug>.+)\.)?(?P<thread_id>\d+)(?:/.*)?$", re.IGNORECASE
)
F95_HOSTS = frozenset({"f95zone.to", "www.f95zone.to"})
# Thread prefixes that are not an engine (F95's games forum prefix filter);
# every other prefix, including "Others", names the engine. Keep in sync with
# extension/content.js. Compared after lowercasing and stripping spaces,
# hyphens and underscores, so "On Hold", "on-hold" and "onhold" all match.
NON_ENGINE_PREFIXES = frozenset(
    {"completed", "abandoned", "onhold", "vn", "collection", "siterip", "readme"}
)


def normalize_prefix_label(label: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(label or "").casefold())


def _coerce_url(value: object) -> str:
    if not isinstance(value, str):
        return ""

    normalized = value.strip()
    if not normalized:
        return ""

    if normalized.startswith(("f95zone.to/", "www.f95zone.to/")):
        normalized = f"https://{normalized}"

    return normalized


def _normalize_path(path: str) -> str:
    collapsed = re.sub(r"/+", "/", path or "/")
    if not collapsed.startswith("/"):
        collapsed = f"/{collapsed}"
    return collapsed


def extract_thread_id(url: object) -> str:
    normalized = _coerce_url(url)
    if not normalized:
        return ""

    try:
        parsed = urlparse(normalized)
    except ValueError:  # e.g. malformed IPv6 netloc
        return ""
    # Thread IDs are only meaningful on F95Zone; another site's /threads/<id>/
    # must not be matched or scraped as an F95 thread.
    if parsed.netloc and parsed.hostname not in F95_HOSTS:
        return ""
    path = _normalize_path(parsed.path or normalized)
    match = THREAD_PATH_RE.match(path)
    return match.group("thread_id") if match else ""


def normalize_thread_url(url: object) -> str:
    normalized = _coerce_url(url)
    if not normalized:
        return ""

    try:
        parsed = urlparse(normalized)
    except ValueError:  # e.g. malformed IPv6 netloc
        return ""
    if not parsed.netloc:
        return normalized

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc == "www.f95zone.to":
        netloc = "f95zone.to"
    if netloc == "f95zone.to":
        scheme = "https"

    path = _normalize_path(parsed.path)
    match = THREAD_PATH_RE.match(path)
    if match:
        slug = (match.group("slug") or "").strip("./")
        thread_id = match.group("thread_id")
        if slug:
            path = f"/threads/{slug}.{thread_id}/"
        else:
            path = f"/threads/{thread_id}/"
    elif path != "/" and not path.endswith("/"):
        path = f"{path}/"

    return urlunparse((scheme, netloc, path, "", "", ""))


def thread_urls_match(left: object, right: object) -> bool:
    left_normalized = normalize_thread_url(left)
    right_normalized = normalize_thread_url(right)
    if not left_normalized or not right_normalized:
        return False

    left_thread_id = extract_thread_id(left_normalized)
    right_thread_id = extract_thread_id(right_normalized)
    if left_thread_id and right_thread_id:
        return left_thread_id == right_thread_id

    return left_normalized == right_normalized


# Mirrored in extension/content.js extractGameInfo (h1 title parsing).
_TITLE_VERSION_RE = re.compile(
    r"^v?(?:er(?:sion)?\.?\s*)?(\d+[\d.]*[a-zA-Z]?(?:\s*(?:beta|alpha|final|fix\d*|hotfix))?)$",
    re.IGNORECASE,
)
_TITLE_CHAPTER_RE = re.compile(
    r"^(Ch(?:apter|\.)?\s*\d+(?:\.\d+)?(?:\s*v[\d.]+[a-zA-Z]?)?|Ep(?:isode|\.)?\s*\d+(?:\.\d+)?|S\d+\s*E\d+|Part\s*\d+)",
    re.IGNORECASE,
)


def split_thread_title(h1_text: str) -> tuple[str, str, str]:
    """Split the thread heading into title, version and developer."""
    text = re.sub(r"^(?:xLibrary|wLib)\s*-\s*", "", h1_text.strip(), flags=re.I)
    while match := re.match(r"^\[([^\]]+)\]\s*", text):
        inner = match[1].strip()
        if _TITLE_VERSION_RE.match(inner) or _TITLE_CHAPTER_RE.match(inner):
            break
        text = text[match.end() :]
    brackets = list(re.finditer(r"\[([^\]]*)\]", text))
    version = ""
    for match in brackets:
        inner = match[1].strip()
        if _TITLE_VERSION_RE.match(inner):
            version = re.sub(r"^v(?:er(?:sion)?\.?\s*)?", "", inner, flags=re.I).strip()
        elif _TITLE_CHAPTER_RE.match(inner):
            version = inner
        if version:
            text = text.replace(match[0], "", 1)
            break
    if not version:
        match = re.search(r"\bv(\d+[\d.]*[a-zA-Z]?)(?:\s|$|\[)", text, re.I)
        if match:
            version = match[1].strip()
            text = (
                text[: match.start()]
                + ("[" if match[0].endswith("[") else " ")
                + text[match.end() :]
            )
    if not version and len(brackets) >= 2:
        version = brackets[0][1].strip()
        text = text.replace(brackets[0][0], "", 1)
    developer = ""
    match = re.search(r"\[([^\]]+)\]$", text)
    if match:
        developer = match[1].strip()
        text = text[: match.start()]
    return re.sub(r"\[.*?\]", "", text).strip(), version, developer
