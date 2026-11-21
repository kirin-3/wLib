# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import shutil
from pathlib import Path

import pytest

from core.api import Api
from core.api_mixins.saves import _write_source_note
from core.database import add_game, init_db
from core.rpgm_saves import decode_save
from tests.test_rgss_tools import build_rgss_v3

FIXTURE = Path(__file__).parent / "fixtures" / "rpgm_saves" / "mv" / "file1.rpgsave"
RGSS_FIXTURES = Path(__file__).parent / "fixtures" / "rgss"


@pytest.fixture(autouse=True)
def _isolated(tmp_path_factory, monkeypatch):
    db_dir = tmp_path_factory.mktemp("rpgm-saves-db")
    monkeypatch.setattr("core.database.DB_PATH", str(db_dir / "saves.db"))
    init_db()
    backups = tmp_path_factory.mktemp("save-backups")
    monkeypatch.setattr(Api, "_save_backups_root", staticmethod(lambda: str(backups)))


def mv_game(tmp_path: Path) -> tuple[Path, int]:
    folder = tmp_path / "MV"
    data = folder / "www" / "data"
    data.mkdir(parents=True)
    (data / "System.json").write_text(json.dumps({"gameTitle": "T"}), encoding="utf-8")
    save = folder / "www" / "save"
    save.mkdir()
    shutil.copy(FIXTURE, save / "file1.rpgsave")
    exe = folder / "Game.exe"
    exe.touch()
    game_id = add_game("MV", str(exe))
    assert game_id is not None
    return folder, game_id


def loaded(api: Api, game_id: int) -> dict:
    result = api.load_rpgm_save(game_id, "file1.rpgsave")
    assert result["success"], result
    return result


def test_unknown_and_non_rpgm_game(tmp_path):
    api = Api()
    assert api.list_rpgm_saves(9999)["success"] is False
    assert api.load_rpgm_save(9999, "file1.rpgsave")["success"] is False
    plain = tmp_path / "plain"
    plain.mkdir()
    exe = plain / "game.sh"
    exe.touch()
    game_id = add_game("Plain", str(exe))
    result = api.list_rpgm_saves(game_id)
    assert result["success"] is False and result["error_code"] == "unsupported_engine"


def test_list_and_load(tmp_path):
    _folder, game_id = mv_game(tmp_path)
    api = Api()
    listing = api.list_rpgm_saves(game_id)
    assert listing["success"]
    assert [s["file"] for s in listing["slots"]] == ["file1.rpgsave"]
    assert all("folder" not in s for s in listing["slots"])
    payload = loaded(api, game_id)
    assert payload["format"] == "mv" and payload["view"]["missing"] == []
    assert isinstance(payload["mtime_ns"], str)
    traversal = api.load_rpgm_save(game_id, "../data/System.json")
    assert traversal["success"] is False


def test_load_unsupported_format(tmp_path):
    folder, game_id = mv_game(tmp_path)
    (folder / "www" / "save" / "file2.rpgsave").write_bytes(b"not a save")
    result = Api().load_rpgm_save(game_id, "file2.rpgsave")
    assert result["success"] is False and result["error_code"] == "unsupported_format"


def test_write_success_shows_in_backups(tmp_path):
    folder, game_id = mv_game(tmp_path)
    api = Api()
    payload = loaded(api, game_id)
    path = folder / "www" / "save" / "file1.rpgsave"
    before = path.read_bytes()
    result = api.write_rpgm_save(
        game_id,
        "file1.rpgsave",
        [{"op": "set_actor", "id": 1, "field": "hp", "value": 1234}],
        payload["size"],
        payload["mtime_ns"],
    )
    assert result["success"], result
    assert result["backup"] in [
        b["name"] for b in api.get_game_saves(game_id)["backups"]
    ]
    tree = decode_save(path.read_bytes(), "mv")
    assert tree["actors"]["_data"]["@a"][1]["_hp"] == 1234
    backup_dir = Path(api._save_backups_root()) / str(game_id) / result["backup"]
    assert (backup_dir / "file1.rpgsave").read_bytes() == before


