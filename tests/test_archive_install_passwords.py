# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for password-protected archive installs (zip, 7z, unrar, bsdtar, tar, API)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from core import archive_install as ai
from core import database
from core.api import Api


def _has_7z() -> bool:
    return shutil.which("7z") is not None


def _make_sample_game_tree(src_dir: Path, payload_size: int = 128) -> Path:
    game_dir = src_dir / "DemoGame"
    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / "Game.exe").write_bytes(b"MZ" + b"A" * payload_size)
    (game_dir / "readme.txt").write_text("hello world\n", encoding="utf-8")
    return game_dir


def _run_7z(args: list[str], cwd: Path) -> None:
    completed = subprocess.run(
        ["7z", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _setup_library(tmp_path: Path) -> Path:
    database.init_db()
    lib = tmp_path / "library"
    lib.mkdir()
    database.update_setting("library_folder", str(lib))
    database.update_setting("library_folders", f'["{lib}"]')
    return lib


# --- Task 5.4: exception classification & sanitization ---


def test_tool_failure_classifies_password_errors_and_strips_password():
    no_pw = subprocess.CompletedProcess(
        ["7z"], 2, "", "ERROR: Can not open encrypted archive. Wrong password?"
    )
    err_req = ai._tool_failure(no_pw, "Extraction failed", password="")
    assert isinstance(err_req, ai.ArchivePasswordRequired)
    assert err_req.error_code == "archive_password_required"

    wrong_pw = subprocess.CompletedProcess(
        ["7z"], 2, "", "ERROR: Data Error in encrypted file. Wrong password? MySecret99"
    )
    err_wrong = ai._tool_failure(wrong_pw, "Extraction failed", password="MySecret99")
    assert isinstance(err_wrong, ai.ArchivePasswordWrong)
    assert err_wrong.error_code == "archive_password_wrong"
    assert "MySecret99" not in str(err_wrong)

    echoed_non_pw = subprocess.CompletedProcess(
        ["7z"], 2, "", "7z: fatal error near -pMySecret99: disk I/O"
    )
    err_other = ai._tool_failure(
        echoed_non_pw, "Extraction failed", password="MySecret99"
    )
    assert type(err_other) is ai.ArchiveInstallError
    assert "MySecret99" not in str(err_other)


# --- Task 5.5: ZipCrypto and AES256 zips ---


@pytest.mark.skipif(not _has_7z(), reason="7z not installed")
@pytest.mark.parametrize("method", ["ZipCrypto", "AES256"])
def test_password_protected_zip_installs_and_reports_password_errors(tmp_path, method):
    lib = _setup_library(tmp_path)
    src = tmp_path / "src"
    _make_sample_game_tree(src)
    archive = tmp_path / f"DemoGame-{method}.zip"
    password = f"Pass-{method}-42"
    _run_7z(
        ["a", "-tzip", f"-p{password}", f"-mem={method}", str(archive), "DemoGame"], src
    )

    # Empty password -> ArchivePasswordRequired, library unchanged.
    with pytest.raises(ai.ArchivePasswordRequired) as exc_req:
        ai.install_archive(
            str(archive),
            library_folder=str(lib),
            pattern="{title}",
            title="DemoGame",
            password="",
        )
    assert exc_req.value.error_code == "archive_password_required"
    assert database.get_all_games() == []
    assert list(lib.iterdir()) == []

    # Wrong password -> ArchivePasswordWrong, password not in message, library unchanged.
    wrong = "WrongPass-999"
    with pytest.raises(ai.ArchivePasswordWrong) as exc_wrong:
        ai.install_archive(
            str(archive),
            library_folder=str(lib),
            pattern="{title}",
            title="DemoGame",
            password=wrong,
        )
    assert exc_wrong.value.error_code == "archive_password_wrong"
    assert wrong not in str(exc_wrong.value)
    assert database.get_all_games() == []
    assert list(lib.iterdir()) == []

    # Correct password -> succeeds.
    res = ai.install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}",
        title="DemoGame",
        password=password,
    )
    assert Path(str(res["exe_path"])).is_file()
    assert len(database.get_all_games()) == 1


