# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F811 - pytest fixtures imported from test_catalog_client
"""Regression tests for the second catalog bug hunt."""

import json
import os
import sqlite3
import threading

import pytest

from catalog_fakes import FakeTransport, fixture_bytes, fixture_json, json_response
from catalog_fakes import make_context, rows
from catalog_server.net import Response
from catalog_server.sources.dlsite import DlsiteSource
from catalog_server.sources.ryuugames import RyuugamesSource
from catalog_server.store import CATALOG_SCHEMA
from core import catalog as catalog_module
from core import database
from core.game_lookup import f95_thread_details, f95_thread_ratings, resolve_page
from test_catalog_client import _client, server  # noqa: F401 - fixture

OLD = "2026-01-01T00:00:00Z"
GONE = "2026-10-20T00:00:00Z"
MAX_ID = 2**63 - 1


# 1. Ryuugames: a response without a valid page count is a failed step.


def _ryuu_ctx(tmp_path, headers):
    transport = FakeTransport()
    transport.route(
        "/wp-json/wp/v2/posts",
        Response(200, fixture_bytes("ryuugames_posts.json"), headers),
    )
    return make_context(tmp_path, transport)


@pytest.mark.parametrize(
    "headers", [{}, {"X-WP-TotalPages": ""}, {"X-WP-TotalPages": "many"}]
)
def test_ryuugames_page_without_a_valid_count_does_not_complete_the_sweep(
    tmp_path, headers
):
    ctx, _ = _ryuu_ctx(tmp_path, headers)
    source = RyuugamesSource()
    with pytest.raises(ValueError, match="page count"):
        source.sweep_step(ctx)
    # The sweep is not advanced, so the next run reads page 1 again.
    assert ctx.state.get("ryuugames", "sweep_page") is None


def test_ryuugames_valid_zero_count_is_an_empty_result(tmp_path):
    transport = FakeTransport()
    transport.route(
        "/wp-json/wp/v2/posts", Response(200, b"[]", {"X-WP-TotalPages": "0"})
    )
    ctx, clock = make_context(tmp_path, transport)
    source = RyuugamesSource()
    assert source.sweep_step(ctx) is True
    ctx.state.put("ryuugames", "modified_since", OLD)
    clock.now += 60
    source.incremental(ctx)
    assert ctx.state.get("ryuugames", "modified_since") != OLD


# 2. DLsite: a stored work the details response omits stays seen.


def test_dlsite_partial_details_keep_an_omitted_stored_work(tmp_path):
    listing = fixture_json("dlsite_listing.json")
    listing["page_info"]["count"] = 3
    details = fixture_json("dlsite_product_info.json")
    del details["VJ01006811"]
    transport = FakeTransport()
    transport.route("/product/info/ajax", json_response(details))
    transport.route("/fsr/ajax/", json_response(listing))
    ctx, clock = make_context(tmp_path, transport)
    ctx.catalog.upsert(
        "dlsite_works",
        "product_id",
        [
            {"product_id": "VJ01006811", "title": "kept", "url": "u"},
            # Stored, but now explicitly all-ages: it must still be retired.
            {"product_id": "VJ01006709", "title": "all ages", "url": "u"},
        ],
        OLD,
    )
    clock.now += 3600
    started = ctx.now()
    ctx.state.put("dlsite", "sweep_cursor", [1, 1])
    assert DlsiteSource().sweep_step(ctx) is True
    clock.now += 60
    ctx.catalog.mark_removed("dlsite", started, ctx.now())
    removed = dict(rows(ctx.catalog, "SELECT product_id, removed_at FROM dlsite_works"))
    assert removed["VJ01006811"] is None
    assert removed["VJ01006817"] is None
    assert removed["VJ01006709"] is not None


# 3. Ryuugames lookup: raw post IDs whose catalog row is removed are dropped.


@pytest.fixture
def lookup_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()
    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.executescript(CATALOG_SCHEMA)
    yield conn
    conn.close()


def _game_with(url: str) -> int:
    game_id = database.add_game("Game", "")
    database.replace_game_links(game_id, "page", [{"url": url}])
    return game_id


def test_ryuugames_lookup_drops_removed_raw_post_ids(lookup_catalog):
    conn = lookup_catalog
    conn.execute(
        "INSERT INTO ryuu_posts (post_id,title,raw_title,rj_codes,vndb_ids,url,first_seen,last_seen) "
        + "VALUES (60,'Post','Post','[\"RJ9\", \"RJ10\"]','[\"v9\"]','https://www.ryuugames.com/post/','','')"
    )
    conn.execute(
        "INSERT INTO dlsite_works (product_id,title,url,first_seen,last_seen,removed_at) "
        + "VALUES ('RJ9','Gone','u','','',?)",
        (GONE,),
    )
    conn.execute(
        "INSERT INTO vndb_vns (vn_id,title,url,first_seen,last_seen,removed_at) "
        + "VALUES ('v9','Gone','u','','',?)",
        (GONE,),
    )
    conn.commit()
    _ = _game_with("https://www.dlsite.com/maniax/work/=/product_id/RJ9.html")
    _ = _game_with("https://vndb.org/v9")
    result = resolve_page("https://www.ryuugames.com/post/", ["ryuugames:60"])
    assert result["matches"] == []
    ids = {(link["source"], link["id"]) for link in result["ids"]}
    assert ("dlsite", "RJ9") not in ids and ("vndb", "v9") not in ids
    # A raw ID without a catalog row still resolves.
    assert ("dlsite", "RJ10") in ids


