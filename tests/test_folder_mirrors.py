# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct folder links are not offered as in-app downloads."""

import pytest

from core import update_downloads
from core.downloads.failures import INVALID, classify

_KEY = "a" * 43


@pytest.mark.parametrize(
    ("host", "url", "expected"),
    [
        ("pixeldrain", "https://pixeldrain.com/u/abc123", True),
        ("pixeldrain", "https://pixeldrain.com/d/abc123", False),
        ("pixeldrain", "https://f95zone.to/dl/1/?fake", True),
        ("mega", f"https://mega.nz/file/abc#{_KEY}", True),
        ("mega", f"https://mega.nz/folder/abc#{_KEY}", False),
        ("mega", "https://mega.nz/#F!abc!def", False),
        ("mega", "https://f95zone.to/dl/1/?fake", True),
        ("mega", "https://lewdcorner.com/masked/out?t=fake", True),
        ("buzzheavier", "https://buzzheavier.com/share", False),
        ("gofile", "https://gofile.io/d/abc", True),
    ],
)
def test_in_app_requires_a_single_file_link(host, url, expected):
    assert update_downloads._in_app(host, url) is expected


@pytest.mark.parametrize("host", ["pixeldrain", "mega", "buzzheavier"])
def test_refusal_is_an_invalid_link_failure(host):
    assert classify(update_downloads._not_in_app_error(host)) == INVALID
