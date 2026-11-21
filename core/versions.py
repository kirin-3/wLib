# SPDX-License-Identifier: GPL-3.0-or-later
import re
from itertools import zip_longest

_VERSION_PUNCTUATION = str.maketrans("", "", "/()[]-_,:")
_ORDERABLE_VERSION = re.compile(r"([0-9]+(?:\.[0-9]+)*)[a-z]*")


def normalize_version(version: object) -> str:
    """Mirror normalizeVersion() in the UI while preserving raw stored versions."""
    value = "".join(str(version or "").split()).lower()
    return re.sub(r"^v(?=\d)", "", value)


def _version_spelling(version: object) -> str:
    value = normalize_version(version).translate(_VERSION_PUNCTUATION)
    return value.removesuffix("final") if value != "final" else value


def same_version(a: object, b: object) -> bool:
    """Match alternate spellings for update decisions."""
    return _version_spelling(a) == _version_spelling(b)


def compare_versions(a: object, b: object) -> int | None:
    """Return 0 for a spelling tie, +/-1 for an order, or None for ambiguity."""
    if same_version(a, b):
        return 0

    left = _ORDERABLE_VERSION.fullmatch(normalize_version(a))
    right = _ORDERABLE_VERSION.fullmatch(normalize_version(b))
    if left is None or right is None:
        return None

    segments = zip_longest(left[1].split("."), right[1].split("."), fillvalue="0")
    for index, (a_segment, b_segment) in enumerate(segments):
        if a_segment == b_segment:
            continue

        try:
            a_number, b_number = int(a_segment), int(b_segment)
            whole = (a_number > b_number) - (a_number < b_number)
        except ValueError:
            # Python caps decimal-to-int conversion. Compare longer digits by magnitude.
            a_digits, b_digits = a_segment.lstrip("0"), b_segment.lstrip("0")
            a_magnitude, b_magnitude = (
                (len(a_digits), a_digits),
                (len(b_digits), b_digits),
            )
            whole = (a_magnitude > b_magnitude) - (a_magnitude < b_magnitude)

        if not whole:
            return None
        if index == 0:
            return whole

        width = max(len(a_segment), len(b_segment))
        a_fraction = a_segment.ljust(width, "0")
        b_fraction = b_segment.ljust(width, "0")
        fraction = (a_fraction > b_fraction) - (a_fraction < b_fraction)
        return whole if whole == fraction else None

    return None


def has_update(installed: object, latest: object, skipped: object = None) -> bool:
    normalized_latest = normalize_version(latest)
    if not normalized_latest or normalized_latest == normalize_version(skipped):
        return False
    if not normalize_version(installed):
        return True
    return compare_versions(latest, installed) in (1, None)


def actionable_remote_version(version: object) -> bool:
    return version is not None and str(version).strip().lower() not in (
        "",
        "unknown",
        "n/a",
        "na",
        "none",
        "null",
    )
