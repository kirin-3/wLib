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
