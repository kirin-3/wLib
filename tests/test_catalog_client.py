# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from catalog_server.snapshot import build_snapshot
from catalog_server.store import CatalogStore
from core import catalog as catalog_module
from core.catalog import CatalogClient


class CatalogServer:
    """Serves /v1/manifest.json and snapshots from memory, recording requests."""

    def __init__(self):
        self.files = {}
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                server.requests.append({"path": self.path, "headers": dict(self.headers)})
                body = server.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def publish(self, tmp_path, stamp, titles=("A",), schema_version=1, corrupt=False):
        folder = tmp_path / f"build-{stamp.replace(':', '')}"
        folder.mkdir()
        store = CatalogStore(str(folder / "catalog.sqlite"))
        store.upsert(
            "f95_threads",
            "thread_id",
            [{"thread_id": index, "title": title, "url": "u"} for index, title in enumerate(titles)],
            stamp,
        )
        snapshot = build_snapshot(store, str(folder / "out"), stamp)
        store.close()
        data = open(snapshot.path, "rb").read()
        path = f"v1/snapshots/{snapshot.name}"
        self.files["/" + path] = data + (b"x" if corrupt else b"")
        manifest = {
            "schema_version": schema_version,
            "generated_at": stamp,
            "snapshot": {
                "path": path,
                "size": len(data) + (1 if corrupt else 0),
                "sha256": hashlib.sha256(data).hexdigest(),
                "uncompressed_size": snapshot.uncompressed_size,
            },
            "sources": {"f95": {"count": len(titles), "updated_at": stamp}},
        }
        self.files["/v1/manifest.json"] = json.dumps(manifest).encode()
        return manifest

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    instance = CatalogServer()
    monkeypatch.setenv("WLIB_CATALOG_MANIFEST_URL", instance.base + "v1/manifest.json")
    yield instance
    instance.close()


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def _client(tmp_path, clock=None, enabled=lambda: True):
    return CatalogClient(str(tmp_path / "data"), app_version="9.9.9", enabled=enabled, clock=clock or Clock())


def _titles(client):
    conn = sqlite3.connect(f"file:{client.catalog_path}?mode=ro", uri=True)
    try:
        return [row[0] for row in conn.execute("SELECT title FROM f95_threads ORDER BY thread_id")]
    finally:
        conn.close()


def _leftovers(client):
    return [name for name in os.listdir(client.data_dir) if name.startswith(".catalog-")]


def test_installs_a_snapshot_and_reports_status(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("A", "B"))
    client = _client(tmp_path)
    assert client.check() == {"success": True, "updated": True}
    assert _titles(client) == ["A", "B"]
    status = client.status()
    assert status["installed"] is True
    assert status["built_at"] == "2026-10-27T05:00:00Z"
    assert status["sources"] == {"f95": {"count": 2, "updated_at": "2026-10-27T05:00:00Z"}}
    assert status["last_check"] and status["last_error"] is None
    assert _leftovers(client) == []


def test_data_dir_with_uri_characters_can_install(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("A",))
    client = CatalogClient(
        # Windows forbids "?" in file names; "%" still exercises URI escaping there.
        str(tmp_path / ("data #1 %3F2" if sys.platform == "win32" else "data #1 ?2")),
        app_version="9.9.9",
    )
    assert client.check() == {"success": True, "updated": True}
    assert client.status()["installed"] is True


