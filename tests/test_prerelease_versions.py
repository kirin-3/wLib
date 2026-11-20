# SPDX-License-Identifier: GPL-3.0-or-later
"""Numbered prereleases ("Beta 2", "Beta 3") must stay apart."""
import pytest

from core.f95zone import split_thread_title
from core.scraper import Scraper
from core.versions import normalize_version


@pytest.mark.parametrize(("raw", "expected"), [
    ("v1.2 Beta 2", "1.2 Beta 2"),
    ("v1.2 Beta 3", "1.2 Beta 3"),
    ("v0.5 Alpha 10", "0.5 Alpha 10"),
    ("v1.0 Fix 2", "1.0 Fix 2"),
    ("v1.0 fix2", "1.0 fix2"),
    ("v2.0 Beta", "2.0 Beta"),
])
def test_checker_keeps_the_prerelease_number(raw, expected):
    assert Scraper()._normalize_checker_version(raw) == expected


def test_title_keeps_the_prerelease_number():
    scraper = Scraper()
    two = scraper._extract_version_from_title("Game [v1.2 Beta 2] [Dev]")
    three = scraper._extract_version_from_title("Game [v1.2 Beta 3] [Dev]")
    assert (two, three) == ("1.2 Beta 2", "1.2 Beta 3")
    assert normalize_version(two) != normalize_version(three)


def test_thread_heading_keeps_the_prerelease_number():
    assert split_thread_title("Game [v1.2 Beta 3] [Dev]") == ("Game", "1.2 Beta 3", "Dev")


def test_post_version_keeps_the_prerelease_number():
    html = "<div>Version: 1.2 Beta 2, released today</div>"
    assert Scraper()._extract_version_from_post(html) == "1.2 Beta 2"