@pytest.mark.skipif(not _has_7z(), reason="7z not installed")
def test_extract_zip_parallel_workers_pass_password(tmp_path):
    src = tmp_path / "src"
    game_dir = _make_sample_game_tree(src)
    for i in range(6):
        (game_dir / f"file_{i}.txt").write_text(f"data {i}\n", encoding="utf-8")
    archive = tmp_path / "multi.zip"
    _run_7z(
        ["a", "-tzip", "-pMultiPw", "-mem=ZipCrypto", str(archive), "DemoGame"], src
    )

    staging = tmp_path / "staging"
    count = ai.extract_zip(str(archive), str(staging), workers=4, password="MultiPw")
    assert count >= 8
    assert (staging / "DemoGame" / "file_5.txt").read_text(
        encoding="utf-8"
    ) == "data 5\n"


# --- Task 5.6: 7z (-mhe=on), split encrypted 7z, unrar argv, bsdtar, tar ---


@pytest.mark.skipif(not _has_7z(), reason="7z not installed")
def test_7z_encrypted_headers_listing_and_install(tmp_path):
    lib = _setup_library(tmp_path)
    src = tmp_path / "src"
    _make_sample_game_tree(src)
    archive = tmp_path / "EncryptedHeaders.7z"
    password = "HeaderSecret77"
    _run_7z(["a", f"-p{password}", "-mhe=on", str(archive), "DemoGame"], src)

    tool = shutil.which("7z")
    assert tool is not None

    # Listing with no password or wrong password fails with the right class.
    with pytest.raises(ai.ArchivePasswordRequired):
        ai.list_tool_entries(tool, str(archive))
    with pytest.raises(ai.ArchivePasswordWrong) as wrong_exc:
        ai.list_tool_entries(tool, str(archive), password="NotThePassword")
    assert "NotThePassword" not in str(wrong_exc.value)

    entries = ai.list_tool_entries(tool, str(archive), password=password)
    assert any(e.endswith("Game.exe") for e in entries)

    # Full install with right password succeeds.
    res = ai.install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}",
        title="DemoGame",
        password=password,
    )
    assert Path(str(res["exe_path"])).is_file()


@pytest.mark.skipif(not _has_7z(), reason="7z not installed")
def test_split_encrypted_7z_installs_with_password(tmp_path):
    lib = _setup_library(tmp_path)
    src = tmp_path / "src"
    # Incompressible bytes ensure multiple split volumes.
    import os

    game_dir = _make_sample_game_tree(src, payload_size=32)
    (game_dir / "blob.bin").write_bytes(os.urandom(32 * 1024))
    base_archive = tmp_path / "SplitGame.7z"
    password = "SplitSecret88"
    _run_7z(
        ["a", f"-p{password}", "-mhe=on", "-v10k", str(base_archive), "DemoGame"], src
    )
    first_part = tmp_path / "SplitGame.7z.001"
    assert first_part.is_file()
    assert (tmp_path / "SplitGame.7z.002").is_file()

    with pytest.raises(ai.ArchivePasswordWrong):
        ai.install_archive(
            str(first_part),
            library_folder=str(lib),
            pattern="{title}",
            title="SplitGame",
            password="WrongSplitPassword",
        )
    assert list(lib.iterdir()) == []

    res = ai.install_archive(
        str(first_part),
        library_folder=str(lib),
        pattern="{title}",
        title="SplitGame",
        password=password,
    )
    assert Path(str(res["exe_path"])).is_file()


