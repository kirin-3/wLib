# SPDX-License-Identifier: GPL-3.0-or-later
"""First-post details of F95 threads."""

import json

import pytest

from catalog_fakes import FakeTransport, fixture_bytes, make_context, rows
from catalog_server.__main__ import main
from catalog_server.net import Response
from catalog_server.runner import run_source
from catalog_server.sources.f95_threads import (
    F95ThreadsSource,
    first_post_html,
    parse_first_post,
)


def _parse(name: str) -> dict[str, object]:
    return parse_first_post(first_post_html(fixture_bytes(name).decode("utf-8")))


def test_overview_language_and_developer_ids():
    details = _parse("f95_thread_25332.html")
    overview = str(details["overview"])
    assert overview.startswith("A young man from a low-income family")
    # The overview ends at the next section (here "Trailer").
    assert overview.endswith("alcohol, drugs and sex.")
    assert details["language"] == "English"
    assert details["original_title"] is None
    assert details["aliases"] == []
    # GOG, X and Bluesky links are not among the kept IDs.
    assert details["external_ids"] == {
        "patreon": ["drpinkcake"],
        "steam": ["1126320"],
        "discord": ["KyCc5E4"],
    }


def test_original_name_and_aliases():
    details = _parse("f95_thread_323533.html")
    assert details["original_title"] == "欲望牢獄ギャルギャルシティ"
    assert details["aliases"] == ["Gyaru Gyaru City"]
    assert details["language"] == "English (Hoshi2Star MTL)"
    # Only the first post counts; the second one has its own labels.
    assert details["language"] != "German"


def test_overview_keeps_colons_and_hidden_spoiler_text():
    zelda = str(_parse("f95_thread_323603.html")["overview"])
    assert zelda.startswith("Zelda: Ocarina of Time Remake fan game is ready.\n")
    assert "Malon" in zelda
    janitor = str(_parse("f95_thread_319416.html")["overview"])
    assert "Spoiler" not in janitor
    assert "a game of leverage, temptation" in janitor


def test_translator_links_are_not_developer_ids():
    post = (
        '<div class="bbWrapper"><b>Overview</b>:<br>Text.<br>'
        + '<b>Original Title</b>: 原題<br>'
        + '<b>Developer</b>: Circle <a href="https://www.dlsite.com/maniax/circle/x">DLsite</a> - '
        + '<a href="https://ci-en.dlsite.com/creator/1">Ci-en</a><br>'
        + '<b>Translator</b>: Someone - <a href="https://ko-fi.com/translator">Ko-fi</a><br>'
        + '<b>Store</b>: <a href="https://store.steampowered.com/app/42/Game/">Steam</a><br>'
    )
    details = parse_first_post(post)
    assert details["original_title"] == "原題"
    assert details["overview"] == "Text."
    assert details["external_ids"] == {"steam": ["42"]}


@pytest.mark.parametrize(
    ("url", "kind", "value"),
    [
        ("https://www.patreon.com/cw/SomeDev", "patreon", "somedev"),
        ("https://patreon.com/SomeDev/posts", "patreon", "somedev"),
        ("https://www.patreon.com/user?u=12345", "patreon", "u:12345"),
        ("https://somedev.itch.io/", "itch", "somedev"),
        ("https://somedev.itch.io/cool-game", "itch", "somedev/cool-game"),
        ("https://itch.io/profile/SomeDev", "itch", "somedev"),
        ("https://subscribestar.adult/some-dev", "subscribestar", "some-dev"),
        ("https://www.subscribestar.com/SomeDev", "subscribestar", "somedev"),
        ("https://ko-fi.com/SomeDev", "kofi", "somedev"),
        ("https://discord.gg/AbC123", "discord", "AbC123"),
        ("https://discord.com/invite/AbC123", "discord", "AbC123"),
        ("https://steamcommunity.com/app/987/", "steam", "987"),
    ],
)
def test_external_id_forms(url, kind, value):
    post = f'<div class="bbWrapper"><b>Developer</b>: Dev <a href="{url}">link</a><br>'
    assert parse_first_post(post)["external_ids"] == {kind: [value]}


@pytest.mark.parametrize(
    "url", ["https://www.patreon.com/posts/123", "https://ko-fi.com/s/abc", "https://x.com/dev"]
)
def test_links_that_are_not_ids_are_skipped(url):
    post = f'<div class="bbWrapper"><b>Developer</b>: Dev <a href="{url}">link</a><br>'
    assert parse_first_post(post)["external_ids"] == {}


def test_a_page_without_posts_is_refused():
    with pytest.raises(ValueError, match="no post"):
        first_post_html("<html><body>Just a moment...</body></html>")


# --- The step ----------------------------------------------------------------


def _thread(thread_id: int, ts: int, views: int = 0) -> dict[str, object]:
    return {
        "thread_id": thread_id,
        "title": f"Game {thread_id}",
        "ts": ts,
        "views": views,
        "prefixes": [],
        "tags": [],
        "url": f"https://f95zone.to/threads/{thread_id}/",
    }


def _page(logged_in: bool = True) -> Response:
    body = fixture_bytes("f95_thread_323533.html")
    if not logged_in:
        body = body.replace(b'data-logged-in="true"', b'data-logged-in="false"')
    return Response(200, body)


