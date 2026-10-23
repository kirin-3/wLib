# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import threading
from pathlib import Path

import pytest

from core.api import Api
from core.database import add_game, init_db
from core.game_tools import detect_engine, game_tool_folder
from test_renpy_tools import build_rpa
from test_rpgm_tools import KEY, PNG, encrypt, system_json


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path_factory, monkeypatch):
    db_dir = tmp_path_factory.mktemp("tools-db")
    monkeypatch.setattr("core.database.DB_PATH", str(db_dir / "tools.db"))
    init_db()


def renpy_game(tmp_path: Path) -> tuple[Path, int]:
    folder = tmp_path / "RenPy"
    (folder / "renpy").mkdir(parents=True)
    (folder / "game").mkdir()
    exe = folder / "game.sh"
    exe.touch()
    game_id = add_game("RenPy", str(exe))
    assert game_id is not None
    return folder, game_id


def mv_game(tmp_path: Path) -> tuple[Path, int]:
    folder = tmp_path / "MV"
    root = folder / "www"
    system_json(root)
    for rel in ("img/faces/Actor1.rpgmvp", "audio/bgm/Theme.rpgmvo"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypt(PNG))
    exe = folder / "Game.exe"
    exe.touch()
    game_id = add_game("MV", str(exe))
    assert game_id is not None
    return folder, game_id


# ── 1.1 Folder and engine detection ──


def test_detect_engine_variants(tmp_path):
    renpy, _ = renpy_game(tmp_path)
    mv, _ = mv_game(tmp_path)
    mz = tmp_path / "MZ"
    system_json(mz)
    unity = tmp_path / "Unity"
    (unity / "Game_Data").mkdir(parents=True)

    assert detect_engine(str(renpy)) == "renpy"
    assert detect_engine(str(mv)) == "rpgm"
    assert detect_engine(str(mz)) == "rpgm"
    assert detect_engine(str(unity)) == ""
    assert detect_engine(str(tmp_path / "missing")) == ""
    assert detect_engine("") == ""
    with pytest.raises(ValueError):
        _ = game_tool_folder({"exe_path": ""})
    assert game_tool_folder({"exe_path": str(renpy / "game.sh")}) == str(renpy)


# ── 1.3 Support ──


def test_tool_support(tmp_path):
    api = Api()
    _, renpy_id = renpy_game(tmp_path)
    _, mv_id = mv_game(tmp_path)
    unity = tmp_path / "Unity"
    unity.mkdir()
    unity_id = add_game("Unity", str(unity / "game.exe"))
    wishlist_id = add_game("Wish", "")

    assert api.get_game_tool_support(renpy_id) == {"success": True, "engine": "renpy"}
    assert api.get_game_tool_support(mv_id) == {"success": True, "engine": "rpgm"}
    assert api.get_game_tool_support(unity_id)["engine"] == ""
    assert api.get_game_tool_support(wishlist_id) == {"success": True, "engine": ""}
    assert api.get_game_tool_support(999)["success"] is False
    assert api.get_game_tool_support(None)["success"] is False
    assert api.get_game_tools(999)["success"] is False


# ── 1.2 Status, busy rule, running games ──


def test_tool_run_rejects_second_run_and_resets_on_error(tmp_path):
    from core.game_tools import GameToolError

    api = Api()
    with api._tool_run(1, "extract"):
        assert api.get_game_tool_status(1)["running"] is True
        assert api.get_game_tool_status(1)["action"] == "extract"
        with pytest.raises(GameToolError, match="already running"):
            with api._tool_run(1, "decrypt"):
                pytest.fail("second run must be rejected")
        with api._tool_run(2, "decrypt"):
            pass
    assert api.get_game_tool_status(1)["running"] is False

    with pytest.raises(RuntimeError):
        with api._tool_run(1, "extract"):
            raise RuntimeError("boom")
    assert api.get_game_tool_status(1)["running"] is False
    with api._tool_run(1, "extract"):
        pass


def test_tool_run_rejects_running_game(tmp_path, monkeypatch):
    from core.game_tools import GameToolError

    api = Api()
    monkeypatch.setattr(api, "get_running_games", lambda: [5])
    with pytest.raises(GameToolError, match="Close the game"):
        with api._tool_run(5, "decrypt"):
            pytest.fail("running game must be rejected")
    assert api.get_game_tool_status(5)["running"] is False


