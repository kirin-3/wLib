# SPDX-License-Identifier: GPL-3.0-or-later
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock

from core import extension_server as server_module


def _handler(path):
    handler = server_module.ExtensionRequestHandler.__new__(server_module.ExtensionRequestHandler)
    handler.path = path
    handler.headers = Message()
    handler.__dict__["wfile"] = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.send_error = MagicMock()
    return handler


def test_screenshot_keys_are_random_and_stable_for_a_session():
    from core.game_covers import LC_KEY_RE, LcImageKeys, lc_image_key

    url = "https://lewdcorner.com/attachments/shot-png.1/"
    keys = LcImageKeys()
    key = keys.add(url)
    assert LC_KEY_RE.fullmatch(key)
    assert key != lc_image_key(url)
    assert keys.add(url) == key
    assert keys.get(key) == url
    assert keys.get(lc_image_key(url)) is None
    assert LcImageKeys().add(url) != key


def test_evicted_url_gets_a_new_key():
    from core.game_covers import LcImageKeys

    keys = LcImageKeys()
    keys.LIMIT = 2
    first = keys.add("https://lewdcorner.com/attachments/a-png.1/")
    _ = keys.add("https://lewdcorner.com/attachments/b-png.2/")
    _ = keys.add("https://lewdcorner.com/attachments/c-png.3/")
    assert keys.get(first) is None
    again = keys.add("https://lewdcorner.com/attachments/a-png.1/")
    assert again != first and keys.get(again) == "https://lewdcorner.com/attachments/a-png.1/"


def test_hash_of_a_known_url_does_not_open_the_image(tmp_path, monkeypatch):
    from core import database, game_covers

    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    url = "https://lewdcorner.com/attachments/shot-png.1/"
    png = b"\x89PNG\r\n\x1a\nimage\x00\x00\x00\x00IEND\xaeB`\x82"
    fetched = []
    monkeypatch.setattr(game_covers, "fetch_lc_image", lambda u, *, background: fetched.append(u) or png)
    api = MagicMock()
    api.lc_image_keys = game_covers.LcImageKeys()
    api.cover_cache = game_covers.CoverCacheService()
    monkeypatch.setattr(server_module, "api_ref", api)
    token = api.lc_image_keys.add(url)

    # Another local program can compute the hash of a URL it knows.
    guessed = _handler("/lc-images/" + game_covers.lc_image_key(url))
    guessed.do_GET()
    guessed.send_error.assert_called_once_with(404)
    assert fetched == []

    handler = _handler("/lc-images/" + token)
    handler.do_GET()
    handler.send_response.assert_called_once_with(200)
    assert handler.wfile.getvalue() == png and fetched == [url]
    # The disk copy keeps its hash name, so a later session finds it.
    assert game_covers.lc_copy_file(game_covers.lc_image_key(url)) is not None
    assert game_covers.lc_copy_file(token) is None

    guessed = _handler("/lc-images/" + game_covers.lc_image_key(url))
    guessed.do_GET()
    guessed.send_error.assert_called_once_with(404)
