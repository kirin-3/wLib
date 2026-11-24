# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared F95 thread-page rating parser for the scraper and catalog publisher."""

import re
from collections.abc import Iterator
from html import unescape


def attribute(opening: str, name: str) -> str:
    match = re.search(
        rf"(?<![\w:-]){re.escape(name)}\s*=\s*([\"'])(.*?)\1", opening, re.S | re.I
    )
    return unescape(match.group(2)) if match else ""


def text(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", html)).strip()


def _class_openings(html: str, class_name: str) -> Iterator[re.Match[str]]:
    """Opening tags of any element whose class list has `class_name`."""
    for match in re.finditer(r"<([a-zA-Z][\w-]*)\b[^>]*>", html):
        if class_name in attribute(match.group(), "class").split():
            yield match


def _class_block(html: str, class_name: str) -> str:
    """Inner HTML of the first element of any tag that has `class_name`."""
    for match in _class_openings(html, class_name):
        block = next(blocks(html[match.start() :], match.group(1), class_name), None)
        if block is not None:
            return block[1]
    return ""


def _first_number(text: str) -> float:
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group()) if match else 0.0


def _rating_and_count_text(html: str) -> tuple[float, str]:
    value = 0.0
    for match in _class_openings(html, "f95rv-banner-ring"):
        value = _first_number(attribute(match.group(), "aria-label"))
        if value > 0:
            break
    if value <= 0:
        stars = _class_block(html, "f95rv-banner-stars")
        for match in _class_openings(stars, "f95rv-stars"):
            value = _first_number(attribute(match.group(), "aria-label"))
            if value > 0:
                break
    banner_count = re.sub(r"\s+", " ", text(_class_block(html, "f95rv-banner-count")))
    if value > 0:
        return value, banner_count

    widget = ""
    for match in _class_openings(html, "bratr-rating"):
        if "br-widget" in attribute(match.group(), "class").split():
            block = next(
                blocks(html[match.start() :], match.group(1), "bratr-rating"), None
            )
            widget = block[1] if block else ""
            break
    if not widget:
        return 0.0, banner_count
    for star in re.finditer(r"<a\b[^>]*>", widget, re.I):
        opening = star.group()
        raw_value = attribute(opening, "data-rating-value")
        if not raw_value.isdigit():
            continue
        classes = attribute(opening, "class").split()
        if "br-selected" in classes or "br-current" in classes:
            value = float(raw_value)
        fractional = next((c for c in classes if c.startswith("br-fractional-")), "")
        percent = fractional.removeprefix("br-fractional-")
        if percent.isdigit():
            value = int(raw_value) - 1 + int(percent) / 100
    count = re.sub(r"\s+", " ", text(_class_block(widget, "bratr-vote-content")))
    return value, count if value > 0 else banner_count or count


def blocks(html: str, tag: str, class_name: str = "") -> Iterator[tuple[str, str]]:
    # A spoiler/div inside bbWrapper must not truncate the starter post.
    tokens = re.compile(rf"</?{tag}\b[^>]*>", re.I)
    for opening in re.finditer(rf"<{tag}\b[^>]*>", html, re.I):
        if class_name and class_name not in attribute(opening.group(), "class").split():
            continue
        depth = 1
        for token in tokens.finditer(html, opening.end()):
            depth += -1 if token.group().startswith("</") else 1
            if depth == 0:
                yield opening.group(), html[opening.end() : token.start()]
                break


def extract_f95_rating(html: str) -> tuple[float, str]:
    """Return the banner or legacy widget rating and its display text."""
    rating, count = _rating_and_count_text(html)
    return (rating, count) if rating > 0 else (0.0, "")


def parse_f95_review_count(count_text: object) -> int | None:
    """Parse count text like '1,234 reviews', '12 votes', or 'No reviews yet'."""
    if not isinstance(count_text, str):
        return None
    cleaned = re.sub(r"\s+", " ", count_text).strip()
    if not cleaned:
        return None
    if re.fullmatch(r"no (?:reviews|votes)(?: yet)?", cleaned, re.I):
        return 0
    match = re.fullmatch(
        r"(0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+) (?:reviews?|votes?)",
        cleaned,
        re.I,
    )
    return int(match[1].replace(",", "")) if match else None


def parse_f95_rating_text_count(rating_text: object) -> int | None:
    """Extract a positive review count from stored rating text like '4.6/5 (1,234 reviews)'."""
    if not isinstance(rating_text, str):
        return None
    match = re.fullmatch(r"\d+(?:\.\d+)?/5\s*\(([^()]+)\)", rating_text.strip())
    if not match:
        return None
    count = parse_f95_review_count(match.group(1))
    return count if count is not None and count > 0 else None


def extract_f95_review_count(html: str) -> int | None:
    """Return a verified count, or None when the page supplies no readable count."""
    _, count = _rating_and_count_text(html)
    return parse_f95_review_count(count)
