# SPDX-License-Identifier: GPL-3.0-or-later
"""Merge a thread's current F95 tags with the user's own tag changes.

Each game keeps its last F95 tag list (`games.f95_tags`) as a baseline. A
current tag that is not in the baseline was added by the user; a baseline
tag that is not current was removed by the user. Tags compare without case
and outer spaces.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence


def _key(tag: str) -> str:
    return tag.strip().lower()


def _clean(tags: Iterable[str]) -> list[str]:
    """Strip tags, drop empty ones and keep the first of each key."""
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = tag.strip()
        key = _key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def split_tags(value: object) -> list[str] | None:
    """A stored comma list as tags; None stays None ("no baseline yet")."""
    if value is None:
        return None
    return _clean(str(value).split(","))


def merge_f95_tags(
    current: Sequence[str],
    baseline: Sequence[str] | None,
    f95: Sequence[str],
    user_edited: bool,
) -> tuple[list[str], list[str] | None]:
    """Return the game's new tags and its new last F95 tag list.

    The thread's F95 tags come first in the thread's order, minus the ones
    the user removed, then the user-added tags in their current order. An
    empty F95 list changes nothing. With no baseline yet, unedited tags count
    as the baseline; edited tags stay as they are and only the baseline is
    recorded, so a tag the user removed never comes back.
    """
    current_tags = _clean(current)
    f95_tags = _clean(f95)
    if not f95_tags:
        return current_tags, None if baseline is None else _clean(baseline)
    if baseline is None:
        if user_edited:
            return current_tags, f95_tags
        baseline_tags = current_tags
    else:
        baseline_tags = _clean(baseline)

    baseline_keys = {_key(tag) for tag in baseline_tags}
    current_keys = {_key(tag) for tag in current_tags}
    removed = baseline_keys - current_keys
    added = current_keys - baseline_keys
    merged = [tag for tag in f95_tags if _key(tag) not in removed] + [
        tag for tag in current_tags if _key(tag) in added
    ]
    return _clean(merged), f95_tags
