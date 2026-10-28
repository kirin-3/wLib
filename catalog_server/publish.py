# SPDX-License-Identifier: GPL-3.0-or-later
"""Upload a snapshot and then the manifest that points to it."""

import importlib
import json
import logging
from collections.abc import Callable, Mapping
from typing import Protocol, cast

from core.catalog_format import (
    MANIFEST_GENERATED_AT,
    MANIFEST_KEY,
    MANIFEST_SCHEMA_VERSION,
    MANIFEST_SNAPSHOT,
    MANIFEST_SOURCES,
    SCHEMA_VERSION,
    SNAPSHOT_PATH,
    SNAPSHOT_PREFIX,
    SNAPSHOT_SHA256,
    SNAPSHOT_SIZE,
    SNAPSHOT_UNCOMPRESSED_SIZE,
)

from .config import SNAPSHOTS_TO_KEEP, R2Config
from .snapshot import Snapshot

log = logging.getLogger("catalog_server.publish")

SNAPSHOT_CACHE = "public, max-age=31536000, immutable"
MANIFEST_CACHE = "public, max-age=300"


class Storage(Protocol):
    def put_file(self, key: str, path: str, content_type: str, cache_control: str) -> None: ...

    def put_bytes(self, key: str, data: bytes, content_type: str, cache_control: str) -> None: ...

    def list_keys(self, prefix: str) -> list[str]: ...

    def delete(self, key: str) -> None: ...


class R2Storage:
    """Cloudflare R2 through its S3-compatible API. boto3 is a server-only dependency."""

    def __init__(self, config: R2Config) -> None:
        boto3 = importlib.import_module("boto3")
        make_client = cast(Callable[..., object], getattr(boto3, "client"))
        self._bucket: str = config.bucket
        self._client: object = make_client(
            "s3",
            endpoint_url=config.endpoint,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            region_name="auto",
        )

    def _call(self, method: str, **kwargs: object) -> object:
        call = cast(Callable[..., object], getattr(self._client, method))
        return call(Bucket=self._bucket, **kwargs)

    def put_file(self, key: str, path: str, content_type: str, cache_control: str) -> None:
        with open(path, "rb") as handle:
            _ = self._call(
                "put_object",
                Key=key,
                Body=handle,
                ContentType=content_type,
                CacheControl=cache_control,
            )

    def put_bytes(self, key: str, data: bytes, content_type: str, cache_control: str) -> None:
        _ = self._call(
            "put_object", Key=key, Body=data, ContentType=content_type, CacheControl=cache_control
        )

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {"Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = cast(dict[str, object], self._call("list_objects_v2", **kwargs))
            for item in cast(list[dict[str, object]], page.get("Contents") or []):
                keys.append(str(item["Key"]))
            if not page.get("IsTruncated"):
                return keys
            token = str(page.get("NextContinuationToken") or "")

    def delete(self, key: str) -> None:
        _ = self._call("delete_object", Key=key)


def build_manifest(
    snapshot: Snapshot, sources: Mapping[str, Mapping[str, object]]
) -> dict[str, object]:
    return {
        MANIFEST_SCHEMA_VERSION: SCHEMA_VERSION,
        MANIFEST_GENERATED_AT: snapshot.built_at,
        MANIFEST_SNAPSHOT: {
            # Relative to the manifest's base URL, so the bucket can change domains.
            SNAPSHOT_PATH: SNAPSHOT_PREFIX + snapshot.name,
            SNAPSHOT_SIZE: snapshot.size,
            SNAPSHOT_SHA256: snapshot.sha256,
            SNAPSHOT_UNCOMPRESSED_SIZE: snapshot.uncompressed_size,
        },
        MANIFEST_SOURCES: {name: dict(value) for name, value in sources.items()},
    }


def publish(
    storage: Storage, snapshot: Snapshot, sources: Mapping[str, Mapping[str, object]]
) -> dict[str, object]:
    """Snapshot first; the manifest is written only after that upload succeeded."""
    key = SNAPSHOT_PREFIX + snapshot.name
    storage.put_file(key, snapshot.path, "application/x-xz", SNAPSHOT_CACHE)
    log.info("uploaded %s (%d bytes)", key, snapshot.size)
    manifest = build_manifest(snapshot, sources)
    data = json.dumps(manifest, indent=2).encode()
    storage.put_bytes(MANIFEST_KEY, data, "application/json", MANIFEST_CACHE)
    log.info("uploaded %s (%d bytes)", MANIFEST_KEY, len(data))
    _ = prune_snapshots(storage, current=key)
    return manifest


def prune_snapshots(
    storage: Storage,
    keep: int = SNAPSHOTS_TO_KEEP,
    *,
    current: str | None = None,
) -> list[str]:
    """Keep the newest `keep` snapshots and the current manifest's snapshot.

    Names sort by their UTC timestamp, but a clock step backwards must not
    delete the snapshot the manifest was just switched to.
    """
    keys = sorted(
        key for key in storage.list_keys(SNAPSHOT_PREFIX) if key.endswith(".sqlite.xz")
    )
    retained: set[str] = set(keys[-keep:]) if keep > 0 else set()
    if current is not None:
        retained.add(current)
    stale = [key for key in keys if key not in retained]
    for key in stale:
        storage.delete(key)
        log.info("deleted %s", key)
    return stale
