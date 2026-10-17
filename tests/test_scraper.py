# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import threading
import time
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock

import pytest

from core import scraper as scraper_module
from core.scraper import Scraper


def test_extract_version_from_title():
    scraper = Scraper()

    # Bracketed version
    assert (
        scraper._extract_version_from_title("Game Title [v1.0.5] [Developer]")
        == "1.0.5"
    )
    assert scraper._extract_version_from_title("Game Title [1.0.2] [PC/Mac]") == "1.0.2"
    assert scraper._extract_version_from_title("Game Title [v2.0 Beta]") == "2.0 Beta"
    assert (
        scraper._extract_version_from_title("Game Title [v3.1a Hotfix]")
        == "3.1a Hotfix"
    )
    assert scraper._extract_version_from_title("Another Game [Version 4.5]") == "4.5"
    assert (
        scraper._extract_version_from_title("Missing Brackets Game v1.5 [Windows]")
        == "1.5"
    )
    assert (
        scraper._extract_version_from_title("Game Title [v0.1.2.3.4] (No Spaces)")
        == "0.1.2.3.4"
    )

    # Chapter/Episode formats
    assert scraper._extract_version_from_title("Game Title [Ch.3] [RenPy]") == "Ch.3"
    assert scraper._extract_version_from_title("Game Title [Ep.5] [HTML]") == "Ep.5"
    assert (
        scraper._extract_version_from_title("Game Title [S2 E3] [Windows]") == "S2 E3"
    )
    assert (
        scraper._extract_version_from_title("Game Title [Part 3] [MacOS]") == "Part 3"
    )
    assert (
        scraper._extract_version_from_title("Visual Novel [Chapter 4 v1.2]")
        == "Chapter 4 v1.2"
    )

    # First-bracket fallback only applies when a developer bracket is present.
    assert scraper._extract_version_from_title("Game Title [Final] [Dev]") == "Final"
    assert scraper._extract_version_from_title("Game Title [b12] [Dev]") == "b12"
    assert (
        scraper._extract_version_from_title("Game Title [RenPy] [Windows]") == "RenPy"
    )
    assert scraper._extract_version_from_title("Game Title [Dev]") == "Unknown"
    assert scraper._extract_version_from_title("Just the Title") == "Unknown"
    assert scraper._extract_version_from_title(None) == "Unknown"


@pytest.fixture
def thread_html():
    return (Path(__file__).parent / "fixtures" / "f95_thread.html").read_text()


def test_checker_normalization_matches_stored_versions():
    cases = json.loads(
        Path(__file__).with_name("checker_version_cases.json").read_text()
    )
    for raw, expected in cases:
        assert Scraper()._normalize_checker_version(raw) == expected


@pytest.mark.parametrize(
    ("status", "title", "html", "code"),
    [
        (404, "Log in", "cf-challenge", "not_found"),
        (200, "", "The requested thread could not be found", "not_found"),
        (503, "Just a moment", "", "blocked"),
        (403, "", "verify you are human", "blocked"),
        (200, "Log in | F95zone", "", "login_required"),
        (200, "", "You must be logged in", "login_required"),
        (200, "Game [v1.0]", "<nav>Log in</nav>", None),
        (
            200,
            "Game [v1.0]",
            '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>',
            None,
        ),
    ],
)
def test_page_classification(status, title, html, code):
    issue = Scraper()._classify_page_issue(status, title, html)
    assert (issue["code"] if issue else None) == code


def test_starter_metadata_and_nested_wrapper(thread_html):
    result = Scraper()._extract_page(thread_html)
    assert result["title"] == "Example & Game [v1.10] [Dev]"
    assert result["version"] == "1.10"
    assert result["engine"] == "Ren'Py"
    assert result["thread_completed"] is True
    assert result["tags"] == ["sandbox", "3dcg"]
    assert result["cover_image"] == "https://attachments.f95zone.to/cover.jpg?x=1&y=2"
    assert result["thread_main_post_last_edit_at"] == "2026-10-08T09:00:00Z"
    assert result["thread_main_post_checked_at"]


