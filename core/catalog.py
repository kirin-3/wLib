# SPDX-License-Identifier: GPL-3.0-or-later
"""Download and keep a local, read-only copy of the published game catalog.

The catalog lives next to the library database as `catalog.db`, with a
`catalog.json` sidecar that records the installed manifest entry and the last
check. Requests are plain GETs for the manifest and snapshot; nothing from the
library is sent. The library database is never touched here.
"""

import hashlib
import json
import lzma
import os
import re
import sqlite3
import ssl
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from http.client import HTTPResponse
from pathlib import Path
from typing import Protocol, cast
from urllib.request import Request, urlopen

from core.catalog_format import (
    MANIFEST_GENERATED_AT,
    MANIFEST_KEY,
    MANIFEST_SCHEMA_VERSION,
    MANIFEST_SNAPSHOT,
    MANIFEST_SOURCES,
    META_SCHEMA_VERSION,
    SNAPSHOT_PATH,
    SNAPSHOT_SHA256,
    SNAPSHOT_SIZE,
    SNAPSHOT_UNCOMPRESSED_SIZE,
    SOURCE_COUNT,
    SOURCE_UPDATED_AT,
    SUPPORTED_SCHEMA_VERSIONS,
)

DEFAULT_MANIFEST_URL = "https://wlib-db.thekirin.net/" + MANIFEST_KEY
MANIFEST_URL_ENV = "WLIB_CATALOG_MANIFEST_URL"
CATALOG_FILE = "catalog.db"
SIDECAR_FILE = "catalog.json"
CHECK_INTERVAL_SECONDS = 12 * 60 * 60
MAX_SNAPSHOT_BYTES = 200 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
_CHUNK = 256 * 1024
_SNAPSHOT_PATH_RE = re.compile(r"^v\d+/snapshots/[A-Za-z0-9._-]+\.sqlite\.xz$")


class CatalogError(Exception):
    pass


class _Response(Protocol):
    def read(self, amt: int | None = None, /) -> bytes: ...

    def close(self) -> None: ...


class _Writable(Protocol):
    def write(self, data: bytes, /) -> int: ...


Opener = Callable[[str, Mapping[str, str]], _Response]


def _urllib_open(url: str, headers: Mapping[str, str]) -> _Response:
    request = Request(url, headers=dict(headers), method="GET")
    return cast(
        HTTPResponse, urlopen(request, timeout=30, context=ssl.create_default_context())
    )


def manifest_url() -> str:
    return os.environ.get(MANIFEST_URL_ENV, "").strip() or DEFAULT_MANIFEST_URL


def snapshot_url(manifest: str, path: str) -> str:
    """Resolve the manifest's relative snapshot path against its base URL."""
    if not _SNAPSHOT_PATH_RE.match(path):
        raise CatalogError("The catalog manifest has an invalid snapshot path")
    if manifest.endswith(MANIFEST_KEY):
        return manifest[: -len(MANIFEST_KEY)] + path
    return manifest.rsplit("/", 1)[0] + "/" + path


