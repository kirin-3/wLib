# SPDX-License-Identifier: GPL-3.0-or-later
"""Which browser extensions may use the local extension server.

Each extension install makes its own random token and sends it with every
request. wLib serves a token only after the user allowed it once in the app.
Only a SHA-256 of the token is stored, in a device-local file that is never
exported or synced."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, UTC
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypedDict, cast

from core.app_log import RepeatLimiter
from core.host_platform import get_data_dir

PAIRING_FILE = "extension-pairing.json"
# Unanswered requests kept at once; more are refused until one is answered
# or expires, so stale or spammed requests cannot block pairing until restart.
MAX_PENDING = 3
PENDING_TTL_SECONDS = 600
MAX_PAIRED = 16
_TOKEN_RE = re.compile(r"[0-9a-f]{64}")

PairingState = Literal["paired", "pending", "denied", "invalid"]

_log = logging.getLogger("wlib.extension")
_repeats = RepeatLimiter()


class PairingEntry(TypedDict):
    id: str
    browser: str
    origin: str
    at: str


class _StoredEntry(PairingEntry):
    hash: str


def valid_token(token: object) -> bool:
    return isinstance(token, str) and _TOKEN_RE.fullmatch(token) is not None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def pairing_code(digest: str) -> str:
    """The short code shown in both wLib and the extension's options page."""
    return f"{digest[:4]}-{digest[4:8]}".upper()


def browser_name(origin: str) -> str:
    if origin.startswith("moz-extension://"):
        return "Firefox"
    if origin.startswith("chrome-extension://"):
        return "Chromium"
    return "Unknown"


# Called (outside any lock) with the id of each pairing that stopped being
# allowed, by removal or by eviction at MAX_PAIRED. The bridge cuts its sockets.
_removal_listeners: list[Callable[[str], None]] = []
_listeners_lock = threading.Lock()


def add_removal_listener(callback: Callable[[str], None]) -> None:
    with _listeners_lock:
        if callback not in _removal_listeners:
            _removal_listeners.append(callback)


def _notify_removed(pairing_ids: list[str]) -> None:
    with _listeners_lock:
        callbacks = list(_removal_listeners)
    for pairing_id in pairing_ids:
        for callback in callbacks:
            try:
                callback(pairing_id)
            except Exception as exc:
                print(f"[wLib] Pairing removal listener failed: {exc}")


def _public(entry: _StoredEntry) -> PairingEntry:
    return {
        "id": entry["id"],
        "browser": entry["browser"],
        "origin": entry["origin"],
        "at": entry["at"],
    }


