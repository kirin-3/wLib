# SPDX-License-Identifier: GPL-3.0-or-later
"""The headless-browser transport, browser sites and the F95 login cookie."""

from dataclasses import dataclass, field

import pytest

from catalog_fakes import FakeClock, FakeTransport, fixture_bytes, json_response
from catalog_server.__main__ import main
from catalog_server.browser import BrowserTransport, f95_login_cookies
from catalog_server.config import browser_sites_from_env, f95_cookie_from_env
from catalog_server.net import Pacer, Request
from catalog_server.store import StateStore

COOKIE = "12345%2Cabcdefghijklmnop"


@dataclass
class FakePage:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


class FakeSession:
    def __init__(self, browser: "FakeBrowser", options: dict[str, object]) -> None:
        self.browser = browser
        self.options = options
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def fetch(self, url: str, **kwargs: object) -> FakePage:
        assert self.started and not self.closed
        self.browser.fetched.append((self.options.get("proxy"), url))
        answer = self.browser.answer(url)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    """A session factory; `answer(url)` returns a page or an exception to raise."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.sessions: list[FakeSession] = []
        self.fetched: list[tuple[object, str]] = []

    def __call__(self, **options: object) -> FakeSession:
        session = FakeSession(self, options)
        self.sessions.append(session)
        return session


def test_the_browser_stays_open_and_restarts_when_the_proxy_changes():
    browser = FakeBrowser(lambda url: FakePage(200, b'{"ok": true}', {"Content-Type": "application/json"}))
    transport = BrowserTransport(session_factory=browser)
    first = transport(Request("GET", "https://f95zone.to/a"))
    transport(Request("GET", "https://f95zone.to/b"))
    transport(Request("GET", "https://www.dlsite.com/x", proxy="http://127.0.0.1:8888"))
    assert first.json() == {"ok": True}
    assert first.header("content-type") == "application/json"
    assert len(browser.sessions) == 2
    direct, proxied = browser.sessions
    assert "proxy" not in direct.options
    assert proxied.options["proxy"] == "http://127.0.0.1:8888"
    assert proxied.options["block_webrtc"] is True
    # The Pacer owns retries, so the browser must not retry on its own.
    assert direct.options["retries"] == 1
    # Playwright's sync API allows only one live browser per thread.
    assert direct.closed and not proxied.closed
    transport.close()
    assert proxied.closed


def test_a_browser_failure_is_retryable_and_starts_a_new_browser():
    answers = [RuntimeError("page crashed"), FakePage(200, b"fine")]
    browser = FakeBrowser(lambda url: answers.pop(0))
    transport = BrowserTransport(session_factory=browser)
    with pytest.raises(ConnectionError, match="page crashed"):
        transport(Request("GET", "https://f95zone.to/a"))
    assert browser.sessions[0].closed
    assert transport(Request("GET", "https://f95zone.to/a")).body == b"fine"
    assert len(browser.sessions) == 2


def test_the_browser_refuses_post_requests():
    transport = BrowserTransport(session_factory=FakeBrowser(lambda url: FakePage(200, b"")))
    with pytest.raises(ValueError, match="only sends GET"):
        transport(Request("POST", "https://api.vndb.org/kana/vn", body=b"{}"))


def test_pacer_sends_browser_sites_through_the_browser_and_counts_them(tmp_path):
    clock = FakeClock()
    plain = FakeTransport()
    plain.route(lambda request: True, json_response({"plain": True}))
    browser = FakeBrowser(lambda url: FakePage(200, b'{"browser": true}'))
    state = StateStore(str(tmp_path / "state.sqlite"))
    pacer = Pacer(
        state,
        plain,
        clock=clock,
        sleep=clock.sleep,
        transports={"f95": BrowserTransport(session_factory=browser)},
    )
    assert pacer.request("f95", "https://f95zone.to/a").json() == {"browser": True}
    assert pacer.request("ryuugames", "https://www.ryuugames.com/a").json() == {"plain": True}
    assert [url for _, url in browser.fetched] == ["https://f95zone.to/a"]
    assert [request.url for request in plain.requests] == ["https://www.ryuugames.com/a"]
    assert pacer.remaining("f95") == 89


def test_f95_login_cookie_goes_only_to_f95():
    (cookie,) = f95_login_cookies(COOKIE)
    assert cookie["name"] == "xf_user"
    assert cookie["value"] == COOKIE
    assert cookie["domain"] == ".f95zone.to"
    assert cookie["secure"] is True


@pytest.mark.parametrize(
    "value",
    [COOKIE, f"xf_user={COOKIE}", " 12345,abcdefghijklmnop "],
)
def test_f95_cookie_accepts_the_value_as_copied(monkeypatch, value):
    monkeypatch.setenv("WLIB_CATALOG_F95_COOKIE", value)
    assert f95_cookie_from_env() == COOKIE


def test_f95_cookie_error_never_shows_the_value(monkeypatch):
    monkeypatch.setenv("WLIB_CATALOG_F95_COOKIE", "xf_user=secret-part; xf_session=other")
    with pytest.raises(ValueError) as error:
        f95_cookie_from_env()
    assert "secret-part" not in str(error.value)


def test_browser_sites_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("WLIB_CATALOG_BROWSER_SITES", " f95, dlsite ,")
    assert browser_sites_from_env() == {"f95", "dlsite"}
    monkeypatch.delenv("WLIB_CATALOG_BROWSER_SITES")
    assert browser_sites_from_env() == frozenset()


@pytest.mark.parametrize("value", ["f95,steam", "vndb"])
def test_browser_sites_refuse_unknown_sites_and_vndb(monkeypatch, value):
    monkeypatch.setenv("WLIB_CATALOG_BROWSER_SITES", value)
    with pytest.raises(ValueError, match="WLIB_CATALOG_BROWSER_SITES"):
        browser_sites_from_env()


def test_cli_refuses_a_login_cookie_without_the_f95_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_CATALOG_F95_COOKIE", COOKIE)
    monkeypatch.setenv("WLIB_CATALOG_BROWSER_SITES", "dlsite")
    code = main(["--data-dir", str(tmp_path), "run", "--no-publish", "--sources", "none"])
    assert code == 2


def test_cli_run_uses_the_browser_for_browser_sites_and_closes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_CATALOG_BROWSER_SITES", "ryuugames")
    monkeypatch.delenv("WLIB_CATALOG_F95_COOKIE", raising=False)
    plain = FakeTransport()
    browser = FakeBrowser(
        lambda url: FakePage(200, fixture_bytes("ryuugames_posts.json"), {"X-WP-TotalPages": "1"})
    )
    clock = FakeClock()
    code = main(
        ["--data-dir", str(tmp_path), "run", "--no-publish", "--sources", "ryuugames"],
        transport=plain,
        browser=BrowserTransport(session_factory=browser),
        sleep=clock.sleep,
    )
    assert code == 0
    assert plain.requests == []
    assert browser.fetched and all("ryuugames.com" in url for _, url in browser.fetched)
    assert all(session.closed for session in browser.sessions)


def _f95_home(logged_in: bool) -> bytes:
    flag = "true" if logged_in else "false"
    return (
        f'<html id="XF" data-logged-in="{flag}"><body>'
        + '<a href="/account/" class="p-navgroup-link">'
        + '<span class="p-navgroup-linkText">SomeUser</span></a></body></html>'
    ).encode()


@pytest.mark.parametrize(("logged_in", "code"), [(True, 0), (False, 1)])
def test_check_f95_login_reports_the_account(tmp_path, monkeypatch, capsys, logged_in, code):
    monkeypatch.setenv("WLIB_CATALOG_F95_COOKIE", COOKIE)
    monkeypatch.setenv("WLIB_CATALOG_BROWSER_SITES", "f95")
    browser = FakeBrowser(lambda url: FakePage(200, _f95_home(logged_in)))
    clock = FakeClock()
    result = main(
        ["--data-dir", str(tmp_path), "check-f95-login"],
        browser=BrowserTransport(cookies=f95_login_cookies(COOKIE), session_factory=browser),
        sleep=clock.sleep,
    )
    assert result == code
    assert browser.fetched == [(None, "https://f95zone.to/")]
    assert browser.sessions[0].options["cookies"] == f95_login_cookies(COOKIE)
    assert browser.sessions[0].closed
    out = capsys.readouterr().out
    assert ("Logged in to F95 as SomeUser" in out) == logged_in


def test_check_f95_login_needs_a_cookie(tmp_path, monkeypatch):
    monkeypatch.delenv("WLIB_CATALOG_F95_COOKIE", raising=False)
    monkeypatch.setenv("WLIB_CATALOG_BROWSER_SITES", "f95")
    assert main(["--data-dir", str(tmp_path), "check-f95-login"]) == 2
