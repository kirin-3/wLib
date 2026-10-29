# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest

from catalog_fakes import FakeTransport, fixture_json, json_response, make_context, rows
from catalog_server.runner import run_source
from catalog_server.sources.vndb import VndbSource


def _transport():
    vn = fixture_json("vndb_vn.json")
    release = fixture_json("vndb_release.json")
    vn["more"] = False
    release["more"] = False
    transport = FakeTransport()
    transport.route("/kana/vn", json_response(vn))
    transport.route("/kana/release", json_response(release))
    return transport


def test_vns_and_release_store_ids_are_stored(tmp_path):
    transport = _transport()
    ctx, _ = make_context(tmp_path, transport)
    result = run_source(VndbSource(), ctx)
    assert result.status == "ok" and result.sweep_completed
    vn = rows(ctx.catalog, "SELECT title, alttitle, developers, tags, url FROM vndb_vns WHERE vn_id = 'v1'")[0]
    assert vn[1] == "みんなでニャンニャン"
    assert json.loads(vn[2]) == ["Yamikumo-Communications"]
    assert json.loads(vn[3]) and vn[4] == "https://vndb.org/v1"
    release = rows(ctx.catalog, "SELECT vn_ids, dlsite_ids FROM vndb_releases WHERE release_id = 'r80'")[0]
    assert json.loads(release[0]) == ["v37"]
    assert json.loads(release[1]) == ["VJ002520"]
    # Only 18+ material is asked for.
    bodies = [json.loads(request.body) for request in transport.requests]
    assert bodies[0]["filters"] == ["release", "=", ["minage", "=", 18]]
    assert ["minage", "=", 18] in bodies[1]["filters"]
    assert all(request.method == "POST" for request in transport.requests)


def test_more_pages_with_no_results_is_a_failed_step(tmp_path):
    transport = FakeTransport()
    transport.route("/kana/vn", json_response({"results": [], "more": True}))
    ctx, _ = make_context(tmp_path, transport)
    with pytest.raises(ValueError, match="no results"):
        VndbSource().sweep_step(ctx)


def test_refresh_follows_the_weekly_schedule(tmp_path):
    transport = _transport()
    ctx, clock = make_context(tmp_path, transport)
    run_source(VndbSource(), ctx)
    sent = len(transport.requests)
    clock.now += 6 * 86400
    run_source(VndbSource(), ctx)
    assert len(transport.requests) == sent  # under a week: nothing
    clock.now += 86400
    run_source(VndbSource(), ctx)
    assert len(transport.requests) > sent


def test_paging_continues_after_the_last_id(tmp_path):
    first = fixture_json("vndb_vn.json")  # "more": true
    last = dict(first, results=[], more=False)
    transport = FakeTransport()
    calls = {"vn": 0}

    def vn(request):
        calls["vn"] += 1
        return json_response(first if calls["vn"] == 1 else last)

    transport.route("/kana/vn", vn)
    transport.route("/kana/release", json_response({"results": [], "more": False}))
    ctx, _ = make_context(tmp_path, transport)
    run_source(VndbSource(), ctx)
    second_filters = json.loads(transport.requests[1].body)["filters"]
    assert ["id", ">", first["results"][-1]["id"]] in second_filters
