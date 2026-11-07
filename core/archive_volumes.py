# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-part archive detection, shared by archive installs and torrent downloads.

It has no wLib imports, so `core.downloads.torrents` can use it without an
import cycle through `core.archive_install`.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Multi-part archive names, matched against the lowercase file name. The
# number of the first part is what install and offers start from.
RAR_PART_RE = re.compile(r"^(?P<base>.+)\.part(?P<n>\d+)\.rar$")
SPLIT_RE = re.compile(r"^(?P<base>.+\.(?:7z|zip|rar))\.(?P<n>\d{3})$")
RAR_OLD_RE = re.compile(r"^(?P<base>.+)\.r(?P<n>\d{2,3})$")
ZIP_SPAN_RE = re.compile(r"^(?P<base>.+)\.z(?P<n>\d{2,3})$")


@dataclass(frozen=True)
class VolumeSet:
    """The files of one multi-part archive.

    `first` is the file to open; `parts` lists every part found, in order;
    `missing` holds the part numbers absent from the run 1..highest (the old
    `.rar` + `.r00` style counts the `.rar` as part 1, and a spanned zip's
    `.zip` is its last part).
    """

    kind: str
    first: str
    parts: tuple[str, ...]
    missing: tuple[int, ...]

    @property
    def complete(self) -> bool:
        return not self.missing


def volume_set(archive_path: str) -> VolumeSet | None:
    """The multi-part set `archive_path` belongs to, or None for one file."""
    path = os.path.abspath(str(archive_path or ""))
    folder, name = os.path.split(path)
    lower = name.lower()
    try:
        siblings = os.listdir(folder)
    except OSError:
        siblings = [name]
    by_lower = {entry.lower(): entry for entry in siblings}

    def numbered(pattern: re.Pattern[str], base: str) -> dict[int, str]:
        found: dict[int, str] = {}
        for entry_lower, entry in by_lower.items():
            match = pattern.match(entry_lower)
            if match and match.group("base") == base:
                found[int(match.group("n"))] = os.path.join(folder, entry)
        return found

    def build(kind: str, parts: dict[int, str], first_number: int) -> VolumeSet | None:
        if len(parts) < 2 and first_number in parts:
            return None
        highest = max(parts) if parts else first_number
        missing = tuple(n for n in range(first_number, highest + 1) if n not in parts)
        first = parts.get(first_number, "")
        ordered = tuple(parts[n] for n in sorted(parts))
        return VolumeSet(kind, first, ordered, missing)

    match = RAR_PART_RE.match(lower)
    if match:
        parts = numbered(RAR_PART_RE, match.group("base"))
        return build("rar_parts", parts, 1) if len(parts) > 1 or 1 not in parts else None
    match = SPLIT_RE.match(lower)
    if match:
        return build("split", numbered(SPLIT_RE, match.group("base")), 1)
    rar_old = RAR_OLD_RE.match(lower)
    if lower.endswith(".rar") or rar_old:
        base = rar_old.group("base") if rar_old else lower[: -len(".rar")]
        parts = {n + 2: p for n, p in numbered(RAR_OLD_RE, base).items()}
        if not parts:
            return None
        if base + ".rar" in by_lower:
            parts[1] = os.path.join(folder, by_lower[base + ".rar"])
        return build("rar_old", parts, 1)
    zip_span = ZIP_SPAN_RE.match(lower)
    if lower.endswith(".zip") or zip_span:
        base = zip_span.group("base") if zip_span else lower[: -len(".zip")]
        parts = numbered(ZIP_SPAN_RE, base)
        if not parts:
            return None
        # .z01 .. .zNN, then the .zip as the last part; 7-Zip opens the .zip.
        last = max(parts) + 1
        zip_name = by_lower.get(base + ".zip")
        if zip_name:
            parts[last] = os.path.join(folder, zip_name)
        span = build("zip_span", parts, 1)
        if span is None:
            return None
        first = parts.get(last, "")
        missing = span.missing + (() if zip_name else (last,))
        return VolumeSet("zip_span", first, span.parts, missing)
    return None
