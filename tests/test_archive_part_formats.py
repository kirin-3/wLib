"""Archive format checks for multi-part sets, old tar headers and long download names."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from core.api import Api
from core.archive_formats import looks_like_archive
from core.archive_install import UNSUPPORTED_FORMAT_MESSAGE, archive_format
from core.downloads.transfer import _safe_name  # pyright: ignore[reportPrivateUsage]


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Game.exe", b"x")
    return buffer.getvalue()


def test_spanned_zip_with_upper_case_parts_needs_7zip(tmp_path: Path) -> None:
    (tmp_path / "Game.ZIP").write_bytes(_zip_bytes())
    (tmp_path / "Game.Z01").write_bytes(b"part")
    assert archive_format(str(tmp_path / "Game.ZIP")) == "7z"
    assert archive_format(str(tmp_path / "Game.Z01")) == "7z"


def test_old_rar_continuation_part_reports_rar(tmp_path: Path) -> None:
    (tmp_path / "Game.rar").write_bytes(b"Rar!\x1a\x07\x00")
    (tmp_path / "Game.r00").write_bytes(b"part")
    assert archive_format(str(tmp_path / "Game.r00")) == "rar"
    assert archive_format(str(tmp_path / "Game.rar")) == "rar"


def test_single_zip_and_lone_part_names_are_unchanged(tmp_path: Path) -> None:
    (tmp_path / "Solo.zip").write_bytes(_zip_bytes())
    assert archive_format(str(tmp_path / "Solo.zip")) == "zip"
    assert archive_format(str(tmp_path / "notes.r00x")) == ""


def test_preview_accepts_any_part_of_a_set(tmp_path: Path) -> None:
    (tmp_path / "Game.rar").write_bytes(b"Rar!\x1a\x07\x00")
    (tmp_path / "Game.r00").write_bytes(b"part")
    (tmp_path / "Span.zip").write_bytes(_zip_bytes())
    (tmp_path / "Span.z01").write_bytes(b"part")
    api = Api()
    for chosen in ("Game.r00", "Span.z01"):
        result = api.inspect_archive(str(tmp_path / chosen))
        assert result.get("error") != UNSUPPORTED_FORMAT_MESSAGE, chosen
        assert result["success"] is True, (chosen, result)


def _v7_tar() -> bytes:
    info = tarfile.TarInfo("Game.exe")
    info.size = 1
    header = bytearray(info.tobuf(format=tarfile.USTAR_FORMAT))
    header[257:265] = b"\0" * 8  # V7 headers have no magic or version.
    header[148:156] = b" " * 8
    header[148:156] = b"%06o\0 " % sum(header[:512])
    return bytes(header[:512]) + b"x".ljust(512, b"\0") + b"\0" * 1024


def test_old_tar_header_passes_download_check() -> None:
    data = _v7_tar()
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        assert tf.getnames() == ["Game.exe"]
    assert looks_like_archive(data[:512], ".tar")


def test_tar_check_rejects_bad_checksum_and_empty_blocks() -> None:
    data = bytearray(_v7_tar()[:512])
    data[0] ^= 1
    assert not looks_like_archive(bytes(data), ".tar")
    assert not looks_like_archive(b"\0" * 512, ".tar")
    assert not looks_like_archive(b"<!DOCTYPE html>".ljust(512, b" "), ".tar")


def test_long_download_name_keeps_archive_suffix() -> None:
    name = _safe_name("G" * 200 + ".zip")
    assert name.endswith(".zip")
    assert len(name.encode()) <= 200
    tar_name = _safe_name("G" * 300 + ".tar.bz2")
    assert tar_name.endswith(".tar.bz2")


def test_long_multibyte_download_name_fits_the_filename_limit() -> None:
    name = _safe_name("ゲーム" * 100 + ".7z")
    assert name.endswith(".7z")
    assert len(name.encode()) <= 200
    assert name[:-3] == name[:-3].encode().decode()
