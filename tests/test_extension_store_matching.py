# SPDX-License-Identifier: GPL-3.0-or-later
import json
import sqlite3
from contextlib import closing
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock
from urllib.parse import urlencode

import pytest

from catalog_server.store import CATALOG_SCHEMA
from conftest import PAIRED_EXTENSION_TOKEN
from core import database, extension_server


DLSITE = "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html"
VNDB = "https://vndb.org/v1"
RYUU_POST = "https://www.ryuugames.com/work/"
RYUU_SHORTLINK = "https://www.ryuugames.com/?p=42"


def _request(endpoint, page, ids):
    handler = extension_server.ExtensionRequestHandler.__new__(
        extension_server.ExtensionRequestHandler
    )
    handler.path = f"/api/{endpoint}?" + urlencode(
        [("url", page), *(("id", identity) for identity in ids)]
    )
    handler.headers = Message()
    handler.headers["Origin"] = "chrome-extension://paired"
    handler.headers["X-wLib-Extension"] = PAIRED_EXTENSION_TOKEN
    handler.__dict__["wfile"] = body = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    handler.do_GET()

    handler.send_response.assert_called_once_with(200)
    return json.loads(body.getvalue())


@pytest.mark.parametrize("endpoint", ["check", "open"])
@pytest.mark.parametrize(
    "page, own_link, ids",
    [(DLSITE, DLSITE, []), (RYUU_POST, RYUU_SHORTLINK, ["ryuugames:42"])],
)
def test_extension_prefers_page_identity(endpoint, page, own_link, ids, tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()
    with closing(sqlite3.connect(tmp_path / "catalog.db")) as catalog, catalog:
        catalog.executescript(CATALOG_SCHEMA)
        catalog.execute(
            "INSERT INTO dlsite_works (product_id, title, url, first_seen, last_seen)"
            " VALUES ('RJ123', 'Work', ?, '', '')",
            (DLSITE,),
        )
        catalog.execute(
            "INSERT INTO vndb_vns (vn_id, title, url, first_seen, last_seen)"
            " VALUES ('v1', 'Work', ?, '', '')",
            (VNDB,),
        )
        catalog.execute(
            "INSERT INTO ryuu_posts (post_id, title, raw_title, vndb_ids, url, first_seen, last_seen)"
            """ VALUES (42, 'Work', 'Work', '["v1"]', ?, '', '')""",
            (RYUU_POST,),
        )
        catalog.execute("INSERT INTO links VALUES ('vndb', 'v1', 'dlsite', 'RJ123', 'vndb')")

    related = database.add_game("Related work", "")
    exact = database.add_game("Exact work", "/game")
    assert related is not None and exact is not None and related < exact
    database.replace_game_links(related, "user", [{"url": VNDB}])
    database.replace_game_links(exact, "user", [{"url": own_link}])
    window = MagicMock()
    monkeypatch.setattr(extension_server, "window_ref", window)

    reply = _request(endpoint, page, ids)

    assert reply["gameId"] == exact
    if endpoint == "open":
        assert f"gameId: {exact}" in window.evaluate_js.call_args.args[0]
    else:
        assert reply["wishlisted"] is False
