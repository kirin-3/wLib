# SPDX-License-Identifier: GPL-3.0-or-later
import bz2
import gzip
import io
import lzma
import tarfile

import pytest

from core.archive_formats import (
    archive_family,
    archive_suffix,
    looks_like_archive,
    split_archive_name,
)


def _plain_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"hello"
        info = tarfile.TarInfo("Game/readme.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.parametrize(
    ("name", "suffix", "family"),
    [
        ("Game-1.0-linux.tar.bz2", ".tar.bz2", "tar"),
        ("Game-1.0-linux.TAR.BZ2", ".tar.bz2", "tar"),
        ("Game.tar.gz", ".tar.gz", "tar"),
        ("Game.tar.xz", ".tar.xz", "tar"),
        ("Game.tar", ".tar", "tar"),
        ("Game.tgz", ".tgz", "tar"),
        ("Game.TBZ", ".tbz", "tar"),
        ("Game.tbz2", ".tbz2", "tar"),
        ("Game.txz", ".txz", "tar"),
        ("Game.zip", ".zip", "zip"),
        ("Game.7Z", ".7z", "7z"),
        ("Game.rar", ".rar", "rar"),
        ("Game.bz2", "", ""),
        ("Game.gz", "", ""),
        ("Game.tar.zst", "", ""),
        ("Game.tar.bz2.crdownload", "", ""),
        ("Game", "", ""),
    ],
)
def test_archive_suffix_and_family(name: str, suffix: str, family: str) -> None:
    assert archive_suffix(name) == suffix
    assert archive_family(name) == family


def test_split_archive_name_keeps_full_suffix_and_case() -> None:
    assert split_archive_name("Game-1.0.tar.bz2") == ("Game-1.0", ".tar.bz2")
    assert split_archive_name("Game-1.0.TAR.GZ") == ("Game-1.0", ".TAR.GZ")
    assert split_archive_name("Game-1.0.zip") == ("Game-1.0", ".zip")
    assert split_archive_name("Game-1.0.bz2") == ("Game-1.0.bz2", "")


@pytest.mark.parametrize(
    ("head", "suffix"),
    [
        (b"PK\x03\x04rest", ".zip"),
        (b"7z\xbc\xaf\x27\x1crest", ".7z"),
        (b"Rar!\x1a\x07\x00", ".rar"),
        (gzip.compress(b"x"), ".tar.gz"),
        (bz2.compress(b"x"), ".tar.bz2"),
        (lzma.compress(b"x"), ".tar.xz"),
        (_plain_tar()[:512], ".tar"),
        # tarfile reads the compression from the content, so any tar
        # signature passes for any tar suffix.
        (bz2.compress(b"x"), ".TGZ"),
        (gzip.compress(b"x"), ".txz"),
    ],
)
def test_looks_like_archive_accepts_signatures(head: bytes, suffix: str) -> None:
    assert looks_like_archive(head, suffix)


@pytest.mark.parametrize(
    "suffix", [".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz"]
)
def test_looks_like_archive_rejects_html(suffix: str) -> None:
    page = b"<!DOCTYPE html><html><head><title>Download</title></head>" + b" " * 600
    assert not looks_like_archive(page[:512], suffix)


def test_looks_like_archive_rejects_wrong_family() -> None:
    assert not looks_like_archive(b"PK\x03\x04", ".tar.gz")
    assert not looks_like_archive(gzip.compress(b"x"), ".zip")
    assert not looks_like_archive(b"PK\x03\x04", ".bz2")
