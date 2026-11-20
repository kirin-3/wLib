"""A mirrored deletion keeps attachments so a snapshot restore can bring them back."""

import os
from pathlib import Path

from core import database
from core.database import add_game, get_all_games, init_db
from core.game_attachments import (
    add_attachment,
    attachments_dir,
    held_attachments_dir,
    list_attachments,
)
from core.library_backup import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    SECTION_EXECUTABLE_PATHS,
    SECTION_METADATA,
    apply_library_backup,
    list_sync_snapshots,
    load_library_backup,
    sync_snapshot_path,
)
from core.library_sync import RESTORE_SECTIONS

SECTIONS = [SECTION_METADATA, SECTION_EXECUTABLE_PATHS]


def _backup(games: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "exported_at": "2026-05-05T12:00:00",
        "app": {"name": "wLib", "version": "test"},
        "selected_sections": SECTIONS,
        "games": games,
    }


def _uid(game_id: int) -> str:
    return str(next(game for game in get_all_games() if game["id"] == game_id)["sync_uid"])


def _setup(tmp_path: Path) -> tuple[int, str, str]:
    init_db()
    keep_id = add_game("Keep", "/games/keep/game.sh")
    gone_id = add_game("Gone", "/games/gone/game.sh")
    pdf = tmp_path / "guide.pdf"
    _ = pdf.write_bytes(b"%PDF-1.4\nwalkthrough")
    _ = add_attachment(gone_id, str(pdf))
    pdf.unlink()
    return gone_id, _uid(keep_id), _uid(gone_id)


def _pull_deletion(keep_uid: str) -> None:
    keep = {
        "uid": keep_uid,
        "metadata": {"title": "Keep"},
        "executable_paths": {"exe_path": "/games/keep/game.sh"},
    }
    result = apply_library_backup(
        _backup([keep]), {"sections": SECTIONS}, mirror=True, sync=True
    )
    assert result["deleted"] == 1


def test_snapshot_restore_brings_back_attachments_of_a_mirrored_deletion(tmp_path):
    gone_id, keep_uid, gone_uid = _setup(tmp_path)

    _pull_deletion(keep_uid)

    assert not os.path.exists(attachments_dir(gone_id))
    assert os.path.isfile(os.path.join(held_attachments_dir(), gone_uid, "guide.pdf"))

    snapshot = list_sync_snapshots()[-1]["name"]
    path = sync_snapshot_path(snapshot)
    assert path is not None
    _ = apply_library_backup(
        load_library_backup(path), {"sections": list(RESTORE_SECTIONS)}, mirror=True, sync=True
    )

    restored = next(game for game in get_all_games() if game["sync_uid"] == gone_uid)
    assert restored["id"] != gone_id
    assert list_attachments(int(restored["id"])) == [
        {"name": "guide.pdf", "size": len(b"%PDF-1.4\nwalkthrough")}
    ]
    assert not os.path.exists(os.path.join(held_attachments_dir(), gone_uid))


def test_held_attachments_go_once_no_snapshot_names_the_game(tmp_path):
    _gone_id, keep_uid, gone_uid = _setup(tmp_path)
    _pull_deletion(keep_uid)
    held = os.path.join(held_attachments_dir(), gone_uid)
    assert os.path.isdir(held)

    # Later syncs push the snapshot that names the removed game out.
    for snapshot in list_sync_snapshots():
        path = sync_snapshot_path(snapshot["name"])
        assert path is not None
        os.remove(path)
    keep = {
        "uid": keep_uid,
        "metadata": {"title": "Keep"},
        "executable_paths": {"exe_path": "/games/keep/game.sh"},
    }
    _ = apply_library_backup(_backup([keep]), {"sections": SECTIONS}, mirror=True, sync=True)

    assert not os.path.exists(held)
    assert os.path.isdir(os.path.join(database.DATA_DIR, "attachments"))
