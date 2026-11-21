# SPDX-License-Identifier: GPL-3.0-or-later
"""LewdCorner first-post details (`lc_threads`)."""

import json

import pytest

from catalog_fakes import FakeTransport, fixture_bytes, make_context, rows
from catalog_server.net import RateLimited, Response
from catalog_server.runner import run_source
from catalog_server.sources.lc_threads import (
    LcThreadsSource,
    first_post_html,
    parse_first_post,
)

NOW = "2026-01-01T00:00:00Z"
CHALLENGE = b"<html><head><title>Just a moment...</title></head><body>cf</body></html>"
NGINX_404 = (
    b"<html>\r\n<head><title>404 Not Found</title></head>\r\n<body></body></html>"
)


def _thread(thread_id, ts, views=0):
    return {
        "thread_id": thread_id,
        "title": f"T{thread_id}",
        "ts": ts,
        "views": views,
        "url": f"https://lewdcorner.com/threads/{thread_id}/",
    }


def _details(catalog):
    return {
        row[0]: row[1:]
        for row in rows(
            catalog,
            "SELECT thread_id, status, developer, external_ids FROM lc_thread_details",
        )
    }


def test_custom_fields_and_overview_are_parsed():
    page = fixture_bytes("lc_thread_6.html").decode()
    details = parse_first_post(first_post_html(page))
    assert details["developer"] == "Nun Ya and Fallen Angel Productions"
    assert details["release_text"] == "Apr 27, 2019"
    assert details["released_at"] == "2019-04-27"
    assert details["language"] == "English" and details["languages"] == ["English"]
    assert details["os"] == ["Windows", "Linux", "MacOS", "Android"]
    ids = details["external_ids"]
    assert ids["patreon"] == [
        {"id": "u:5648613", "url": "https://www.patreon.com/user?u=5648613"}
    ]
    assert ids["subscribestar"][0]["id"] == "dark-coven"
    overview = details["overview"]
    assert overview.startswith("You're the descendant of the infamous occultist")
    # Spoilers (genre, changelog) and the download section are not the overview.
    assert "3DCG" not in overview and "Lilith" not in overview
    assert "DOWNLOAD" not in overview and "example.invalid" not in overview


def test_threads_are_read_and_stored(tmp_path):
    transport = FakeTransport()
    transport.route("/threads/6/", Response(200, fixture_bytes("lc_thread_6.html")))
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert("lc_threads", "thread_id", [_thread(6, 100)], NOW)
    LcThreadsSource().incremental(ctx)
    status, developer, ids = _details(ctx.catalog)[6]
    assert (status, developer) == ("ok", "Nun Ya and Fallen Angel Productions")
    assert json.loads(ids)["patreon"][0]["id"] == "u:5648613"
    assert [request.url for request in transport.requests] == [
        "https://lewdcorner.com/threads/6/"
    ]


@pytest.mark.parametrize(
    ("status", "body"),
    # LC's "no permission" page as a guest sees it.
    [
        (
            403,
            fixture_bytes("lc_no_permission.html").replace(
                b'data-logged-in="true"', b'data-logged-in="false"'
            ),
        ),
        (404, NGINX_404),
    ],
)
def test_no_permission_and_not_found_are_missing(tmp_path, status, body):
    transport = FakeTransport()
    transport.route("/threads/7/", Response(status, body))
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert("lc_threads", "thread_id", [_thread(7, 100)], NOW)
    LcThreadsSource().incremental(ctx)
    assert _details(ctx.catalog)[7][0] == "missing"
    # The feed row stays as it was.
    assert rows(ctx.catalog, "SELECT title FROM lc_threads WHERE thread_id = 7") == [
        ("T7",)
    ]


@pytest.mark.parametrize("status", [200, 403])
def test_a_challenge_page_stops_the_step_and_stores_nothing(tmp_path, status):
    transport = FakeTransport()
    transport.route("/threads/", Response(status, CHALLENGE))
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert("lc_threads", "thread_id", [_thread(8, 100)], NOW)
    result = run_source(LcThreadsSource(), ctx)
    assert result.status == "error" and "without its own page" in (result.detail or "")
    assert _details(ctx.catalog) == {}


