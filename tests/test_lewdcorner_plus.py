"""LC Plus detection (`core/lewdcorner_plus.py`) with the bridge and the catalog faked."""

from datetime import UTC, datetime, timedelta

import pytest

from core import database, extension_bridge, game_lookup, lewdcorner_plus
from core.extension_bridge import BridgeResponse


def _page(
    *, node: int = 119, template: str = "thread_view", logged_in: str = "true"
) -> str:
    return (
        f'<html data-template="{template}" data-container-key="node-{node}" data-logged-in="{logged_in}">'
        '<h1 class="p-title-value">Game [v1.0] [Dev]</h1></html>'
    )


PLUS = _page()
RELEASED = _page(node=6)
DENIED = '<html data-template="error" data-logged-in="true"><p>You do not have permission.</p></html>'
GUEST = _page(logged_in="false")


@pytest.fixture
def lc(monkeypatch):
    """LC on, mode Auto, a catalog with Premium threads 1-5, and an extension that answers ``pages``."""
    database.init_db()
    database.update_setting("lc_enabled", "1")
    database.update_setting("lc_plus_mode", "auto")
    state = {
        "pages": {},
        "fetched": [],
        "available": True,
        "premium": ["1", "2", "3", "4", "5"],
        "tiers": {},
    }
    monkeypatch.setattr(game_lookup, "catalog_installed", lambda: True)
    monkeypatch.setattr(
        game_lookup, "premium_lc_threads", lambda limit=3: state["premium"][:limit]
    )
    monkeypatch.setattr(
        game_lookup,
        "lc_thread_tiers",
        lambda ids: {i: state["tiers"][i] for i in ids if i in state["tiers"]},
    )
    monkeypatch.setattr(
        extension_bridge, "is_available", lambda site="f95": state["available"]
    )

    def fetch(url, timeout=30, *, site="f95", background=False):
        assert site == "lc" and background
        state["fetched"].append(url)
        thread = url.rstrip("/").rsplit("/", 1)[1]
        status, body = state["pages"].get(thread, (200, RELEASED))
        return BridgeResponse(status, url, body)

    monkeypatch.setattr(extension_bridge, "fetch", fetch)
    turned_on = []
    lewdcorner_plus.set_turned_on_callback(lambda: turned_on.append(True))
    state["turned_on"] = turned_on
    yield state
    lewdcorner_plus.set_turned_on_callback(None)


def test_plus_account(lc):
    lc["pages"]["1"] = (200, PLUS)
    assert lewdcorner_plus.detect("settings") == {"result": "plus", "skipped": ""}
    assert database.get_setting("lc_plus_detected") == "plus"
    assert database.get_setting("lc_plus_detected_at")
    assert lc["fetched"] == ["https://lewdcorner.com/threads/1/"]
    # The effective state turned on, so the Plus fill starts.
    assert lc["turned_on"] == [True]


def test_standard_account(lc):
    lc["pages"]["1"] = (403, DENIED)
    assert lewdcorner_plus.detect("settings")["result"] == "standard"
    assert database.get_setting("lc_plus_detected") == "standard"
    assert database.lc_plus_effective() is False
    assert lc["turned_on"] == []


def test_thread_released_to_everyone_tries_the_next(lc):
    lc["pages"]["1"] = (200, RELEASED)
    lc["pages"]["2"] = (200, PLUS)
    assert lewdcorner_plus.detect("settings")["result"] == "plus"
    assert lc["fetched"] == [
        "https://lewdcorner.com/threads/1/",
        "https://lewdcorner.com/threads/2/",
    ]


def test_every_try_unknown_keeps_the_tier_and_tries_three(lc):
    database.update_setting("lc_plus_detected", "plus")
    for thread in ("1", "2", "3", "4"):
        lc["pages"][thread] = (200, GUEST)
    assert lewdcorner_plus.detect("check_now") == {"result": "", "skipped": ""}
    assert len(lc["fetched"]) == 3
    assert database.get_setting("lc_plus_detected") == "plus"
    assert database.get_setting("lc_plus_detected_at")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda s: database.update_setting("lc_enabled", "0"), "lc_off"),
        (lambda s: database.update_setting("lc_plus_mode", "yes"), "mode"),
        (lambda s: database.update_setting("lc_plus_mode", "no"), "mode"),
        (lambda s: s.__setitem__("available", False), "no_extension"),
    ],
)
def test_skip_rules_send_no_request(lc, change, reason):
    database.update_setting("lc_plus_detected", "standard")
    change(lc)
    assert lewdcorner_plus.detect("settings") == {"result": None, "skipped": reason}
    assert lc["fetched"] == []
    assert database.get_setting("lc_plus_detected") == "standard"


