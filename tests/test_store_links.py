# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from core.store_links import coerce_store_links, parse_store_link


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parent / "fixtures/store_links.json").read_text()),
)
def test_store_link_url_rules(case):
    link = parse_store_link(case["url"])
    if case["source"] is None:
        assert link is None
    else:
        assert link is not None
        assert (link["source"], link["id"]) == (case["source"], case["id"])
        assert not urlsplit(link["url"]).fragment
        assert parse_store_link(link["url"]) == link


def test_coerce_links_normalizes_ids_and_collapses_duplicates():
    url = "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html"
    assert coerce_store_links(
        [
            {"source": "steam", "id": "999", "url": url},
            {"url": url + "?locale=en_US"},
            {"url": "javascript:alert(1)"},
            None,
        ]
    ) == [{"source": "dlsite", "id": "RJ123", "url": url}]
    assert coerce_store_links(None) == []