def test_queue_reads_changed_threads_first(tmp_path):
    transport = FakeTransport()
    transport.route("/threads/", Response(200, fixture_bytes("lc_thread_6.html")))
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert(
        "lc_threads",
        "thread_id",
        [_thread(1, 100, views=1), _thread(2, 200, views=500), _thread(3, 300)],
        NOW,
    )
    ctx.catalog.upsert_lc_details(3, 250, NOW, {"status": "ok"})
    ctx.state.put("lc_threads", "since_ts", 300)
    LcThreadsSource().incremental(ctx)
    order = [request.url.rsplit("/", 2)[-2] for request in transport.requests]
    # Thread 3 changed since its details; then never-read threads by views.
    assert order == ["3", "2", "1"]


def test_skipped_while_the_lc_feed_is_paused(tmp_path):
    transport = FakeTransport()
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert("lc_threads", "thread_id", [_thread(1, 100)], NOW)
    ctx.pacer.paused["lc"] = "rate limited (HTTP 429)"
    with pytest.raises(RateLimited):
        LcThreadsSource().incremental(ctx)
    assert transport.requests == []


def _premium(thread_id, ts, **extra):
    return {
        **_thread(thread_id, ts),
        "tier": "Premium",
        "version": None,
        "tags": [],
        **extra,
    }


def test_a_plus_page_read_as_a_guest_stores_its_version_and_tags(tmp_path):
    transport = FakeTransport()
    transport.route(
        "/threads/24724/", Response(200, fixture_bytes("lc_thread_24724.html"))
    )
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert("lc_threads", "thread_id", [_premium(24724, 100)], NOW)
    LcThreadsSource().incremental(ctx)
    version, tags = rows(ctx.catalog, "SELECT version, tags FROM lc_threads")[0]
    # The Version field, not the "[v1.2.0]" of the listing title.
    assert version == "1.20"
    assert json.loads(tags)[:3] == ["2dcg", "adventure", "censored"]
    assert len(json.loads(tags)) == 18
    assert _details(ctx.catalog)[24724][:2] == ("ok", "opopoworld (おぽぽわーるど)")


def test_a_free_page_keeps_the_feed_version_and_tags(tmp_path):
    transport = FakeTransport()
    transport.route("/threads/6/", Response(200, fixture_bytes("lc_thread_6.html")))
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert(
        "lc_threads",
        "thread_id",
        [{**_thread(6, 100), "tier": "Free", "version": "v1.0", "tags": ["3dcg"]}],
        NOW,
    )
    LcThreadsSource().incremental(ctx)
    assert rows(ctx.catalog, "SELECT version, tags FROM lc_threads") == [
        ("v1.0", '["3dcg"]')
    ]


def test_a_logged_in_page_stops_lc_and_stores_nothing(tmp_path):
    page = fixture_bytes("lc_thread_24724.html").replace(
        b'data-logged-in="false"', b'data-logged-in="true"'
    )
    transport = FakeTransport()
    transport.route("/threads/", Response(200, page))
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert(
        "lc_threads", "thread_id", [_premium(24724, 100), _premium(2, 50)], NOW
    )
    result = run_source(LcThreadsSource(), ctx)
    assert result.status == "error" and "logged-in" in (result.detail or "")
    assert len(transport.requests) == 1
    assert _details(ctx.catalog) == {}
    assert rows(
        ctx.catalog, "SELECT version FROM lc_threads WHERE thread_id = 24724"
    ) == [(None,)]
    assert "lc_threads" in ctx.pacer.paused


def test_a_lost_feed_login_does_not_stop_the_thread_pages(tmp_path):
    from catalog_server.sources.lc import LcSource

    transport = FakeTransport()
    transport.route(
        "/latest-updates/api", Response(200, fixture_bytes("lc_feed_logged_out.json"))
    )
    transport.route("/threads/6/", Response(200, fixture_bytes("lc_thread_6.html")))
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert("lc_threads", "thread_id", [_thread(6, 100)], NOW)
    ctx.state.put("lc", "hwm_ts", 1)
    assert run_source(LcSource(), ctx).status == "error"
    assert run_source(LcThreadsSource(), ctx).status == "ok"
    assert _details(ctx.catalog)[6][0] == "ok"


def test_a_rate_limit_on_the_plus_listing_stops_the_thread_pages(tmp_path):
    transport = FakeTransport()
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert("lc_threads", "thread_id", [_thread(1, 100)], NOW)
    ctx.pacer.paused["lc_plus"] = "rate limited (HTTP 429)"
    result = run_source(LcThreadsSource(), ctx)
    assert result.status == "rate_limited" and "lc_plus" in (result.detail or "")
    assert transport.requests == []
