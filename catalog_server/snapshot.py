# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the published snapshot: a compacted, xz-compressed copy of catalog.sqlite."""

import hashlib
import lzma
import os
import sqlite3
from dataclasses import dataclass

from core.catalog_format import META_BUILT_AT, META_SCHEMA_VERSION, SCHEMA_VERSION

from .config import XZ_PRESET
from .store import CatalogStore

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    path: str
    name: str
    size: int
    sha256: str
    uncompressed_size: int
    built_at: str


def snapshot_name(built_at: str) -> str:
    """`2026-10-27T05:12:09Z` → `catalog-20261027T051209Z.sqlite.xz`."""
    return f"catalog-{built_at.replace('-', '').replace(':', '')}.sqlite.xz"


def build_snapshot(catalog: CatalogStore, out_dir: str, built_at: str) -> Snapshot:
    os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, "snapshot.tmp.sqlite")
    name = snapshot_name(built_at)
    xz_path = os.path.join(out_dir, name)
    for path in (raw_path, xz_path):
        if os.path.exists(path):
            os.remove(path)
    # VACUUM INTO gives a consistent, compacted copy while the store stays open.
    _ = catalog.conn.execute("VACUUM INTO ?", (raw_path,))
    conn = sqlite3.connect(raw_path)
    try:
        _ = conn.execute("PRAGMA journal_mode=DELETE")
        with conn:
            _ = conn.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                + "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(META_SCHEMA_VERSION, str(SCHEMA_VERSION)), (META_BUILT_AT, built_at)],
            )
    finally:
        conn.close()

    digest = hashlib.sha256()
    compressor = lzma.LZMACompressor(preset=XZ_PRESET)
    size = 0
    with open(raw_path, "rb") as source, open(xz_path, "wb") as target:
        while chunk := source.read(_CHUNK):
            data = compressor.compress(chunk)
            digest.update(data)
            size += len(data)
            _ = target.write(data)
        data = compressor.flush()
        digest.update(data)
        size += len(data)
        _ = target.write(data)
    uncompressed_size = os.path.getsize(raw_path)
    os.remove(raw_path)
    return Snapshot(xz_path, name, size, digest.hexdigest(), uncompressed_size, built_at)
