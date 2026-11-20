# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

from core import rpgm_tools
from core.game_tools import GameToolError
from test_rpgm_tools import PNG, encrypt, never, no_progress, system_json


@pytest.fixture(params=["dangling symlink", "directory"])
def blocked_copy(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    system_json(tmp_path)
    _ = (tmp_path / "Title.rpgmvp").write_bytes(encrypt(PNG))
    destination = tmp_path / "Title.png"
    if request.param == "dangling symlink":
        destination.symlink_to(tmp_path / "shared.png")
    else:
        destination.mkdir()
    return destination


def test_in_game_switch_refuses_unusable_copy(blocked_copy: Path):
    root = str(blocked_copy.parent)
    system = Path(rpgm_tools.system_json_path(root))
    original = system.read_bytes()

    with pytest.raises(
        GameToolError, match=r"1 encrypted asset.*Title\.png.*readable regular file"
    ):
        rpgm_tools.set_use_decrypted(root, True)

    assert system.read_bytes() == original
    assert not rpgm_tools.has_backup(root)
    pending = rpgm_tools.missing_copies(rpgm_tools.scan_assets(root))
    assert [asset.destination for asset in pending] == [str(blocked_copy)]


def test_decrypt_reports_unusable_copy_and_accepts_readable_symlink(blocked_copy: Path):
    root = str(blocked_copy.parent)

    with pytest.raises(GameToolError, match=r"Title\.png.*readable regular file"):
        _ = rpgm_tools.decrypt_assets(root, no_progress, never)

    if blocked_copy.is_symlink():
        assert blocked_copy.readlink() == blocked_copy.with_name("shared.png")
    else:
        assert blocked_copy.is_dir()
        blocked_copy.rmdir()
        blocked_copy.symlink_to(blocked_copy.with_name("shared.png"))
    _ = blocked_copy.with_name("shared.png").write_bytes(PNG)

    result = rpgm_tools.decrypt_assets(root, no_progress, never)
    assert result["already"] == 1
    assert result["decrypted"] == 0
    assert blocked_copy.is_symlink()
    rpgm_tools.set_use_decrypted(root, True)
    assert rpgm_tools.read_use_decrypted(root)
