"""A failed attachment hold after a mirrored deletion must not delete the files."""

import os
from pathlib import Path

import pytest

from core import game_attachments
from core.database import add_game, get_all_games, init_db
from core.game_attachments import (
    add_attachment,
    attachments_dir,
    held_attachments_dir,
    list_attachments,
    prune_orphan_attachments,
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
PDF = b"%PDF-1.4\nwalkthrough"


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
    return str(
        next(game for game in get_all_games() if game["id"] == game_id)["sync_uid"]
    )


def _setup(tmp_path: Path) -> tuple[int, str, str]:
    init_db()
    keep_id = add_game("Keep", "/games/keep/game.sh")
    gone_id = add_game("Gone", "/games/gone/game.sh")
    pdf = tmp_path / "guide.pdf"
    _ = pdf.write_bytes(PDF)
    _ = add_attachment(gone_id, str(pdf))
    pdf.unlink()
    return gone_id, _uid(keep_id), _uid(gone_id)


def _pull_deletion_with_failed_hold(
    monkeypatch: pytest.MonkeyPatch, keep_uid: str
) -> None:
    real_hold = game_attachments.hold_game_attachments
    calls = {"count": 0}

    def failing_once(game_id: int, sync_uid: object) -> bool:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("disk full")
        return real_hold(game_id, sync_uid)

    monkeypatch.setattr(game_attachments, "hold_game_attachments", failing_once)
    keep = {
        "uid": keep_uid,
        "metadata": {"title": "Keep"},
        "executable_paths": {"exe_path": "/games/keep/game.sh"},
    }
    result = apply_library_backup(
        _backup([keep]), {"sections": SECTIONS}, mirror=True, sync=True
    )
    assert result["deleted"] == 1
    assert calls["count"] == 1
    monkeypatch.setattr(game_attachments, "hold_game_attachments", real_hold)


def _restore_last_snapshot() -> None:
    snapshot = list_sync_snapshots()[-1]["name"]
    path = sync_snapshot_path(snapshot)
    assert path is not None
    _ = apply_library_backup(
        load_library_backup(path),
        {"sections": list(RESTORE_SECTIONS)},
        mirror=True,
        sync=True,
    )


def _restored_attachments(gone_id: int, gone_uid: str) -> list[dict[str, object]]:
    restored = next(game for game in get_all_games() if game["sync_uid"] == gone_uid)
    assert restored["id"] != gone_id
    return list_attachments(int(restored["id"]))


def test_failed_hold_keeps_files_through_restart_and_restore(tmp_path, monkeypatch):
    gone_id, keep_uid, gone_uid = _setup(tmp_path)

    _pull_deletion_with_failed_hold(monkeypatch, keep_uid)

    # The prune after the mirror must not delete the folder whose hold failed.
    assert os.path.isfile(os.path.join(attachments_dir(gone_id), "guide.pdf"))

    # The startup prune retries the hold instead of deleting the files.
    live_ids = {int(str(game["id"])) for game in get_all_games()}
    prune_orphan_attachments(live_ids)
    assert not os.path.exists(attachments_dir(gone_id))
    held = os.path.join(held_attachments_dir(), gone_uid)
    assert sorted(os.listdir(held)) == ["guide.pdf"]

    _restore_last_snapshot()

    assert _restored_attachments(gone_id, gone_uid) == [
        {"name": "guide.pdf", "size": len(PDF)}
    ]
    assert not os.path.exists(held)


def test_restore_reclaims_files_whose_hold_failed(tmp_path, monkeypatch):
    gone_id, keep_uid, gone_uid = _setup(tmp_path)

    _pull_deletion_with_failed_hold(monkeypatch, keep_uid)
    _restore_last_snapshot()

    assert _restored_attachments(gone_id, gone_uid) == [
        {"name": "guide.pdf", "size": len(PDF)}
    ]
    assert not os.path.exists(attachments_dir(gone_id))


def test_failed_marker_write_still_keeps_files_after_restart(tmp_path, monkeypatch):
    gone_id, keep_uid, gone_uid = _setup(tmp_path)

    def no_marker(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk full")

    # The marker cannot be written either (the same full drive).
    monkeypatch.setattr(game_attachments, "open", no_marker, raising=False)
    _pull_deletion_with_failed_hold(monkeypatch, keep_uid)
    monkeypatch.delattr(game_attachments, "open")
    assert not os.path.exists(
        os.path.join(attachments_dir(gone_id), game_attachments.PENDING_HOLD_MARKER)
    )

    # A restart forgets the in-memory record; the setting still has it.
    game_attachments._pending_in_memory.clear()  # pyright: ignore[reportPrivateUsage]
    live_ids = {int(str(game["id"])) for game in get_all_games()}
    prune_orphan_attachments(live_ids)

    held = os.path.join(held_attachments_dir(), gone_uid)
    assert sorted(os.listdir(held)) == ["guide.pdf"]
    assert game_attachments._pending_records() == {}  # pyright: ignore[reportPrivateUsage]


def test_marker_moves_last_when_a_merge_fails(tmp_path, monkeypatch):
    init_db()
    uid = "a" * 32
    source = attachments_dir(42)
    os.makedirs(source)
    for name in ("guide.pdf", "locked.pdf"):
        _ = Path(source, name).write_bytes(PDF)
    _ = Path(source, game_attachments.PENDING_HOLD_MARKER).write_text(uid)
    # An earlier partial merge left the held folder in place.
    held = os.path.join(held_attachments_dir(), uid)
    os.makedirs(held)
    real_replace = os.replace

    def locked(src: str, dst: str) -> None:
        if os.path.basename(src) == "locked.pdf":
            raise OSError("file is locked")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(OSError):
        _ = game_attachments.hold_game_attachments(42, uid)
    monkeypatch.setattr(os, "replace", real_replace)

    # The source keeps its marker, so the next prune does not delete it.
    assert os.path.isfile(os.path.join(source, game_attachments.PENDING_HOLD_MARKER))
    prune_orphan_attachments(set())
    assert sorted(os.listdir(held)) == ["guide.pdf", "locked.pdf"]
    assert not os.path.exists(source)
