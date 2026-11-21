# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path

import pytest

from core.versions import compare_versions, has_update, same_version

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "version_order_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize(("a", "b", "expected"), CASES["same_version"])
def test_same_version(a, b, expected):
    assert same_version(a, b) is expected


@pytest.mark.parametrize(("a", "b", "expected"), CASES["compare_versions"])
def test_compare_versions(a, b, expected):
    assert compare_versions(a, b) == expected


def test_compare_versions_with_very_long_segments():
    assert compare_versions("1" + "0" * 5000, "9" * 5000) == 1


@pytest.mark.parametrize(
    ("installed", "latest", "skipped", "expected"),
    [
        ("1.0", "", None, False),
        (None, "1.0", None, True),
        ("0.9", "1.0", "v1.0", False),
        ("0.9", "1.0", "1.0 Final", True),
        ("1.3.0", "1.2.1", None, False),
        ("1.2.1", "1.3.0", None, True),
        ("v1.1a/UNCEN 3.1", "1.1a(UNCEN 3.1) Final", None, False),
        ("0.8k", "0.75", None, True),
    ],
)
def test_has_update(installed, latest, skipped, expected):
    assert has_update(installed, latest, skipped) is expected
