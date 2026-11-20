import shutil
import zipfile

import pytest

from core.game_tools import GameToolError
from core.unity_tools import install_build, restore


def _install(root, value, plugins_entry=True):
    archive = root.parent / f"{root.name}-package.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("BepInEx/core/BepInEx.Core.dll", value)
        if plugins_entry:
            z.writestr("BepInEx/plugins/", "")
        z.writestr("winhttp.dll", value)
    return install_build(str(root), str(archive), None, lambda *a: None, lambda: False)


def test_failed_update_keeps_the_previous_backup_and_restore_works(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    _install(root, "v1")
    _install(root, "v2")
    # A regular file where the package has a folder makes placement fail.
    shutil.rmtree(root / "BepInEx/plugins")
    (root / "BepInEx/plugins").write_text("not a folder")

    with pytest.raises(OSError):
        _install(root, "v3")

    core = root / "BepInEx/core/BepInEx.Core.dll"
    assert core.read_text() == "v2"
    assert (root / "winhttp.dll").read_text() == "v2"
    assert not (root / ".wlib-bepinex-old-backup").exists()
    # The backup from the v2 update (holding v1) is still there.
    assert (root / "BepInEx.wlib-backup/BepInEx/core/BepInEx.Core.dll").read_text() == "v1"

    restore(str(root))
    assert core.read_text() == "v1"
    assert (root / "winhttp.dll").read_text() == "v1"
    assert not (root / "BepInEx.wlib-backup").exists()


def test_first_failed_install_leaves_no_backup(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    _install(root, "v1")
    (root / "BepInEx/plugins").rmdir()
    (root / "BepInEx/plugins").write_text("not a folder")

    with pytest.raises(OSError):
        _install(root, "v2")

    assert (root / "BepInEx/core/BepInEx.Core.dll").read_text() == "v1"
    assert not (root / "BepInEx.wlib-backup").exists()


def test_restore_with_missing_backup_files_changes_nothing(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    _install(root, "v1")
    _install(root, "v2")
    (root / "BepInEx.wlib-backup/winhttp.dll").unlink()

    with pytest.raises(GameToolError, match="incomplete"):
        restore(str(root))

    assert (root / "BepInEx/core/BepInEx.Core.dll").read_text() == "v2"
    assert (root / "winhttp.dll").read_text() == "v2"
    assert (root / "BepInEx.wlib-backup/manifest.json").exists()


def test_restore_rolls_back_when_a_move_fails(tmp_path, monkeypatch):
    root = tmp_path / "game"
    root.mkdir()
    _install(root, "v1")
    _install(root, "v2")
    real_move = shutil.move

    def failing_move(source, destination):
        if str(destination).endswith("BepInEx.Core.dll") and "wlib-backup" in str(source):
            raise OSError("disk error")
        return real_move(source, destination)

    monkeypatch.setattr(shutil, "move", failing_move)
    with pytest.raises(OSError, match="disk error"):
        restore(str(root))
    monkeypatch.setattr(shutil, "move", real_move)

    assert (root / "BepInEx/core/BepInEx.Core.dll").read_text() == "v2"
    assert (root / "winhttp.dll").read_text() == "v2"
    assert not (root / ".wlib-bepinex-restoring").exists()
    restore(str(root))
    assert (root / "BepInEx/core/BepInEx.Core.dll").read_text() == "v1"
    assert (root / "winhttp.dll").read_text() == "v1"
