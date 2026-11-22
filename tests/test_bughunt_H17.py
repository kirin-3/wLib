# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
"""H17: a save-editor backup restores into the folder it came from."""

import json
import shutil
from pathlib import Path

import pytest

from core.api import Api
from core.database import add_game, init_db

FIXTURE = Path(__file__).parent / "fixtures" / "rpgm_saves" / "mv" / "file1.rpgsave"
PATCH = [{"op": "set_actor", "id": 1, "field": "hp", "value": 1234}]


@pytest.fixture(autouse=True)
def _isolated(tmp_path_factory, monkeypatch):
    db_dir = tmp_path_factory.mktemp("h17-db")
    monkeypatch.setattr("core.database.DB_PATH", str(db_dir / "saves.db"))
    init_db()
    backups = tmp_path_factory.mktemp("h17-backups")
    monkeypatch.setattr(Api, "_save_backups_root", staticmethod(lambda: str(backups)))


def _game_with_override(tmp_path: Path) -> tuple[Api, int, Path, Path]:
    folder = tmp_path / "MV"
    data = folder / "www" / "data"
    data.mkdir(parents=True)
    (data / "System.json").write_text(json.dumps({"gameTitle": "T"}), encoding="utf-8")
    local = folder / "www" / "save"
    local.mkdir()
    shutil.copy(FIXTURE, local / "file1.rpgsave")
    exe = folder / "Game.exe"
    exe.touch()
    game_id = add_game("MV", str(exe))
    assert game_id is not None
    external = tmp_path / "external"
    external.mkdir()
    (external / "global.rpgsave").write_text("external global", encoding="utf-8")
    (external / "file2.rpgsave").write_text("external slot", encoding="utf-8")
    api = Api()
    assert api.set_game_save_folder(game_id, str(external))["success"]
    return api, game_id, local, external


def _edit(api: Api, game_id: int) -> str:
    payload = api.load_rpgm_save(game_id, "file1.rpgsave")
    assert payload["success"], payload
    result = api.write_rpgm_save(
        game_id, "file1.rpgsave", PATCH, payload["size"], payload["mtime_ns"]
    )
    assert result["success"], result
    return str(result["backup"])


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


def test_editor_backup_restores_into_the_game_save_folder(tmp_path):
    api, game_id, local, external = _game_with_override(tmp_path)
    original = (local / "file1.rpgsave").read_bytes()
    external_before = _snapshot(external)
    backup = _edit(api, game_id)
    assert (local / "file1.rpgsave").read_bytes() != original

    result = api.restore_game_saves_backup(game_id, backup)

    assert result["success"], result
    assert (local / "file1.rpgsave").read_bytes() == original
    assert _snapshot(external) == external_before
    # The note is not a backup and does not end up in a saves folder.
    names = [b["name"] for b in api.get_game_saves(game_id)["backups"]]
    assert all(not n.startswith(".") for n in names)
    assert not any(p.name.startswith(".") for p in local.iterdir())


def test_safety_backup_of_the_game_save_folder_also_restores_there(tmp_path):
    api, game_id, local, external = _game_with_override(tmp_path)
    backup = _edit(api, game_id)
    edited = (local / "file1.rpgsave").read_bytes()
    external_before = _snapshot(external)
    restored = api.restore_game_saves_backup(game_id, backup)
    assert restored["success"], restored
    safety = str(restored["backup"])
    assert safety

    # Undo the restore with the backup the restore made first.
    undo = api.restore_game_saves_backup(game_id, safety)

    assert undo["success"], undo
    assert (local / "file1.rpgsave").read_bytes() == edited
    assert _snapshot(external) == external_before


def test_normal_backup_still_restores_into_the_effective_folder(tmp_path):
    api, game_id, local, external = _game_with_override(tmp_path)
    backup = api.backup_game_saves(game_id)
    assert backup["success"], backup
    (external / "file2.rpgsave").write_text("changed", encoding="utf-8")
    local_before = _snapshot(local)

    result = api.restore_game_saves_backup(game_id, backup["backup"])

    assert result["success"], result
    assert (external / "file2.rpgsave").read_text(encoding="utf-8") == "external slot"
    assert _snapshot(local) == local_before


