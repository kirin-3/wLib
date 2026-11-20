# SPDX-License-Identifier: GPL-3.0-or-later
"""LC feed cursor and Completed flag freshness during update checks."""
import json

from core import database, extension_bridge, lewdcorner
from core.api import Api

A_URL = "https://lewdcorner.com/threads/1/"
B_URL = "https://lewdcorner.com/threads/2/"
MARK = 1794000000


class _InlineThread:
    def __init__(self, target, args=(), daemon=None):
        _ = daemon
        self._run = lambda: target(*args)

    def start(self):
        self._run()


def _item(thread_id, version, timestamp):
    return {"id": thread_id, "node_id": 6, "version": version, "timestamp": timestamp}


def _mock_feed(monkeypatch, feed, catalog=None):
    def fetch(url, *, site):
        assert site == "lc" and "/latest-updates/api?" in url
        body = json.dumps({"status": "ok", "result": {"items": feed, "hasMore": False}})
        return extension_bridge.BridgeResponse(200, url, body)

    monkeypatch.setattr(extension_bridge, "is_available", lambda *, site="f95": site == "lc")
    monkeypatch.setattr(extension_bridge, "fetch", fetch)
    monkeypatch.setattr("core.game_lookup.lc_thread_entries", lambda ids: catalog or {})
    monkeypatch.setattr("threading.Thread", _InlineThread)


def test_selected_game_check_keeps_the_feed_cursor(monkeypatch):
    api = Api()
    a = database.add_game("A", "/a", lc_url=A_URL)
    b = database.add_game("B", "/b", lc_url=B_URL)
    assert a and b
    database.store_thread_update(b, "lc", B_URL, ("1.0", lewdcorner.timestamp_iso(MARK - 50)))
    database.update_setting("lc_feed_read_at", lewdcorner.timestamp_iso(MARK))
    _mock_feed(monkeypatch, [_item(1, "3.0", MARK + 200), _item(2, "2.0", MARK + 100)])

    assert api.check_all_updates([a])["success"]
    # B was not checked, so its feed item must stay unread.
    assert database.get_setting("lc_feed_read_at") == lewdcorner.timestamp_iso(MARK)
    assert database.get_game(b)["lc_latest_version"] == "1.0"

    assert api.check_all_updates()["success"]
    assert database.get_game(b)["lc_latest_version"] == "2.0"
    assert database.get_setting("lc_feed_read_at") == lewdcorner.timestamp_iso(MARK + 200)


def test_cancelled_check_keeps_the_feed_cursor(monkeypatch):
    api = Api()
    a = database.add_game("A", "/a", lc_url=A_URL)
    assert a
    database.update_setting("lc_feed_read_at", lewdcorner.timestamp_iso(MARK))
    _mock_feed(monkeypatch, [_item(1, "3.0", MARK + 200)])
    from core import lewdcorner_updates

    real_read = lewdcorner_updates.read_feed

    def read_then_cancel(cancelled):
        read = real_read(cancelled)
        api._update_cancelled = True
        return read

    monkeypatch.setattr(lewdcorner_updates, "read_feed", read_then_cancel)
    _ = api.check_all_updates()
    assert database.get_setting("lc_feed_read_at") == lewdcorner.timestamp_iso(MARK)


def test_stale_catalog_does_not_mark_the_game_completed(monkeypatch):
    api = Api()
    game_id = database.add_game("LC only", "", lc_url=A_URL)
    assert game_id
    # A live read stored 2.0 without Completed.
    database.store_thread_update(
        game_id, "lc", A_URL, ("2.0", lewdcorner.timestamp_iso(MARK)),
        keep_newer=True, lc_completed=False,
    )
    _mock_feed(monkeypatch, [], catalog={"1": {
        "version": "1.8", "ts": MARK - 1000, "tier": "Free", "thread_completed": True,
    }})
    database.update_setting("lc_feed_read_at", lewdcorner.timestamp_iso(MARK + 10))

    assert api.check_all_updates()["success"]
    game = database.get_game(game_id)
    assert game["lc_latest_version"] == "2.0"
    assert not game["thread_completed"]
    assert not any(result.get("newly_finished") for result in api.get_update_status()["results"])


def test_current_catalog_still_marks_the_game_completed(monkeypatch):
    api = Api()
    game_id = database.add_game("LC only", "", lc_url=A_URL)
    assert game_id
    database.store_thread_update(
        game_id, "lc", A_URL, ("2.0", lewdcorner.timestamp_iso(MARK)), keep_newer=True,
    )
    _mock_feed(monkeypatch, [], catalog={"1": {
        "version": "2.0", "ts": MARK, "tier": "Free", "thread_completed": True,
    }})
    database.update_setting("lc_feed_read_at", lewdcorner.timestamp_iso(MARK + 10))

    assert api.check_all_updates()["success"]
    game = database.get_game(game_id)
    assert game["thread_completed"] and game["lc_thread_completed"]
