# SPDX-License-Identifier: GPL-3.0-or-later
"""Which browser extensions may use the local extension server.

Each extension install makes its own random token and sends it with every
request. wLib serves a token only after the user allowed it once in the app.
Only a SHA-256 of the token is stored, in a device-local file that is never
exported or synced."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Literal, TypedDict, cast

from core.host_platform import get_data_dir

PAIRING_FILE = "extension-pairing.json"
# Unanswered requests kept at once; more are refused until one is answered.
MAX_PENDING = 3
MAX_PAIRED = 16
_TOKEN_RE = re.compile(r"[0-9a-f]{64}")

PairingState = Literal["paired", "pending", "denied", "invalid"]


class PairingEntry(TypedDict):
    id: str
    browser: str
    origin: str
    at: str


class _StoredEntry(PairingEntry):
    hash: str


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
        self._denied: set[str] = set()

    def _load(self) -> dict[str, _StoredEntry]:
        if self._paired is not None:
            return self._paired
        paired: dict[str, _StoredEntry] = {}
        try:
            raw = cast(object, json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            raw = None
        items = cast(dict[str, object], raw).get("paired") if isinstance(raw, dict) else None
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
        return paired

    def _save(self, paired: dict[str, _StoredEntry]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{PAIRING_FILE}.{os.getpid()}")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"paired": list(paired.values())}, stream)
        os.replace(temporary, self._path)

    def check(self, token: object, origin: str = "") -> tuple[PairingState, bool]:
        """State of a caller's token, and whether this call made a new request."""
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            return "invalid", False
        digest = token_hash(token)
        with self._lock:
            if digest in self._load():
                return "paired", False
            if digest in self._denied:
                return "denied", False
            if digest in self._pending:
                return "pending", False
            if len(self._pending) >= MAX_PENDING:
                return "denied", False
            self._pending[digest] = {
                "hash": digest,
                "id": pairing_code(digest),
                "browser": browser_name(origin),
                "origin": origin[:128],
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            return "pending", True

    def snapshot(self) -> dict[str, list[PairingEntry]]:
        with self._lock:
            return {
                "pending": [_public(entry) for entry in self._pending.values()],
                "paired": [_public(entry) for entry in self._load().values()],
            }

    def resolve(self, request_id: str, allow: bool) -> bool:
        """Answer a pending request; False when it does not exist or cannot be saved."""
        with self._lock:
            digest = next(
                (key for key, entry in self._pending.items() if entry["id"] == request_id),
                None,
            )
            if digest is None:
                return False
            if not allow:
                del self._pending[digest]
                self._denied.add(digest)
                return True
            paired = dict(self._load())
            paired[digest] = self._pending[digest]
            while len(paired) > MAX_PAIRED:
                del paired[next(iter(paired))]
            try:
                self._save(paired)
            except OSError:
                return False
            del self._pending[digest]
            self._paired = paired
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
            except OSError:
                return False
            self._paired = paired
            return True


_store: PairingStore | None = None
_store_lock = threading.Lock()


def store() -> PairingStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = PairingStore()
        return _store