@pytest.mark.parametrize(
    ("labels", "engine", "completed"),
    [
        (["Completed", "Ren'Py"], "Ren'Py", True),
        (["Ren'Py", "Completed"], "Ren'Py", True),
        (["Unity", "Abandoned"], "Unity", False),
        (["Ren'Py"], "Ren'Py", False),
        (["Onhold", "Completed"], "", True),
        (["VN", "RPGM"], "RPGM", False),
        (["VN", "Ren'Py", "Completed"], "Ren'Py", True),
        (["Collection", "VN", "Others"], "Others", False),
        (["VN"], "", False),
    ],
)
def test_thread_prefixes_choose_engine_and_completion(
    thread_html, labels, engine, completed
):
    prefix_html = "".join(f'<a class="labelLink">{label}</a>' for label in labels)
    html = thread_html.replace(
        '<a class="labelLink"><span>Ren\'Py</span></a>', prefix_html
    ).replace('<span class="label-append">Completed</span>', "")
    result = Scraper()._extract_page(html)
    assert result["engine"] == engine
    assert result["thread_completed"] is completed
    assert result["title"] == "Example & Game [v1.10] [Dev]"


def test_reply_edit_ignored_when_starter_not_edited(thread_html):
    html = thread_html.replace(
        '<div class="message-lastEdit">Last edited: <time datetime="2026-10-08T09:00:00Z">yesterday</time></div>',
        "",
    )
    result = Scraper()._extract_page(html)
    assert result["thread_main_post_last_edit_at"] == ""
    assert result["thread_main_post_checked_at"]


@pytest.mark.parametrize(
    "body",
    [
        "<b>Version</b>: v2.5a<br>",
        "Current Version: 2.5a",
        "<b>Ver.</b>: 2.5a<br>",
        "Release: 2.5a",
    ],
)
def test_post_version_fallback(body):
    assert Scraper()._extract_version_from_post(body) == "2.5a"


def checker_response(ids, version="v1.10"):
    return 200, json.dumps({"status": "ok", "msg": {i: version for i in ids}})


def test_250_urls_need_three_requests(monkeypatch):
    requests = []

    def get(url):
        ids = url.split("threads=")[1].split(",")
        requests.append(ids)
        return checker_response(ids)

    monkeypatch.setattr(scraper_module, "_f95_get", get)
    urls = [f"https://f95zone.to/threads/{i}/" for i in range(1, 251)]
    results = Scraper().check_versions(urls)
    assert [len(ids) for ids in requests] == [100, 100, 50]
    assert all(r["version"] == "1.10" for r in results.values())


@pytest.mark.parametrize(
    "response",
    [
        (400, '{"status":"error"}'),
        (200, "not json"),
        (200, '{"status":"error"}'),
        (200, '{"status":"ok","msg":{}}'),
        (200, '{"status":"ok","msg":{"1":"Unknown"}}'),
        (200, '{"status":"ok","msg":{"1":""}}'),
    ],
)
def test_batch_falls_back_to_page(monkeypatch, thread_html, response):
    get = MagicMock(side_effect=[response, (200, thread_html)])
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    result = Scraper().get_thread_version("https://f95zone.to/threads/1/")
    assert result["version"] == "1.10"
    assert get.call_count == 2


def test_missing_thread_is_not_found(monkeypatch):
    monkeypatch.setattr(
        scraper_module,
        "_f95_get",
        MagicMock(side_effect=[(200, '{"status":"ok","msg":{}}'), (404, "missing")]),
    )
    assert (
        Scraper().get_thread_version("https://f95zone.to/threads/1/")["code"]
        == "not_found"
    )


def test_no_actionable_page_version(monkeypatch):
    monkeypatch.setattr(
        scraper_module,
        "_f95_get",
        MagicMock(
            side_effect=[
                checker_response(["1"], "Unknown"),
                (200, "<title>Game</title>"),
            ]
        ),
    )
    assert (
        Scraper().get_thread_version("https://f95zone.to/threads/1/")["code"]
        == "extract_failed"
    )


