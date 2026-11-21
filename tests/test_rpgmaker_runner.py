# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for RPGMaker Linux runner install folder, engine rules, and installers."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from core import rpgmaker_runner
from core.rpgmaker_runner import (
    install_mkxpz,
    install_runner,
    mkxpz_status,
    resolve_auto_runtime,
    runner_engine,
    runner_install_dir,
)


def test_runner_install_dir_defaults_without_config_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert runner_install_dir() == str(tmp_path / "desktopapps")


def test_runner_install_dir_defaults_when_config_file_empty(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / ".config" / "defrpgmakerlinuxpath.txt"
    config.parent.mkdir(parents=True)
    config.write_text("   \n", encoding="utf-8")
    assert runner_install_dir() == str(tmp_path / "desktopapps")


def test_runner_install_dir_strips_trailing_slash(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / ".config" / "defrpgmakerlinuxpath.txt"
    config.parent.mkdir(parents=True)
    config.write_text("/data/rpgm///\n", encoding="utf-8")
    assert runner_install_dir() == "/data/rpgm"


def test_runner_install_dir_expands_tilde(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / ".config" / "defrpgmakerlinuxpath.txt"
    config.parent.mkdir(parents=True)
    config.write_text("~/custom-rpgm/\n", encoding="utf-8")
    assert runner_install_dir() == str(tmp_path / "custom-rpgm")


def test_mkxpz_status_present_missing_and_not_executable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("core.rpgmaker_runner.platform.machine", lambda: "x86_64")

    assert mkxpz_status() == {"installed": False, "path": ""}

    binary = tmp_path / "desktopapps" / "mkxp-z" / "mkxp-z.x86_64"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"#!/bin/sh\n")
    os.chmod(binary, 0o644)
    assert mkxpz_status() == {"installed": False, "path": ""}

    os.chmod(binary, 0o755)
    assert mkxpz_status() == {"installed": True, "path": str(binary)}


def test_runner_engine_detects_mv_mz_mkxpz_and_ignores_others(tmp_path: Path):
    mv_dir = tmp_path / "mv"
    (mv_dir / "www" / "js").mkdir(parents=True)
    (mv_dir / "package.json").write_text("{}", encoding="utf-8")
    (mv_dir / "www" / "js" / "plugins.js").write_text("", encoding="utf-8")
    assert runner_engine(str(mv_dir)) == "mv"
    # Passing the www subfolder adjusts to parent when ../package.json exists.
    assert runner_engine(str(mv_dir / "www")) == "mv"

    mz_dir = tmp_path / "mz"
    (mz_dir / "data").mkdir(parents=True)
    (mz_dir / "js").mkdir(parents=True)
    (mz_dir / "package.json").write_text("{}", encoding="utf-8")
    (mz_dir / "js" / "plugins.js").write_text("", encoding="utf-8")
    assert runner_engine(str(mz_dir)) == "mz"

    vxace_dir = tmp_path / "vxace"
    vxace_dir.mkdir()
    (vxace_dir / "Game.ini").write_text("[Game]\n", encoding="utf-8")
    (vxace_dir / "Game.exe").write_bytes(b"MZ")
    assert runner_engine(str(vxace_dir)) == "mkxpz"

    rm2k3_dir = tmp_path / "rm2k3"
    rm2k3_dir.mkdir()
    (rm2k3_dir / "RPG_RT.exe").write_bytes(b"MZ")
    (rm2k3_dir / "RPG_RT.ini").write_text("[RPG_RT]\n", encoding="utf-8")
    assert runner_engine(str(rm2k3_dir)) == ""

    unity_dir = tmp_path / "unity"
    (unity_dir / "Game_Data").mkdir(parents=True)
    (unity_dir / "Game.exe").write_bytes(b"MZ")
    (unity_dir / "UnityPlayer.dll").write_bytes(b"MZ")
    assert runner_engine(str(unity_dir)) == ""


def test_runner_engine_matches_upstream_precedence_order(tmp_path: Path):
    # A folder matching MV + MZ + mkxpz rules selects MV first, then MZ, then mkxpz.
    both = tmp_path / "both"
    (both / "www" / "js").mkdir(parents=True)
    (both / "data").mkdir(parents=True)
    (both / "js").mkdir(parents=True)
    (both / "package.json").write_text("{}", encoding="utf-8")
    (both / "www" / "js" / "plugins.js").write_text("", encoding="utf-8")
    (both / "js" / "plugins.js").write_text("", encoding="utf-8")
    (both / "Game.ini").write_text("[Game]\n", encoding="utf-8")
    (both / "Game.exe").write_bytes(b"MZ")

    assert runner_engine(str(both)) == "mv"
    (both / "www" / "js" / "plugins.js").unlink()
    assert runner_engine(str(both)) == "mz"
    (both / "js" / "plugins.js").unlink()
    assert runner_engine(str(both)) == "mkxpz"


def _make_mv_exe(root: Path) -> Path:
    game_dir = root / "mv_game"
    (game_dir / "www" / "js").mkdir(parents=True)
    (game_dir / "package.json").write_text("{}", encoding="utf-8")
    (game_dir / "www" / "js" / "plugins.js").write_text("", encoding="utf-8")
    exe = game_dir / "Game.exe"
    exe.write_bytes(b"MZ")
    return exe


def _make_mz_exe(root: Path) -> Path:
    game_dir = root / "mz_game"
    (game_dir / "data").mkdir(parents=True)
    (game_dir / "js").mkdir(parents=True)
    (game_dir / "package.json").write_text("{}", encoding="utf-8")
    (game_dir / "js" / "plugins.js").write_text("", encoding="utf-8")
    exe = game_dir / "Game.exe"
    exe.write_bytes(b"MZ")
    return exe


def _make_vxace_exe(root: Path) -> Path:
    game_dir = root / "vxace_game"
    game_dir.mkdir(parents=True)
    (game_dir / "Game.ini").write_text("[Game]\n", encoding="utf-8")
    exe = game_dir / "Game.exe"
    exe.write_bytes(b"MZ")
    return exe


def _install_fake_mkxpz(home: Path) -> Path:
    binary = home / "desktopapps" / "mkxp-z" / "mkxp-z.x86_64"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"#!/bin/sh\n")
    os.chmod(binary, 0o755)
    return binary


def test_resolve_auto_runtime_non_linux(tmp_path: Path, monkeypatch):
    exe = _make_mv_exe(tmp_path)
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: False)
    result = resolve_auto_runtime(str(exe), runner_available=True)
    assert result["runtime"] == "default"
    assert result["reason"] == "non_linux"


