# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn F95's tag and prefix lists into catalog_server/data/f95_tags.json.

The lists sit in the `latestUpdates` object of the logged-in
https://f95zone.to/sam/latest_alpha/ page. Save that page (or just the
object) from your own browser and run:

    python -m catalog_server.tools.import_f95_tags saved-page.html

The builder never logs in to F95; this file is refreshed by hand.
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from typing import cast

from ..sources.f95 import TAGS_FILE


def _extract_object(text: str, key: str) -> object:
    """Return the JSON value following `"key":` (or `key:`) in text."""
    match = re.search(rf'["\']?{key}["\']?\s*:\s*', text)
    if match is None:
        raise ValueError(f"no {key!r} list found")
    decoded = cast(tuple[object, int], json.JSONDecoder().raw_decode(text, match.end()))
    return decoded[0]


def _prefixes(value: object, category: str | None = None) -> dict[str, dict[str, str | None]]:
    """Walk the nested prefix groups; leaves have an id and a name."""
    found: dict[str, dict[str, str | None]] = {}
    if isinstance(value, list):
        for item in cast(list[object], value):
            found.update(_prefixes(item, category))
    elif isinstance(value, dict):
        entry = cast(dict[str, object], value)
        children = entry.get("prefixes")
        name = entry.get("name")
        if isinstance(children, (list, dict)):
            found.update(
                _prefixes(
                    cast(object, children), name if isinstance(name, str) else category
                )
            )
        elif isinstance(entry.get("id"), int) and isinstance(name, str):
            found[str(entry["id"])] = {"name": name, "category": category}
        else:
            for child in entry.values():
                found.update(_prefixes(child, category))
    return found


def convert(text: str) -> dict[str, object]:
    raw_tags = _extract_object(text, "tags")
    tags: dict[str, str] = {}
    if isinstance(raw_tags, dict):
        for key, name in cast(dict[str, object], raw_tags).items():
            if str(key).isdigit() and isinstance(name, str):
                tags[str(key)] = name
    elif isinstance(raw_tags, list):
        for item in cast(list[object], raw_tags):
            if isinstance(item, dict):
                entry = cast(dict[str, object], item)
                if isinstance(entry.get("id"), int) and isinstance(entry.get("name"), str):
                    tags[str(entry["id"])] = str(entry["name"])
    prefixes = _prefixes(_extract_object(text, "prefixes"))
    if not tags:
        raise ValueError("the tag list is empty")
    return {
        "tags": dict(sorted(tags.items(), key=lambda item: int(item[0]))),
        "prefixes": dict(sorted(prefixes.items(), key=lambda item: int(item[0]))),
    }


class Args(argparse.Namespace):
    source: str = ""
    output: str = TAGS_FILE


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    _ = parser.add_argument("source", help="saved latest_alpha page or its latestUpdates JSON")
    _ = parser.add_argument("--output", default=TAGS_FILE)
    args = parser.parse_args(argv, namespace=Args())
    with open(args.source, encoding="utf-8") as handle:
        result = convert(handle.read())
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
        _ = handle.write("\n")
    tags = cast(dict[str, str], result["tags"])
    prefixes = cast(dict[str, object], result["prefixes"])
    print(f"wrote {len(tags)} tags and {len(prefixes)} prefixes to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
