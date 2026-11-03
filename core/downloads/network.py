# SPDX-License-Identifier: GPL-3.0-or-later
"""Restrict file-host requests and redirects to the selected provider."""
from __future__ import annotations

from http.client import HTTPMessage, HTTPResponse
from typing import IO, cast, final, override
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def allowed(url: str, domains: tuple[str, ...]) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        return parsed.scheme == 'https' and parsed.port in (None, 443) and any(host == domain or host.endswith('.' + domain) for domain in domains)
    except ValueError:
        return False


@final
class _Redirect(HTTPRedirectHandler):
    def __init__(self, domains: tuple[str, ...]) -> None:
        self.domains = domains

    @override
    def redirect_request(self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str) -> Request | None:
        if not allowed(newurl, self.domains):
            raise URLError('redirect to an unrelated host refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_host(request: Request, domains: tuple[str, ...], timeout: int = 30) -> HTTPResponse:
    if not allowed(request.full_url, domains):
        raise URLError('unrelated host refused')
    return cast(HTTPResponse, build_opener(_Redirect(domains)).open(request, timeout=timeout))
