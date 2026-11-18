# SPDX-License-Identifier: GPL-3.0-or-later
"""The guest LewdCorner Plus listings (`lc_plus`)."""

import re
from datetime import UTC, datetime

import pytest

from catalog_fakes import FakeClock, FakeTransport, fixture_bytes, make_context, rows
from catalog_server.__main__ import build_parser
from catalog_server.net import Response
from catalog_server.runner import run_source
from catalog_server.sources import STEPS, get_sources
from catalog_server.sources.lc_plus import (
    LcPlusPageError,
    LcPlusSource,
    listing_url,
    parse_listing,
    split_title,
)

NOW = "2026-01-01T00:00:00Z"
CHALLENGE = b"<html><head><title>Just a moment...</title></head><body>cf</body></html>"
# The prefix names the feed gave (Completed is 14 on LC).
PREFIXES = {14: "Completed", 18: "VN", 4: "Other", 30: "Hand Drawn", 7: "Unity", 9: "Ren'Py"}


def _day(text):
    return int(datetime.strptime(text, "%b %d, %Y").replace(tzinfo=UTC).timestamp())


def _page(name):
    return fixture_bytes(name).decode()


def _one_page(name):
    return re.sub(r'<div class="pageNav">.*?</div>', "", _page(name), flags=re.S)


def _with_last_page(name, last):
    links = "".join(
        f'<a href="/forums/games.119/page-{n}?order=latest_update_date">{n}</a>'
        for n in range(2, last + 1)
    )
    return re.sub(
        r'<div class="pageNav">.*?</div>', f'<div class="pageNav">{links}</div>',
        _page(name), flags=re.S,
    )


def _context(tmp_path, transport):
    ctx, clock = make_context(tmp_path, transport)
    ctx.catalog.upsert_lc_prefix_names(PREFIXES)
    return ctx, clock


def _stored(catalog):
    return {
        row[0]: row[1:]
        for row in rows(
            catalog,
            "SELECT thread_id, title, creator, version, ts, node_id, tier, prefixes, origin "
            "FROM lc_threads",
        )
    }


def test_cards_are_parsed():
    listing = parse_listing(_page("lc_plus_119_page1.html"))
    assert listing.last_page == 33 and listing.skipped == 0
    ai, acmesia, mad = listing.cards
    assert (acmesia.thread_id, acmesia.title, acmesia.creator) == (
        24724, "Acmesia ~Harem Impregnation Hunter Life~", "opopoworld"
    )
    assert acmesia.labels == ("Complete", "Other", "Hand Drawn")
    assert acmesia.ts == _day("Oct 13, 2026")
    assert acmesia.cover_url == "https://lewdcorner.com/attachments/1789435974800-png.857019/"
    assert (ai.thread_id, ai.title, ai.creator) == (
        25444, "Ai Musume: My Beloved Daughter", "Lemopro Games"
    )
    assert mad.labels == ("Unity", "Hand Drawn")
    second = parse_listing(_page("lc_plus_213_page1.html"))
    assert [card.thread_id for card in second.cards] == [15013, 23129]
    assert second.cards[0].labels == ("VN", "Ren'Py")
    assert second.last_page == 2


@pytest.mark.parametrize(
    ("text", "title", "creator"),
    [
        ("Acmesia ~Harem Impregnation Hunter Life~ [v1.2.0] [opopoworld]",
         "Acmesia ~Harem Impregnation Hunter Life~", "opopoworld"),
        ("My Next Door Girl Intimate Sim {1.0.2.2} [opnas]",
         "My Next Door Girl Intimate Sim {1.0.2.2}", None),
        ("Bravery Ordeal - The Tale of a Human -", "Bravery Ordeal - The Tale of a Human -", None),
        ("[Full] [Dev]", "[Full]", None),
    ],
)
def test_title_and_creator(text, title, creator):
    assert split_title(text) == (title, creator)