def test_cancel_sets_flag_the_callback_reads():
    api = Api()
    assert api.cancel_game_tool(1)["success"] is False
    with api._tool_run(1, "extract"):
        cancelled = api._tool_cancelled(1)
        progress = api._tool_progress(1)
        assert cancelled() is False
        progress(3, 10, "a.png")
        status = api.get_game_tool_status(1)
        assert (status["done"], status["total"], status["current"]) == (3, 10, "a.png")
        assert api.cancel_game_tool(1)["success"] is True
        assert cancelled() is True
    assert api.get_game_tool_status(1)["cancel"] is False


def test_actions_refuse_running_game_and_change_nothing(tmp_path, monkeypatch):
    api = Api()
    folder, game_id = renpy_game(tmp_path)
    monkeypatch.setattr(api, "get_running_games", lambda: [game_id])
    result = api.set_renpy_console(game_id, True, False)
    assert result["success"] is False
    assert "Close the game" in str(result["error"])
    assert not (folder / "game" / "zzz_wlib_console.rpy").exists()


# ── 2.2 Console ──


def test_console_api(tmp_path):
    api = Api()
    folder, game_id = renpy_game(tmp_path)
    rpy = folder / "game" / "zzz_wlib_console.rpy"

    assert api.set_renpy_console(game_id, True, False)["state"] == "on"
    assert "config.console = True" in rpy.read_text(encoding="utf-8")
    assert api.get_game_tools(game_id)["renpy"]["console"] == "on"
    assert api.set_renpy_console(game_id, True, True)["state"] == "developer"
    assert api.set_renpy_console(game_id, False, False)["state"] == "off"
    assert not rpy.exists()

    assert api.set_renpy_console(game_id, "yes", False)["success"] is False
    assert api.set_renpy_console(999, True, False)["success"] is False
    _, mv_id = mv_game(tmp_path)
    rejected = api.set_renpy_console(mv_id, True, False)
    assert rejected["success"] is False
    assert not (tmp_path / "MV" / "game").exists()


# ── 3.4 Extraction ──


def test_extract_api_both_modes_and_listing(tmp_path):
    api = Api()
    folder, game_id = renpy_game(tmp_path)
    build_rpa(folder / "game" / "images.rpa", {"bg/room.png": b"png", "s.rpyc": b"c"})
    (folder / "game" / "bad.rpa").write_bytes(b"RPA-9.9 0\n")
    build_rpa(folder / "game" / "0x52_URM.rpa", {"u": b"u"})

    tools = api.get_game_tools(game_id)
    listed = {a["name"]: a for a in tools["renpy"]["archives"]}
    assert set(listed) == {"images.rpa", "bad.rpa"}
    assert listed["images.rpa"]["format"] == "RPA-3.0"
    assert "path" not in listed["images.rpa"]
    assert tools["renpy"]["urm"]["renpy"] is True

    out = tmp_path / "rip"
    out.mkdir()
    result = api.extract_renpy_archives(game_id, {"mode": "folder", "output": str(out)})
    assert result["success"] is True
    assert result["files"] == 2
    assert result["unsupported"] == ["bad.rpa"]
    assert (out / "images" / "bg" / "room.png").read_bytes() == b"png"
    assert api.get_game_tool_status(game_id)["running"] is False

    placed = api.extract_renpy_archives(game_id, {"mode": "in_place"})
    assert placed["success"] is True
    assert placed["files"] == 1
    assert placed["skipped_scripts"] == 1
    assert (folder / "game" / "bg" / "room.png").read_bytes() == b"png"


def test_extract_api_rejects_bad_output_and_non_renpy(tmp_path):
    api = Api()
    folder, game_id = renpy_game(tmp_path)
    build_rpa(folder / "game" / "images.rpa", {"a": b"1"})
    for options in (
        {"mode": "folder", "output": str(folder / "game")},
        {"mode": "folder", "output": "relative"},
        {"mode": "folder", "output": str(tmp_path / "missing")},
        {"mode": "folder"},
        {"mode": "weird"},
        None,
    ):
        assert api.extract_renpy_archives(game_id, options)["success"] is False
    assert sorted(p.name for p in (folder / "game").iterdir()) == ["images.rpa"]
    _, mv_id = mv_game(tmp_path)
    assert api.extract_renpy_archives(mv_id, {"mode": "in_place"})["success"] is False


