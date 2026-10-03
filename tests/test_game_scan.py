# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.api import Api
from core.database import add_game, init_db
from core.game_scan import guess_title_and_version, scan_games_folder


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "test_wlib_scan.db"))
    init_db()


def test_guess_title_and_version_from_folder_names():
    assert guess_title_and_version("SummertimeSaga-0.20.16-pc") == ("Summertime Saga", "0.20.16")
    assert guess_title_and_version("Being_A_DIK_v0.9.1") == ("Being A DIK", "0.9.1")
    assert guess_title_and_version("Game 2") == ("Game 2", "")


def test_scan_games_folder_detects_launchers_and_engines(tmp_path, monkeypatch):
    monkeypatch.setattr("core.game_scan.is_linux", lambda: False)
    renpy = tmp_path / "Eternum-0.6-pc"
    (renpy / "renpy").mkdir(parents=True)
    for name in ("Eternum.exe", "Eternum-32.exe", "Eternum.sh"):
        (renpy / name).touch()
    rpgm = tmp_path / "Quest" / "Quest-1.2"  # extra nesting from the archive
    (rpgm / "www").mkdir(parents=True)
    (rpgm / "Game.exe").touch()
    (rpgm / "UnityCrashHandler64.exe").touch()
    (tmp_path / "Empty").mkdir()

    games = {game["title"]: game for game in scan_games_folder(str(tmp_path))}

    assert set(games) == {"Eternum", "Quest"}
    assert games["Eternum"]["exe_path"] == str(renpy / "Eternum.exe")
    assert (games["Eternum"]["engine"], games["Eternum"]["version"]) == ("Ren'Py", "0.6")
    assert games["Quest"]["exe_path"] == str(rpgm / "Game.exe")
    assert games["Quest"]["engine"] == "RPGM"

    add_game("Eternum", str(renpy / "Eternum.exe"))
    scanned = {game["title"]: game["in_library"] for game in Api().scan_games_folder(str(tmp_path))["games"]}
    assert scanned == {"Eternum": True, "Quest": False}
