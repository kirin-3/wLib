# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for F95 rating and review count parsing."""

import pytest

from core.f95_rating import (
    extract_f95_review_count,
    parse_f95_rating_text_count,
    parse_f95_review_count,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234 reviews", 1234),
        ("1,234,567 reviews", 1234567),
        ("1 review", 1),
        ("12 votes", 12),
        ("1 vote", 1),
        ("0 reviews", 0),
        ("No reviews yet", 0),
        ("No reviews", 0),
        ("No votes yet", 0),
        ("No votes", 0),
        ("  120   REVIEWS  ", 120),
        ("42", None),
        ("1,23 reviews", None),
        ("01 reviews", None),
        ("many reviews", None),
        ("12 ratings", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_f95_review_count(text, expected):
    assert parse_f95_review_count(text) == expected


@pytest.mark.parametrize(
    ("rating_text", "expected"),
    [
        ("4.6/5 (1,234 reviews)", 1234),
        ("4.6/5 (1 review)", 1),
        ("4.2/5 (12 votes)", 12),
        ("4.3/5", None),
        ("4.5/5 (No reviews yet)", None),
        ("4.5/5 (0 reviews)", None),
        ("4.5/5 (few)", None),
        ("4.5", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_f95_rating_text_count(rating_text, expected):
    assert parse_f95_rating_text_count(rating_text) == expected


def test_extract_f95_review_count_uses_count_helper():
    banner = (
        '<div class="f95rv-banner-ring" aria-label="4.6 out of 5"></div>'
        '<div class="f95rv-banner-count">1,234 reviews</div>'
    )
    assert extract_f95_review_count(banner) == 1234

    no_reviews = '<div class="f95rv-banner-count">No reviews yet</div>'
    assert extract_f95_review_count(no_reviews) == 0

    legacy = (
        '<div class="br-widget bratr-rating">'
        '<a data-rating-value="4" class="br-selected"></a>'
        '<span class="bratr-vote-content">12 votes</span>'
        "</div>"
    )
    assert extract_f95_review_count(legacy) == 12

    unreadable = '<div class="f95rv-banner-count">not a count</div>'
    assert extract_f95_review_count(unreadable) is None
