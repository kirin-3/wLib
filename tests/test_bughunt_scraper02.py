# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for scraper and updates bug hunt 02."""

from __future__ import annotations

import json

import pytest

from core import database, extension_bridge, lewdcorner, lewdcorner_updates
from core.api import Api

LC_URL = "https://lewdcorner.com/threads/1/"
LC_URL_2 = "https://lewdcorner.com/threads/2/"
F95_URL = "https://f95zone.to/threads/9/"
MARK = 1794000000


class _InlineThread:
    def __init__(self, target, args=(), daemon=None):
        _ = daemon
        self._run = lambda: target(*args)

    def start(self):
        self._run()


def _item(thread_id, version, timestamp, *prefixes):
    return {
        "id": thread_id,
        "node_id": 6,
        "version": version,
        "timestamp": timestamp,
        "badges": [{"name": name} for name in prefixes],
    }


def _mock_lc(monkeypatch, feed, catalog=None):
    """Serve `feed` as the only LC feed page; return the fetched URLs."""
    urls: list[str] = []

    def fetch(url, *, site):
        assert site == "lc" and "/latest-updates/api?" in url
        urls.append(url)
        body = json.dumps(
            {"status": "ok", "result": {"items": list(feed), "hasMore": False}}
        )
        return extension_bridge.BridgeResponse(200, url, body)

    monkeypatch.setattr(
        extension_bridge, "is_available", lambda *, site="f95": site == "lc"
    )
    monkeypatch.setattr(extension_bridge, "fetch", fetch)
    monkeypatch.setattr("core.game_lookup.lc_thread_entries", lambda ids: catalog or {})
    monkeypatch.setattr(
        "core.api_mixins.updates.lc_thread_entries", lambda ids: catalog or {}
    )
    monkeypatch.setattr("threading.Thread", _InlineThread)
    return urls


def _check_all(api, monkeypatch, feed, f95_url=""):
    _ = _mock_lc(monkeypatch, feed)
    monkeypatch.setattr(
        api.scraper,
        "check_versions",
        lambda urls, callback: (
            callback(
                f95_url,
                {"success": True, "version": "1.1", "thread_completed": False},
            )
            and {}
        ),
    )
    assert api.check_all_updates()["success"]
    return database.get_all_games()[0]


# ---------------------------------------------------------------------------
# Bug 1: An older LewdCorner feed item can mark a game completed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("has_f95", [False, True])
def test_older_completed_lc_feed_item_does_not_mark_the_game_completed(
    monkeypatch, has_f95
):
    api = Api()
    f95_url = F95_URL if has_f95 else ""
    _ = database.add_game("Game", "", f95_url, lc_url=LC_URL, lc_mode="on")
    # A newer feed read stores 2.0 without the Completed prefix.
    game = _check_all(api, monkeypatch, [_item(1, "2.0", MARK, "Ren'Py")], f95_url)
    assert (game["thread_completed"], game["lc_thread_completed"]) == (0, 0)

    # An older feed item for 1.0 says Completed.
    game = _check_all(
        api, monkeypatch, [_item(1, "1.0", MARK - 1000, "Completed")], f95_url
    )
    assert game["lc_latest_version"] == "2.0"
    assert (game["thread_completed"], game["lc_thread_completed"]) == (0, 0)
    assert not any(
        result.get("newly_finished") for result in api.get_update_status()["results"]
    )


def test_older_completed_feed_observation_keeps_the_stored_state():
    database.init_db()
    game_id = database.add_game("Game", "", lc_url=LC_URL)
    assert game_id
    _ = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("2.0", lewdcorner.timestamp_iso(MARK)),
        keep_newer=True,
        lc_completed=False,
        lc_completed_from_feed=True,
    )
    stored = database.store_thread_update(
        game_id,
        "lc",
        LC_URL,
        ("1.0", lewdcorner.timestamp_iso(MARK - 1000)),
        completed=True,
        keep_newer=True,
        lc_completed=True,
        lc_completed_from_feed=True,
    )
    assert stored is not None
    game, newly_finished, _ = stored
    assert newly_finished is False
    assert game["lc_latest_version"] == "2.0"
    assert not game["thread_completed"]
    assert not game["lc_thread_completed"]


# ---------------------------------------------------------------------------
# Bug 2: The LewdCorner feed cursor advances after cancellation or
# incomplete coverage
# ---------------------------------------------------------------------------


