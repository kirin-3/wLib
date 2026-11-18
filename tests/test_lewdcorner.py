# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core import lewdcorner

URL = "https://lewdcorner.com/threads/123/"


@pytest.mark.parametrize("url", [
    URL, "https://www.lewdcorner.com/threads/game.123/post-7#post-7",
    "http://lewdcorner.com/threads/game.123/page-2?foo=bar",
])
def test_thread_identity(url):
    assert lewdcorner.normalize_thread_url(url) == URL
    assert lewdcorner.thread_urls_match(url, URL)


@pytest.mark.parametrize("url", [
    "https://lewdcorner.com/forums/games.6/", "https://example.com/threads/123/",
    "https://lewdcorner.com.evil/threads/123/", "/threads/123/",
])
def test_non_thread(url):
    assert lewdcorner.normalize_thread_url(url) == ""
    assert not lewdcorner.thread_urls_match(url, URL)


@pytest.mark.parametrize("mode, enabled, f95, expected", [
    ("", False, "f95", False), ("", True, "f95", True), ("", False, "", True),
    ("on", False, "f95", True), ("off", True, "", False),
])
def test_active(mode, enabled, f95, expected):
    game = {"lc_url": URL, "lc_mode": mode, "f95_url": f95}
    assert lewdcorner.lc_active(game, enabled) is expected
    assert not lewdcorner.lc_active({**game, "lc_url": ""}, enabled)


def test_prefixes():
    assert lewdcorner.prefix_details(["DAZ", "Ren'Py", "Unity", "Completed"]) == ("Ren'Py", True)
    assert lewdcorner.prefix_details(["AI", "Koikatsu"]) == ("", False)


@pytest.mark.parametrize(("f95", "lc", "winner"), [
    (None, None, ("", "")),
    (("0.5", ""), None, ("0.5", "f95")),
    (None, ("0.6", ""), ("0.6", "lc")),
    (("v0.6", "2026-11-10T00:00:00Z"), ("0.6", ""), ("v0.6", "both")),
    (("0.5", "2026-11-01T00:00:00Z"), ("0.6", "2026-11-10T00:00:00Z"), ("0.6", "lc")),
    (("0.6", "2026-11-10T00:00:00Z"), ("0.5", "2026-11-01T00:00:00Z"), ("0.6", "f95")),
    (("0.6", "2026-11-10T00:00:00Z"), ("0.5", ""), ("0.6", "f95")),
    (("0.5", ""), ("0.6", "2026-11-10T00:00:00Z"), ("0.6", "lc")),
    (("0.5", ""), ("0.6", ""), ("0.5", "f95")),
    (("0.5", "2026-11-10T00:00:00Z"), ("0.6", "2026-11-10T00:00:00Z"), ("0.5", "f95")),
])
def test_pick_latest(f95, lc, winner):
    assert lewdcorner.pick_latest(f95, lc) == winner


def test_store_thread_update_combines_sites_and_preserves_time():
    from core import database

    database.init_db()
    f95 = "https://f95zone.to/threads/123/"
    game_id = database.add_game("Game", "", f95, lc_url=URL, lc_mode="on")
    early = "2026-11-01T00:00:00Z"
    late = "2026-11-10T00:00:00Z"
    assert database.store_thread_update(game_id, "f95", f95, ("0.5", early))
    assert database.store_thread_update(game_id, "lc", URL, ("0.6", late))
    assert database.store_thread_update(game_id, "f95", f95, ("v0.5", late))
    stored = database.get_game(game_id)
    assert (stored["latest_version"], stored["latest_site"]) == ("0.6", "lc")
    assert stored["f95_latest_at"] == early
    assert database.store_thread_update(game_id, "lc", "https://lewdcorner.com/threads/321/", ("9", late)) is None


def test_feed_parser_uses_saved_responses():
    import json
    from pathlib import Path

    samples = Path(__file__).parent / "fixtures" / "catalog"
    body = (samples / "lc_latest_page.json").read_text()
    page = lewdcorner.parse_feed_page(body)
    item = page.items["25050"]
    assert item.version == "0.2"
    assert item.at == lewdcorner.timestamp_iso(1794991483)
    assert not item.premium and "Ren'Py" in item.prefixes
    payload = json.loads(body)
    payload["result"]["items"][0]["node_id"] = 42
    assert "25050" not in lewdcorner.parse_feed_page(json.dumps(payload)).items
    for logged_out in ((samples / "lc_feed_logged_out.json").read_text(), "<html>Login</html>"):
        with pytest.raises(lewdcorner.LcUpdateError) as error:
            lewdcorner.parse_feed_page(logged_out)
        assert error.value.code == "lc_login_required"


def test_thread_parser_uses_saved_responses():
    from pathlib import Path

    samples = Path(__file__).parent / "fixtures" / "catalog"
    body = (samples / "lc_thread_6.html").read_text()
    assert lewdcorner.parse_thread_page(body, 200).version == "v1.0 Elite"
    denied = (samples / "lc_no_permission.html").read_text()
    for html, status, code in [
        (denied, 403, "lc_plus_required"),
        (denied.replace('data-logged-in="true"', 'data-logged-in="false"'), 403, "lc_login_required"),
        ('<html data-template="login">', 200, "lc_login_required"),
        ("<html>404 Not Found</html>", 404, "lc_extract_failed"),
    ]:
        with pytest.raises(lewdcorner.LcUpdateError) as error:
            lewdcorner.parse_thread_page(html, status, premium=True)
        assert error.value.code == code
    with_prefixes = body.replace("<body>", """
        <body><h1 class="p-title-value"><a><span class="label label--green">Completed</span></a>
        <span class="label">Ren&#39;Py</span>Game</h1>""")
    assert lewdcorner.parse_thread_page(with_prefixes, 200).prefixes == ("Completed", "Ren'Py")


