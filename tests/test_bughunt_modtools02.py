# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for the mod-tools bug hunt (BepInEx, extraction, Wine, RPG Maker)."""

import errno
import json
import shutil
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from api_patching import patch_api_name

# ── Bug 1: a file added inside a folder the backup already holds ──


def _bepinex_package(archive: Path, value: str) -> None:
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("BepInEx/core/BepInEx.Core.dll", value)
        z.writestr("winhttp.dll", value)


def _install(root: Path, archive: Path) -> None:
    from core.unity_tools import install_build

    _ = install_build(str(root), str(archive), None, lambda *a: None, lambda: False)


def test_restore_after_new_file_inside_backed_up_bepinex_folder(tmp_path):
    from core.unity_tools import restore, uninstall

    core = tmp_path / "BepInEx/core"
    core.mkdir(parents=True)
    (core / "BepInEx.Core.dll").write_text("user")
    plugin = tmp_path / "BepInEx/plugins/mod.dll"
    plugin.parent.mkdir()
    plugin.write_text("mine")
    archive = tmp_path / "package.zip"
    _bepinex_package(archive, "wlib")
    _install(tmp_path, archive)
    # The user adds a DLL to BepInEx/core after the install; the backup
    # already holds the user's own BepInEx/core folder.
    (core / "Custom.dll").write_text("custom")

    _ = uninstall(str(tmp_path), True)
    manifest = json.loads(
        (tmp_path / "BepInEx.wlib-backup/manifest.json").read_text(encoding="utf-8")
    )
    moved = [Path(p) for p in manifest["moved"]]
    assert not any(p != q and p.is_relative_to(q) for p in moved for q in moved), (
        manifest["moved"]
    )

    _ = restore(str(tmp_path))
    assert (core / "BepInEx.Core.dll").read_text() == "user"
    assert (core / "Custom.dll").read_text() == "custom"
    assert plugin.read_text() == "mine"
    assert not (tmp_path / "winhttp.dll").exists()
    assert not (tmp_path / "BepInEx.wlib-backup").exists()


def test_restore_accepts_manifest_with_nested_moved_paths(tmp_path):
    # Manifests that older builds wrote can list a folder and a file inside it.
    from core.unity_tools import restore

    backup = tmp_path / "BepInEx.wlib-backup"
    (backup / "BepInEx/core").mkdir(parents=True)
    (backup / "BepInEx/core/BepInEx.Core.dll").write_text("user")
    (backup / "BepInEx/core/Custom.dll").write_text("custom")
    (backup / "BepInEx/plugins").mkdir()
    (backup / "BepInEx/plugins/mod.dll").write_text("mine")
    (backup / "manifest.json").write_text(
        json.dumps(
            {
                "moved": [
                    "BepInEx/core",
                    "BepInEx/core/Custom.dll",
                    "BepInEx/plugins",
                ],
                "installed": [],
                "foreign": True,
            }
        ),
        encoding="utf-8",
    )

    _ = restore(str(tmp_path))
    assert (tmp_path / "BepInEx/core/BepInEx.Core.dll").read_text() == "user"
    assert (tmp_path / "BepInEx/core/Custom.dll").read_text() == "custom"
    assert (tmp_path / "BepInEx/plugins/mod.dll").read_text() == "mine"


def test_restore_still_rolls_back_a_genuine_conflict(tmp_path):
    from core.game_tools import GameToolError
    from core.unity_tools import restore

    backup = tmp_path / "BepInEx.wlib-backup"
    (backup / "BepInEx/core").mkdir(parents=True)
    (backup / "BepInEx/core/BepInEx.Core.dll").write_text("user")
    (backup / "winhttp.dll").write_text("old")
    (backup / "manifest.json").write_text(
        json.dumps({"moved": ["BepInEx/core", "winhttp.dll"], "installed": []}),
        encoding="utf-8",
    )
    (tmp_path / "winhttp.dll").write_text("someone else's")

    with pytest.raises(GameToolError, match="winhttp.dll"):
        _ = restore(str(tmp_path))
    assert (backup / "BepInEx/core/BepInEx.Core.dll").read_text() == "user"
    assert not (tmp_path / "BepInEx/core").exists()
    assert (tmp_path / "winhttp.dll").read_text() == "someone else's"