@pytest.mark.parametrize(
    "page",
    [
        CHALLENGE,
        b'<html data-template="login" data-logged-in="false"></html>',
        fixture_bytes("lc_plus_119_page1.html").replace(
            b'data-logged-in="false"', b'data-logged-in="true"'
        ),
    ],
    ids=["challenge", "login-page", "logged-in"],
)
def test_a_page_that_is_not_the_guest_listing_stores_nothing_and_stops_lc(tmp_path, page):
    transport = FakeTransport()
    transport.route("/forums/", Response(200, page))
    ctx, _ = _context(tmp_path, transport)
    with pytest.raises(LcPlusPageError):
        parse_listing(page.decode())
    result = run_source(LcPlusSource(), ctx)
    assert result.status == "error"
    assert _stored(ctx.catalog) == {}
    assert "lc_plus" in ctx.pacer.paused
    assert len(transport.requests) == 1


def test_listing_urls():
    assert listing_url(119, 1) == (
        "https://lewdcorner.com/forums/119/?order=latest_update_date&direction=desc"
    )
    assert listing_url(213, 3) == (
        "https://lewdcorner.com/forums/213/page-3?order=latest_update_date&direction=desc"
    )


def test_a_new_plus_thread_is_added_without_a_version(tmp_path):
    transport = FakeTransport()
    transport.route("/forums/119/", Response(200, _one_page("lc_plus_119_page1.html").encode()))
    ctx, _ = _context(tmp_path, transport)
    LcPlusSource()._store(ctx, 119, parse_listing(_one_page("lc_plus_119_page1.html")).cards)
    stored = _stored(ctx.catalog)
    title, creator, version, ts, node, tier, prefixes, origin = stored[25444]
    assert (title, creator, version, node, tier, origin) == (
        "Ai Musume: My Beloved Daughter", "Lemopro Games", None, 119, "Premium", None
    )
    assert ts == _day("Nov 17, 2026")
    # "Complete" on the card is the feed's "Completed".
    assert prefixes == "[14, 18, 4, 30]"
    assert rows(ctx.catalog, "SELECT updated_at, url FROM lc_threads WHERE thread_id = 24724") == [
        ("2026-10-13T00:00:00Z", "https://lewdcorner.com/threads/24724/")
    ]


def test_a_same_date_keeps_the_version_and_a_new_date_clears_it(tmp_path):
    ctx, _ = _context(tmp_path, FakeTransport())
    oct13 = _day("Oct 13, 2026")
    ctx.catalog.upsert(
        "lc_threads", "thread_id",
        [
            {"thread_id": 24724, "title": "A", "version": "1.20", "ts": oct13, "tier": "Premium",
             "tags": ["2dcg"], "views": 16000, "url": "u"},
            {"thread_id": 25444, "title": "B", "version": "1.0.0", "ts": 1, "tier": "Premium",
             "tags": [], "views": 5, "url": "u"},
        ],
        NOW,
    )
    cards = parse_listing(_one_page("lc_plus_119_page1.html")).cards
    unchanged = LcPlusSource()._store(ctx, 119, cards)
    assert unchanged is False
    stored = _stored(ctx.catalog)
    assert stored[24724][2] == "1.20"
    # 25444 has a new date: its version waits for the page read.
    assert stored[25444][2] is None
    # The listing does not write tags or views.
    assert rows(ctx.catalog, "SELECT tags, views FROM lc_threads WHERE thread_id = 24724") == [
        ('["2dcg"]', 16000)
    ]
    assert LcPlusSource()._store(ctx, 119, cards) is True


def test_an_imported_row_becomes_ordinary_and_is_read_again(tmp_path):
    ctx, _ = _context(tmp_path, FakeTransport())
    jun22 = _day("Jun 22, 2026")
    ctx.catalog.upsert(
        "lc_threads", "thread_id",
        [{"thread_id": 23129, "title": "Needs", "version": "v0.86a", "ts": jun22,
          "tier": "Premium", "url": "u"}],
        NOW, origin="import",
    )
    ctx.catalog.import_lc_details({23129: {"overview": "Atlas"}}, NOW)
    # The Standard account was refused this page before.
    ctx.catalog.upsert_lc_details(23129, jun22, NOW, {"status": "missing"})
    assert ctx.catalog.lc_details_queue(0, 10) == []
    LcPlusSource()._store(ctx, 213, parse_listing(_one_page("lc_plus_213_page1.html")).cards)
    title, _creator, version, ts, node, tier, _prefixes, origin = _stored(ctx.catalog)[23129]
    assert (title, version, ts, node, tier, origin) == ("Needs", None, jun22, 213, "Premium", None)
    assert (23129, jun22) in ctx.catalog.lc_details_queue(0, 10)


