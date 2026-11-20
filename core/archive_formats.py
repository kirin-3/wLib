# SPDX-License-Identifier: GPL-3.0-or-later
"""The archive formats wLib accepts: file name suffixes and file signatures.

This module has no heavy imports, so the download code can use it too.
"""

from __future__ import annotations

# Longest first, so `.tar.bz2` matches before a shorter suffix could.
ARCHIVE_SUFFIX_FAMILIES: tuple[tuple[str, str], ...] = (
    (".tar.bz2", "tar"),
    (".tar.gz", "tar"),
    (".tar.xz", "tar"),
    (".tbz2", "tar"),
    (".tbz", "tar"),
    (".tgz", "tar"),
    (".txz", "tar"),
    (".tar", "tar"),
    (".zip", "zip"),
    (".rar", "rar"),
    (".7z", "7z"),
)
ARCHIVE_SUFFIXES: tuple[str, ...] = tuple(s for s, _family in ARCHIVE_SUFFIX_FAMILIES)
TAR_SUFFIXES: tuple[str, ...] = tuple(
    s for s, family in ARCHIVE_SUFFIX_FAMILIES if family == "tar"
)
# Compressed tar suffixes, by the standard-library module that reads them.
TAR_COMPRESSION_MODULES: dict[str, str] = {
    ".tar.bz2": "bz2",
    ".tbz2": "bz2",
    ".tbz": "bz2",
    ".tar.gz": "zlib",
    ".tgz": "zlib",
    ".tar.xz": "lzma",
    ".txz": "lzma",
}

# Bytes needed from the start of a file for looks_like_archive().
SIGNATURE_BYTES = 512

_ZIP = (b"PK\x03\x04",)
_SEVEN_ZIP = (b"7z\xbc\xaf\x27\x1c",)
_RAR = (b"Rar!\x1a\x07",)
_TAR_COMPRESSED = (
    b"\x1f\x8b",  # gzip
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
)
_TAR_MAGIC_OFFSET = 257


def archive_suffix(name: str) -> str:
    """The full archive suffix of `name` in lower case (`.tar.bz2`), or ""."""
    lower = (name or "").lower()
    for suffix, _family in ARCHIVE_SUFFIX_FAMILIES:
        if lower.endswith(suffix) and len(lower) > len(suffix):
            return suffix
    return ""


def archive_family(name: str) -> str:
    """'zip' | '7z' | 'rar' | 'tar' | '' from the file name alone."""
    suffix = archive_suffix(name)
    for candidate, family in ARCHIVE_SUFFIX_FAMILIES:
        if candidate == suffix:
            return family
    return ""


def split_archive_name(name: str) -> tuple[str, str]:
    """(stem, suffix) with the suffix in its original case.

    `Game-1.0.tar.bz2` gives ("Game-1.0", ".tar.bz2"). A name without an
    archive suffix gives (name, "").
    """
    suffix = archive_suffix(name)
    if not suffix:
        return name, ""
    return name[: -len(suffix)], name[-len(suffix) :]


def looks_like_archive(head: bytes, suffix: str) -> bool:
    """True when the first bytes of a file match the format its suffix names.

    Any tar signature passes for any tar suffix, because tarfile detects the
    compression from the content.
    """
    suffix = (suffix or "").lower()
    if suffix == ".zip":
        return head.startswith(_ZIP)
    if suffix == ".7z":
        return head.startswith(_SEVEN_ZIP)
    if suffix == ".rar":
        return head.startswith(_RAR)
    if suffix in TAR_SUFFIXES:
        if head.startswith(_TAR_COMPRESSED):
            return True
        if head[_TAR_MAGIC_OFFSET : _TAR_MAGIC_OFFSET + 5] == b"ustar":
            return True
        return _tar_checksum_ok(head)
    return False


def _tar_checksum_ok(head: bytes) -> bool:
    """True when `head` starts with a tar header whose checksum is correct.

    Old (V7) tar headers have no `ustar` magic; like `tarfile`, accept them
    when the octal checksum matches the header bytes (unsigned or signed sum,
    with the checksum field counted as spaces).
    """
    if len(head) < 512 or not head[0]:
        return False
    field = head[148:156].split(b"\0", 1)[0].strip(b" ")
    if not field or any(c not in b"01234567" for c in field):
        return False
    stored = int(field, 8)
    block = head[:148] + b" " * 8 + head[156:512]
    unsigned = sum(block)
    signed = sum(c - 256 if c > 127 else c for c in block)
    return stored in (unsigned, signed)