# ── Bug 2: reinstall after "uninstall, keep user data" ──


def test_reinstall_after_uninstall_keeps_restore_usable(tmp_path):
    from core.unity_tools import restore, uninstall

    archive = tmp_path / "package.zip"
    _bepinex_package(archive, "v1")
    _install(tmp_path, archive)
    _ = uninstall(str(tmp_path), False)
    assert not (tmp_path / "winhttp.dll").exists()

    _bepinex_package(archive, "v2")
    _install(tmp_path, archive)
    assert (tmp_path / "winhttp.dll").read_text() == "v2"

    _ = restore(str(tmp_path))
    assert (tmp_path / "winhttp.dll").read_text() == "v1"
    assert (tmp_path / "BepInEx/core/BepInEx.Core.dll").read_text() == "v1"
    assert not (tmp_path / "BepInEx.wlib-backup").exists()


def test_failed_reinstall_after_uninstall_leaves_backup_usable(tmp_path, monkeypatch):
    from core.unity_tools import restore, uninstall

    archive = tmp_path / "package.zip"
    _bepinex_package(archive, "v1")
    _install(tmp_path, archive)
    _ = uninstall(str(tmp_path), False)
    manifest = tmp_path / "BepInEx.wlib-backup/manifest.json"
    before = manifest.read_text(encoding="utf-8")

    real_move = shutil.move

    def failing_move(src, dst, *args, **kwargs):
        if ".wlib-bepinex-staging" in str(src) and str(dst).endswith("winhttp.dll"):
            raise OSError(errno.EIO, "injected")
        return real_move(src, dst, *args, **kwargs)

    _bepinex_package(archive, "v2")
    monkeypatch.setattr(shutil, "move", failing_move)
    with pytest.raises(OSError, match="injected"):
        _install(tmp_path, archive)
    monkeypatch.setattr(shutil, "move", real_move)

    assert manifest.read_text(encoding="utf-8") == before
    assert not (tmp_path / "winhttp.dll").exists()
    _ = restore(str(tmp_path))
    assert (tmp_path / "winhttp.dll").read_text() == "v1"


def test_first_install_without_backup_still_works(tmp_path):
    archive = tmp_path / "package.zip"
    _bepinex_package(archive, "v1")
    _install(tmp_path, archive)
    assert (tmp_path / "winhttp.dll").read_text() == "v1"
    assert not (tmp_path / "BepInEx.wlib-backup").exists()


# ── Bug 3: two in-root aliases of the same extracted file ──


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks")
def test_renpy_in_place_alias_does_not_overwrite_first_archive(tmp_path):
    from test_renpy_tools import build_rpa, make_game

    from core.renpy_tools import extract_archives

    game = make_game(tmp_path)
    (game / "game" / "images").mkdir()
    (game / "game" / "alias").symlink_to("images", target_is_directory=True)
    # Ren'Py uses b.rpa first (reverse name order), so its copy must win.
    build_rpa(game / "game" / "a.rpa", {"images/a.png": b"from-a"})
    build_rpa(game / "game" / "b.rpa", {"alias/a.png": b"from-b"})

    result = extract_archives(str(game), "in_place", "", lambda *a: None, lambda: False)

    assert (game / "game" / "images" / "a.png").read_bytes() == b"from-b"
    assert result["files"] == 1
    assert result["skipped_existing"] == 1


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks")
def test_rgss_in_place_alias_does_not_overwrite_first_archive(tmp_path):
    from test_rgss_tools import build_rgss_v3, make_rgss_game

    from core.rgss_tools import extract_archives

    game = make_rgss_game(tmp_path)
    (game / "Graphics").mkdir()
    (game / "Alias").symlink_to("Graphics", target_is_directory=True)
    # RGSS uses the first archive in name order.
    build_rgss_v3(game / "A_Main.rgss3a", [("Graphics\\title.png", b"title-a")])
    build_rgss_v3(game / "B_Extra.rgss3a", [("Alias\\title.png", b"title-b")])

    result = extract_archives(str(game), "in_place", "", lambda *a: None, lambda: False)

    assert (game / "Graphics" / "title.png").read_bytes() == b"title-a"
    assert result["files"] == 1
    assert result["skipped_existing"] == 1


