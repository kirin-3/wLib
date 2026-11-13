# SPDX-License-Identifier: GPL-3.0-or-later
"""Failure codes for queued downloads.

The workers report failures as user-facing strings. The queue turns them into
one of the codes below; the UI owns the sentence and the actions for each code
(`ui/src/utils/downloadQueue.ts`). Only `network` retries automatically.
"""
from __future__ import annotations

NETWORK = "network"
REMOVED = "removed"
QUOTA = "quota"
CHALLENGE = "challenge"
INVALID = "invalid"
DISK = "disk"
WRITE = "write"
SIGN_IN = "sign_in"
EXTENSION = "extension"
CHECK_TIMEOUT = "check_timeout"
PERMISSION = "permission"
MIRROR_GONE = "mirror_gone"
ENGINE = "engine"
UNKNOWN = "unknown"

CODES = (
    NETWORK, REMOVED, QUOTA, CHALLENGE, INVALID, DISK, WRITE, SIGN_IN,
    EXTENSION, CHECK_TIMEOUT, PERMISSION, MIRROR_GONE, ENGINE, UNKNOWN,
)
RETRYABLE = frozenset({NETWORK})

# (code, prefixes): the first message prefix that matches, case-insensitively, wins.
_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (NETWORK, (
        "network error",
        "rpdl is unavailable",
        "f95zone returned http",
    )),
    (REMOVED, (
        "link expired or removed",
        "rpdl torrent not found",
    )),
    (QUOTA, (
        "transfer quota or rate limit reached",
        "rpdl rate limit reached",
    )),
    (CHALLENGE, ("blocked by a challenge",)),
    (INVALID, (
        "file invalid",
        "invalid torrent",
        "rpdl sent an invalid response",
        "unsupported",
        "mega link is missing its key",
        "invalid mega download url",
        "pixeldrain folder links are not supported",
        "pixeldrain returned an unexpected response",
        "split archives are not supported",
        "this buzzheavier link cannot be downloaded in-app",
    )),
    (DISK, ("not enough disk space", "free disk space")),
    (WRITE, ("could not save the download", "files could not be written")),
    (SIGN_IN, (
        "sign in to rpdl",
        "sign in to f95zone",
        "mega session expired",
        "pixeldrain rejected your api key",
    )),
    (EXTENSION, (
        "open a browser with",
        "the browser extension disconnected",
    )),
    (CHECK_TIMEOUT, (
        "resolving the link timed out",
        "the browser window was closed before the link resolved",
    )),
    (PERMISSION, ("enable buzzheavier downloads",)),
    (MIRROR_GONE, (
        "mirror no longer available",
        "download link is unavailable",
        "game has no f95zone thread",
    )),
    (ENGINE, (
        "torrent downloads are unavailable",
        "torrent engine keeps stopping",
    )),
)


def classify(message: str) -> str:
    """The failure code for a worker's error message."""
    lower = message.strip().lower()
    for code, prefixes in _PREFIXES:
        if lower.startswith(prefixes):
            return code
    return UNKNOWN


def retryable(code: str) -> bool:
    return code in RETRYABLE