def test_an_imported_row_read_later_that_day_is_read_again(tmp_path):
    # The page was read for the Atlas time (15:13), which is later than the
    # card's midnight; the cleared version must not stay empty.
    ctx, _ = _context(tmp_path, FakeTransport())
    jun22 = _day("Jun 22, 2026")
    ctx.catalog.upsert(
        "lc_threads", "thread_id",
        [{"thread_id": 23129, "title": "Needs", "version": "v0.86a", "ts": jun22 + 54780,
          "tier": "Premium", "url": "u"}],
        NOW, origin="import",
    )
    ctx.catalog.upsert_lc_details(23129, jun22 + 54780, NOW, {"status": "ok"})
    LcPlusSource()._store(ctx, 213, parse_listing(_one_page("lc_plus_213_page1.html")).cards)
    assert _stored(ctx.catalog)[23129][2] is None
    assert (23129, jun22) in ctx.catalog.lc_details_queue(0, 10)


def test_a_new_date_before_the_read_time_is_read_again(tmp_path):
    ctx, _ = _context(tmp_path, FakeTransport())
    oct13 = _day("Oct 13, 2026")
    ctx.catalog.upsert(
        "lc_threads", "thread_id",
        [{"thread_id": 24724, "title": "A", "version": "1.30", "ts": oct13 + 86400,
          "tier": "Premium", "url": "u"}],
        NOW,
    )
    ctx.catalog.upsert_lc_details(24724, oct13 + 86400, NOW, {"status": "ok"})
    # LC moved the date back a day: the stored version belongs to the other date.
    LcPlusSource()._store(ctx, 119, parse_listing(_one_page("lc_plus_119_page1.html")).cards)
    assert _stored(ctx.catalog)[24724][2] is None
    assert (24724, oct13) in ctx.catalog.lc_details_queue(0, 10)


def test_a_free_thread_moved_to_plus_becomes_premium(tmp_path):
    ctx, _ = _context(tmp_path, FakeTransport())
    oct13 = _day("Oct 13, 2026")
    ctx.catalog.upsert(
        "lc_threads", "thread_id",
        [{"thread_id": 24724, "title": "A", "version": "1.1", "ts": oct13 + 3600, "tier": "Free",
          "url": "u"}],
        NOW,
    )
    ctx.catalog.upsert_lc_details(24724, oct13 + 3600, NOW, {"status": "ok"})
    LcPlusSource()._store(ctx, 119, parse_listing(_one_page("lc_plus_119_page1.html")).cards)
    stored = _stored(ctx.catalog)[24724]
    assert (stored[2], stored[5]) == (None, "Premium")
    assert (24724, oct13) in ctx.catalog.lc_details_queue(0, 10)


def _listing_transport(pages):
    """`pages` maps (node, page) to HTML."""
    transport = FakeTransport()

    def respond(request):
        match = re.search(r"/forums/(\d+)/(?:page-(\d+))?", request.url)
        key = (int(match.group(1)), int(match.group(2) or 1))
        return Response(200, pages[key].encode())

    transport.route("/forums/", respond)
    return transport


def _requested(transport):
    return [
        tuple(int(part or 1) for part in re.search(r"/forums/(\d+)/(?:page-(\d+))?", r.url).groups())
        for r in transport.requests
    ]


def test_an_incremental_read_stops_after_the_first_unchanged_page(tmp_path):
    first = _with_last_page("lc_plus_119_page1.html", 5)
    pages = {(119, 1): first, (119, 2): first, (119, 3): first,
             (213, 1): _one_page("lc_plus_213_page1.html")}
    transport = _listing_transport(pages)
    ctx, _ = _context(tmp_path, transport)
    ctx.state.mark_sweep_completed("lc_plus", NOW)
    # Page 1 shows a newer date than stored; page 2 shows only stored dates.
    LcPlusSource()._store(ctx, 119, parse_listing(first).cards)
    ctx.catalog.conn.execute("UPDATE lc_threads SET ts = 1 WHERE thread_id = 25444")
    ctx.catalog.conn.commit()
    LcPlusSource().incremental(ctx)
    assert _requested(transport) == [(119, 1), (119, 2), (213, 1)]


