# SPDX-License-Identifier: GPL-3.0-or-later
"""Pixeldrain single-file download adapter."""
from __future__ import annotations

import json
import re
from http.client import HTTPResponse
from typing import BinaryIO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from core.downloads.network import open_host

from core.downloads.transfer import DownloadError, FileInfo


def urlopen(request: Request, timeout: int = 30) -> object:
    return open_host(request, ("pixeldrain.com", "pixeldrain.net", "pixeldra.in"), timeout)


def _id(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").removeprefix("www.") not in ("pixeldrain.com", "pixeldrain.net", "pixeldra.in"):
        return ""
    match = re.fullmatch(r"/(?:u|d)/([a-zA-Z0-9_-]+)/?", parsed.path)
    return match.group(1) if match else ""


def matches(url: str) -> bool:
    return bool(_id(url))


def classify_error(status: int, body: str = "") -> str:
    if status in (404, 410):
        return "link expired or removed"
    # 403 is Pixeldrain's hotlink/captcha limit for heavily downloaded files.
    if status in (403, 429, 509) or "rate limit" in body.lower() or "quota" in body.lower() or "captcha" in body.lower():
        return "transfer quota or rate limit reached"
    return "network error"


def probe(url: str) -> FileInfo:
    file_id = _id(url)
    if not file_id:
        raise DownloadError("unsupported Pixeldrain link")
    base = f"https://pixeldrain.com/api/file/{file_id}"
    try:
        with cast(HTTPResponse, urlopen(Request(base + "/info"), timeout=30)) as response:
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
                return cast(BinaryIO, urlopen(Request(base + "?download"), timeout=30))
            except HTTPError as exc:
                raise DownloadError(classify_error(exc.code)) from exc
        return FileInfo(name, size if isinstance(size, int) and size >= 0 else None, stream)
    except HTTPError as exc:
        raise DownloadError(classify_error(exc.code)) from exc
    except (URLError, OSError, ValueError) as exc:
        raise DownloadError("network error") from exc