def test_resolve_auto_runtime_non_exe(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: True)
    sh = tmp_path / "game.sh"
    sh.write_text("#!/bin/sh\n", encoding="utf-8")
    result = resolve_auto_runtime(str(sh), runner_available=True)
    assert result["runtime"] == "default"
    assert result["reason"] == "not_exe"


def test_resolve_auto_runtime_cheat_engine_on(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: True)
    exe = _make_mv_exe(tmp_path)
    result = resolve_auto_runtime(str(exe), auto_inject_ce=True, runner_available=True)
    assert result["runtime"] == "default"
    assert result["reason"] == "wine_settings"


def test_resolve_auto_runtime_custom_prefix(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: True)
    exe = _make_mv_exe(tmp_path)
    result = resolve_auto_runtime(
        str(exe), custom_prefix="/tmp/prefix", runner_available=True
    )
    assert result["runtime"] == "default"
    assert result["reason"] == "wine_settings"


def test_resolve_auto_runtime_proton_version_only(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: True)
    exe = _make_mv_exe(tmp_path)
    result = resolve_auto_runtime(
        str(exe), proton_version="GE-Proton", runner_available=True
    )
    assert result["runtime"] == "default"
    assert result["reason"] == "wine_settings"


def test_resolve_auto_runtime_no_engine(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: True)
    exe = tmp_path / "other" / "Game.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    result = resolve_auto_runtime(str(exe), runner_available=True)
    assert result["runtime"] == "default"
    assert result["engine"] == ""
    assert result["reason"] == "not_rpgmaker"


