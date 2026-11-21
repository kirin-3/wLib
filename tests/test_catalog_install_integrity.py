# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import shutil
import tempfile

import pytest

from tests.test_catalog_client import CatalogServer, _client, _leftovers, _titles

# Sidecar key that holds the SHA-256 of the installed catalog.db.
SIDECAR_DB_SHA256 = "catalog_sha256"


@pytest.fixture
def server(monkeypatch):
    instance = CatalogServer()
    monkeypatch.setenv("WLIB_CATALOG_MANIFEST_URL", instance.base + "v1/manifest.json")
    yield instance
    instance.close()


def test_older_valid_catalog_under_the_current_manifest_is_replaced(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("old",))
    client = _client(tmp_path)
    assert client.check() == {"success": True, "updated": True}
    old_copy = tmp_path / "old-catalog.db"
    shutil.copy2(client.catalog_path, old_copy)

    server.publish(tmp_path, "2026-10-28T05:00:00Z", titles=("new",))
    assert client.check(force=True) == {"success": True, "updated": True}
    assert _titles(client) == ["new"]

    # The user restores the older, valid database and keeps catalog.json.
    shutil.copy2(old_copy, client.catalog_path)
    server.requests.clear()

    assert client.check(force=True) == {"success": True, "updated": True}
    assert _titles(client) == ["new"]
    assert len(server.requests) == 2


def test_unchanged_catalog_is_not_downloaded_again(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("A",))
    client = _client(tmp_path)
    client.check()
    server.requests.clear()

    assert client.check(force=True) == {"success": True, "updated": False}
    assert [request["path"] for request in server.requests] == ["/v1/manifest.json"]


def test_sidecar_without_fingerprint_keeps_the_catalog_and_records_it(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("A",))
    client = _client(tmp_path)
    client.check()
    with open(client.sidecar_path, encoding="utf-8") as handle:
        data = json.load(handle)
    recorded = data.pop(SIDECAR_DB_SHA256)
    with open(client.sidecar_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    server.requests.clear()

    assert client.check(force=True) == {"success": True, "updated": False}
    assert [request["path"] for request in server.requests] == ["/v1/manifest.json"]
    with open(client.sidecar_path, encoding="utf-8") as handle:
        assert json.load(handle)[SIDECAR_DB_SHA256] == recorded


def test_failed_second_temp_file_closes_and_removes_the_first(
    tmp_path, server, monkeypatch
):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("A",))
    client = _client(tmp_path)
    real_mkstemp = tempfile.mkstemp
    opened = []

    def mkstemp(*args, **kwargs):
        if kwargs.get("suffix") == ".db":
            raise OSError(28, "No space left on device")
        fd, path = real_mkstemp(*args, **kwargs)
        if kwargs.get("suffix") == ".xz":
            info = os.fstat(fd)
            opened.append((fd, info.st_dev, info.st_ino))
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)

    result = client.check()

    assert result["success"] is False
    assert _leftovers(client) == []
    assert len(opened) == 1
    fd, dev, ino = opened[0]
    # Another thread can reuse the number, so check that it no longer names the temp file.
    try:
        info = os.fstat(fd)
    except OSError:
        return
    assert (info.st_dev, info.st_ino) != (dev, ino)
