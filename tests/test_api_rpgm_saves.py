# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import shutil
from pathlib import Path

import pytest

from core.api import Api
from core.database import add_game, init_db
from core.rpgm_saves import decode_save

FIXTURE = Path(__file__).parent / "fixtures" / "rpgm_saves" / "mv" / "file1.rpgsave"


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
