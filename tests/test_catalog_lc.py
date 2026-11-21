# SPDX-License-Identifier: GPL-3.0-or-later
"""LewdCorner latest-updates feed (`lc`)."""

import copy
import json
import re

import pytest

from catalog_fakes import (
    FakeClock,
    fixture_bytes,
    fixture_json,
    json_response,
    make_context,
    rows,
)
from catalog_server.net import Pacer, Response
from catalog_server.runner import run_source
from catalog_server.sources.lc import LcSource, parse_page

PAGE = fixture_json("lc_latest_page.json")
CHALLENGE = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"


def _items():
    return copy.deepcopy(PAGE["result"]["items"])


def _page(number, items, total_items):
    payload = copy.deepcopy(PAGE)
    payload["result"].update(
        {
            "items": items,
            "page": number,
            "total": total_items,
            "hasMore": number * 90 < total_items,
        }
    )
    return json_response(payload)


def _page_number(request):
    return int(re.search(r"[?&]page=(\d+)", request.url).group(1))


def test_feed_items_become_rows_with_tier_prefixes_and_screens():
    found, names, count, pages, has_more = parse_page(PAGE)
    assert (count, pages, has_more) == (90, 105, True)
    assert len(found) == 90
    row = next(row for row in found if row["thread_id"] == 25452)
    assert (
        row["title"]
        == "The Room You Can't Leave If You Have Sex With Your Female Friend"
    )
    assert (row["creator"], row["version"], row["node_id"], row["tier"]) == (
        "moguyo",
        "Final",
        6,
        "Free",
    )
    assert row["prefixes"] == [14, 8, 28]
    assert names[14] == "Completed" and names[8] == "RPGM" and names[28] == "Hand Drawn"
    assert row["updated_at"] == "2026-11-17T15:08:59Z" and row["ts"] == 1794928139
    assert row["tags"][:2] == ["2d-game", "2dcg"]
    assert row["platforms"] == ["Windows"]
    assert (
        row["cover_url"]
        == "https://lewdcorner.com/attachments/1794927833369-png.891449/"
    )
    assert row["cover_url"] not in row["screens"] and len(row["screens"]) == 2
    assert row["url"] == "https://lewdcorner.com/threads/25452/"
    ai_game = next(row for row in found if row["thread_id"] == 25050)
    assert ai_game["node_id"] == 212


def test_premium_items_and_other_forums(caplog):
    items = _items()[:3]
    items[0]["is_premium"] = True
    items[0]["node_id"] = 119
    items[1]["node_id"] = 215  # The test zone is not a game forum.
    payload = copy.deepcopy(PAGE)
    payload["result"]["items"] = items
    with caplog.at_level("INFO"):
        found, _names, count, _pages, _more = parse_page(payload)
    assert count == 3
    assert [(row["thread_id"], row["tier"]) for row in found] == [
        (25050, "Premium"),
        (25452, "Free"),
    ]
    assert "skipped 1 items outside the game forums: node 215: 1" in caplog.text


def test_sweep_stores_prefix_names_and_resumes_after_429(tmp_path):
    items = _items()
    # 135 items is two feed pages.
    pages = {1: _page(1, items[:45], 135), 2: _page(2, items[45:], 135)}
    limited = {"on": True}

    def transport(request):
        number = _page_number(request)
        if number == 2 and limited["on"]:
            return Response(429, b"")
        return pages[number]

    ctx, _ = make_context(tmp_path, transport)
    result = run_source(LcSource(), ctx)
    assert result.status == "rate_limited"
    assert ctx.catalog.count("lc") == 45
    assert ctx.state.get("lc", "sweep_page") == 2
    assert ctx.catalog.conn.execute(
        "SELECT name FROM lc_prefix_names WHERE id = 8"
    ).fetchone() == ("RPGM",)

    limited["on"] = False
    clock = FakeClock(start=1_900_000_000.0)
    ctx.pacer = Pacer(ctx.state, transport, clock=clock, sleep=clock.sleep)
    result = run_source(LcSource(), ctx)
    assert result.status == "ok" and result.sweep_completed
    assert ctx.catalog.count("lc") == 90


def test_incremental_stops_at_the_high_water_mark(tmp_path):
    items = _items()
    newest = items[0]["timestamp"]
    requested = []

    def transport(request):
        number = _page_number(request)
        requested.append(number)
        return _page(number, items[(number - 1) * 10 : number * 10], 9443)

    ctx, _ = make_context(tmp_path, transport)
    # The mark is inside page 2: page 2 still has newer items, page 3 does not.
    ctx.state.put("lc", "hwm_ts", items[15]["timestamp"])
    LcSource().incremental(ctx)
    assert requested == [1, 2, 3]
    assert ctx.state.get("lc", "hwm_ts") == newest


def test_an_empty_page_inside_the_sweep_is_an_error(tmp_path):
    ctx, _ = make_context(
        tmp_path, lambda request: _page(_page_number(request), [], 9443)
    )
    ctx.state.put("lc", "sweep_page", 3)
    ctx.state.put("lc", "sweep_total_pages", 105)
    result = run_source(LcSource(), ctx)
    assert result.status == "error" and "empty page 3 of 105" in (result.detail or "")
    assert ctx.state.get("lc", "sweep_page") == 3


def test_a_page_without_a_total_does_not_end_the_sweep(tmp_path):
    payload = copy.deepcopy(PAGE)
    del payload["result"]["total"]
    ctx, _ = make_context(tmp_path, lambda request: json_response(payload))
    result = run_source(LcSource(), ctx)
    assert result.status == "error" and "without a total" in (result.detail or "")
    assert ctx.state.source_status("lc")["sweep_completed_at"] is None


@pytest.mark.parametrize(
    "response",
    [
        Response(200, fixture_bytes("lc_feed_logged_out.json")),
        Response(403, CHALLENGE),
        Response(200, CHALLENGE),
        Response(401, b'{"status": "error"}'),
    ],
    ids=["logged-out-json", "challenge-403", "html-200", "http-401"],
)
def test_no_login_stores_nothing_and_ends_only_the_feed(tmp_path, response):
    ctx, _ = make_context(tmp_path, lambda request: response)
    result = run_source(LcSource(), ctx)
    assert result.status == "error" and "no LC login" in (result.detail or "")
    assert rows(ctx.catalog, "SELECT COUNT(*) FROM lc_threads") == [(0,)]
    # The guest steps still run after a lost login.
    assert "lc" not in ctx.pacer.paused
    # Imported rows and prefix names are not touched either.
    assert rows(ctx.catalog, "SELECT COUNT(*) FROM lc_prefix_names") == [(0,)]


def test_a_feed_listing_turns_an_imported_premium_row_into_a_free_row(tmp_path):
    items = _items()
    ctx, _ = make_context(tmp_path, lambda request: _page(1, items, 90))
    ctx.catalog.upsert(
        "lc_threads",
        "thread_id",
        [{"thread_id": 25452, "title": "Old", "tier": "Premium", "url": "u"}],
        "2026-01-01T00:00:00Z",
        origin="import",
    )
    assert LcSource().sweep_step(ctx) is True
    title, tier, origin, prefixes = rows(
        ctx.catalog,
        "SELECT title, tier, origin, prefixes FROM lc_threads WHERE thread_id = 25452",
    )[0]
    assert (tier, origin) == ("Free", None) and title.startswith("The Room")
    assert json.loads(prefixes) == [14, 8, 28]
