# SPDX-License-Identifier: GPL-3.0-or-later
import json
import logging

import pytest

from catalog_server.config import R2Config, r2_config_from_env
from catalog_server.publish import publish
from catalog_server.snapshot import build_snapshot
from catalog_server.store import CatalogStore


class MemoryStorage:
    def __init__(self, fail_on=None):
        self.objects = {}
        self.order = []
        self.fail_on = fail_on

    def _put(self, key, data, content_type, cache_control):
        if self.fail_on and key.startswith(self.fail_on):
            raise ConnectionError("upload interrupted")
        self.objects[key] = {"data": data, "type": content_type, "cache": cache_control}
        self.order.append(key)

    def put_file(self, key, path, content_type, cache_control):
        with open(path, "rb") as handle:
            self._put(key, handle.read(), content_type, cache_control)

    def put_bytes(self, key, data, content_type, cache_control):
        self._put(key, data, content_type, cache_control)

    def list_keys(self, prefix):
        return [key for key in self.objects if key.startswith(prefix)]

    def delete(self, key):
        del self.objects[key]


def _snapshot(tmp_path, stamp="2026-10-27T05:12:09Z"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    return build_snapshot(catalog, str(tmp_path / "out"), stamp)


SOURCES = {"f95": {"count": 1, "updated_at": "2026-10-27T05:00:00Z"}}


def test_snapshot_then_manifest_with_relative_path(tmp_path):
    storage = MemoryStorage()
    snapshot = _snapshot(tmp_path)
    manifest = publish(storage, snapshot, SOURCES)
    assert storage.order == ["v1/snapshots/catalog-20261027T051209Z.sqlite.xz", "v1/manifest.json"]
    assert storage.objects[storage.order[0]]["cache"] == "public, max-age=31536000, immutable"
    assert storage.objects["v1/manifest.json"]["cache"] == "public, max-age=300"
    stored = json.loads(storage.objects["v1/manifest.json"]["data"])
    assert stored == manifest
    assert stored["schema_version"] == 1
    assert stored["generated_at"] == "2026-10-27T05:12:09Z"
    assert stored["snapshot"]["path"] == "v1/snapshots/catalog-20261027T051209Z.sqlite.xz"
    assert not stored["snapshot"]["path"].startswith(("/", "http"))
    assert stored["snapshot"]["sha256"] == snapshot.sha256
    assert stored["snapshot"]["size"] == snapshot.size
    assert stored["sources"] == SOURCES


def test_failed_snapshot_upload_leaves_the_manifest_alone(tmp_path):
    storage = MemoryStorage(fail_on="v1/snapshots/")
    storage.objects["v1/manifest.json"] = {"data": b"previous", "type": "", "cache": ""}
    with pytest.raises(ConnectionError):
        publish(storage, _snapshot(tmp_path), SOURCES)
    assert storage.objects["v1/manifest.json"]["data"] == b"previous"


def test_retention_keeps_the_four_newest(tmp_path):
    storage = MemoryStorage()
    stamps = [f"2026-10-2{day}T05:00:00Z" for day in range(1, 6)]
    for index, stamp in enumerate(stamps):
        publish(storage, _snapshot(tmp_path / str(index), stamp), SOURCES)
    kept = sorted(storage.list_keys("v1/snapshots/"))
    assert len(kept) == 4
    assert "v1/snapshots/catalog-20261021T050000Z.sqlite.xz" not in kept
    current = json.loads(storage.objects["v1/manifest.json"]["data"])["snapshot"]["path"]
    assert current == kept[-1]


def test_current_snapshot_survives_a_backwards_clock(tmp_path):
    storage = MemoryStorage()
    for stamp in (
        "2026-10-24T05:00:00Z",
        "2026-10-25T05:00:00Z",
        "2026-10-26T05:00:00Z",
        "2026-10-27T05:00:00Z",
    ):
        publish(storage, _snapshot(tmp_path / stamp.replace(":", ""), stamp), SOURCES)
    manifest = publish(
        storage,
        _snapshot(tmp_path / "older", "2026-10-20T05:00:00Z"),
        SOURCES,
    )
    current = manifest["snapshot"]["path"]
    assert current in storage.objects
    assert len(storage.list_keys("v1/snapshots/")) == 5


def test_logs_name_keys_and_sizes_but_no_credentials(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "AKIDSECRETVALUE")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "SUPERSECRETKEY")
    config = r2_config_from_env()
    assert isinstance(config, R2Config) and config.bucket == "wlib-db"
    with caplog.at_level(logging.DEBUG):
        publish(MemoryStorage(), _snapshot(tmp_path), SOURCES)
    text = caplog.text
    assert "v1/manifest.json" in text and "bytes" in text
    for secret in ("AKIDSECRETVALUE", "SUPERSECRETKEY"):
        assert secret not in text
