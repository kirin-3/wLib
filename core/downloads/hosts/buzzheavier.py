# SPDX-License-Identifier: GPL-3.0-or-later
"""Transfer a Buzzheavier address resolved in the browser."""
from __future__ import annotations

from http.client import HTTPResponse
from typing import BinaryIO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request

from core.downloads.network import open_host

from core.downloads.transfer import DownloadError, FileInfo, open_range


def urlopen(request: Request, timeout: int = 30) -> object:
    return open_host(request, ("buzzheavier.com", "bzzhr.to"), timeout)


def matches(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and any(host == domain or host.endswith("." + domain) for domain in ("buzzheavier.com", "bzzhr.to"))


def classify_error(status: int, body: str = "") -> str:
    if status == 403 and "cf-mitigated" in body.lower():
        return "blocked by a challenge"
    if status in (404, 410):
        return "link expired or removed"
    if status == 429:
        return "transfer quota or rate limit reached"
    return "network error"


def probe(url: str, share_url: str = "") -> FileInfo:
    if not matches(url) or not matches(share_url):
        raise DownloadError("unsupported Buzzheavier link")
    try:
        response = cast(HTTPResponse, urlopen(Request(url, headers={"Referer": share_url}), timeout=30))
    except HTTPError as exc:
        raise DownloadError(classify_error(exc.code, "cf-mitigated" if exc.headers.get("cf-mitigated") else "")) from exc
    except (URLError, OSError) as exc:
        raise DownloadError("network error") from exc
    disposition = response.headers.get_filename()
    name = disposition or unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    length = response.headers.get("Content-Length", "")
    # Ownership of this response passes to the transfer loop.
    def stream() -> BinaryIO:
        return cast(BinaryIO, response)

    def resume(offset: int) -> BinaryIO | None:
        return open_range(
            lambda extra: cast(
                BinaryIO,
                urlopen(Request(url, headers={"Referer": share_url, **extra}), timeout=30),
            ),
            offset,
        )

    return FileInfo(
        name,
        int(length) if length.isdigit() else None,
        stream,
        close=response.close,
        resume_factory=resume,
        # The share page names the file; the resolved address can change.
        resume_key=f"buzzheavier:{urlsplit(share_url).path}",
    )