def test_no_incremental_read_before_the_first_sweep(tmp_path):
    transport = FakeTransport()
    ctx, _ = _context(tmp_path, transport)
    LcPlusSource().incremental(ctx)
    assert transport.requests == []


def test_a_sweep_reads_both_forums_and_resumes_after_the_budget(tmp_path):
    from catalog_server.config import SiteLimits
    from catalog_server.net import Pacer

    page = _with_last_page("lc_plus_119_page1.html", 3)
    pages = {(119, 1): page, (119, 2): page, (119, 3): _one_page("lc_plus_119_page1.html"),
             (213, 1): _one_page("lc_plus_213_page1.html")}
    transport = _listing_transport(pages)
    ctx, clock = _context(tmp_path, transport)
    ctx.pacer = Pacer(
        ctx.state, transport, clock=clock, sleep=clock.sleep,
        limits={"lc_plus": SiteLimits(min_delay=5.0, budget=2, window=3600.0)},
    )
    first = run_source(LcPlusSource(), ctx)
    assert first.status == "budget" and not first.sweep_completed
    assert ctx.state.get("lc_plus", "sweep_page") == 3
    clock.sleep(3700)
    second = run_source(LcPlusSource(), ctx)
    assert second.sweep_completed
    assert _requested(transport) == [(119, 1), (119, 2), (119, 3), (213, 1)]
    assert ctx.state.get("lc_plus", "sweep_page") is None


def test_the_real_budget_is_40_listing_pages_per_hour(tmp_path):
    from catalog_server.config import SITE_LIMITS

    page = _with_last_page("lc_plus_119_page1.html", 60)
    transport = FakeTransport()
    transport.route("/forums/", Response(200, page.encode()))
    ctx, _ = _context(tmp_path, transport)
    assert SITE_LIMITS["lc_plus"].min_delay == 5.0
    result = run_source(LcPlusSource(), ctx)
    assert result.status == "budget" and result.requests == 40
    assert ctx.state.get("lc_plus", "sweep_page") == 41


def test_an_empty_page_inside_the_listing_is_an_error(tmp_path):
    empty = re.sub(r'<div class="lcForumCards">.*</div>\s*</body>',
                   '<div class="lcForumCards"></div></body>',
                   _with_last_page("lc_plus_119_page1.html", 3), flags=re.S)
    transport = _listing_transport({(119, 1): empty})
    ctx, _ = _context(tmp_path, transport)
    result = run_source(LcPlusSource(), ctx)
    assert result.status == "error" and "empty page 1 of 3" in (result.detail or "")


def test_a_completed_sweep_marks_only_premium_rows(tmp_path):
    pages = {(119, 1): _one_page("lc_plus_119_page1.html"),
             (213, 1): _one_page("lc_plus_213_page1.html")}
    transport = _listing_transport(pages)
    clock = FakeClock()
    ctx, clock = _context(tmp_path, transport)
    ctx.catalog.upsert(
        "lc_threads", "thread_id",
        [{"thread_id": 900, "title": "Gone", "ts": 1, "tier": "Premium", "url": "u"},
         {"thread_id": 6, "title": "Free", "ts": 1, "tier": "Free", "url": "u"}],
        "2020-01-01T00:00:00Z",
    )
    for _ in range(2):
        assert run_source(LcPlusSource(), ctx).sweep_completed
        clock.sleep(10)
    removed = rows(ctx.catalog, "SELECT thread_id FROM lc_threads WHERE removed_at IS NOT NULL")
    assert removed == [(900,)]


def test_a_paused_lc_step_stops_the_listing(tmp_path):
    transport = FakeTransport()
    ctx, _ = _context(tmp_path, transport)
    ctx.pacer.paused["lc"] = "rate limited (HTTP 429)"
    result = run_source(LcPlusSource(), ctx)
    assert result.status == "rate_limited" and transport.requests == []


def test_lc_plus_runs_between_the_feed_and_the_thread_pages():
    assert STEPS[:5] == ("f95", "f95_threads", "lc", "lc_plus", "lc_threads")
    assert "lc_plus" in get_sources()
    args = build_parser().parse_args(["run", "--sources", "lc_plus", "--until-swept", "lc_plus"])
    assert args.sources == ["lc_plus"] and args.until_swept == ["lc_plus"]
