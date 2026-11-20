"""An unreadable folder inside the saves must fail the backup, not shrink it."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from core.game_saves import (
    GameSavesError,
    backup_saves,
    copy_folder_contents,
    game_backup_dir,
    import_saves,
    list_backups,
)

NOW = datetime(2026, 1, 2, 3, 4, 5)
LATER = datetime(2026, 1, 3, 3, 4, 5)

pytestmark = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs a non-root POSIX user so chmod 000 blocks reads",
)


@pytest.fixture
def locked_dir(tmp_path: Path) -> Iterator[Path]:
    saves = tmp_path / "saves"
    (saves / "slots").mkdir(parents=True)
    _ = (saves / "persistent").write_text("p")
    _ = (saves / "slots" / "1-1.save").write_text("slot")
    yield saves
    (saves / "slots").chmod(0o755)


def test_backup_fails_and_keeps_old_backup_when_a_folder_is_unreadable(
    tmp_path: Path, locked_dir: Path
) -> None:
    root = tmp_path / "backups"
    first = backup_saves(str(locked_dir), str(root), 1, 1, now=NOW)

    (locked_dir / "slots").chmod(0)
    with pytest.raises(GameSavesError, match="cannot be read"):
        _ = backup_saves(str(locked_dir), str(root), 1, 1, now=LATER)

    backup_dir = game_backup_dir(str(root), 1)
    assert list_backups(backup_dir) == [first.name]
    assert (Path(first.path) / "slots" / "1-1.save").read_text() == "slot"
    assert not [n for n in os.listdir(backup_dir) if n.endswith(".partial")]


def test_import_fails_when_part_of_the_source_is_unreadable(
    tmp_path: Path, locked_dir: Path
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (locked_dir / "slots").chmod(0)
    with pytest.raises(GameSavesError, match="cannot be read"):
        _ = import_saves(str(locked_dir), str(target), str(tmp_path / "b"), 1, None)
    assert not any(target.iterdir())


def test_copy_folder_contents_raises_when_part_of_the_source_is_unreadable(
    tmp_path: Path, locked_dir: Path
) -> None:
    (locked_dir / "slots").chmod(0)
    with pytest.raises(OSError):
        _ = copy_folder_contents(str(locked_dir), str(tmp_path / "dest"))
