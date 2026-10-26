# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import re

import pytest

from catalog_fakes import FIXTURES

NAMES = sorted(name for name in os.listdir(FIXTURES) if name.endswith(".json"))


def test_expected_fixtures_exist():
    assert set(NAMES) >= {
        "f95_latest_page.json",
        "f95_rate_limited.json",
        "dlsite_listing.json",
        "dlsite_product_info.json",
        "ryuugames_posts.json",
        "vndb_vn.json",
        "vndb_release.json",
    }


@pytest.mark.parametrize("name", NAMES)
def test_fixture_parses_and_holds_no_credentials(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        text = handle.read()
    json.loads(text)
    assert not re.search(r"set-cookie|\bcookie\b|xf_csrf|_xfToken|\btoken\b|authorization", text, re.I)
