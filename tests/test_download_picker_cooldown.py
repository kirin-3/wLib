# SPDX-License-Identifier: GPL-3.0-or-later
"""The download picker reports the F95 429 cooldown as a result, not an exception."""
import time

import core.scraper
from core import extension_bridge


def test_cooldown_returns_rate_limited(monkeypatch):
    monkeypatch.setattr(extension_bridge, "is_available", lambda *, site="f95": True)

    def fetch(*_args, **_kwargs):
        raise AssertionError("the cooldown must refuse the request first")

    monkeypatch.setattr(extension_bridge, "fetch", fetch)
    monkeypatch.setattr(core.scraper, "_f95_cooldown_until", time.monotonic() + 600)
    result = core.scraper.fetch_thread_download_links("https://f95zone.to/threads/game.123/")
    assert result["success"] is False
    assert result["code"] == "rate_limited"
    assert "Rate limited" in str(result["error"])