# ── Bug 4: Winecfg and RTP use the selected Wine executable ──


def _custom_wine(tmp_path):
    """A selected Wine build outside PATH, with wineserver beside it."""
    import os

    bin_dir = tmp_path / "wine-custom" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("wine", "wineserver"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        os.chmod(bin_dir / name, 0o755)
    return str(bin_dir / "wine")


def _which(on_path):
    """host_which like shutil.which: a path must be an executable file."""
    import os

    def which(name):
        if os.sep in name:
            return name if os.path.isfile(name) and os.access(name, os.X_OK) else None
        return f"/usr/bin/{name}" if name in on_path else None

    return which


@pytest.mark.skipif(
    sys.platform == "win32", reason="Wine prefix tools run only on Linux hosts"
)
def test_winecfg_uses_selected_wine_executable(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from core.api import Api

    patch_api_name(monkeypatch, "is_linux", lambda: True)
    patch_api_name(monkeypatch, "host_which", _which({"wine", "winetricks", "zenity"}))
    process = MagicMock()
    process.poll.return_value = 0
    popen = MagicMock(return_value=process)
    monkeypatch.setattr("subprocess.Popen", popen)
    api = Api()
    prefix = str(tmp_path / "prefix")

    custom = _custom_wine(tmp_path)
    assert api.open_wine_prefix_tool("winecfg", prefix, custom)["success"]
    assert popen.call_args.args[0] == [custom, "winecfg"]

    # A bare runner name is looked up on the host PATH.
    assert api.open_wine_prefix_tool("winecfg", prefix, "wine")["success"]
    assert popen.call_args.args[0] == ["/usr/bin/wine", "winecfg"]

    # No Wine on PATH: the selected build still opens Winecfg.
    patch_api_name(monkeypatch, "host_which", _which(set()))
    assert api.open_wine_prefix_tool("winecfg", prefix, custom)["success"]
    assert popen.call_args.args[0] == [custom, "winecfg"]
    result = api.open_wine_prefix_tool("winecfg", prefix, "wine")
    assert result["success"] is False and "Wine is not installed" in result["error"]


@pytest.mark.skipif(
    sys.platform == "win32", reason="Wine prefix tools run only on Linux hosts"
)
def test_rtp_install_uses_selected_wine_executable(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from core.api import Api
    from core.database import init_db

    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    patch_api_name(monkeypatch, "is_linux", lambda: True)
    patch_api_name(monkeypatch, "host_which", _which(set()))

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(threading, "Thread", InlineThread)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return MagicMock(returncode=0)

    monkeypatch.setattr("subprocess.run", run)
    init_db()
    api = Api()
    rtp_dir = tmp_path / "rtps"
    rtp_dir.mkdir()
    (rtp_dir / "setup.exe").write_bytes(b"MZ")
    patch_api_name(monkeypatch, "get_rtp_dir", lambda: str(rtp_dir))
    monkeypatch.setattr(
        api,
        "_get_rtp_packages",
        lambda: [
            {"name": "test", "filename": "setup.exe", "url": "https://example.test"}
        ],
    )
    monkeypatch.setattr(api, "_verify_rtp_packages", lambda _: {"test": True})

    custom = _custom_wine(tmp_path)
    result = api.install_rpgmaker_rtp(str(tmp_path / "prefix"), custom)
    assert result["success"], result
    setup = [c for c in commands if str(rtp_dir / "setup.exe") in c]
    assert setup == [[custom, str(rtp_dir / "setup.exe"), "/S", "/v/qn"]]


@pytest.mark.skipif(
    sys.platform == "win32", reason="Wine prefix tools run only on Linux hosts"
)
def test_missing_selected_wine_says_wine_is_not_installed(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from core.api import Api

    patch_api_name(monkeypatch, "is_linux", lambda: True)
    # System Wine is installed, but the selected build was deleted.
    patch_api_name(monkeypatch, "host_which", _which({"wine", "winetricks", "zenity"}))
    api = Api()
    popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", popen)
    prefix = str(tmp_path / "prefix")
    gone = str(tmp_path / "deleted-runner" / "bin" / "wine")

    for tool in ("winecfg", "winetricks"):
        result = api.open_wine_prefix_tool(tool, prefix, gone)
        assert result["success"] is False, tool
        assert "Wine is not installed" in result["error"], tool
    popen.assert_not_called()
    result = api.install_rpgmaker_rtp(prefix, gone)
    assert result["success"] is False and "Wine is not installed" in result["error"]


@pytest.mark.skipif(
    sys.platform == "win32", reason="Wine prefix tools run only on Linux hosts"
)
def test_winetricks_runs_the_selected_wine(tmp_path, monkeypatch):
    import os
    from unittest.mock import MagicMock

    from core.api import Api

    patch_api_name(monkeypatch, "is_linux", lambda: True)
    # Only winetricks and zenity on PATH: no system Wine at all.
    patch_api_name(monkeypatch, "host_which", _which({"winetricks", "zenity"}))
    process = MagicMock()
    process.poll.return_value = 0
    popen = MagicMock(return_value=process)
    monkeypatch.setattr("subprocess.Popen", popen)
    api = Api()
    custom = _custom_wine(tmp_path)

    result = api.open_wine_prefix_tool("winetricks", str(tmp_path / "prefix"), custom)
    assert result["success"], result
    assert popen.call_args.args[0] == ["/usr/bin/winetricks", "--gui"]
    env = popen.call_args.kwargs["env"]
    assert env["WINE"] == custom
    assert env["WINESERVER"] == os.path.join(os.path.dirname(custom), "wineserver")


# ── Bug 5: a failed System.json backup copy must not be trusted later ──


def test_failed_backup_copy_does_not_leave_a_truncated_backup(tmp_path, monkeypatch):
    from test_rpgm_tools import PNG, encrypt, system_json

    from core import rpgm_tools
    from core.game_tools import GameToolError

    root = tmp_path / "www"
    system_json(root, hasEncryptedAudio=False)
    face = root / "img" / "faces" / "Actor1.rpgmvp"
    face.parent.mkdir(parents=True)
    face.write_bytes(encrypt(PNG))
    _ = rpgm_tools.decrypt_assets(str(root), lambda *a: None, lambda: False)
    path = root / "data" / "System.json"
    original = path.read_bytes()

    def disk_full() -> OSError:
        return OSError(errno.ENOSPC, "No space left on device")

    def half_copy_path(src, dst, *args, **kwargs):
        with open(src, "rb") as source, open(dst, "wb") as out:
            _ = out.write(source.read()[:5])
        raise disk_full()

    def half_copy_file(fsrc, fdst, *args, **kwargs):
        _ = fdst.write(fsrc.read()[:5])
        raise disk_full()

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copy2", half_copy_path)
        patch.setattr(shutil, "copyfile", half_copy_path)
        patch.setattr(shutil, "copyfileobj", half_copy_file)
        with pytest.raises(GameToolError):
            rpgm_tools.set_use_decrypted(str(root), True)

    assert path.read_bytes() == original
    assert not rpgm_tools.has_backup(str(root))
    assert sorted(p.name for p in path.parent.iterdir()) == ["System.json"]

    # Retry once space is free, then turn the switch off again.
    rpgm_tools.set_use_decrypted(str(root), True)
    assert Path(str(path) + ".wlib-backup").read_bytes() == original
    rpgm_tools.set_use_decrypted(str(root), False)
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["hasEncryptedImages"] is True
    assert after["hasEncryptedAudio"] is False