def test_cancel_during_the_last_lc_game_keeps_the_feed_cursor(monkeypatch):
    api = Api()
    a = database.add_game("A", "/a", lc_url=LC_URL)
    assert a
    database.update_setting("lc_feed_read_at", lewdcorner.timestamp_iso(MARK))
    _ = _mock_lc(monkeypatch, [_item(1, "3.0", MARK + 200)])
    real_check = lewdcorner_updates.check_thread

    def check_then_cancel(*args, **kwargs):
        result = real_check(*args, **kwargs)
        api._update_cancelled = True
        return result

    monkeypatch.setattr(lewdcorner_updates, "check_thread", check_then_cancel)
    _ = api.check_all_updates()
    assert database.get_setting("lc_feed_read_at") == lewdcorner.timestamp_iso(MARK)


def test_cancel_during_the_f95_step_keeps_the_feed_cursor(monkeypatch):
    api = Api()
    _ = database.add_game("Both", "/a", F95_URL, lc_url=LC_URL, lc_mode="on")
    database.update_setting("lc_feed_read_at", lewdcorner.timestamp_iso(MARK))
    _ = _mock_lc(monkeypatch, [_item(1, "3.0", MARK + 200)])

    def cancel_in_f95(urls, callback):
        api._update_cancelled = True
        return {}

    monkeypatch.setattr(api.scraper, "check_versions", cancel_in_f95)
    _ = api.check_all_updates()
    assert database.get_setting("lc_feed_read_at") == lewdcorner.timestamp_iso(MARK)


def test_archived_lc_game_keeps_its_feed_item_after_the_cursor_moves(monkeypatch):
    api = Api()
    a = database.add_game("A", "/a", lc_url=LC_URL)
    archived = database.add_game("Archived", "/b", lc_url=LC_URL_2)
    assert a and archived
    # Premium thread: no usable catalog entry, so only the feed has updates.
    _ = database.store_thread_update(
        archived, "lc", LC_URL_2, ("1.0", lewdcorner.timestamp_iso(MARK - 50))
    )
    assert database.update_game(archived, {"play_status": "Archived"})
    database.update_setting("lc_feed_read_at", lewdcorner.timestamp_iso(MARK))

    urls = _mock_lc(
        monkeypatch, [_item(1, "3.0", MARK + 200), _item(2, "2.0", MARK + 100)]
    )
    assert api.check_all_updates()["success"]
    status = api.get_update_status()
    # The Archived game is still left out of the check: not counted, no
    # result and no request other than the feed read.
    assert status["total"] == 1
    assert [result["id"] for result in status["results"]] == [a]
    assert all("/latest-updates/api?" in url for url in urls)
    assert database.get_setting("lc_feed_read_at") == lewdcorner.timestamp_iso(
        MARK + 200
    )

    # The user unarchives the game; its feed item is now behind the cursor.
    assert database.update_game(archived, {"play_status": "Playing"})
    _ = _mock_lc(monkeypatch, [_item(1, "3.1", MARK + 300)])
    assert api.check_all_updates()["success"]
    assert database.get_game(archived)["lc_latest_version"] == "2.0"
    result = next(
        result
        for result in api.get_update_status()["results"]
        if result["id"] == archived
    )
    assert result["has_update"] is True


# ---------------------------------------------------------------------------
# Bug 3: An F95 title placeholder overwrites the archive's version
# ---------------------------------------------------------------------------


def _lookup(monkeypatch, title, version):
    api = Api()
    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda *_args, **_kwargs: {
            "success": True,
            "title": title,
            "version": version,
            "engine": "",
            "cover_image": "",
            "tags": [],
        },
    )
    result = api.lookup_thread_details("https://f95zone.to/threads/game.123/")
    assert result["success"] is True
    return result


def test_title_placeholder_version_uses_the_post_version(monkeypatch):
    result = _lookup(monkeypatch, "Game [Unknown] [Dev]", "1.2")
    assert (result["title"], result["version"], result["creator"]) == (
        "Game",
        "1.2",
        "Dev",
    )


def test_title_placeholder_version_without_post_version_is_empty(monkeypatch):
    assert _lookup(monkeypatch, "Game [Unknown] [Dev]", "Unknown")["version"] == ""
    assert _lookup(monkeypatch, "Game [N/A] [Dev]", "")["version"] == ""


def test_text_title_version_still_fills_in(monkeypatch):
    assert (
        _lookup(monkeypatch, "Game [Final release] [Dev]", "Unknown")["version"]
        == "Final release"
    )
