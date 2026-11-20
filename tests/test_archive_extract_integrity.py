"""Tar symlink aliases, compressed-tar checksums and zip cancel inside a member."""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import tarfile
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

import core.archive_install as ai
from core.archive_install import (
    ArchiveInstallError,
    ArchiveSafetyError,
    InstallCancelled,
    extract_tar,
    extract_zip,
)


def _tar_bytes(members: list[tuple[str, str, bytes | str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, kind, value in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tf.addfile(info)
            elif kind == "sym":
                info.type = tarfile.SYMTYPE
                assert isinstance(value, str)
                info.linkname = value
                tf.addfile(info)
            elif kind == "hard":
                info.type = tarfile.LNKTYPE
                assert isinstance(value, str)
                info.linkname = value
                tf.addfile(info)
            else:
                assert isinstance(value, bytes)
                info.size = len(value)
                tf.addfile(info, io.BytesIO(value))
    return buf.getvalue()


def test_tar_refuses_two_members_writing_one_file_through_a_symlink(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "alias.tar"
    _ = archive.write_bytes(
        _tar_bytes(
            [
                ("d", "dir", b""),
                ("s", "sym", "d"),
                ("d/f", "file", b"first"),
                ("s/f", "file", b"second"),
            ]
        )
    )
    with pytest.raises(ArchiveSafetyError, match="colliding"):
        _ = extract_tar(str(archive), str(tmp_path / "staging"))


def test_tar_still_allows_distinct_files_through_an_internal_symlink(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "ok.tar"
    _ = archive.write_bytes(
        _tar_bytes(
            [
                ("lib64", "dir", b""),
                ("lib", "sym", "lib64"),
                ("lib64/a.so", "file", b"a"),
                ("lib/b.so", "file", b"b"),
                ("lib/c.so", "hard", "lib64/a.so"),
            ]
        )
    )
    staging = tmp_path / "staging"
    assert extract_tar(str(archive), str(staging)) == 5
    assert (staging / "lib64" / "b.so").read_bytes() == b"b"
    assert (staging / "lib64" / "c.so").read_bytes() == b"a"


def _corrupt_gzip(data: bytes) -> bytes:
    packed = bytearray(gzip.compress(data))
    packed[-8] ^= 0xFF  # CRC32 in the gzip trailer
    return bytes(packed)


def test_gzip_tar_with_bad_crc_fails(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    _ = archive.write_bytes(_corrupt_gzip(_tar_bytes([("x.txt", "file", b"hi" * 500)])))
    with pytest.raises(ArchiveInstallError, match="CRC"):
        _ = extract_tar(str(archive), str(tmp_path / "staging"))


@pytest.mark.parametrize(
    ("suffix", "compress"),
    [
        (".tar", bytes),
        (".tar.gz", gzip.compress),
        (".tar.bz2", bz2.compress),
        (".tar.xz", lzma.compress),
    ],
)
def test_valid_tars_still_extract(
    tmp_path: Path, suffix: str, compress: Callable[[bytes], bytes]
) -> None:
    archive = tmp_path / f"good{suffix}"
    raw = _tar_bytes([("dir", "dir", b""), ("dir/x.txt", "file", b"payload")])
    _ = archive.write_bytes(compress(raw))
    staging = tmp_path / "staging"
    assert extract_tar(str(archive), str(staging)) == 2
    assert (staging / "dir" / "x.txt").read_bytes() == b"payload"


def test_xz_tar_with_bad_check_fails(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.xz"
    packed = bytearray(lzma.compress(_tar_bytes([("x.txt", "file", b"hi" * 500)])))
    # The CRC64 check sits just before the 12-byte stream footer and the
    # index; flipping the whole tail region breaks the check or index.
    packed[-13] ^= 0xFF
    packed[-20] ^= 0xFF
    _ = archive.write_bytes(bytes(packed))
    with pytest.raises(ArchiveInstallError):
        _ = extract_tar(str(archive), str(tmp_path / "staging"))


def test_zip_cancel_stops_inside_one_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("big.bin", os.urandom(64 * 1024))
    monkeypatch.setattr(ai, "_COPY_CHUNK", 4096)
    cancel = threading.Event()
    writes: list[int] = []
    real_write_all = ai._write_all  # pyright: ignore[reportPrivateUsage]

    def _write_and_cancel(fd: int, data: bytes) -> None:
        real_write_all(fd, data)
        writes.append(len(data))
        cancel.set()

    monkeypatch.setattr(ai, "_write_all", _write_and_cancel)
    with pytest.raises(InstallCancelled):
        _ = extract_zip(str(archive), str(tmp_path / "staging"), cancel=cancel)
    assert len(writes) == 1
