# SPDX-License-Identifier: GPL-3.0-or-later
"""Pixeldrain single-file download adapter."""
from __future__ import annotations

import base64
import json
import re
from http.client import HTTPResponse
from typing import BinaryIO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from core.downloads import credentials
from core.downloads.network import open_host
from core.downloads.transfer import DownloadError, FileInfo, open_range


def urlopen(request: Request, timeout: int = 30) -> object:
    return open_host(request, ("pixeldrain.com", "pixeldrain.net", "pixeldra.in"), timeout)


def _path(url: str) -> str:
    """The link's path on a Pixeldrain domain, or '' for any other URL."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").removeprefix("www.") not in ("pixeldrain.com", "pixeldrain.net", "pixeldra.in"):
        return ""
    return parsed.path


def _id(url: str) -> str:
    # Only /u/ links are single files; /d/ links are folders (filesystem shares).
    match = re.fullmatch(r"/u/([a-zA-Z0-9_-]+)/?", _path(url))
    return match.group(1) if match else ""


def matches(url: str) -> bool:
    return bool(re.fullmatch(r"/(?:u|d)/[a-zA-Z0-9_-]+(?:/.*)?", _path(url)))


def _headers(api_key: str) -> dict[str, str]:
    # Pixeldrain authenticates API keys as HTTP Basic auth with an empty username.
    if not api_key:
        return {}
    token = base64.b64encode(f":{api_key}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def classify_error(status: int, body: str = "") -> str:
    if status == 401:
        return "Pixeldrain rejected your API key; update it in Settings"
    if status in (404, 410):
        return "link expired or removed"
    # 403 is Pixeldrain's hotlink/captcha limit for heavily downloaded files.
    if status in (403, 429, 509) or "rate limit" in body.lower() or "quota" in body.lower() or "captcha" in body.lower():
        return "transfer quota or rate limit reached"
    return "network error"


def verify_key(api_key: str) -> str:
    """Return the account name for an API key, or raise DownloadError."""
    api_key = api_key.strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", api_key):
        raise DownloadError("Enter a valid Pixeldrain API key")
    try:
        with cast(HTTPResponse, urlopen(Request("https://pixeldrain.com/api/user", headers=_headers(api_key)), timeout=30)) as response:
            account = cast(object, json.load(response))
    except HTTPError as exc:
        raise DownloadError(classify_error(exc.code)) from exc
    except (URLError, OSError, ValueError) as exc:
        raise DownloadError("network error") from exc
    if not isinstance(account, dict):
        raise DownloadError("Pixeldrain returned an unexpected response")
    username = cast(dict[str, object], account).get("username")
    if not isinstance(username, str) or not username:
        # Pixeldrain answers anonymous sessions without a username.
        raise DownloadError("Pixeldrain rejected your API key; update it in Settings")
    return username


def probe(url: str, api_key: str | None = None) -> FileInfo:
    file_id = _id(url)
    if not file_id:
        if _path(url).startswith("/d/"):
            raise DownloadError("Pixeldrain folder links are not supported; open in browser")
        raise DownloadError("unsupported Pixeldrain link")
    headers = _headers(credentials.get(credentials.PIXELDRAIN_API_KEY) if api_key is None else api_key)
    base = f"https://pixeldrain.com/api/file/{file_id}"
    try:
        with cast(HTTPResponse, urlopen(Request(base + "/info", headers=headers), timeout=30)) as response:
            metadata = cast(object, json.load(response))
        if not isinstance(metadata, dict):
            raise DownloadError("file invalid")
        metadata = cast(dict[str, object], metadata)
        if not isinstance(metadata.get("name"), str):
            raise DownloadError("file invalid")
        name = metadata["name"]
        assert isinstance(name, str)
        size = metadata.get("size")
        def stream() -> BinaryIO:
            try:
                return cast(BinaryIO, urlopen(Request(base + "?download", headers=headers), timeout=30))
            except HTTPError as exc:
                raise DownloadError(classify_error(exc.code)) from exc

        def resume(offset: int) -> BinaryIO | None:
            return open_range(
                lambda extra: cast(
                    BinaryIO,
                    urlopen(Request(base + "?download", headers={**headers, **extra}), timeout=30),
                ),
                offset,
            )

        return FileInfo(
            name,
            size if isinstance(size, int) and size >= 0 else None,
            stream,
            resume_factory=resume,
            resume_key=f"pixeldrain:{file_id}",
        )
    except HTTPError as exc:
        raise DownloadError(classify_error(exc.code)) from exc
    except (URLError, OSError, ValueError) as exc:
        raise DownloadError("network error") from exc
