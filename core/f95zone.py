import re
from urllib.parse import urlparse, urlunparse


THREAD_PATH_RE = re.compile(r"^/threads/(?:(?P<slug>.+)\.)?(?P<thread_id>\d+)(?:/.*)?$")


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

    parsed = urlparse(normalized)
    path = _normalize_path(parsed.path or normalized)
    match = THREAD_PATH_RE.match(path)
    return match.group("thread_id") if match else ""


def normalize_thread_url(url: object) -> str:
    normalized = _coerce_url(url)
    if not normalized:
        return ""

    parsed = urlparse(normalized)
    if not parsed.netloc:
        return normalized

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc == "www.f95zone.to":
        netloc = "f95zone.to"

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