def test_write_refusals(tmp_path, monkeypatch):
    folder, game_id = mv_game(tmp_path)
    api = Api()
    payload = loaded(api, game_id)
    path = folder / "www" / "save" / "file1.rpgsave"
    patch = [{"op": "set_actor", "id": 1, "field": "hp", "value": 5}]
    args = (game_id, "file1.rpgsave")
    original = path.read_bytes()

    bad = api.write_rpgm_save(
        *args, [{"op": "nope"}], payload["size"], payload["mtime_ns"]
    )
    assert bad["error_code"] == "invalid_patch"
    malformed = api.write_rpgm_save(*args, "x", payload["size"], payload["mtime_ns"])
    assert malformed["error_code"] == "invalid_patch"

    stale = api.write_rpgm_save(*args, patch, payload["size"] + 1, payload["mtime_ns"])
    assert stale["error_code"] == "changed"

    with monkeypatch.context() as scoped:
        scoped.setattr(api, "get_running_games", lambda: [game_id])
        running = api.write_rpgm_save(
            *args, patch, payload["size"], payload["mtime_ns"]
        )
    assert running["error_code"] == "game_running"

    monkeypatch.setattr(Api, "_save_backups_root", staticmethod(lambda: str(path)))
    failed = api.write_rpgm_save(*args, patch, payload["size"], payload["mtime_ns"])
    assert failed["success"] is False and failed["error_code"] == "backup_failed"
    assert path.read_bytes() == original


def test_write_busy(tmp_path, monkeypatch):
    _folder, game_id = mv_game(tmp_path)
    api = Api()
    payload = loaded(api, game_id)
    api._saves_busy.add(game_id)
    result = api.write_rpgm_save(
        game_id, "file1.rpgsave", [], payload["size"], payload["mtime_ns"]
    )
    assert result["error_code"] == "busy"
    api._saves_busy.discard(game_id)


def test_load_allowed_while_running(tmp_path, monkeypatch):
    _folder, game_id = mv_game(tmp_path)
    api = Api()
    monkeypatch.setattr(api, "get_running_games", lambda: [game_id])
    assert api.load_rpgm_save(game_id, "file1.rpgsave")["success"]
    assert os.path.exists(str(tmp_path))


def _rgss_game(
    tmp_path: Path, engine: str, save_file: str, *, subfolder: str = ""
) -> tuple[Path, int]:
    folder = tmp_path / f"RGSS_{engine}_{subfolder or 'root'}"
    folder.mkdir(parents=True)
    shutil.copytree(RGSS_FIXTURES / engine / "Data", folder / "Data")
    # Also place a Scripts.<ext> so is_rgss_folder detects it cleanly
    ext = {"xp": ".rxdata", "vx": ".rvdata", "vxace": ".rvdata2"}[engine]
    (folder / "Data" / f"Scripts{ext}").write_bytes(b"\x04\x08[\x00")
    target_dir = folder / subfolder if subfolder else folder
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(RGSS_FIXTURES / engine / save_file, target_dir / save_file)
    exe = folder / "Game.exe"
    exe.touch()
    game_id = add_game(f"RGSS-{engine}", str(exe))
    assert game_id is not None
    return folder, game_id


@pytest.mark.parametrize(
    ("engine", "save_file", "expected_gold"),
    [
        ("xp", "Save1.rxdata", 750),
        ("vx", "Save1.rvdata", 120),
        ("vxace", "Save01.rvdata2", 2500),
    ],
)
def test_rgss_list_load_and_write_per_engine(
    tmp_path: Path, engine: str, save_file: str, expected_gold: int
) -> None:
    folder, game_id = _rgss_game(tmp_path, engine, save_file)
    api = Api()

    listing = api.list_rpgm_saves(game_id)
    assert listing["success"]
    assert [s["file"] for s in listing["slots"]] == [save_file]
    assert listing["slots"][0]["format"] == engine
    assert all("folder" not in s and "relative" not in s for s in listing["slots"])

    loaded_res = api.load_rpgm_save(game_id, save_file)
    assert loaded_res["success"], loaded_res
    assert loaded_res["format"] == engine
    assert loaded_res["view"]["gold"] == expected_gold
    gold_ref = loaded_res["view"]["gold_ref"]
    assert isinstance(gold_ref, int)

    write_res = api.write_rpgm_save(
        game_id,
        save_file,
        [{"op": "set_node", "node": gold_ref, "value": 9999}],
        loaded_res["size"],
        loaded_res["mtime_ns"],
    )
    assert write_res["success"], write_res
    assert write_res["backup"] in [
        b["name"] for b in api.get_game_saves(game_id)["backups"]
    ]

    reloaded = api.load_rpgm_save(game_id, save_file)
    assert reloaded["success"]
    assert reloaded["view"]["gold"] == 9999


def test_rgss_running_and_busy_refusals(tmp_path: Path, monkeypatch) -> None:
    _folder, game_id = _rgss_game(tmp_path, "vxace", "Save01.rvdata2")
    api = Api()
    payload = api.load_rpgm_save(game_id, "Save01.rvdata2")
    assert payload["success"]

    with monkeypatch.context() as scoped:
        scoped.setattr(api, "get_running_games", lambda: [game_id])
        running = api.write_rpgm_save(
            game_id,
            "Save01.rvdata2",
            [{"op": "set_switch", "id": 1, "value": False}],
            payload["size"],
            payload["mtime_ns"],
        )
    assert running["success"] is False and running["error_code"] == "game_running"

    api._saves_busy.add(game_id)
    try:
        busy = api.write_rpgm_save(
            game_id,
            "Save01.rvdata2",
            [{"op": "set_switch", "id": 1, "value": False}],
            payload["size"],
            payload["mtime_ns"],
        )
    finally:
        api._saves_busy.discard(game_id)
    assert busy["success"] is False and busy["error_code"] == "busy"