def test_unrar_argv_passes_password_or_no_prompt_flag(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(tool, args, env):
        calls.append(list(args))
        if args[0] == "lb":
            return subprocess.CompletedProcess([tool, *args], 0, "Game.exe\n", "")
        if args[0] == "lt":
            return subprocess.CompletedProcess([tool, *args], 0, "", "")
        return subprocess.CompletedProcess([tool, *args], 0, "", "")

    monkeypatch.setattr(ai, "_run_tool", fake_run)
    staging = tmp_path / "stage"

    ai.extract_with_tool(
        "/usr/bin/unrar", "/tmp/game.rar", str(staging), password="secret"
    )
    assert calls[0][:2] == ["lb", "-psecret"]
    assert calls[1][:2] == ["lt", "-psecret"]
    assert calls[2][:3] == ["x", "-o+", "-psecret"]

    calls.clear()
    ai.extract_with_tool("/usr/bin/unrar", "/tmp/game.rar", str(staging))
    assert calls[0][:2] == ["lb", "-p-"]
    assert calls[1][:2] == ["lt", "-p-"]
    assert calls[2][:3] == ["x", "-o+", "-p-"]


def test_bsdtar_uses_passphrase_for_zip_and_refuses_7z_and_rar_with_password(
    monkeypatch, tmp_path
):
    calls: list[list[str]] = []

    def fake_run(tool, args, env):
        calls.append(list(args))
        if "-tf" in args:
            return subprocess.CompletedProcess([tool, *args], 0, "Game.exe\n", "")
        return subprocess.CompletedProcess([tool, *args], 0, "", "")

    monkeypatch.setattr(ai, "_run_tool", fake_run)
    ai.extract_with_tool(
        "/usr/bin/bsdtar", "/tmp/game.zip", str(tmp_path / "zip_stage"), password="zpw"
    )
    assert calls[0][:3] == ["--passphrase", "zpw", "-tf"]
    assert calls[1][:3] == ["--passphrase", "zpw", "-xf"]

    # Direct extract/list calls refuse 7z and rar when bsdtar is given a password.
    for path in ("/tmp/game.7z", "/tmp/game.rar"):
        with pytest.raises(ai.ArchiveInstallError, match="7-Zip"):
            ai.list_tool_entries("/usr/bin/bsdtar", path, password="pw")
        with pytest.raises(ai.ArchiveInstallError, match="7-Zip"):
            ai.extract_with_tool(
                "/usr/bin/bsdtar", path, str(tmp_path / "s"), password="pw"
            )

    # detect_archive_tool also refuses when bsdtar is the only tool and password is set.
    monkeypatch.setattr(ai, "is_flatpak", lambda: False)
    monkeypatch.setattr(ai, "_is_windows", lambda _p=None: False)
    monkeypatch.setattr(
        ai, "host_which", lambda name: "/usr/bin/bsdtar" if name == "bsdtar" else None
    )
    tool, err = ai.detect_archive_tool("/tmp/game.7z", password="pw")
    assert tool is None and err is not None and "7-Zip" in err
    tool_rar, err_rar = ai.detect_archive_tool("/tmp/game.rar", password="pw")
    assert tool_rar is None and err_rar is not None and "7-Zip" in err_rar


def test_tar_archive_ignores_password(tmp_path):
    lib = _setup_library(tmp_path)
    src = tmp_path / "src"
    game_dir = _make_sample_game_tree(src)
    archive = tmp_path / "DemoGame.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(game_dir, arcname="DemoGame")

    res = ai.install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}",
        title="DemoGame",
        password="ignored-tar-password",
    )
    assert Path(str(res["exe_path"])).is_file()


# --- Task 5.3: API install_archive passes password without logging or exposing it ---


@pytest.mark.skipif(not _has_7z(), reason="7z not installed")
def test_api_install_archive_passes_password_without_logging_or_status_exposure(
    tmp_path, caplog
):
    lib = _setup_library(tmp_path)
    src = tmp_path / "src"
    _make_sample_game_tree(src)
    archive = tmp_path / "ApiDemo.zip"
    secret = "ApiSecretPassword123"
    _run_7z(
        ["a", "-tzip", f"-p{secret}", "-mem=ZipCrypto", str(archive), "DemoGame"], src
    )

    api = Api()
    try:
        # Wrong password returns archive_password_wrong without leaking password.
        wrong = "WrongApiPassword456"
        with caplog.at_level(logging.DEBUG):
            bad_res = api.install_archive(
                {
                    "archive_path": str(archive),
                    "library_folder": str(lib),
                    "title": "ApiDemo",
                    "archive_password": wrong,
                }
            )
        assert bad_res["success"] is False
        assert bad_res["error_code"] == "archive_password_wrong"
        assert wrong not in str(bad_res)
        assert wrong not in str(api.get_archive_install_status())
        assert all(wrong not in r.getMessage() for r in caplog.records)

        # Missing password returns archive_password_required.
        req_res = api.install_archive(
            {
                "archive_path": str(archive),
                "library_folder": str(lib),
                "title": "ApiDemo",
            }
        )
        assert req_res["success"] is False
        assert req_res["error_code"] == "archive_password_required"

        # Correct password succeeds and never enters logs or status.
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            ok_res = api.install_archive(
                {
                    "archive_path": str(archive),
                    "library_folder": str(lib),
                    "title": "ApiDemo",
                    "archive_password": secret,
                }
            )
        assert ok_res["success"] is True
        assert secret not in str(ok_res)
        assert secret not in str(api.get_archive_install_status())
        assert all(secret not in r.getMessage() for r in caplog.records)
    finally:
        api._download_queue.close()