def test_challenge_does_not_fallback_and_next_chunk_continues(monkeypatch):
    # Blocked batch with no extension: fails as blocked+unavailable, no page
    # fallback (still one request per batch), next chunk continues.
    scraper_module._prefer_extension_until = None
    scraper_module._f95_last_request_finished = None
    get = MagicMock(side_effect=[(503, "cf-challenge"), checker_response(["101"])])
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: False
    )
    urls = [f"https://f95zone.to/threads/{i}/" for i in range(1, 102)]
    results = Scraper().check_versions(urls)
    assert all(results[url]["code"] == "blocked" for url in urls[:100])
    assert all(
        results[url].get("browser_fallback") == "unavailable" for url in urls[:100]
    )
    assert results[urls[-1]]["version"] == "1.10"
    assert get.call_count == 2
    scraper_module._prefer_extension_until = None
    scraper_module._f95_last_request_finished = None


def test_rate_limited_chunk_does_not_fallback_to_pages(monkeypatch):
    get = MagicMock(return_value=(429, "Too Many Requests"))
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    urls = [f"https://f95zone.to/threads/{i}/" for i in range(1, 4)]
    results = Scraper().check_versions(urls)
    assert all(results[url]["code"] == "rate_limited" for url in urls)
    assert get.call_count == 1


def test_duplicate_ids_and_invalid_urls_keep_callback_order(monkeypatch):
    get = MagicMock(return_value=checker_response(["1"]))
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    urls = [
        "bad",
        "https://f95zone.to/threads/1/",
        "https://f95zone.to/threads/game.1/",
    ]
    seen = []
    results = Scraper().check_versions(
        urls, lambda url, result: seen.append(url) or True
    )
    assert seen == urls
    assert results["bad"]["code"] == "invalid_url"
    assert results[urls[1]] == results[urls[2]]
    assert get.call_count == 1


@pytest.mark.parametrize(
    "response",
    [
        checker_response(["1", "2"]),
        (503, "cf-challenge"),
        (200, '{"status":"ok","msg":{}}'),
    ],
)
def test_callback_exception_propagates(monkeypatch, thread_html, response):
    get = MagicMock(side_effect=[response, (200, thread_html)])
    monkeypatch.setattr(scraper_module, "_f95_get", get)

    def callback(url, result):
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        Scraper().check_versions(
            ["https://f95zone.to/threads/1/", "https://f95zone.to/threads/2/"], callback
        )
    assert get.call_count == (2 if '"msg":{}' in response[1] else 1)


def test_invalid_url_callback_exception_propagates_without_request(monkeypatch):
    get = MagicMock()
    monkeypatch.setattr(scraper_module, "_f95_get", get)

    def callback(url, result):
        raise RuntimeError("invalid URL callback")

    with pytest.raises(RuntimeError, match="invalid URL callback"):
        Scraper().check_versions(["bad"], callback)
    get.assert_not_called()


def test_cancellation_stops_page_fallbacks(monkeypatch, thread_html):
    get = MagicMock(side_effect=[(400, "error"), (200, thread_html)])
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    urls = [f"https://f95zone.to/threads/{i}/" for i in range(1, 102)]
    results = Scraper().check_versions(urls, lambda url, result: False)
    assert list(results) == urls[:1]
    assert get.call_count == 2


def test_single_check_and_metadata_keep_result_shape(monkeypatch, thread_html):
    get = MagicMock(
        side_effect=[checker_response(["1"]), (200, thread_html), (200, thread_html)]
    )
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    scraper = Scraper()
    expected = {
        "success",
        "title",
        "version",
        "engine",
        "tags",
        "cover_image",
        "thread_main_post_last_edit_at",
        "thread_main_post_checked_at",
    }
    assert (
        expected
        <= scraper.get_thread_version(
            "https://f95zone.to/threads/1/", include_metadata=True
        ).keys()
    )
    assert (
        expected <= scraper.get_thread_metadata("https://f95zone.to/threads/1/").keys()
    )
    assert get.call_count == 3


@pytest.mark.parametrize(
    ("raw", "expected"), [("bad", 2), ("0", 1), ("60", 30), ("3", 3)]
)
def test_request_interval_bounds(monkeypatch, raw, expected):
    monkeypatch.setenv("WLIB_UPDATE_CHECK_DELAY_SECONDS", raw)
    assert scraper_module.get_request_interval_seconds() == expected