class PairingStore:
    def __init__(self, data_dir: str | None = None) -> None:
        self._path: Path = Path(data_dir or get_data_dir()) / PAIRING_FILE
        self._lock: threading.Lock = threading.Lock()
        self._paired: dict[str, _StoredEntry] | None = None
        self._pending: dict[str, _StoredEntry] = {}
        self._pending_since: dict[str, float] = {}
        self._denied: set[str] = set()

    def _load(self) -> dict[str, _StoredEntry]:
        if self._paired is not None:
            return self._paired
        paired: dict[str, _StoredEntry] = {}
        try:
            raw = cast(object, json.loads(self._path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raw = None
        except (OSError, ValueError) as exc:
            _log.warning(
                "Could not read %s; every browser extension must be allowed again: %s",
                self._path,
                exc,
            )
            raw = None
        items = (
            cast(dict[str, object], raw).get("paired")
            if isinstance(raw, dict)
            else None
        )
        for item in cast(list[object], items) if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            fields = cast(dict[str, object], item)
            digest = fields.get("hash")
            if not isinstance(digest, str) or not _TOKEN_RE.fullmatch(digest):
                continue
            paired[digest] = {
                "hash": digest,
                "id": pairing_code(digest),
                "browser": str(fields.get("browser") or "Unknown")[:32],
                "origin": str(fields.get("origin") or "")[:128],
                "at": str(fields.get("at") or "")[:40],
            }
        self._paired = paired
        _log.info(
            "Allowed browser extensions: %s",
            ", ".join(f"{entry['browser']} {entry['id']}" for entry in paired.values())
            or "none",
        )
        return paired

    def _save(self, paired: dict[str, _StoredEntry]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{PAIRING_FILE}.{os.getpid()}")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"paired": list(paired.values())}, stream)
        os.replace(temporary, self._path)

    def _drop_expired(self) -> None:
        """Forget unanswered requests older than PENDING_TTL_SECONDS (lock held)."""
        cutoff = time.monotonic() - PENDING_TTL_SECONDS
        for digest in [
            key for key, since in self._pending_since.items() if since <= cutoff
        ]:
            del self._pending_since[digest]
            _ = self._pending.pop(digest, None)

    def _forget_pending(self, digest: str) -> None:
        del self._pending[digest]
        _ = self._pending_since.pop(digest, None)

    def check(self, token: object, origin: str = "") -> tuple[PairingState, bool]:
        """State of a caller's token, and whether this call made a new request."""
        if not valid_token(token):
            return "invalid", False
        digest = token_hash(cast(str, token))
        with self._lock:
            if digest in self._load():
                return "paired", False
            if digest in self._denied:
                return "denied", False
            self._drop_expired()
            if digest in self._pending:
                return "pending", False
            if len(self._pending) >= MAX_PENDING:
                _repeats.log(
                    _log,
                    logging.WARNING,
                    "pending-full",
                    "Refused a new browser extension pairing request: %d requests already wait "
                    + "for an answer in wLib (they expire after %d minutes)",
                    len(self._pending),
                    PENDING_TTL_SECONDS // 60,
                )
                return "denied", False
            self._pending[digest] = {
                "hash": digest,
                "id": pairing_code(digest),
                "browser": browser_name(origin),
                "origin": origin[:128],
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            self._pending_since[digest] = time.monotonic()
            return "pending", True

    def snapshot(self) -> dict[str, list[PairingEntry]]:
        with self._lock:
            self._drop_expired()
            return {
                "pending": [_public(entry) for entry in self._pending.values()],
                "paired": [_public(entry) for entry in self._load().values()],
            }

    def resolve(self, request_id: str, allow: bool) -> bool:
        """Answer a pending request; False when it does not exist or cannot be saved."""
        with self._lock:
            self._drop_expired()
            digest = next(
                (
                    key
                    for key, entry in self._pending.items()
                    if entry["id"] == request_id
                ),
                None,
            )
            if digest is None:
                return False
            entry = self._pending[digest]
            if not allow:
                self._forget_pending(digest)
                self._denied.add(digest)
                _log.info(
                    "Refused %s browser extension %s until wLib restarts",
                    entry["browser"],
                    entry["id"],
                )
                return True
            paired = dict(self._load())
            paired[digest] = entry
            evicted: list[str] = []
            while len(paired) > MAX_PAIRED:
                evicted.append(paired.pop(next(iter(paired)))["id"])
            try:
                self._save(paired)
            except OSError as exc:
                _log.warning(
                    "Could not save the allowed %s browser extension %s to %s: %s",
                    entry["browser"],
                    entry["id"],
                    self._path,
                    exc,
                )
                return False
            self._forget_pending(digest)
            self._paired = paired
        _log.info("Allowed %s browser extension %s", entry["browser"], entry["id"])
        for pairing_id in evicted:
            _log.info(
                "Removed browser extension %s: more than %d extensions are allowed",
                pairing_id,
                MAX_PAIRED,
            )
        _notify_removed(evicted)
        return True

    def revoke(self, pairing_id: str) -> bool:
        with self._lock:
            paired = {
                key: entry
                for key, entry in self._load().items()
                if entry["id"] != pairing_id
            }
            if len(paired) == len(self._load()):
                return False
            try:
                self._save(paired)
            except OSError as exc:
                _log.warning(
                    "Could not remove browser extension %s from %s: %s",
                    pairing_id,
                    self._path,
                    exc,
                )
                return False
            self._paired = paired
        _log.info("Removed browser extension %s", pairing_id)
        _notify_removed([pairing_id])
        return True

    def is_paired_id(self, pairing_id: str) -> bool:
        """True while an allowed extension has this pairing code."""
        with self._lock:
            return any(entry["id"] == pairing_id for entry in self._load().values())


_store: PairingStore | None = None
_store_lock = threading.Lock()


def store() -> PairingStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = PairingStore()
        return _store