def _utc_now(clock: Callable[[], float]) -> str:
    return datetime.fromtimestamp(clock(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


class CatalogClient:
    def __init__(
        self,
        data_dir: str,
        *,
        app_version: str,
        enabled: Callable[[], bool] = lambda: True,
        opener: Opener = _urllib_open,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.data_dir: str = data_dir
        self._app_version: str = app_version
        self._enabled: Callable[[], bool] = enabled
        self._opener: Opener = opener
        self._clock: Callable[[], float] = clock
        self._lock: threading.Lock = threading.Lock()
        self._checking: bool = False

    @property
    def catalog_path(self) -> str:
        return os.path.join(self.data_dir, CATALOG_FILE)

    @property
    def sidecar_path(self) -> str:
        return os.path.join(self.data_dir, SIDECAR_FILE)

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": f"wLib/{self._app_version} (+https://github.com/kirin-3/wLib)"}

    def _read_sidecar(self) -> dict[str, object]:
        try:
            with open(self.sidecar_path, encoding="utf-8") as handle:
                data = cast(object, json.load(handle))
        except (OSError, ValueError):
            return {}
        return cast(dict[str, object], data) if isinstance(data, dict) else {}

    def _write_sidecar(self, data: Mapping[str, object]) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.data_dir, prefix=".catalog-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(dict(data), handle, indent=2)
            os.replace(tmp, self.sidecar_path)
        except BaseException:
            _remove(tmp)
            raise

    def status(self) -> dict[str, object]:
        sidecar = self._read_sidecar()
        manifest = sidecar.get("manifest")
        installed = isinstance(manifest, dict) and os.path.isfile(self.catalog_path)
        sources: dict[str, dict[str, object]] = {}
        built_at: object = None
        if installed:
            body = cast(dict[str, object], manifest)
            built_at = body.get(MANIFEST_GENERATED_AT)
            raw_sources = body.get(MANIFEST_SOURCES)
            if isinstance(raw_sources, dict):
                for name, value in cast(dict[str, object], raw_sources).items():
                    if isinstance(value, dict):
                        entry = cast(dict[str, object], value)
                        sources[name] = {
                            "count": entry.get(SOURCE_COUNT),
                            "updated_at": entry.get(SOURCE_UPDATED_AT),
                        }
        return {
            "installed": installed,
            "built_at": built_at,
            "sources": sources,
            "last_check": sidecar.get("last_check"),
            "last_error": sidecar.get("last_error"),
            "checking": self._checking,
        }

    def start_background(self, *, force: bool = False) -> bool:
        """Start a check on a daemon thread; False if one is already running."""
        with self._lock:
            if self._checking:
                return False
            self._checking = True
        try:
            threading.Thread(target=self._run_check, args=(force,), daemon=True).start()
        except Exception as exc:
            # A catalog problem must never stop the app from starting.
            print(f"[wLib] Could not start the catalog check: {exc}")
            with self._lock:
                self._checking = False
            return False
        return True

    def _run_check(self, force: bool) -> None:
        try:
            _ = self.check(force=force, _claimed=True)
        except Exception as exc:  # never let a catalog problem escape the thread
            print(f"[wLib] Catalog check failed: {exc}")

    def check(self, *, force: bool = False, _claimed: bool = False) -> dict[str, object]:
        """Fetch the manifest and install a newer snapshot when there is one."""
        if not _claimed:
            with self._lock:
                if self._checking:
                    return {"success": False, "error": "A catalog check is already running"}
                self._checking = True
        try:
            return self._check(force)
        finally:
            with self._lock:
                self._checking = False

    def _check(self, force: bool) -> dict[str, object]:
        if not self._enabled():
            return {"success": True, "skipped": "disabled"}
        sidecar = self._read_sidecar()
        last = _parse_time(sidecar.get("last_check"))
        if not force and last is not None and self._clock() - last < CHECK_INTERVAL_SECONDS:
            return {"success": True, "skipped": "recent"}
        sidecar["last_check"] = _utc_now(self._clock)
        try:
            updated = self._update(sidecar)
        except Exception as exc:
            sidecar["last_error"] = str(exc) or type(exc).__name__
            if self._enabled():
                self._write_sidecar(sidecar)
            return {"success": False, "error": sidecar["last_error"]}
        sidecar["last_error"] = None
        if self._enabled():
            self._write_sidecar(sidecar)
        return {"success": True, "updated": updated}

    def _fetch_manifest(self, url: str) -> dict[str, object]:
        with closing(self._opener(url, self._headers())) as response:
            data = response.read(MAX_MANIFEST_BYTES + 1)
        if len(data) > MAX_MANIFEST_BYTES:
            raise CatalogError("The catalog manifest is too large")
        try:
            manifest = cast(object, json.loads(data))
        except ValueError as exc:
            raise CatalogError("The catalog manifest is not valid JSON") from exc
        if not isinstance(manifest, dict):
            raise CatalogError("The catalog manifest is not an object")
        return cast(dict[str, object], manifest)

    def _update(self, sidecar: dict[str, object]) -> bool:
        url = manifest_url()
        manifest = self._fetch_manifest(url)
        version = manifest.get(MANIFEST_SCHEMA_VERSION)
        if not isinstance(version, int) or version not in SUPPORTED_SCHEMA_VERSIONS:
            raise CatalogError("The published catalog needs a newer version of wLib")
        raw_snapshot = manifest.get(MANIFEST_SNAPSHOT)
        if not isinstance(raw_snapshot, dict):
            raise CatalogError("The catalog manifest has no snapshot")
        snapshot = cast(dict[str, object], raw_snapshot)
        path = snapshot.get(SNAPSHOT_PATH)
        size = snapshot.get(SNAPSHOT_SIZE)
        digest = snapshot.get(SNAPSHOT_SHA256)
        if not isinstance(path, str) or not isinstance(size, int) or not isinstance(digest, str):
            raise CatalogError("The catalog manifest's snapshot entry is incomplete")
        if not 0 < size <= MAX_SNAPSHOT_BYTES:
            raise CatalogError("The catalog snapshot is larger than allowed")

        installed = sidecar.get("manifest")
        if isinstance(installed, dict) and os.path.isfile(self.catalog_path):
            current = cast(dict[str, object], installed).get(MANIFEST_SNAPSHOT)
            if isinstance(current, dict) and cast(dict[str, object], current).get(
                SNAPSHOT_SHA256
            ) == digest:
                return False

        os.makedirs(self.data_dir, exist_ok=True)
        xz_fd, xz_tmp = tempfile.mkstemp(dir=self.data_dir, prefix=".catalog-", suffix=".xz")
        db_fd, db_tmp = tempfile.mkstemp(dir=self.data_dir, prefix=".catalog-", suffix=".db")
        os.close(db_fd)
        try:
            with os.fdopen(xz_fd, "wb") as target:
                self._download(snapshot_url(url, path), target, size, digest)
            limit = snapshot.get(SNAPSHOT_UNCOMPRESSED_SIZE)
            self._decompress(
                xz_tmp, db_tmp, limit if isinstance(limit, int) else MAX_UNCOMPRESSED_BYTES
            )
            self._verify(db_tmp)
            if not self._enabled():
                return False
            os.replace(db_tmp, self.catalog_path)
        finally:
            _remove(xz_tmp)
            _remove(db_tmp)
        sidecar["manifest"] = manifest
        return True

    def _download(self, url: str, target: _Writable, size: int, digest: str) -> None:
        hasher = hashlib.sha256()
        received = 0
        with closing(self._opener(url, self._headers())) as response:
            while chunk := response.read(_CHUNK):
                received += len(chunk)
                if received > size or received > MAX_SNAPSHOT_BYTES:
                    raise CatalogError("The catalog snapshot is larger than the manifest says")
                hasher.update(chunk)
                _ = target.write(chunk)
        if received != size:
            raise CatalogError("The catalog snapshot download was incomplete")
        if hasher.hexdigest() != digest:
            raise CatalogError("The catalog snapshot failed its checksum")

    def _decompress(self, source: str, target: str, expected: int) -> None:
        limit = min(max(expected, 0), MAX_UNCOMPRESSED_BYTES)
        written = 0
        try:
            with lzma.open(source, "rb") as reader, open(target, "wb") as writer:
                while chunk := reader.read(_CHUNK):
                    written += len(chunk)
                    if written > limit:
                        raise CatalogError("The catalog snapshot is larger than allowed")
                    _ = writer.write(chunk)
        except lzma.LZMAError as exc:
            raise CatalogError("The catalog snapshot could not be decompressed") from exc

    def _verify(self, path: str) -> None:
        try:
            conn = sqlite3.connect(f"{Path(os.path.abspath(path)).as_uri()}?mode=ro", uri=True)
            try:
                check = cast(tuple[str] | None, conn.execute("PRAGMA quick_check").fetchone())
                row = cast(
                    tuple[str] | None,
                    conn.execute(
                        "SELECT value FROM meta WHERE key = ?", (META_SCHEMA_VERSION,)
                    ).fetchone(),
                )
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise CatalogError("The catalog snapshot is not a readable database") from exc
        if check is None or check[0] != "ok":
            raise CatalogError("The catalog snapshot failed its integrity check")
        if row is None or not row[0].isdigit() or int(row[0]) not in SUPPORTED_SCHEMA_VERSIONS:
            raise CatalogError("The published catalog needs a newer version of wLib")

    def remove(self) -> None:
        """Delete the local catalog; used when the setting is turned off."""
        _remove(self.catalog_path)
        _remove(self.sidecar_path)