def test_http_requests_serialize_and_wait_after_finish(monkeypatch):
    monkeypatch.setenv("WLIB_UPDATE_CHECK_DELAY_SECONDS", "1")
    monkeypatch.setattr(scraper_module, "_f95_last_request_finished", None)
    events = []
    headers = Message()
    headers["Content-Type"] = "text/html; charset=utf-8"

    def urlopen(request, **kwargs):
        events.append(("start", time.monotonic()))
        assert request.get_header("User-agent") == "Mozilla/5.0"
        assert kwargs["timeout"] == 30
        response = MagicMock()
        response.status = 200
        response.headers = headers

        def read():
            time.sleep(0.02)
            events.append(("finish", time.monotonic()))
            return b"ok"

        response.read.side_effect = read
        response.__enter__.return_value = response
        return response

    monkeypatch.setattr(scraper_module, "urlopen", urlopen)
    threads = [
        threading.Thread(target=scraper_module._f95_get, args=("https://f95zone.to/",))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert [event for event, _ in events] == ["start", "finish", "start", "finish"]
    assert events[2][1] - events[1][1] >= 1


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TimeoutError("timeout"), "navigation_timeout"),
        (URLError(TimeoutError("timeout")), "navigation_timeout"),
        (URLError("offline"), "scraper_error"),
    ],
)
def test_network_failure_codes(monkeypatch, error, code):
    monkeypatch.setattr(scraper_module, "_f95_last_request_finished", None)
    monkeypatch.setattr(scraper_module, "urlopen", MagicMock(side_effect=error))
    assert (
        Scraper().get_thread_metadata("https://f95zone.to/threads/1/")["code"] == code
    )


def test_http_error_body_is_preserved(monkeypatch):
    from io import BytesIO

    monkeypatch.setattr(scraper_module, "_f95_last_request_finished", None)
    error = HTTPError(
        "https://f95zone.to/", 404, "Not Found", Message(), BytesIO(b"missing")
    )
    monkeypatch.setattr(scraper_module, "urlopen", MagicMock(side_effect=error))
    assert scraper_module._f95_get("https://f95zone.to/") == (404, "missing")


def test_title_versions_match_extension_parser():
    # Shared with tests/extension_version.test.cjs so both parsers stay identical.
    cases_path = Path(__file__).with_name("version_title_cases.json")
    scraper = Scraper()
    for title, version in json.loads(cases_path.read_text(encoding="utf-8")):
        assert scraper._extract_version_from_title(title) == version, title


def test_extract_version_from_title_real_f95_formats():
    """Test version extraction against real F95Zone title patterns."""
    scraper = Scraper()

    # Real F95Zone title: after label removal, the text looks like this
    assert (
        scraper._extract_version_from_title(
            "Ambrosia [v1.07] [Shimobashira Workshop / Kagura Games]"
        )
        == "1.07"
    )
    assert (
        scraper._extract_version_from_title("Some Game [0.45.1] [Some Developer]")
        == "0.45.1"
    )
    assert (
        scraper._extract_version_from_title("Another Game [v2.5a] [Dev Studio]")
        == "2.5a"
    )
    # Title with "Version" prefix in brackets
    assert (
        scraper._extract_version_from_title("Cool Game [Version 3.2] [Cool Dev]")
        == "3.2"
    )
    # Bare version (no brackets)
    assert (
        scraper._extract_version_from_title("Game Name v0.9.1 [Developer]") == "0.9.1"
    )
    # Season/Episode format
    assert (
        scraper._extract_version_from_title("Story Game [S2 E5] [Writer Studio]")
        == "S2 E5"
    )
    # Chapter format with version suffix
    assert (
        scraper._extract_version_from_title("RPG Game [Ch.12 v0.3] [RPG Dev]")
        == "Ch.12 v0.3"
    )


