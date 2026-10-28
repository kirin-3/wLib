# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.api import Api
from core.database import add_game, get_all_games, init_db, update_setting
from core.game_scan import guess_title_and_version, scan_games_folder


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "test_wlib_scan.db"))
    init_db()


def test_guess_title_and_version_from_folder_names():
    assert guess_title_and_version("SummertimeSaga-0.20.16-pc") == ("Summertime Saga", "0.20.16")
    assert guess_title_and_version("Being_A_DIK_v0.9.1") == ("Being A DIK", "0.9.1")
    assert guess_title_and_version("Game 2") == ("Game 2", "")


@pytest.mark.parametrize("default", ["false", "true"])
def test_scanned_game_add_uses_sandbox_default(tmp_path, default):
    update_setting("sandbox_new_games", default)
    folder = tmp_path / "Scanned Game"
    folder.mkdir()
    (folder / "Game.exe").touch()
    api = Api()
    scanned = api.scan_games_folder(str(tmp_path))["games"]
    assert len(scanned) == 1
    game = scanned[0]
    # api.importGames sends each scan result to this same backend entry point.
    result = api.add_game(
        game["title"], game["exe_path"], version=game["version"], engine=game["engine"]
    )
    assert result["success"]
    assert get_all_games()[0]["sandbox_enabled"] == int(default == "true")


@pytest.mark.parametrize("filename, content", [
    ("game.x86_64", b"\x7fELF"), ("game.x86", b"\x7fELF"),
    ("game", b"\x7fELF"), ("start.bat", b""), ("start.cmd", b""),
    ("Story.html", b""), ("Story.htm", b""),
])
def test_scan_supported_launchers(tmp_path, filename, content):
    folder = tmp_path / "Game"
    folder.mkdir()
    launcher = folder / filename
    launcher.write_bytes(content)
    (folder / "README").write_text("not executable")
    (folder / "UnityPlayer.so").write_bytes(b"\x7fELF")
    games = scan_games_folder(str(tmp_path))
    assert len(games) == 1
    assert games[0]["exe_path"] == str(launcher)


def test_scan_prefers_native_on_linux_and_index_html(tmp_path, monkeypatch):
    from core.game_scan import find_launcher

    monkeypatch.setattr("core.game_scan.is_linux", lambda: True)
    (tmp_path / "game").write_bytes(b"\x7fELF")
    (tmp_path / "game.exe").touch()
    assert find_launcher(str(tmp_path)) == str(tmp_path / "game")
    html = tmp_path / "html"
    html.mkdir()
    (html / "aaa.html").touch()
    (html / "index.html").touch()
    assert find_launcher(str(html)) == str(html / "index.html")


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
    godot = tmp_path / "Brew"
    godot.mkdir()
    (godot / "Brew.exe").touch()
    (godot / "Brew.pck").touch()
    (tmp_path / "Empty").mkdir()

    games = {game["title"]: game for game in scan_games_folder(str(tmp_path))}

    assert set(games) == {"Eternum", "Quest", "Brew"}
    assert games["Brew"]["engine"] == "Godot"
    assert games["Eternum"]["exe_path"] == str(renpy / "Eternum.exe")
    assert (games["Eternum"]["engine"], games["Eternum"]["version"]) == ("Ren'Py", "0.6")
    assert games["Quest"]["exe_path"] == str(rpgm / "Game.exe")
    assert games["Quest"]["engine"] == "RPGM"

    add_game("Eternum", str(renpy / "Eternum.exe"))
    scanned = {game["title"]: game["in_library"] for game in Api().scan_games_folder(str(tmp_path))["games"]}
    assert scanned == {"Eternum": True, "Quest": False, "Brew": False}