def test_thread_parser_reads_live_lc_markup():
    # Markup from a guest read of LC thread 19995 and a signed-in non-Plus read (Nov 2026).
    page = """<html lang="en-US" data-app="public" data-template="thread_view" data-logged-in="false">
        <body><h1 class="p-title-value"><span class="p-title-prefix"><a href="/forums/games.119/?prefix_id=14"
        class="labelLink" rel="nofollow"><span class="label label--completed" dir="auto">Complete</span></a>
        <span class="label-append">&nbsp;</span><a href="/forums/games.119/?prefix_id=18" class="labelLink"
        rel="nofollow"><span class="label label--vn" dir="auto">VN</span></a><span class="label-append">&nbsp;</span>
        </span>Honey Village [1.3.0] [Pink Cafe Art]</h1>
        <dl class="pairs pairs--columns pairs--fixedSmall pairs--customField" data-field="version">
            <dt>Version</dt>
            <dd>1.3.0</dd>
        </dl></body></html>"""
    parsed = lewdcorner.parse_thread_page(page, 200)
    assert (parsed.version, parsed.prefixes) == ("1.3.0", ("Complete", "VN"))
    assert lewdcorner.prefix_details(parsed.prefixes)[1] is True
    denied = """<html lang="en-US" data-app="public" data-template="error" data-logged-in="true">
        <body><h1 class="p-title-value">Oops! We ran into some problems.</h1>
        <div class="blockMessage">You do not have permission to view this page or perform this action.</div>
        </body></html>"""
    with pytest.raises(lewdcorner.LcUpdateError) as error:
        lewdcorner.parse_thread_page(denied, 403)
    assert error.value.code == "lc_plus_required"
    with pytest.raises(lewdcorner.LcUpdateError) as error:
        lewdcorner.parse_thread_page("<html><body>Not found</body></html>", 404)
    assert error.value.code == "lc_extract_failed"


@pytest.mark.parametrize(("mark", "last_page", "broken", "expected_calls"), [
    (1794991483 - 2 * 86400, 12, False, 1),
    (0, 12, False, 10),
    (0, 1, False, 1),
    (123, 12, True, 2),
])
def test_feed_limits_and_cursor(monkeypatch, mark, last_page, broken, expected_calls):
    import json
    from urllib.parse import parse_qs, urlsplit

    from core import database, extension_bridge, lewdcorner_updates

    database.init_db()
    if mark:
        database.update_setting("lc_feed_read_at", lewdcorner.timestamp_iso(mark))
    calls = []

    def fetch(url, *, site):
        assert site == "lc"
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        calls.append(page)
        if broken and page == 2:
            raise extension_bridge.BridgeFailed("timeout")
        body = json.dumps({"status": "ok", "result": {
            "hasMore": page < last_page,
            "items": [
                {"id": 21, "timestamp": 1794991483, "node_id": 6, "version": "1"},
                {"id": 22, "timestamp": 1794991483 - 5 * 86400, "node_id": 212, "version": "2"},
            ],
        }})
        return extension_bridge.BridgeResponse(200, url, body)

    monkeypatch.setattr(extension_bridge, "fetch", fetch)
    if broken:
        with pytest.raises(lewdcorner.LcUpdateError):
            lewdcorner_updates.read_feed(lambda: False)
    else:
        assert lewdcorner_updates.read_feed(lambda: False)["21"].version == "1"
    assert calls == list(range(1, expected_calls + 1))
    assert database.get_setting("lc_feed_read_at") == lewdcorner.timestamp_iso(mark if broken else 1794991483)


def test_switching_thread_or_mode_recomputes_the_winning_version():
    from core import database

    database.init_db()
    f95 = "https://f95zone.to/threads/123/"
    game_id = database.add_game("Game", "", f95, lc_url=URL, lc_mode="on")
    early, late = "2026-11-01T00:00:00Z", "2026-11-10T00:00:00Z"
    assert database.store_thread_update(game_id, "f95", f95, ("0.5", early))
    assert database.store_thread_update(game_id, "lc", URL, ("0.6", late))
    database.update_game(game_id, {"lc_mode": "off"})
    assert database.get_game(game_id)["latest_version"] == "0.5"
    assert database.get_game(game_id)["lc_latest_version"] == "0.6"
    database.update_game(game_id, {"lc_mode": "on"})
    assert database.get_game(game_id)["latest_version"] == "0.6"
    database.update_game(game_id, {"lc_url": "https://lewdcorner.com/threads/1234/"})
    assert database.get_game(game_id)["latest_version"] == "0.5"
    assert database.get_game(game_id)["lc_latest_version"] == ""


@pytest.mark.parametrize(("entry", "usable"), [
    ({"version": "0.5", "tier": "Free"}, True),
    ({"version": "1.20", "tier": "Premium"}, True),
    ({"version": "", "tier": "Premium"}, False),
    ({"tier": "Premium"}, False),
    ({"version": "v0.86a", "tier": "Premium", "origin": "import"}, False),
    ({"version": "0.2", "tier": "Free", "origin": "import"}, False),
    ({}, False),
])
def test_usable_catalog_entry_needs_a_version_and_no_import(entry, usable):
    assert lewdcorner.usable_catalog_entry(entry) is usable