def test_post_versions_match_extension_parser():
    # Shared with tests/extension_version.test.cjs so both parsers stay identical.
    cases_path = Path(__file__).with_name("post_version_cases.json")
    scraper = Scraper()
    for html, version in json.loads(cases_path.read_text(encoding="utf-8")):
        assert scraper._extract_version_from_post(html) == version, html


def test_engine_prefix_ignores_spaces_and_hyphens(thread_html):
    scraper = Scraper()
    orig = '<a class="labelLink"><span>Ren\'Py</span></a>'
    for labels, expected in [
        (["On Hold", "RenPy"], "RenPy"),
        (["on-hold", "RenPy"], "RenPy"),
        (["Site Rip", "Unity"], "Unity"),
    ]:
        prefix = "".join(f'<a class="labelLink">{label}</a>' for label in labels)
        html = thread_html.replace(orig, prefix).replace(
            '<span class="label-append">Completed</span>', ""
        )
        assert scraper._extract_page(html)["engine"] == expected


def test_http_exception_is_wrapped_as_scraper_error(monkeypatch):
    import http.client

    monkeypatch.setattr(scraper_module, "_f95_last_request_finished", None)
    monkeypatch.setattr(
        scraper_module,
        "urlopen",
        MagicMock(side_effect=http.client.BadStatusLine("bad")),
    )
    with pytest.raises(scraper_module._F95RequestError) as excinfo:
        scraper_module._f95_get("https://f95zone.to/threads/1/")
    assert excinfo.value.code == "scraper_error"


def test_thread_id_matching_is_case_insensitive_for_threads_prefix():
    from core.f95zone import extract_thread_id, thread_urls_match

    assert extract_thread_id("HTTPS://F95ZONE.TO/THREADS/GAME.123/") == "123"
    assert thread_urls_match(
        "https://f95zone.to/threads/game.123/",
        "HTTPS://F95ZONE.TO/THREADS/GAME.123/",
    )


def test_urllib_and_extension_requests_serialize_and_space(monkeypatch):
    from core import extension_bridge as bridge_module
    from core.extension_bridge import BridgeResponse

    monkeypatch.setenv("WLIB_UPDATE_CHECK_DELAY_SECONDS", "1")
    monkeypatch.setattr(scraper_module, "_f95_last_request_finished", None)
    events: list[tuple[str, float]] = []
    headers = Message()
    headers["Content-Type"] = "text/html; charset=utf-8"

    def fake_urlopen(request, **kwargs):
        events.append(("urllib-start", time.monotonic()))
        time.sleep(0.02)
        events.append(("urllib-finish", time.monotonic()))
        response = MagicMock()
        response.status = 200
        response.headers = headers
        response.read.return_value = b"ok"
        response.__enter__.return_value = response
        return response

    def fake_fetch(url, timeout=30):
        events.append(("extension-start", time.monotonic()))
        time.sleep(0.02)
        events.append(("extension-finish", time.monotonic()))
        return BridgeResponse(status=200, final_url=url, body="ok")

    monkeypatch.setattr(scraper_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(bridge_module, "fetch", fake_fetch)
    threads = [
        threading.Thread(
            target=scraper_module._f95_get, args=("https://f95zone.to/",)
        ),
        threading.Thread(
            target=scraper_module._f95_get_via_extension,
            args=("https://f95zone.to/threads/1/",),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    kinds = [kind for kind, _ in events]
    assert kinds in (
        ["urllib-start", "urllib-finish", "extension-start", "extension-finish"],
        ["extension-start", "extension-finish", "urllib-start", "urllib-finish"],
    )
    assert events[2][1] - events[1][1] >= 1


def _reset_scraper_globals():
    scraper_module._prefer_extension_until = None
    scraper_module._f95_last_request_finished = None


def test_challenged_page_recovered_through_extension(monkeypatch, thread_html):
    _reset_scraper_globals()
    monkeypatch.setattr(
        scraper_module, "_f95_get", MagicMock(return_value=(503, "cf-challenge"))
    )
    monkeypatch.setattr(
        scraper_module,
        "_f95_get_via_extension",
        MagicMock(return_value=(200, thread_html)),
    )
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: True
    )
    result = Scraper().get_thread_metadata("https://f95zone.to/threads/1/")
    assert result["success"] is True
    assert result["version"] == "1.10"
    assert "browser_fallback" not in result
    _reset_scraper_globals()


def test_login_page_recovered_through_extension(monkeypatch, thread_html):
    _reset_scraper_globals()
    monkeypatch.setattr(
        scraper_module,
        "_f95_get",
        MagicMock(return_value=(200, "<title>Log in | F95zone</title>")),
    )
    monkeypatch.setattr(
        scraper_module,
        "_f95_get_via_extension",
        MagicMock(return_value=(200, thread_html)),
    )
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: True
    )
    result = Scraper().get_thread_metadata("https://f95zone.to/threads/1/")
    assert result["success"] is True
    assert "browser_fallback" not in result
    _reset_scraper_globals()


def test_extension_also_challenged_reports_blocked_failed(monkeypatch):
    _reset_scraper_globals()
    monkeypatch.setattr(
        scraper_module, "_f95_get", MagicMock(return_value=(503, "cf-challenge"))
    )
    monkeypatch.setattr(
        scraper_module,
        "_f95_get_via_extension",
        MagicMock(return_value=(503, "cf-challenge")),
    )
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: True
    )
    result = Scraper().get_thread_metadata("https://f95zone.to/threads/1/")
    assert result["code"] == "blocked"
    assert result.get("browser_fallback") == "failed"
    _reset_scraper_globals()