def test_resolve_auto_runtime_runner_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: True)
    exe = _make_mv_exe(tmp_path)
    result = resolve_auto_runtime(str(exe), runner_available=False)
    assert result["runtime"] == "default"
    assert result["engine"] == "mv"
    assert result["reason"] == "runner_missing"


def test_resolve_auto_runtime_mkxpz_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("core.rpgmaker_runner.platform.machine", lambda: "x86_64")
    exe = _make_vxace_exe(tmp_path)
    result = resolve_auto_runtime(str(exe), runner_available=True)
    assert result["runtime"] == "default"
    assert result["engine"] == "mkxpz"
    assert result["reason"] == "mkxpz_missing"


def test_resolve_auto_runtime_sandboxed_mkxpz_without_overlay(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("core.rpgmaker_runner.platform.machine", lambda: "x86_64")
    _install_fake_mkxpz(tmp_path)
    monkeypatch.setattr(
        "core.rpgmaker_runner.game_sandbox.get_sandbox_status",
        lambda: {
            "status": "ready",
            "tool": "bwrap",
            "path": "/usr/bin/bwrap",
            "version": "",
            "reason": "",
        },
    )
    monkeypatch.setattr(
        "core.rpgmaker_runner.game_sandbox.runner_overlay_supported",
        lambda _bwrap, _root: False,
    )
    exe = _make_vxace_exe(tmp_path)
    result = resolve_auto_runtime(str(exe), sandboxed=True, runner_available=True)
    assert result["runtime"] == "default"
    assert result["engine"] == "mkxpz"
    assert result["reason"] == "mkxpz_sandbox_overlay_unsupported"


def test_resolve_auto_runtime_success_mv_mz_and_vxace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("core.rpgmaker_runner.is_linux", lambda: True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("core.rpgmaker_runner.platform.machine", lambda: "x86_64")
    _install_fake_mkxpz(tmp_path)
    monkeypatch.setattr(
        "core.rpgmaker_runner.game_sandbox.get_sandbox_status",
        lambda: {
            "status": "ready",
            "tool": "bwrap",
            "path": "/usr/bin/bwrap",
            "version": "",
            "reason": "",
        },
    )
    monkeypatch.setattr(
        "core.rpgmaker_runner.game_sandbox.runner_overlay_supported",
        lambda _bwrap, _root: True,
    )

    mv_res = resolve_auto_runtime(str(_make_mv_exe(tmp_path)), runner_available=True)
    assert mv_res == {"runtime": "rpgmaker_linux", "engine": "mv", "reason": ""}

    mz_res = resolve_auto_runtime(str(_make_mz_exe(tmp_path)), runner_available=True)
    assert mz_res == {"runtime": "rpgmaker_linux", "engine": "mz", "reason": ""}

    vxace_exe = _make_vxace_exe(tmp_path)
    vx_res = resolve_auto_runtime(str(vxace_exe), runner_available=True)
    assert vx_res == {"runtime": "rpgmaker_linux", "engine": "mkxpz", "reason": ""}

    vx_sandbox_res = resolve_auto_runtime(
        str(vxace_exe), sandboxed=True, runner_available=True
    )
    assert vx_sandbox_res == {
        "runtime": "rpgmaker_linux",
        "engine": "mkxpz",
        "reason": "",
    }


def _build_runner_tarball(
    dest: Path, script_body: str = "#!/bin/bash\nexit 0\n"
) -> tuple[Path, str]:
    tar_path = dest / "runner.tar.gz"
    script_bytes = script_body.encode("utf-8")
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo(name="rpgmakerlinux-x86_64-v1.1.9/install.sh")
        info.size = len(script_bytes)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(script_bytes))
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    return tar_path, digest


class _FakeHttpResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_install_runner_hash_mismatch_deletes_download_and_extracts_nothing(
    tmp_path: Path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(rpgmaker_runner, "_default_runner_available", lambda: False)
    monkeypatch.setattr(rpgmaker_runner, "host_which", lambda tool: f"/usr/bin/{tool}")

    tar_path, _digest = _build_runner_tarball(tmp_path)
    monkeypatch.setattr(
        rpgmaker_runner,
        "RUNNER_TARBALL_URL_X86_64",
        "https://example.test/runner.tar.gz",
    )
    monkeypatch.setattr(rpgmaker_runner, "RUNNER_TARBALL_SHA256_X86_64", "0" * 64)
    monkeypatch.setattr(
        rpgmaker_runner.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHttpResponse(tar_path.read_bytes()),
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        install_runner()

    downloads_dir = data_dir / "downloads"
    assert not list(downloads_dir.glob("rpgm-runner-*"))
    scratch_dir = data_dir / "install-scratch"
    assert not scratch_dir.exists() or not list(scratch_dir.iterdir())


def test_install_runner_missing_file_command_skips_download(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(rpgmaker_runner, "_default_runner_available", lambda: False)
    monkeypatch.setattr(
        rpgmaker_runner,
        "host_which",
        lambda tool: "/usr/bin/bash" if tool == "bash" else None,
    )
    urlopen = Mock()
    monkeypatch.setattr(rpgmaker_runner.urllib.request, "urlopen", urlopen)

    with pytest.raises(ValueError, match="file"):
        install_runner()

    urlopen.assert_not_called()


def test_install_runner_uses_host_command_and_cleans_scratch(
    tmp_path: Path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(rpgmaker_runner, "_default_runner_available", lambda: False)
    monkeypatch.setattr(rpgmaker_runner, "host_which", lambda tool: f"/usr/bin/{tool}")

    tar_path, digest = _build_runner_tarball(tmp_path)
    monkeypatch.setattr(
        rpgmaker_runner,
        "RUNNER_TARBALL_URL_X86_64",
        "https://example.test/runner.tar.gz",
    )
    monkeypatch.setattr(rpgmaker_runner, "RUNNER_TARBALL_SHA256_X86_64", digest)
    monkeypatch.setattr(
        rpgmaker_runner.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHttpResponse(tar_path.read_bytes()),
    )

    seen_host_cmds: list[list[str]] = []

    def fake_host_command(cmd, env=None, **_kwargs):
        seen_host_cmds.append(list(cmd))
        return ["flatpak-spawn", "--host", *cmd]

    monkeypatch.setattr(rpgmaker_runner, "host_command", fake_host_command)
    run_mock = Mock(
        return_value=Mock(returncode=0, stdout="Installation Done\n", stderr="")
    )
    monkeypatch.setattr(rpgmaker_runner.subprocess, "run", run_mock)
    monkeypatch.setattr(
        "core.launcher.Launcher.get_rpgmaker_linux_runner_status",
        lambda _self: {
            "available": True,
            "path": "/home/user/.local/bin/rpgmaker-linux",
            "source": "local-bin",
            "configured_path": "",
            "error": "",
        },
    )

    status = install_runner()
    assert status["available"] is True
    assert len(seen_host_cmds) == 1
    assert seen_host_cmds[0][0] == "bash"
    assert seen_host_cmds[0][1].startswith(str(data_dir / "install-scratch"))
    assert seen_host_cmds[0][1].endswith("/install.sh")
    assert run_mock.call_args.args[0][:2] == ["flatpak-spawn", "--host"]

    assert not list((data_dir / "downloads").glob("rpgm-runner-*"))
    assert not list((data_dir / "install-scratch").glob("rpgm-runner-*"))


def test_install_runner_nonzero_exit_reports_error_and_cleans_scratch(
    tmp_path: Path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(rpgmaker_runner, "_default_runner_available", lambda: False)
    monkeypatch.setattr(rpgmaker_runner, "host_which", lambda tool: f"/usr/bin/{tool}")

    tar_path, digest = _build_runner_tarball(tmp_path)
    monkeypatch.setattr(
        rpgmaker_runner,
        "RUNNER_TARBALL_URL_X86_64",
        "https://example.test/runner.tar.gz",
    )
    monkeypatch.setattr(rpgmaker_runner, "RUNNER_TARBALL_SHA256_X86_64", digest)
    monkeypatch.setattr(
        rpgmaker_runner.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHttpResponse(tar_path.read_bytes()),
    )
    monkeypatch.setattr(rpgmaker_runner, "host_command", lambda cmd, **_kw: list(cmd))
    monkeypatch.setattr(
        rpgmaker_runner.subprocess,
        "run",
        Mock(return_value=Mock(returncode=2, stdout="", stderr="Wrong architecture!!")),
    )

    with pytest.raises(RuntimeError, match="exit code 2"):
        install_runner()

    assert not list((data_dir / "downloads").glob("rpgm-runner-*"))
    assert not list((data_dir / "install-scratch").glob("rpgm-runner-*"))


def _build_mkxpz_zip(
    dest: Path, entries: list[tuple[str, bytes, int]]
) -> tuple[Path, str]:
    zip_path = dest / "mkxp-z.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(info, data)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return zip_path, digest


def test_install_mkxpz_unpacks_preserves_exec_bit_and_updates_mkxp_json(
    tmp_path: Path, monkeypatch
):
    home = tmp_path / "home"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(rpgmaker_runner, "_default_runner_available", lambda: True)

    mkxp_json = (
        b"{\n"
        b"  // comment kept\n"
        b'  "preloadScript": ["Kawariki-patches/preload.rb"],\n'
        b'  "RTP": ["RGSS3/RPGVXAce", "RGSS2/RPGVX", "RGSS/Standard"]\n'
        b"}\n"
    )
    zip_path, digest = _build_mkxpz_zip(
        tmp_path,
        [
            ("mkxp-z.x86_64", b"#!/bin/sh\nexit 0\n", 0o755),
            ("mkxp.json", mkxp_json, 0o644),
        ],
    )
    monkeypatch.setattr(
        rpgmaker_runner, "MKXPZ_ZIP_URL_X86_64", "https://example.test/mkxp-z.zip"
    )
    monkeypatch.setattr(rpgmaker_runner, "MKXPZ_ZIP_SHA256_X86_64", digest)
    monkeypatch.setattr(
        rpgmaker_runner.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHttpResponse(zip_path.read_bytes()),
    )

    status = install_mkxpz()
    expected_dir = home / "desktopapps" / "mkxp-z"
    expected_bin = expected_dir / "mkxp-z.x86_64"
    assert status == {"installed": True, "path": str(expected_bin)}
    assert os.access(expected_bin, os.X_OK)

    updated = (expected_dir / "mkxp.json").read_text(encoding="utf-8")
    assert "// comment kept" in updated
    assert f'"{expected_dir}/Kawariki-patches/preload.rb"' in updated
    assert f'"{expected_dir}/RGSS3/RPGVXAce"' in updated
    assert f'"{expected_dir}/RGSS/Standard"' in updated
    assert not list((home / "desktopapps").glob("mkxp-z.partial-*"))


def test_install_mkxpz_refuses_dotdot_entry_and_cleans_partial(
    tmp_path: Path, monkeypatch
):
    home = tmp_path / "home"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(rpgmaker_runner, "_default_runner_available", lambda: True)

    zip_path, digest = _build_mkxpz_zip(
        tmp_path,
        [
            ("mkxp-z.x86_64", b"ok", 0o755),
            ("../escape.txt", b"bad", 0o644),
        ],
    )
    monkeypatch.setattr(
        rpgmaker_runner, "MKXPZ_ZIP_URL_X86_64", "https://example.test/mkxp-z.zip"
    )
    monkeypatch.setattr(rpgmaker_runner, "MKXPZ_ZIP_SHA256_X86_64", digest)
    monkeypatch.setattr(
        rpgmaker_runner.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHttpResponse(zip_path.read_bytes()),
    )

    with pytest.raises(ValueError, match="Unsafe path"):
        install_mkxpz()

    assert not (home / "desktopapps" / "mkxp-z").exists()
    assert not (home / "desktopapps" / "escape.txt").exists()
    assert not list((home / "desktopapps").glob("mkxp-z.partial-*"))


def test_install_mkxpz_hash_mismatch_leaves_install_dir_unchanged(
    tmp_path: Path, monkeypatch
):
    home = tmp_path / "home"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(rpgmaker_runner, "_default_runner_available", lambda: True)

    zip_path, _digest = _build_mkxpz_zip(tmp_path, [("mkxp-z.x86_64", b"ok", 0o755)])
    monkeypatch.setattr(
        rpgmaker_runner, "MKXPZ_ZIP_URL_X86_64", "https://example.test/mkxp-z.zip"
    )
    monkeypatch.setattr(rpgmaker_runner, "MKXPZ_ZIP_SHA256_X86_64", "f" * 64)
    monkeypatch.setattr(
        rpgmaker_runner.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHttpResponse(zip_path.read_bytes()),
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        install_mkxpz()

    assert not (home / "desktopapps").exists()
    assert not list((data_dir / "downloads").glob("rpgm-mkxpz-*"))


def test_install_mkxpz_partway_failure_removes_partial_folder(
    tmp_path: Path, monkeypatch
):
    home = tmp_path / "home"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(rpgmaker_runner, "_default_runner_available", lambda: True)

    zip_path, digest = _build_mkxpz_zip(
        tmp_path,
        [
            ("first.txt", b"one", 0o644),
            ("second.txt", b"two", 0o644),
        ],
    )
    monkeypatch.setattr(
        rpgmaker_runner, "MKXPZ_ZIP_URL_X86_64", "https://example.test/mkxp-z.zip"
    )
    monkeypatch.setattr(rpgmaker_runner, "MKXPZ_ZIP_SHA256_X86_64", digest)
    monkeypatch.setattr(
        rpgmaker_runner.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHttpResponse(zip_path.read_bytes()),
    )

    real_copy = rpgmaker_runner.shutil.copyfileobj
    calls = {"n": 0}

    def flaky_copy(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_copy(src, dst)

    monkeypatch.setattr(rpgmaker_runner.shutil, "copyfileobj", flaky_copy)

    with pytest.raises(OSError, match="disk full"):
        install_mkxpz()

    assert not (home / "desktopapps" / "mkxp-z").exists()
    assert not list((home / "desktopapps").glob("mkxp-z.partial-*"))


def test_api_rpgmaker_runner_install_concurrency_stage_and_error(
    tmp_path: Path, monkeypatch
):
    import threading
    import time

    from core.api import Api
    from core.database import init_db

    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    init_db()
    api = Api()
    monkeypatch.setattr("core.api_mixins.rpgm_runtime.is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(rpgmaker_runner, "host_which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(
        api.launcher,
        "get_rpgmaker_linux_runner_status",
        lambda _cfg=None: {
            "available": False,
            "path": "",
            "source": "",
            "configured_path": "",
            "error": "not installed",
            "mkxpz": {"installed": False, "path": ""},
            "installable": {
                "supported": True,
                "can_install": True,
                "missing_tools": [],
                "reason": "",
            },
        },
    )

    entered = threading.Event()
    release = threading.Event()

    def fake_install_runner(progress=None):
        if progress:
            progress("Downloading RPGMaker Linux runner v1.1.9", 50, 100)
        entered.set()
        release.wait(timeout=5)
        raise RuntimeError("boom hash mismatch")

    monkeypatch.setattr(rpgmaker_runner, "install_runner", fake_install_runner)

    first = api.install_rpgmaker_linux_runner()
    assert first == {"success": True}
    assert entered.wait(timeout=5)

    running_status = api.get_rpgmaker_runner_install_status()
    assert running_status["success"] is True
    assert running_status["running"] is True
    assert running_status["target"] == "runner"
    assert running_status["stage"] == "Downloading RPGMaker Linux runner v1.1.9"

    second = api.install_rpgmaker_linux_runner()
    assert second["success"] is False
    assert "already running" in str(second["error"]).lower()

    release.set()
    deadline = time.monotonic() + 5
    final_status = api.get_rpgmaker_runner_install_status()
    while final_status["running"] and time.monotonic() < deadline:
        time.sleep(0.02)
        final_status = api.get_rpgmaker_runner_install_status()

    assert final_status["running"] is False
    assert final_status["target"] == "runner"
    assert "boom hash mismatch" in str(final_status["error"])


def test_api_rpgmaker_runner_install_windows_returns_unsupported_platform(
    tmp_path: Path, monkeypatch
):
    from core.api import Api
    from core.database import init_db

    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    init_db()
    api = Api()
    monkeypatch.setattr("core.api_mixins.rpgm_runtime.is_linux", lambda: False)

    for result in (
        api.install_rpgmaker_linux_runner(),
        api.install_rpgmaker_mkxpz(),
        api.get_rpgmaker_runner_install_status(),
    ):
        assert result["success"] is False
        assert result["code"] == "unsupported_platform"


def test_runner_status_payload_includes_mkxpz_and_installable(
    tmp_path: Path, monkeypatch
):
    from core.launcher import Launcher

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    monkeypatch.setattr("core.launcher.get_setting", lambda _k: "")
    monkeypatch.setattr("core.launcher.host_which", lambda _cmd: None)
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(rpgmaker_runner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        rpgmaker_runner,
        "host_which",
        lambda tool: "/usr/bin/bash" if tool == "bash" else None,
    )

    status = Launcher().get_rpgmaker_linux_runner_status()
    assert status["mkxpz"] == {"installed": False, "path": ""}
    assert status["installable"]["supported"] is True
    assert status["installable"]["can_install"] is False
    assert status["installable"]["missing_tools"] == ["file"]
    assert "file" in status["installable"]["reason"]


def test_api_get_auto_launch_runtime_mv_with_runner_and_with_custom_prefix(
    tmp_path: Path, monkeypatch
):
    from core.api import Api
    from core.database import init_db

    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    init_db()
    api = Api()
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr(
        api.launcher,
        "get_rpgmaker_linux_runner_status",
        lambda _cfg=None: {
            "available": True,
            "path": "/usr/local/bin/rpgmaker-linux",
            "source": "path",
            "configured_path": "",
            "error": "",
            "mkxpz": {"installed": False, "path": ""},
            "installable": {
                "supported": True,
                "can_install": True,
                "missing_tools": [],
                "reason": "",
            },
        },
    )

    mv_exe = _make_mv_exe(tmp_path)
    resolved = api.get_auto_launch_runtime(str(mv_exe), False, "", "", False)
    assert resolved == {"runtime": "rpgmaker_linux", "engine": "mv", "reason": ""}

    with_prefix = api.get_auto_launch_runtime(
        str(mv_exe), False, "/tmp/custom-pfx", "", False
    )
    assert with_prefix == {
        "runtime": "default",
        "engine": "",
        "reason": "wine_settings",
    }


def test_runner_installability_without_tool_check_skips_host_lookups(monkeypatch):
    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    lookup = Mock(return_value=None)
    monkeypatch.setattr(rpgmaker_runner, "host_which", lookup)

    result = rpgmaker_runner.runner_installability(machine="x86_64", check_tools=False)

    assert result["supported"] is True
    assert result["missing_tools"] == []
    lookup.assert_not_called()


def test_detected_runner_status_skips_installer_tool_lookups(monkeypatch):
    from core.launcher import Launcher

    monkeypatch.setattr(rpgmaker_runner, "is_linux", lambda: True)
    monkeypatch.setattr("core.launcher.is_windows", lambda: False)
    monkeypatch.setattr("core.launcher.get_setting", lambda _key: "")
    monkeypatch.setattr(
        "core.launcher.host_which",
        lambda name: "/usr/bin/rpgmaker-linux" if name == "rpgmaker-linux" else None,
    )
    monkeypatch.setattr(
        Launcher,
        "_validate_runner_candidate",
        lambda _self, path: (
            path == "/usr/bin/rpgmaker-linux",
            path,
            "",
        ),
    )
    tool_lookup = Mock(return_value=None)
    monkeypatch.setattr(rpgmaker_runner, "host_which", tool_lookup)

    status = Launcher().get_rpgmaker_linux_runner_status()

    assert status["available"] is True
    tool_lookup.assert_not_called()