def test_extract_api_blocks_a_concurrent_run(tmp_path):
    api = Api()
    folder, game_id = renpy_game(tmp_path)
    build_rpa(folder / "game" / "images.rpa", {"a": b"1"})
    with api._tool_run(game_id, "decrypt"):
        result = api.extract_renpy_archives(game_id, {"mode": "in_place"})
    assert result["success"] is False
    assert "already running" in str(result["error"])
    assert not (folder / "game" / "a").exists()


# ── 4.4 RPG Maker ──


def test_rpgm_api_decrypt_and_switch(tmp_path):
    api = Api()
    folder, game_id = mv_game(tmp_path)
    root = folder / "www"
    system_path = root / "data" / "System.json"

    state = api.get_game_tools(game_id)["rpgm"]
    assert state == {
        "encrypted": 2,
        "images": 1,
        "audio": 1,
        "missing": 2,
        "key_source": "system",
        "use_decrypted": False,
        "backup": False,
    }
    refused = api.set_rpgm_use_decrypted(game_id, True)
    assert refused["success"] is False
    assert "2 encrypted" in str(refused["error"])
    assert json.loads(system_path.read_text())["hasEncryptedImages"] is True

    result = api.decrypt_rpgm_assets(game_id)
    assert result["success"] is True
    assert result["decrypted"] == 2
    assert (root / "img/faces/Actor1.png").read_bytes() == PNG
    assert api.get_game_tools(game_id)["rpgm"]["missing"] == 0

    on = api.set_rpgm_use_decrypted(game_id, True)
    assert on == {"success": True, "use_decrypted": True}
    after = api.get_game_tools(game_id)["rpgm"]
    assert after["use_decrypted"] is True and after["backup"] is True
    assert json.loads(system_path.read_text())["encryptionKey"] == KEY

    off = api.set_rpgm_use_decrypted(game_id, False)
    assert off["use_decrypted"] is False
    assert api.set_rpgm_use_decrypted(game_id, "on")["success"] is False


def test_rpgm_api_rejects_non_rpgm_and_unknown(tmp_path):
    api = Api()
    _, renpy_id = renpy_game(tmp_path)
    assert api.decrypt_rpgm_assets(renpy_id)["success"] is False
    assert api.set_rpgm_use_decrypted(renpy_id, True)["success"] is False
    assert api.decrypt_rpgm_assets(999)["success"] is False


def test_rpgm_api_nothing_to_decrypt_and_key_recovery(tmp_path):
    api = Api()
    folder = tmp_path / "Plain"
    system_json(folder / "www")
    (folder / "Game.exe").touch()
    plain_id = add_game("Plain", str(folder / "Game.exe"))
    result = api.decrypt_rpgm_assets(plain_id)
    assert result["success"] is False
    assert "not encrypted" in str(result["error"])

    mv, mv_id = mv_game(tmp_path)
    system_json(mv / "www", encryptionKey="")
    assert api.get_game_tools(mv_id)["rpgm"]["key_source"] == "recovered"
    recovered = api.decrypt_rpgm_assets(mv_id)
    assert recovered["key_source"] == "recovered"
    assert "key recovered" in str(recovered["message"])


def test_status_is_visible_while_a_run_is_in_flight(tmp_path, monkeypatch):
    api = Api()
    folder, game_id = renpy_game(tmp_path)
    build_rpa(folder / "game" / "images.rpa", {f"f{i}": b"x" for i in range(3)})
    out = tmp_path / "rip"
    out.mkdir()
    gate = threading.Event()
    reached = threading.Event()
    from core import renpy_tools

    real = renpy_tools._write_entry

    def slow(*args, **kwargs):
        reached.set()
        assert gate.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(renpy_tools, "_write_entry", slow)
    results: list[dict[str, object]] = []
    thread = threading.Thread(
        target=lambda: results.append(
            api.extract_renpy_archives(game_id, {"mode": "folder", "output": str(out)})
        )
    )
    thread.start()
    assert reached.wait(5)
    status = api.get_game_tool_status(game_id)
    assert status["running"] is True and status["total"] == 3
    assert api.cancel_game_tool(game_id)["success"] is True
    gate.set()
    thread.join(5)
    assert results[0]["success"] is True
    assert results[0]["cancelled"] is True