def test_extension_also_login_reports_login_failed(monkeypatch):
    _reset_scraper_globals()
    monkeypatch.setattr(
        scraper_module,
        "_f95_get",
        MagicMock(return_value=(200, "<title>Log in</title>")),
    )
    monkeypatch.setattr(
        scraper_module,
        "_f95_get_via_extension",
        MagicMock(return_value=(200, "<title>Log in</title>")),
    )
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: True
    )
    result = Scraper().get_thread_metadata("https://f95zone.to/threads/1/")
    assert result["code"] == "login_required"
    assert result.get("browser_fallback") == "failed"
    _reset_scraper_globals()


def test_no_extension_reports_unavailable(monkeypatch):
    _reset_scraper_globals()
    monkeypatch.setattr(
        scraper_module, "_f95_get", MagicMock(return_value=(503, "cf-challenge"))
    )
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: False
    )
    result = Scraper().get_thread_metadata("https://f95zone.to/threads/1/")
    assert result["code"] == "blocked"
    assert result.get("browser_fallback") == "unavailable"
    _reset_scraper_globals()


def test_blocked_batch_retried_as_one_extension_request(monkeypatch):
    _reset_scraper_globals()
    get = MagicMock(return_value=(503, "cf-challenge"))
    via = MagicMock(return_value=checker_response(["1", "2", "3"]))
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    monkeypatch.setattr(scraper_module, "_f95_get_via_extension", via)
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: True
    )
    urls = [f"https://f95zone.to/threads/{i}/" for i in (1, 2, 3)]
    results = Scraper().check_versions(urls)
    assert all(r["version"] == "1.10" for r in results.values())
    assert get.call_count == 1
    assert via.call_count == 1
    _reset_scraper_globals()


def test_rate_limited_not_retried(monkeypatch):
    _reset_scraper_globals()
    get = MagicMock(return_value=(429, "Too Many Requests"))
    via = MagicMock()
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    monkeypatch.setattr(scraper_module, "_f95_get_via_extension", via)
    urls = [f"https://f95zone.to/threads/{i}/" for i in range(1, 4)]
    results = Scraper().check_versions(urls)
    assert all(r["code"] == "rate_limited" for r in results.values())
    assert all("browser_fallback" not in r for r in results.values())
    via.assert_not_called()
    _reset_scraper_globals()