def test_editor_backup_without_its_folder_is_refused(tmp_path):
    api, game_id, local, external = _game_with_override(tmp_path)
    backup = _edit(api, game_id)
    shutil.rmtree(local.parent / "data")
    external_before = _snapshot(external)

    result = api.restore_game_saves_backup(game_id, backup)

    assert result["success"] is False
    assert _snapshot(external) == external_before


def test_stale_note_does_not_redirect_a_reused_backup_name(tmp_path):
    api, game_id, local, external = _game_with_override(tmp_path)
    backup = _edit(api, game_id)
    backup_dir = Path(api._save_backups_root()) / str(game_id)
    # Same name, different folder: the note no longer matches it.
    shutil.rmtree(backup_dir / backup)
    shutil.copytree(external, backup_dir / backup)
    (external / "file2.rpgsave").write_text("changed", encoding="utf-8")
    local_before = _snapshot(local)

    result = api.restore_game_saves_backup(game_id, backup)

    assert result["success"], result
    assert (external / "file2.rpgsave").read_text(encoding="utf-8") == "external slot"
    assert _snapshot(local) == local_before


def _backup_names(api: Api, game_id: int) -> list[str]:
    return [str(b["name"]) for b in api.get_game_saves(game_id)["backups"]]


def test_safety_backup_of_a_failed_restore_restores_into_its_folder(
    tmp_path, monkeypatch
):
    import core.game_saves

    api, game_id, local, external = _game_with_override(tmp_path)
    backup = _edit(api, game_id)
    edited = (local / "file1.rpgsave").read_bytes()
    external_before = _snapshot(external)
    real_copy = core.game_saves._copy_tree

    def failing_merge(*args, **kwargs):
        if kwargs.get("touch"):
            raise OSError("disk full")
        return real_copy(*args, **kwargs)

    monkeypatch.setattr(core.game_saves, "_copy_tree", failing_merge)
    failed = api.restore_game_saves_backup(game_id, backup)
    assert failed["success"] is False
    monkeypatch.setattr(core.game_saves, "_copy_tree", real_copy)
    safety = [name for name in _backup_names(api, game_id) if name != backup]
    assert len(safety) == 1
    assert safety[0] in str(failed["error"])
    (local / "file1.rpgsave").write_bytes(b"partly restored")

    undo = api.restore_game_saves_backup(game_id, safety[0])

    assert undo["success"], undo
    assert (local / "file1.rpgsave").read_bytes() == edited
    assert _snapshot(external) == external_before


def test_restore_stops_when_the_safety_backup_folder_cannot_be_noted(
    tmp_path, monkeypatch
):
    import core.api_mixins.saves

    api, game_id, local, external = _game_with_override(tmp_path)
    backup = _edit(api, game_id)
    local_before = _snapshot(local)
    external_before = _snapshot(external)
    names_before = _backup_names(api, game_id)

    def failing_note(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(core.api_mixins.saves, "_write_source_note", failing_note)
    result = api.restore_game_saves_backup(game_id, backup)

    assert result["success"] is False
    assert _snapshot(local) == local_before
    assert _snapshot(external) == external_before
    # No safety backup without its folder note is left behind.
    assert _backup_names(api, game_id) == names_before


def test_restore_drops_the_notes_of_backups_its_prune_removed(tmp_path, monkeypatch):
    import core.api_mixins.saves
    import core.database

    api, game_id, _local, _external = _game_with_override(tmp_path)
    backup = _edit(api, game_id)
    backup_dir = Path(api._save_backups_root()) / str(game_id)
    old = "2000-01-01_00-00-00"
    (backup_dir / old).mkdir()
    (backup_dir / old / "file1.rpgsave").write_text("old", encoding="utf-8")
    old_note = backup_dir / f".{old}{core.api_mixins.saves._NOTE_SUFFIX}"
    old_note.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(core.database, "get_save_backup_keep", lambda: 1)

    result = api.restore_game_saves_backup(game_id, backup)

    assert result["success"], result
    assert not (backup_dir / old).exists()
    assert not old_note.exists()