def _thread_id(url: str) -> int:
    return int(url.rstrip("/").rsplit("/", 1)[1])


def _details(ctx) -> dict[int, tuple[object, ...]]:
    found = rows(
        ctx.catalog,
        "SELECT thread_id, thread_ts, status, original_title, aliases, external_ids "
        + "FROM f95_thread_details",
    )
    return {row[0]: row[1:] for row in found}


def test_changed_threads_first_then_most_viewed_old_ones(tmp_path):
    transport = FakeTransport()
    transport.route("/threads/", _page())
    ctx, _ = make_context(tmp_path, transport)
    ctx.upsert("f95_threads", "thread_id", [_thread(1, 100, views=5), _thread(2, 200, views=50)])
    source = F95ThreadsSource()
    # The first run takes the newest feed `ts` as "now": nothing has changed yet.
    source.incremental(ctx)
    assert [_thread_id(r.url) for r in transport.requests] == [2, 1]
    assert ctx.state.get("f95_threads", "since_ts") == 200
    status, original, aliases = _details(ctx)[1][1:4]
    assert (status, original, json.loads(str(aliases))) == ("ok", "欲望牢獄ギャルギャルシティ", ["Gyaru Gyaru City"])

    # Thread 1 changes, 3 is new, 4 is old and never read: changed ones first.
    transport.requests.clear()
    ctx.upsert(
        "f95_threads",
        "thread_id",
        [_thread(1, 300, views=5), _thread(3, 250, views=1), _thread(4, 50, views=999)],
    )
    source.incremental(ctx)
    assert [_thread_id(r.url) for r in transport.requests] == [1, 3, 4]
    assert _details(ctx)[1][0] == 300

    transport.requests.clear()
    source.incremental(ctx)
    assert transport.requests == []


def test_missing_and_unreadable_threads_are_kept_so_the_queue_moves(tmp_path):
    transport = FakeTransport()
    transport.route("/threads/1/", Response(404, b"gone"))
    transport.route("/threads/2/", Response(200, b'<html data-logged-in="true"><body></body></html>'))
    transport.route("/threads/3/", _page())
    ctx, _ = make_context(tmp_path, transport)
    ctx.upsert("f95_threads", "thread_id", [_thread(1, 30), _thread(2, 20), _thread(3, 10)])
    F95ThreadsSource().incremental(ctx)
    statuses = {thread_id: values[1] for thread_id, values in _details(ctx).items()}
    assert statuses == {1: "missing", 2: "unreadable", 3: "ok"}


def test_a_logged_out_page_stops_the_step_without_storing_it(tmp_path):
    transport = FakeTransport()
    transport.route("/threads/", _page(logged_in=False))
    ctx, _ = make_context(tmp_path, transport)
    ctx.upsert("f95_threads", "thread_id", [_thread(1, 10)])
    result = run_source(F95ThreadsSource(), ctx)
    assert result.status == "error"
    assert "no login" in str(result.detail)
    assert _details(ctx) == {}


def test_the_step_stops_at_its_own_budget(tmp_path):
    transport = FakeTransport()
    transport.route("/threads/", _page())
    ctx, _ = make_context(tmp_path, transport)
    ctx.upsert("f95_threads", "thread_id", [_thread(n, n) for n in range(1, 71)])
    result = run_source(F95ThreadsSource(), ctx)
    assert result.status == "budget"
    assert result.requests == 60
    assert len(_details(ctx)) == 60
    # The feed's own budget is untouched.
    assert ctx.pacer.remaining("f95") == 90


def test_cli_skips_thread_pages_without_the_login(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("WLIB_CATALOG_F95_COOKIE", raising=False)
    monkeypatch.delenv("WLIB_CATALOG_BROWSER_SITES", raising=False)
    transport = FakeTransport()
    caplog.set_level("INFO")
    code = main(
        ["--data-dir", str(tmp_path), "run", "--no-publish", "--sources", "f95_threads"],
        transport=transport,
    )
    assert code == 0
    assert transport.requests == []
    assert "f95_threads: skipped" in caplog.text


def test_cli_reads_thread_pages_through_the_logged_in_browser(tmp_path, monkeypatch):
    from test_catalog_browser import COOKIE, FakeBrowser, FakePage

    from catalog_server.browser import BrowserTransport, f95_login_cookies
    from catalog_server.store import CatalogStore

    monkeypatch.setenv("WLIB_CATALOG_F95_COOKIE", COOKIE)
    monkeypatch.setenv("WLIB_CATALOG_BROWSER_SITES", "f95")
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert("f95_threads", "thread_id", [_thread(7, 10)], "2026-11-17T00:00:00Z")
    catalog.close()
    browser = FakeBrowser(lambda url: FakePage(200, fixture_bytes("f95_thread_323533.html")))
    plain = FakeTransport()
    code = main(
        ["--data-dir", str(tmp_path), "run", "--no-publish", "--sources", "f95_threads"],
        transport=plain,
        browser=BrowserTransport(cookies=f95_login_cookies(COOKIE), session_factory=browser),
        sleep=lambda seconds: None,
    )
    assert code == 0
    assert plain.requests == []
    assert browser.fetched == [(None, "https://f95zone.to/threads/7/")]
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    try:
        assert rows(catalog, "SELECT thread_id, status FROM f95_thread_details") == [(7, "ok")]
    finally:
        catalog.close()