def test_scan_skips_crash_reporters_and_prereqs(tmp_path):
    from core.game_scan import find_launcher

    folder = tmp_path / "UnrealGame"
    folder.mkdir()
    (folder / "CrashReportClient.exe").touch()
    (folder / "UE4PrereqSetup_x64.exe").touch()
    (folder / "UnityCrashHandler64.exe").touch()
    (folder / "notification_helper.exe").touch()
    (folder / "python.exe").touch()
    (folder / "UnrealGame.exe").touch()

    launcher = find_launcher(str(folder))
    assert launcher == str(folder / "UnrealGame.exe")


def test_scan_prefers_game_over_config(tmp_path):
    from core.game_scan import find_launcher

    folder = tmp_path / "MyAdventure"
    folder.mkdir()
    (folder / "Config.exe").touch()
    (folder / "Setup.exe").touch()
    (folder / "Game.exe").touch()

    launcher = find_launcher(str(folder))
    assert launcher == str(folder / "Game.exe")


def test_scan_ignores_macosx_folder(tmp_path):
    from core.game_scan import find_launcher

    folder = tmp_path / "MacGame"
    folder.mkdir()
    mac = folder / "__MACOSX"
    mac.mkdir()
    (mac / "launcher.exe").touch()

    sub = folder / "InnerGame"
    sub.mkdir()
    (sub / "InnerGame.exe").touch()

    launcher = find_launcher(str(folder))
    assert launcher == str(sub / "InnerGame.exe")


def test_scan_title_match_beats_generic_launcher(tmp_path):
    from core.game_scan import find_launcher

    folder = tmp_path / "MyAdventure"
    folder.mkdir()
    (folder / "Game.exe").touch()
    (folder / "MyAdventure.exe").touch()

    assert find_launcher(str(folder)) == str(folder / "MyAdventure.exe")


def test_scan_relative_folder_returns_absolute_exe_paths(tmp_path, monkeypatch):
    import os

    folder = tmp_path / "library" / "Demo"
    folder.mkdir(parents=True)
    (folder / "Demo.exe").touch()
    monkeypatch.chdir(tmp_path)

    result = Api().scan_games_folder("library")

    exe_path = result["games"][0]["exe_path"]
    assert os.path.isabs(exe_path)
    assert exe_path == str(folder / "Demo.exe")


def test_inspect_relative_exe_path_returns_absolute_path(tmp_path, monkeypatch):
    folder = tmp_path / "Game"
    folder.mkdir()
    exe = folder / "Game.exe"
    exe.touch()
    monkeypatch.chdir(tmp_path)

    result = Api().inspect_game_path("Game/Game.exe")

    assert result["exe_path"] == str(exe)
    assert result["title"] == "Game"


def test_scan_ignores_staging_folder(tmp_path):
    from core.game_scan import find_launcher

    folder = tmp_path / "Wrapped"
    folder.mkdir()
    (folder / ".wlib-staging-abc123").mkdir()
    (folder / ".wlib-staging-abc123" / "Stale.exe").touch()
    (folder / "Inner").mkdir()
    (folder / "Inner" / "Inner.exe").touch()

    assert find_launcher(str(folder)) == str(folder / "Inner" / "Inner.exe")


def test_scan_prefers_exact_title_over_setup(tmp_path):
    from core.game_scan import find_launcher

    folder = tmp_path / "MyGame"
    folder.mkdir()
    (folder / "MyGame-Setup.exe").touch()
    (folder / "MyGame.exe").touch()

    assert find_launcher(str(folder)) == str(folder / "MyGame.exe")


def test_scan_ignores_symlinked_exe(tmp_path):
    from core.game_scan import find_launcher

    folder = tmp_path / "Game"
    folder.mkdir()
    (folder / "Real.exe").touch()
    target = tmp_path / "outside.exe"
    target.touch()
    link = folder / "Link.exe"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks need privileges")
    assert find_launcher(str(folder)) == str(folder / "Real.exe")


def test_scan_ignores_symlinked_subdir(tmp_path):
    from core.game_scan import find_launcher

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Evil.exe").touch()
    folder = tmp_path / "Game"
    folder.mkdir()
    try:
        (folder / "Link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges")
    assert find_launcher(str(folder)) == ""