def test_not_found_and_network_errors_do_not_fallback(monkeypatch):
    _reset_scraper_globals()
    via = MagicMock()
    monkeypatch.setattr(scraper_module, "_f95_get_via_extension", via)
    monkeypatch.setattr(
        scraper_module, "_f95_get", MagicMock(return_value=(404, "missing"))
    )
    assert (
        Scraper().get_thread_metadata("https://f95zone.to/threads/1/")["code"]
        == "not_found"
    )
    assert (
        "browser_fallback"
        not in Scraper().get_thread_metadata("https://f95zone.to/threads/1/")
    )
    via.assert_not_called()
    from core.scraper import _F95RequestError

    monkeypatch.setattr(
        scraper_module,
        "_f95_get",
        MagicMock(side_effect=_F95RequestError("navigation_timeout", "timed out")),
    )
    result = Scraper().get_thread_metadata("https://f95zone.to/threads/1/")
    assert result["code"] == "navigation_timeout"
    assert "browser_fallback" not in result
    via.assert_not_called()
    _reset_scraper_globals()


def test_extension_first_window_skips_urllib_then_expires(monkeypatch, thread_html):
    _reset_scraper_globals()
    now = [1000.0]
    monkeypatch.setattr(scraper_module.time, "monotonic", lambda: now[0])
    get = MagicMock(return_value=(503, "cf-challenge"))
    via = MagicMock(return_value=(200, thread_html))
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    monkeypatch.setattr(scraper_module, "_f95_get_via_extension", via)
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: True
    )
    scraper = Scraper()
    first = scraper.get_thread_metadata("https://f95zone.to/threads/1/")
    assert first["success"] is True
    assert get.call_count == 1 and via.call_count == 1
    # Inside the window: extension first, urllib skipped.
    now[0] = 1100.0
    second = scraper.get_thread_metadata("https://f95zone.to/threads/2/")
    assert second["success"] is True
    assert get.call_count == 1 and via.call_count == 2
    # After expiry: urllib first again.
    now[0] = 2000.0
    get.return_value = (200, thread_html)
    third = scraper.get_thread_metadata("https://f95zone.to/threads/3/")
    assert third["success"] is True
    assert get.call_count == 2
    _reset_scraper_globals()


def test_disconnect_inside_window_uses_urllib(monkeypatch, thread_html):
    _reset_scraper_globals()
    now = [1000.0]
    monkeypatch.setattr(scraper_module.time, "monotonic", lambda: now[0])
    get = MagicMock(return_value=(503, "cf-challenge"))
    via = MagicMock(return_value=(200, thread_html))
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    monkeypatch.setattr(scraper_module, "_f95_get_via_extension", via)
    available = [True]
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: available[0]
    )
    scraper = Scraper()
    assert scraper.get_thread_metadata("https://f95zone.to/threads/1/")["success"]
    now[0] = 1100.0
    available[0] = False
    get.return_value = (200, thread_html)
    result = scraper.get_thread_metadata("https://f95zone.to/threads/2/")
    assert result["success"] is True
    # Second request used urllib (get called again), not extension-first.
    assert get.call_count == 2 and via.call_count == 1
    _reset_scraper_globals()


def test_login_required_does_not_open_window(monkeypatch, thread_html):
    _reset_scraper_globals()
    now = [1000.0]
    monkeypatch.setattr(scraper_module.time, "monotonic", lambda: now[0])
    get = MagicMock(return_value=(200, "<title>Log in</title>"))
    via = MagicMock(return_value=(200, thread_html))
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    monkeypatch.setattr(scraper_module, "_f95_get_via_extension", via)
    monkeypatch.setattr(
        scraper_module.extension_bridge, "is_available", lambda: True
    )
    scraper = Scraper()
    assert scraper.get_thread_metadata("https://f95zone.to/threads/1/")["success"]
    assert scraper_module._prefer_extension_until is None
    now[0] = 1100.0
    get.return_value = (200, thread_html)
    assert scraper.get_thread_metadata("https://f95zone.to/threads/2/")["success"]
    # No window, so the second request still tried urllib first.
    assert get.call_count == 2
    _reset_scraper_globals()


def test_screenshots_collected_in_post_order(thread_html):
    result = Scraper()._extract_page(thread_html)
    assert result["screenshots"] == [
        "https://attachments.f95zone.to/2026/01/shot1-full.jpg",
        "https://attachments.f95zone.to/2026/01/shot2-full.jpg",
        "https://example.com/external-shot.png",
    ]