def test_no_catalog_sends_no_request(lc, monkeypatch):
    monkeypatch.setattr(game_lookup, "catalog_installed", lambda: False)
    assert lewdcorner_plus.detect("settings")["skipped"] == "no_catalog"
    assert lc["fetched"] == []


def test_connect_waits_24_hours(lc):
    lc["pages"]["1"] = (403, DENIED)
    recent = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    database.update_setting("lc_plus_detected_at", recent)
    assert lewdcorner_plus.detect("connect")["skipped"] == "recent"
    assert lc["fetched"] == []
    # "Check now" and the settings triggers do not wait.
    assert lewdcorner_plus.detect("check_now")["result"] == "standard"
    old = (datetime.now(UTC) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    database.update_setting("lc_plus_detected_at", old)
    lc["fetched"].clear()
    assert lewdcorner_plus.detect("connect")["result"] == "standard"
    assert len(lc["fetched"]) == 1


def test_connect_listener_starts_one_detection(lc, monkeypatch):
    started = []
    monkeypatch.setattr(lewdcorner_plus, "start", started.append)
    extension_bridge.reset_for_tests()
    extension_bridge.add_lc_fetch_listener(lewdcorner_plus.on_lc_fetch_available)
    conn = extension_bridge.BridgeConnection(lambda _raw: None)
    extension_bridge._register_connection(conn)
    extension_bridge._handle_message(
        conn, {"type": "hello", "protocol": 2, "can_fetch": True, "can_fetch_lc": True}
    )
    assert started == ["connect"]
    extension_bridge.reset_for_tests()


def test_single_flight(lc):
    assert lewdcorner_plus._single_flight.acquire(blocking=False)
    try:
        assert lewdcorner_plus.detect("check_now") == {
            "result": None,
            "skipped": "running",
        }
    finally:
        lewdcorner_plus._single_flight.release()


def test_page_body_is_never_logged(lc, caplog):
    secret = "user-name-secret"
    lc["pages"]["1"] = (200, PLUS.replace("Game", secret))
    with caplog.at_level("DEBUG"):
        lewdcorner_plus.detect("settings")
    assert secret not in caplog.text


def test_reads_that_already_happen_update_the_tier(lc):
    lc["tiers"] = {"24724": "Premium", "100": "Free"}
    database.update_setting("lc_plus_detected", "plus")
    # A 403 for a catalog Free thread says nothing about the tier.
    lewdcorner_plus.note_thread_page("100", DENIED, 403)
    assert database.get_setting("lc_plus_detected") == "plus"
    # Plus expired: a 403 for a catalog Premium thread.
    lewdcorner_plus.note_thread_page("24724", DENIED, 403)
    assert database.get_setting("lc_plus_detected") == "standard"
    # A Games+ thread page sets Plus.
    lewdcorner_plus.note_thread_page("555", PLUS, 200)
    assert database.get_setting("lc_plus_detected") == "plus"
    assert lc["turned_on"] == [True]
    # Guest pages and pages outside Plus forums change nothing.
    lewdcorner_plus.note_thread_page("24724", GUEST, 200)
    lewdcorner_plus.note_thread_page("24724", RELEASED, 200)
    assert database.get_setting("lc_plus_detected") == "plus"
    assert lc["fetched"] == []


def test_reads_do_nothing_unless_auto(lc):
    database.update_setting("lc_plus_mode", "no")
    lewdcorner_plus.note_thread_page("555", PLUS, 200)
    assert database.get_setting("lc_plus_detected") in (None, "")


def test_download_link_reads_feed_the_tier(lc, monkeypatch):
    from core import scraper

    lc["tiers"] = {"24724": "Premium", "100": "Free"}
    pages = {"24724": (403, DENIED), "100": (403, DENIED), "555": (200, PLUS)}

    def fetch(url, timeout=30, *, site="f95", background=False):
        thread = url.rstrip("/").rsplit("/", 1)[1]
        status, body = pages[thread]
        return BridgeResponse(status, url, body)

    monkeypatch.setattr(extension_bridge, "fetch", fetch)
    database.update_setting("lc_plus_detected", "plus")
    assert (
        scraper.fetch_thread_download_links(
            "https://lewdcorner.com/threads/100/", "lc"
        )["code"]
        == "lc_plus_required"
    )
    assert database.get_setting("lc_plus_detected") == "plus"
    assert (
        scraper.fetch_thread_download_links(
            "https://lewdcorner.com/threads/24724/", "lc"
        )["code"]
        == "lc_plus_required"
    )
    assert database.get_setting("lc_plus_detected") == "standard"
    scraper.fetch_thread_download_links("https://lewdcorner.com/threads/555/", "lc")
    assert database.get_setting("lc_plus_detected") == "plus"


def test_update_check_reads_feed_the_tier(lc, monkeypatch):
    from core import lewdcorner_updates

    lc["tiers"] = {"24724": "Premium"}
    database.update_setting("lc_plus_detected", "plus")
    monkeypatch.setattr(
        lewdcorner_updates, "_fetch", lambda url: BridgeResponse(403, url, DENIED)
    )
    game_id = database.add_game(
        "Plus game", "", lc_url="https://lewdcorner.com/threads/24724/"
    )
    _ = lewdcorner_updates.check_thread(
        game_id,
        "https://lewdcorner.com/threads/24724/",
        "",
        {"tier": "Premium"},
        single=True,
    )
    assert database.get_setting("lc_plus_detected") == "standard"


def test_check_now_reports_the_result(lc, monkeypatch):
    from core.api import Api

    api = Api()
    monkeypatch.setattr(api, "_catalog_enabled", lambda: True)
    for thread in ("1", "2", "3"):
        lc["pages"][thread] = (200, GUEST)
    result = api.check_lc_plus()
    assert result["success"] is True and result["checked_without_result"] is True
    assert result["lc_plus_detected"] == "" and result["lc_plus_detected_at"]
    lc["pages"]["1"] = (403, DENIED)
    result = api.check_lc_plus()
    assert (
        result["checked_without_result"] is False
        and result["lc_plus_detected"] == "standard"
    )
    lc["available"] = False
    result = api.check_lc_plus()
    assert result["success"] is False and result["code"] == "no_extension"


def test_no_premium_threads_sends_no_request_and_keeps_the_time(lc):
    lc["premium"] = []
    assert lewdcorner_plus.detect("check_now") == {
        "result": None,
        "skipped": "no_candidates",
    }
    assert lc["fetched"] == []
    assert not database.get_setting("lc_plus_detected_at")


def test_extension_gone_before_any_request_keeps_the_time(lc, monkeypatch):
    def gone(url, timeout=30, *, site="f95", background=False):
        raise extension_bridge.BridgeUnavailable("no eligible extension connected")

    monkeypatch.setattr(extension_bridge, "fetch", gone)
    assert lewdcorner_plus.detect("connect") == {
        "result": None,
        "skipped": "no_extension",
    }
    # The next connect tries again instead of waiting 24 hours.
    assert not database.get_setting("lc_plus_detected_at")


def test_failed_requests_do_not_count_as_a_check(lc, monkeypatch):
    def failed(url, timeout=30, *, site="f95", background=False):
        lc["fetched"].append(url)
        raise extension_bridge.BridgeFailed("extension did not answer in time")

    monkeypatch.setattr(extension_bridge, "fetch", failed)
    assert lewdcorner_plus.detect("connect") == {"result": None, "skipped": "failed"}
    assert len(lc["fetched"]) == 3
    # No page came back, so the next connect tries again instead of waiting 24 hours.
    assert not database.get_setting("lc_plus_detected_at")
    assert lewdcorner_plus.skip_reason("connect") == ""


def test_one_page_read_after_a_failure_counts_as_a_check(lc, monkeypatch):
    def first_fails(url, timeout=30, *, site="f95", background=False):
        lc["fetched"].append(url)
        if len(lc["fetched"]) == 1:
            raise extension_bridge.BridgeFailed("extension did not answer in time")
        return BridgeResponse(200, url, GUEST)

    monkeypatch.setattr(extension_bridge, "fetch", first_fails)
    assert lewdcorner_plus.detect("connect") == {"result": "", "skipped": ""}
    assert len(lc["fetched"]) == 3
    assert database.get_setting("lc_plus_detected_at")
