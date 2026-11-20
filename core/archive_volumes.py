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
    `.zip` is its last part). `ambiguous` names sibling files that differ only
    in letter case and could not be told apart; they are never in `parts`.
    """

    kind: str
    first: str
    parts: tuple[str, ...]
    missing: tuple[int, ...]
    ambiguous: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing and not self.ambiguous


def volume_set(archive_path: str) -> VolumeSet | None:
    """The multi-part set `archive_path` belongs to, or None for one file.

    Part names match without regard to letter case. When the folder holds
    names that differ only in case (possible on a case-sensitive filesystem),
    only files with the chosen file's exact base name count, and the chosen
    file itself always stands for its own part number. A part that still has
    more than one candidate goes to `ambiguous`, so nothing installs or
    deletes a file from another set.
    """
    path = os.path.abspath(str(archive_path or ""))
    folder, name = os.path.split(path)
    lower = name.lower()
    try:
        siblings = os.listdir(folder)
    except OSError:
        siblings = [name]
    by_lower: dict[str, list[str]] = {}
    for entry in sorted(siblings):
        by_lower.setdefault(entry.lower(), []).append(entry)
    ambiguous: list[str] = []

    def numbered(pattern: re.Pattern[str], base: str) -> dict[int, str]:
        """Part number -> lowercase sibling name, for every part of `base`."""
        found: dict[int, str] = {}
        for entry_lower in by_lower:
            match = pattern.match(entry_lower)
            if match and match.group("base") == base:
                found[int(match.group("n"))] = entry_lower
        return found

    def resolve(keys: dict[int, str], base: str) -> dict[int, str]:
        """Part number -> real path, keeping only files of the chosen set."""

        def exact_base(entry: str) -> str:
            # The part suffix is ASCII, so it has the same length in both cases.
            return entry[: len(entry) - (len(entry.lower()) - len(base))]

        groups = {n: by_lower.get(key, []) for n, key in keys.items()}
        if any(len(entries) > 1 for entries in groups.values()):
            own = exact_base(name)
            groups = {n: [e for e in entries if exact_base(e) == own] for n, entries in groups.items()}
        resolved: dict[int, str] = {}
        for n, entries in groups.items():
            if name in entries:
                entries = [name]
            if len(entries) == 1:
                resolved[n] = os.path.join(folder, entries[0])
            elif entries:
                ambiguous.extend(os.path.join(folder, e) for e in entries)
        return resolved

    def build(kind: str, parts: dict[int, str], first_number: int) -> VolumeSet | None:
        if len(parts) < 2 and first_number in parts and not ambiguous:
            return None
        highest = max(parts) if parts else first_number
        missing = tuple(n for n in range(first_number, highest + 1) if n not in parts)
        first = parts.get(first_number, "")
        ordered = tuple(parts[n] for n in sorted(parts))
        return VolumeSet(kind, first, ordered, missing, tuple(sorted(ambiguous)))

    match = RAR_PART_RE.match(lower)
    if match:
        base = match.group("base")
        return build("rar_parts", resolve(numbered(RAR_PART_RE, base), base), 1)
    match = SPLIT_RE.match(lower)
    if match:
        base = match.group("base")
        return build("split", resolve(numbered(SPLIT_RE, base), base), 1)
    rar_old = RAR_OLD_RE.match(lower)
    if lower.endswith(".rar") or rar_old:
        base = rar_old.group("base") if rar_old else lower[: -len(".rar")]
        keys = {n + 2: key for n, key in numbered(RAR_OLD_RE, base).items()}
        if not keys:
            return None
        if base + ".rar" in by_lower:
            keys[1] = base + ".rar"
        return build("rar_old", resolve(keys, base), 1)
    zip_span = ZIP_SPAN_RE.match(lower)
    if lower.endswith(".zip") or zip_span:
        base = zip_span.group("base") if zip_span else lower[: -len(".zip")]
        keys = numbered(ZIP_SPAN_RE, base)
        if not keys:
            return None
        # .z01 .. .zNN, then the .zip as the last part; 7-Zip opens the .zip.
        last = max(keys) + 1
        if base + ".zip" in by_lower:
            keys[last] = base + ".zip"
        parts = resolve(keys, base)
        span = build("zip_span", parts, 1)
        if span is None:
            return None
        first = parts.get(last, "")
        missing = span.missing + (() if last in parts else (last,))
        return VolumeSet("zip_span", first, span.parts, missing, span.ambiguous)
    return None
