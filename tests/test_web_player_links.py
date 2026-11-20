# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import patch

from core import web_player


def test_mailto_link_reaches_system_opener() -> None:
    url = "mailto:author@example.com?subject=Game%20feedback"

    assert web_player.classify_navigation(url, "open") == "open"
    with patch("core.web_player._open_system_target", return_value=True) as opener:
        assert web_player.open_url_on_host(url)
    opener.assert_called_once_with(url)