def test_rgss_exe_folder_backup_excludes_game_files_and_restores(
    tmp_path: Path,
) -> None:
    folder, game_id = _rgss_game(tmp_path, "vxace", "Save01.rvdata2")
    build_rgss_v3(folder / "Game.rgss3a", [("Data/Test.txt", b"hello")])
    (folder / "Graphics").mkdir()
    (folder / "Graphics" / "Title.png").write_bytes(b"PNGDATA")
    other_file = folder / "readme.txt"
    other_file.write_text("untouched", encoding="utf-8")

    save_path = folder / "Save01.rvdata2"
    original_bytes = save_path.read_bytes()

    api = Api()
    loaded_res = api.load_rpgm_save(game_id, "Save01.rvdata2")
    gold_ref = loaded_res["view"]["gold_ref"]
    write_res = api.write_rpgm_save(
        game_id,
        "Save01.rvdata2",
        [{"op": "set_node", "node": gold_ref, "value": 77777}],
        loaded_res["size"],
        loaded_res["mtime_ns"],
    )
    assert write_res["success"], write_res
    backup_name = write_res["backup"]
    backup_path = Path(api._save_backups_root()) / str(game_id) / backup_name
    # Backup holds ONLY loose save files, never Game.rgss3a or Graphics/
    assert (backup_path / "Save01.rvdata2").read_bytes() == original_bytes
    assert not (backup_path / "Game.rgss3a").exists()
    assert not (backup_path / "Graphics").exists()
    assert not (backup_path / "Game.exe").exists()

    # Restore puts old save back next to Game.exe without touching other game files
    restore_res = api.restore_game_saves_backup(game_id, backup_name)
    assert restore_res["success"], restore_res
    assert save_path.read_bytes() == original_bytes
    assert other_file.read_text(encoding="utf-8") == "untouched"
    assert (folder / "Graphics" / "Title.png").read_bytes() == b"PNGDATA"
    # Safety backup created during restore ALSO excludes Game.rgss3a and Graphics/
    safety = Path(api._save_backups_root()) / str(game_id) / restore_res["backup"]
    assert (safety / "Save01.rvdata2").exists()
    assert not (safety / "Game.rgss3a").exists()
    assert not (safety / "Graphics").exists()


def test_rgss_subfolder_restore_bad_relative_and_missing_folder(
    tmp_path: Path,
) -> None:
    folder, game_id = _rgss_game(tmp_path, "vx", "Save1.rvdata", subfolder="Saves")
    eff_dir = tmp_path / "override_saves"
    eff_dir.mkdir()
    api = Api()
    assert api.set_game_save_folder(game_id, str(eff_dir))["success"]

    save_path = folder / "Saves" / "Save1.rvdata"
    original_bytes = save_path.read_bytes()

    loaded_res = api.load_rpgm_save(game_id, "Save1.rvdata")
    gold_ref = loaded_res["view"]["gold_ref"]
    write_res = api.write_rpgm_save(
        game_id,
        "Save1.rvdata",
        [{"op": "set_node", "node": gold_ref, "value": 4242}],
        loaded_res["size"],
        loaded_res["mtime_ns"],
    )
    assert write_res["success"], write_res
    backup_name = write_res["backup"]

    # Restoring goes back into <exe_folder>/Saves even with an override set
    restore_res = api.restore_game_saves_backup(game_id, backup_name)
    assert restore_res["success"], restore_res
    assert save_path.read_bytes() == original_bytes
    assert not (eff_dir / "Save1.rvdata").exists()

    # If Saves/ is deleted, restore refuses and tells user to copy by hand
    shutil.rmtree(folder / "Saves")
    missing_res = api.restore_game_saves_backup(game_id, backup_name)
    assert missing_res["success"] is False
    assert "by hand" in str(missing_res["error"]).lower()

    # A bad relative value in the source note is ignored -> treated as effective-folder backup
    backup_dir = Path(api._save_backups_root()) / str(game_id)
    _write_source_note(str(backup_dir), backup_name, "rgss_game_save:../outside")
    eff_restore = api.restore_game_saves_backup(game_id, backup_name)
    assert eff_restore["success"], eff_restore
    assert (eff_dir / "Save1.rvdata").read_bytes() == original_bytes
