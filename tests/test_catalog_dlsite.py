# SPDX-License-Identifier: GPL-3.0-or-later
import json

from catalog_fakes import FakeTransport, fixture_json, json_response, make_context, rows
from catalog_server.sources.dlsite import DlsiteSource, parse_listing


def _transport(listing=None):
    transport = FakeTransport()
    transport.route("/product/info/ajax", json_response(fixture_json("dlsite_product_info.json")))
    transport.route("/fsr/ajax/", json_response(listing or fixture_json("dlsite_listing.json")))
    return transport


def test_listing_parses_ids_circles_and_genres():
    items, total = parse_listing(fixture_json("dlsite_listing.json"))
    assert total == 39684
    assert [item["product_id"] for item in items] == ["VJ01006811", "VJ01006817", "VJ01006709"]
    assert items[0]["circle"] == "アニム" and items[0]["circle_id"] == "VG01022"
    assert items[0]["genres"]


def test_non_adult_works_are_dropped_and_circles_merged(tmp_path):
    transport = _transport()
    ctx, _ = make_context(tmp_path, transport)
    source = DlsiteSource()
    ctx.state.put("dlsite", "sweep_cursor", [1, 1])
    source.sweep_step(ctx)
    stored = rows(ctx.catalog, "SELECT product_id, circle, age_category, genres, cover_url, url FROM dlsite_works ORDER BY product_id")
    assert [row[0] for row in stored] == ["VJ01006811", "VJ01006817"]
    assert stored[0][1] == "アニム" and stored[1][1] == "縁"
    assert {row[2] for row in stored} == {3}
    assert json.loads(stored[0][3])
    assert stored[0][4].startswith("https://img.dlsite.jp/")
    assert stored[0][5] == "https://www.dlsite.com/pro/work/=/product_id/VJ01006811.html"
    # The all-ages work is remembered as skipped, not stored.
    assert ctx.state.skipped_dlsite(["VJ01006709"]) == {"VJ01006709"}
    info_requests = [request for request in transport.requests if "info/ajax" in request.url]
    assert "VJ01006811,VJ01006817,VJ01006709" in info_requests[0].url


def test_sweep_walks_maniax_then_pro(tmp_path):
    listing = fixture_json("dlsite_listing.json")
    listing["page_info"]["count"] = 150
    transport = _transport(listing)
    ctx, _ = make_context(tmp_path, transport)
    source = DlsiteSource()
    done = []
    while not (finished := source.sweep_step(ctx)):
        done.append(ctx.state.get("dlsite", "sweep_cursor"))
    assert finished
    assert done == [[0, 2], [1, 1], [1, 2]]
    listing_urls = [request.url for request in transport.requests if "/fsr/ajax/" in request.url]
    assert [url.split("/")[3] for url in listing_urls] == ["maniax", "maniax", "pro", "pro"]


def test_incremental_fetches_details_only_for_new_ids(tmp_path):
    transport = _transport()
    ctx, _ = make_context(tmp_path, transport)
    ctx.state.mark_sweep_completed("dlsite", "2026-01-01T00:00:00Z")
    ctx.catalog.upsert(
        "dlsite_works",
        "product_id",
        [{"product_id": "VJ01006811", "title": "old", "url": "u"}],
        "2026-01-01T00:00:00Z",
    )
    ctx.state.add_skipped_dlsite(["VJ01006709"])
    DlsiteSource().incremental(ctx)
    info = [request.url for request in transport.requests if "info/ajax" in request.url]
    assert info and all("product_id=VJ01006817&" in url for url in info)