def test_screenshots_exclude_cover_smilie_and_reply(thread_html):
    result = Scraper()._extract_page(thread_html)
    screenshots = result["screenshots"]
    assert result["cover_image"] not in screenshots
    assert not any("smilie" in url for url in screenshots)
    assert "https://example.com/reply.png" not in screenshots


def test_screenshots_thumb_stripped_and_external_verbatim(thread_html):
    result = Scraper()._extract_page(thread_html)
    assert "https://attachments.f95zone.to/2026/01/shot1-full.jpg" in result["screenshots"]
    assert not any("/thumb/" in url for url in result["screenshots"])
    assert "https://example.com/external-shot.png" in result["screenshots"]


def test_screenshots_dedupe_thumb_and_full(thread_html):
    result = Scraper()._extract_page(thread_html)
    assert result["screenshots"].count(
        "https://attachments.f95zone.to/2026/01/shot1-full.jpg"
    ) == 1


def test_screenshots_cap_at_20():
    images = "".join(
        f'<img class="bbImage" src="https://example.com/shot{i:02d}.png">'
        for i in range(25)
    )
    html = (
        "<title>Game [v1.0]</title>"
        '<h1 class="p-title-value">Game [v1.0]</h1>'
        '<article class="message-threadStarterPost">'
        f'<div class="bbWrapper"><img class="bbImage" src="https://example.com/cover.png">{images}</div>'
        "</article>"
    )
    result = Scraper()._extract_page(html)
    assert result["cover_image"] == "https://example.com/cover.png"
    assert len(result["screenshots"]) == 20
    assert result["screenshots"][0] == "https://example.com/shot00.png"
    assert result["screenshots"][-1] == "https://example.com/shot19.png"


def test_screenshots_empty_for_post_without_images():
    html = (
        "<title>Game [v1.0]</title>"
        '<h1 class="p-title-value">Game [v1.0]</h1>'
        '<article class="message-threadStarterPost">'
        '<div class="bbWrapper"><b>Version</b>: v1.0<br>No images here</div>'
        "</article>"
    )
    assert Scraper()._extract_page(html)["screenshots"] == []


def test_screenshots_carry_through_page_fallback_without_extra_request(
    monkeypatch, thread_html
):
    get = MagicMock(
        side_effect=[(200, '{"status":"ok","msg":{}}'), (200, thread_html)]
    )
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    scraper = Scraper()
    result = scraper.get_thread_version(
        "https://f95zone.to/threads/1/", include_metadata=True
    )
    assert result["success"] is True
    assert result["screenshots"] == [
        "https://attachments.f95zone.to/2026/01/shot1-full.jpg",
        "https://attachments.f95zone.to/2026/01/shot2-full.jpg",
        "https://example.com/external-shot.png",
    ]
    assert get.call_count == 2


def test_screenshots_carry_through_batch_and_metadata_fetch(
    monkeypatch, thread_html
):
    get = MagicMock(
        side_effect=[checker_response(["1"]), (200, thread_html)]
    )
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    scraper = Scraper()
    result = scraper.get_thread_version(
        "https://f95zone.to/threads/1/", include_metadata=True
    )
    assert result["success"] is True
    assert result["screenshots"] == [
        "https://attachments.f95zone.to/2026/01/shot1-full.jpg",
        "https://attachments.f95zone.to/2026/01/shot2-full.jpg",
        "https://example.com/external-shot.png",
    ]
    assert get.call_count == 2


def test_check_versions_page_fallback_carries_screenshots(
    monkeypatch, thread_html
):
    get = MagicMock(
        side_effect=[(200, '{"status":"ok","msg":{}}'), (200, thread_html)]
    )
    monkeypatch.setattr(scraper_module, "_f95_get", get)
    results = Scraper().check_versions(["https://f95zone.to/threads/1/"])
    assert results["https://f95zone.to/threads/1/"]["screenshots"] == [
        "https://attachments.f95zone.to/2026/01/shot1-full.jpg",
        "https://attachments.f95zone.to/2026/01/shot2-full.jpg",
        "https://example.com/external-shot.png",
    ]
    assert get.call_count == 2