# 4. One thread ID above the SQLite range must not abort the bulk reads.


def test_bulk_reads_skip_ids_outside_the_sqlite_range(lookup_catalog):
    conn = lookup_catalog
    conn.executemany(
        "INSERT INTO f95_threads (thread_id,title,rating,url,first_seen,last_seen) "
        + "VALUES (?,'T',4.5,'u','','')",
        [(1,), (MAX_ID,)],
    )
    conn.executemany(
        "INSERT INTO f95_thread_details (thread_id,thread_ts,fetched_at,status,overview,"
        + "original_title,aliases,language,languages,external_ids) "
        + "VALUES (?,1,'x','ok','Text','','[]','English','[]','{}')",
        [(1,), (MAX_ID,)],
    )
    conn.commit()
    thread_ids = [1, 1, MAX_ID, MAX_ID + 1, 2**64]
    _, ratings = f95_thread_ratings(thread_ids)
    assert ratings == {1: (4.5, None), MAX_ID: (4.5, None)}
    details = f95_thread_details(thread_ids)
    assert set(details) == {1, MAX_ID}
    assert details[1]["overview"] == "Text"


# 5. Turning the catalog off while a check writes catalog.json.


def _pause_sidecar_replace(monkeypatch, client):
    real_replace = os.replace
    entered = threading.Event()
    release = threading.Event()

    def replace(source, target):
        if target == client.sidecar_path and not entered.is_set():
            entered.set()
            assert release.wait(5)
        real_replace(source, target)

    monkeypatch.setattr(catalog_module.os, "replace", replace)
    return entered, release


def _disable_during_sidecar_write(client, enabled, entered, release):
    worker = threading.Thread(target=lambda: client.check(force=True))
    remover = threading.Thread(target=client.remove)
    worker.start()
    try:
        assert entered.wait(5)
        # The setting is saved before remove() runs, as in save_settings().
        enabled["on"] = False
        remover.start()
        # Give remove() time to finish while the sidecar write is paused.
        remover.join(0.3)
    finally:
        release.set()
        worker.join(5)
    remover.join(5)
    assert not worker.is_alive() and not remover.is_alive()
    assert not os.path.exists(client.sidecar_path)
    assert not os.path.exists(client.catalog_path)


def test_disable_during_a_failed_check_leaves_no_sidecar(tmp_path, monkeypatch):
    enabled = {"on": True}

    def opener(url, headers):
        raise OSError("offline")

    client = catalog_module.CatalogClient(
        str(tmp_path / "data"),
        app_version="9.9.9",
        enabled=lambda: enabled["on"],
        opener=opener,
    )
    entered, release = _pause_sidecar_replace(monkeypatch, client)
    _disable_during_sidecar_write(client, enabled, entered, release)


def test_disable_during_an_install_leaves_no_sidecar(tmp_path, server, monkeypatch):
    server.publish(tmp_path, "2026-10-27T05:00:00Z")
    enabled = {"on": True}
    client = _client(tmp_path, enabled=lambda: enabled["on"])
    entered, release = _pause_sidecar_replace(monkeypatch, client)
    _disable_during_sidecar_write(client, enabled, entered, release)


# 6. A retention failure after the manifest upload is a published run.


def test_retention_failure_after_the_manifest_upload_records_the_publish(tmp_path):
    from test_catalog_publish import MemoryStorage

    from catalog_server.notify import build_report
    from catalog_server.publish import MANIFEST_KEY
    from catalog_server.runner import run

    class PruneFails(MemoryStorage):
        def list_keys(self, prefix):
            raise ConnectionError("listing failed")

    storage = PruneFails()
    ctx, clock = make_context(tmp_path, FakeTransport())
    reports = []
    with pytest.raises(ConnectionError):
        run(
            ctx.catalog,
            ctx.state,
            ctx.pacer,
            {},
            [],
            storage=storage,
            work_dir=str(tmp_path),
            now=clock.iso,
            force_publish=True,
            report=reports.append,
        )
    manifest = json.loads(storage.objects[MANIFEST_KEY]["data"])
    published = ctx.state.recent_runs()[0]["published"]
    assert published and manifest["snapshot"]["path"].endswith(str(published))
    (result,) = reports
    assert result.manifest == manifest
    assert "listing failed" in result.error
    embed = build_report(result, sources=frozenset(), skipped={})["embeds"][0]
    assert embed["title"] == "Catalog published, with problems"
