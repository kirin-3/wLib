# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.store_links import parse_store_link

_DETAIL = "https://www.dmm.co.jp/dc/doujin/-/detail/"
_PATH_LINK = _DETAIL + "=/cid=d_123456"


@pytest.mark.parametrize(
    "query",
    ["?i3_ref=search", "?i3_ref=search&cid=d_999999"],
)
def test_fanza_path_id_survives_query(query: str):
    assert parse_store_link(_PATH_LINK + query) == {
        "source": "fanza",
        "id": "d_123456",
        "url": _PATH_LINK,
    }


def test_fanza_query_id_is_decoded_and_retained():
    assert parse_store_link(_DETAIL + "?CID=D%5F123456&i3_ref=search#top") == {
        "source": "fanza",
        "id": "d_123456",
        "url": _DETAIL + "?cid=d_123456",
    }
