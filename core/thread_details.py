# SPDX-License-Identifier: GPL-3.0-or-later
"""F95 thread details a game keeps: overview, original title, aliases,
language and the developer's links.

The catalog fills them and the user may edit them. `thread_details_baseline`
holds the catalog values applied last (JSON, NULL = never filled). A field
takes a new catalog value only while it is empty or still equal to that
baseline, so the user's own edits are never overwritten. The installed
`version` is only filled while empty.
"""

import json
from collections.abc import Mapping
from typing import cast

THREAD_DETAIL_FIELDS: tuple[str, ...] = (
    "overview",
    "original_title",
    "aliases",
    "language",
    "external_links",
)
LC_FILL_FIELDS = ("title", "developer", "version", "cover_image_path", "tags", "rating", "engine", "thread_completed")
LIST_FIELDS: frozenset[str] = frozenset({"aliases", "external_links"})
# The installed version: the catalog may fill it while it is empty, but never
# replaces it, because a newer thread version does not mean newer game files.
FILL_EMPTY_ONLY_FIELDS: frozenset[str] = frozenset({"version"})
BASELINE_COLUMN = "thread_details_baseline"
_MAX_LIST_ITEMS = 50
_MAX_TEXT_CHARS = 20_000


def _decoded(value: object) -> object:
    if isinstance(value, str):
        try:
            return cast(object, json.loads(value))
        except ValueError:
            return None
    return value


def normalize_detail(field: str, value: object) -> str | list[str]:
    """The clean value of one field; lists come as Python lists or JSON text."""
    if field not in LIST_FIELDS:
        text = str(value) if field in LC_FILL_FIELDS and value is not None else (
            value if isinstance(value, str) else ""
        )
        return text.strip()[:_MAX_TEXT_CHARS]
    items = _decoded(value)
    if field == "aliases" and isinstance(value, str) and not isinstance(items, list):
        # Plain text holds one alias per line; a title such as "Hello, World"
        # may contain commas, so never split on them.
        items = value.splitlines()
    if not isinstance(items, list):
        return []
    cleaned: list[str] = []
    for item in cast(list[object], items):
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in cleaned:
            continue
        if field == "external_links" and not text.startswith(("https://", "http://")):
            continue
        cleaned.append(text)
        if len(cleaned) >= _MAX_LIST_ITEMS:
            break
    return cleaned


def column_value(field: str, value: object) -> str:
    """The value stored in the games column (lists as JSON text)."""
    clean = normalize_detail(field, value)
    return json.dumps(clean, ensure_ascii=False) if isinstance(clean, list) else clean


def normalize_baseline(value: object) -> str | None:
    """Stored baseline JSON with only known fields, or None for "never filled"."""
    decoded = _decoded(value)
    if not isinstance(decoded, dict):
        return None
    source = cast(dict[str, object], decoded)
    return json.dumps(
        {field: normalize_detail(field, source.get(field)) for field in (*THREAD_DETAIL_FIELDS, *(field for field in LC_FILL_FIELDS if field in source))},
        ensure_ascii=False,
    )


def merge_thread_details(
    current: Mapping[str, object], catalog: Mapping[str, object]
) -> dict[str, object]:
    """Column updates that apply `catalog` to a game row; {} when nothing changes."""
    raw_baseline = _decoded(current.get(BASELINE_COLUMN))
    baseline = (
        cast(dict[str, object], raw_baseline) if isinstance(raw_baseline, dict) else None
    )
    updates: dict[str, object] = {}
    new_baseline: dict[str, object] = {
        field: baseline[field] for field in LC_FILL_FIELDS if baseline is not None and field in baseline
    }
    for field in (*THREAD_DETAIL_FIELDS, *(field for field in LC_FILL_FIELDS if field in catalog)):
        if field == "tags" and current.get("tags_user_edited"):
            continue
        new = normalize_detail(field, catalog.get(field))
        new_baseline[field] = new
        stored = normalize_detail(field, current.get(field))
        untouched = (
            baseline is not None
            and field not in FILL_EMPTY_ONLY_FIELDS
            and stored == normalize_detail(field, baseline.get(field))
        )
        empty = not stored or (field == "thread_completed" and stored == "0" and not (baseline and field in baseline))
        if (empty or untouched) and stored != new:
            updates[field] = column_value(field, new)
    encoded = json.dumps(new_baseline, ensure_ascii=False)
    if updates or current.get(BASELINE_COLUMN) != encoded:
        updates[BASELINE_COLUMN] = encoded
    return updates