def test_unchanged_hash_downloads_only_the_manifest(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z")
    client = _client(tmp_path)
    client.check()
    server.requests.clear()
    assert client.check(force=True) == {"success": True, "updated": False}
    assert [request["path"] for request in server.requests] == ["/v1/manifest.json"]


def test_checks_at_most_every_12_hours_unless_forced(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z")
    clock = Clock()
    _client(tmp_path, clock).check()
    server.requests.clear()
    # Restarted within the hour: a new client reads the sidecar and skips.
    clock.now += 3600
    assert _client(tmp_path, clock).check()["skipped"] == "recent"
    assert server.requests == []
    assert _client(tmp_path, clock).check(force=True)["success"] is True
    assert len(server.requests) == 1
    clock.now += 12 * 3600 + 1
    _client(tmp_path, clock).check()
    assert len(server.requests) == 2


def test_corrupt_installed_catalog_is_replaced_even_when_manifest_is_unchanged(
    tmp_path, server
):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("A",))
    client = _client(tmp_path)
    client.check()
    with open(client.catalog_path, "wb") as handle:
        _ = handle.write(b"not a database")
    server.requests.clear()

    assert client.check(force=True) == {"success": True, "updated": True}
    assert _titles(client) == ["A"]
    assert [request["path"] for request in server.requests] == [
        "/v1/manifest.json",
        "/v1/snapshots/catalog-20261027T050000Z.sqlite.xz",
    ]


def test_newer_snapshot_replaces_the_old_one(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("old",))
    client = _client(tmp_path)
    client.check()
    server.publish(tmp_path, "2026-10-27T17:00:00Z", titles=("new",))
    assert client.check(force=True)["updated"] is True
    assert _titles(client) == ["new"]
    assert client.status()["built_at"] == "2026-10-27T17:00:00Z"


def test_hash_mismatch_keeps_the_old_catalog(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("old",))
    client = _client(tmp_path)
    client.check()
    manifest = server.publish(tmp_path, "2026-10-27T17:00:00Z", titles=("new",))
    manifest["snapshot"]["sha256"] = "0" * 64
    server.files["/v1/manifest.json"] = json.dumps(manifest).encode()
    result = client.check(force=True)
    assert result["success"] is False and "checksum" in result["error"]
    assert _titles(client) == ["old"]
    status = client.status()
    assert status["built_at"] == "2026-10-27T05:00:00Z"
    assert "checksum" in status["last_error"]
    assert _leftovers(client) == []


def test_oversized_download_is_rejected(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", corrupt=True)
    manifest = json.loads(server.files["/v1/manifest.json"])
    manifest["snapshot"]["size"] -= 1
    server.files["/v1/manifest.json"] = json.dumps(manifest).encode()
    client = _client(tmp_path)
    assert client.check()["success"] is False
    assert not os.path.exists(client.catalog_path)


def test_unsupported_schema_is_not_downloaded(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("old",))
    client = _client(tmp_path)
    client.check()
    server.publish(tmp_path, "2026-10-27T17:00:00Z", schema_version=2)
    server.requests.clear()
    result = client.check(force=True)
    assert result["success"] is False and "newer version of wLib" in result["error"]
    assert [request["path"] for request in server.requests] == ["/v1/manifest.json"]
    assert _titles(client) == ["old"]


def test_failure_before_replace_leaves_the_old_file(tmp_path, server, monkeypatch):
    server.publish(tmp_path, "2026-10-27T05:00:00Z", titles=("old",))
    client = _client(tmp_path)
    client.check()
    before = open(client.catalog_path, "rb").read()
    server.publish(tmp_path, "2026-10-27T17:00:00Z", titles=("new",))

    def crash(path):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(client, "_verify", crash)
    assert client.check(force=True)["success"] is False
    assert open(client.catalog_path, "rb").read() == before
    assert _leftovers(client) == []


def test_library_database_is_not_modified(tmp_path, server, monkeypatch):
    from core import database

    library = tmp_path / "data" / "wlib.db"
    library.parent.mkdir(parents=True)
    monkeypatch.setattr(database, "DB_PATH", str(library))
    database.init_db()
    database.add_game("Kept", "/games/kept")
    before = hashlib.sha256(library.read_bytes()).hexdigest()
    mtime = library.stat().st_mtime_ns
    server.publish(tmp_path, "2026-10-27T05:00:00Z")
    _client(tmp_path).check()
    assert hashlib.sha256(library.read_bytes()).hexdigest() == before
    assert library.stat().st_mtime_ns == mtime


def test_requests_carry_no_query_cookies_or_library_data(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z")
    _client(tmp_path).check()
    assert len(server.requests) == 2
    for request in server.requests:
        assert "?" not in request["path"]
        headers = {key.lower(): value for key, value in request["headers"].items()}
        assert "cookie" not in headers and "authorization" not in headers
        assert headers["user-agent"].startswith("wLib/9.9.9")
        assert set(headers) <= {"host", "user-agent", "accept-encoding", "connection"}


def test_unreachable_address_is_recorded_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_CATALOG_MANIFEST_URL", "http://127.0.0.1:9/v1/manifest.json")
    client = _client(tmp_path)
    result = client.check()
    assert result["success"] is False
    assert client.status()["last_error"]
    assert client.status()["installed"] is False


def test_disabled_makes_no_requests_and_remove_deletes_files(tmp_path, server):
    server.publish(tmp_path, "2026-10-27T05:00:00Z")
    enabled = {"on": True}
    client = _client(tmp_path, enabled=lambda: enabled["on"])
    client.check()
    assert os.path.exists(client.catalog_path) and os.path.exists(client.sidecar_path)
    enabled["on"] = False
    client.remove()
    server.requests.clear()
    assert client.check(force=True)["skipped"] == "disabled"
    assert server.requests == []
    assert not os.path.exists(client.catalog_path)
    assert not os.path.exists(client.sidecar_path)


def test_default_url_and_override(monkeypatch):
    monkeypatch.delenv("WLIB_CATALOG_MANIFEST_URL", raising=False)
    assert catalog_module.manifest_url() == "https://wlib-db.thekirin.net/v1/manifest.json"
    assert (
        catalog_module.snapshot_url(catalog_module.manifest_url(), "v1/snapshots/catalog-1.sqlite.xz")
        == "https://wlib-db.thekirin.net/v1/snapshots/catalog-1.sqlite.xz"
    )
    monkeypatch.setenv("WLIB_CATALOG_MANIFEST_URL", "http://127.0.0.1:8000/v1/manifest.json")
    assert catalog_module.manifest_url() == "http://127.0.0.1:8000/v1/manifest.json"
    with pytest.raises(catalog_module.CatalogError):
        catalog_module.snapshot_url(catalog_module.manifest_url(), "../../etc/passwd")
    with pytest.raises(catalog_module.CatalogError):
        catalog_module.snapshot_url(catalog_module.manifest_url(), "https://evil.example/x.sqlite.xz")


def test_background_check_stays_available_when_a_thread_cannot_start(
    tmp_path, monkeypatch
):
    class FailingThread:
        def __init__(self, *args, **kwargs):
            _ = (args, kwargs)

        def start(self):
            raise OSError("no threads")

    monkeypatch.setattr(catalog_module.threading, "Thread", FailingThread)
    client = _client(tmp_path)
    assert client.start_background() is False
    # A later attempt is not blocked by a stuck "checking" flag.
    assert client.status()["checking"] is False


def test_background_check_does_not_block(tmp_path, server, monkeypatch):
    # conftest disables start_background for Api(); exercise the real one here.
    monkeypatch.undo()
    monkeypatch.setenv("WLIB_CATALOG_MANIFEST_URL", server.base + "v1/manifest.json")
    server.publish(tmp_path, "2026-10-27T05:00:00Z")
    client = _client(tmp_path)
    assert client.start_background() is True
    for _ in range(200):
        if not client.status()["checking"]:
            break
        threading.Event().wait(0.02)
    assert client.status()["installed"] is True
