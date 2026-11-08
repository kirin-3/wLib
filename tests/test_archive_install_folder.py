# SPDX-License-Identifier: GPL-3.0-or-later
"""Installing a game folder downloaded by a torrent."""
import json
import time

import pytest

from core.archive_install import ArchiveInstallError, in_torrent_area, install_archive
from core.downloads import torrents
from core.downloads.torrents import TorrentSupervisor


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    import core.database as database

    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "db.sqlite"))
    database.init_db()
    sup = TorrentSupervisor(command=lambda: (["true"], {}), free_space_fn=lambda p: 10**12, options=dict)
    monkeypatch.setattr(torrents, "supervisor", sup)
    return sup


def make_download(tmp_path, wrapper="Game-0.8-pc", files=None, phase="seeding"):
    folder = tmp_path / "data" / "downloads" / "torrents" / ("ab" * 20)
    game = folder / "data" / wrapper
    game.mkdir(parents=True)
    for name, body in (files or {"Game.exe": b"exe", "sub/readme.txt": b"hi"}).items():
        path = game / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    (folder / "state.json").write_text(json.dumps({
        "job_id": "j1", "game_id": 1, "size": 10, "phase": phase, "updated": time.time(),
    }))
    return folder, game


def run(source, lib, **extra):
    return install_archive(
        str(source), library_folder=str(lib), pattern="{title}/{version}", title="Game", version="0.8",
        source_kind="folder", **extra,
    )


def test_folder_install_flattens_wrapper_and_points_to_new_exe(tmp_path, env):
    import core.database as database

    folder, game = make_download(tmp_path, phase="ended")
    env.load()
    lib = tmp_path / "lib"
    lib.mkdir()
    result = run(folder / "data", lib)
    dest = lib / "Game" / "0.8"
    assert result["success"] is True and (dest / "Game.exe").is_file() and not (dest / "Game-0.8-pc").exists()
    assert (dest / "sub" / "readme.txt").read_text() == "hi"
    game_row = database.get_all_games()[0]
    assert game_row["exe_path"] == str(dest / "Game.exe") and game_row["version"] == "0.8"


def test_folder_outside_staging_is_refused(tmp_path):
    outside = tmp_path / "elsewhere" / "Game"
    outside.mkdir(parents=True)
    (outside / "Game.exe").write_bytes(b"x")
    lib = tmp_path / "lib"
    lib.mkdir()
    with pytest.raises(ArchiveInstallError):
        run(outside, lib)
    assert not any(lib.iterdir())
    assert not in_torrent_area(str(outside))


def test_torrent_area_root_itself_is_refused(tmp_path):
    root = torrents.torrents_dir()
    lib = tmp_path / "lib"
    lib.mkdir()
    with pytest.raises(ArchiveInstallError):
        run(root, lib)


def test_not_enough_space_copies_nothing(tmp_path, monkeypatch):
    folder, _game = make_download(tmp_path, files={"Game.exe": b"x" * 1000})
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setattr("core.archive_install.shutil.disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(ArchiveInstallError, match="Not enough free space"):
        run(folder / "data", lib)
    assert (folder / "data" / "Game-0.8-pc" / "Game.exe").is_file()
    assert [p.name for p in lib.iterdir()] == []


def test_seeding_torrent_is_copied_and_left_unchanged(tmp_path, env):
    folder, game = make_download(tmp_path, phase="seeding")
    env.load()
    lib = tmp_path / "lib"
    lib.mkdir()
    result = run(folder / "data", lib, delete_archive=True)
    assert result["success"] is True and (lib / "Game" / "0.8" / "Game.exe").is_file()
    assert (game / "Game.exe").is_file() and (game / "sub" / "readme.txt").is_file()


def test_not_seeding_same_drive_moves_the_folder(tmp_path, env):
    folder, game = make_download(tmp_path, phase="ended")
    env.load()
    lib = tmp_path / "lib"
    lib.mkdir()
    inode = (game / "Game.exe").stat().st_ino
    run(folder / "data", lib)
    assert (lib / "Game" / "0.8" / "Game.exe").stat().st_ino == inode
    assert not (game / "Game.exe").exists()


def test_failed_install_puts_a_moved_download_back(tmp_path, env, monkeypatch):
    folder, game = make_download(tmp_path, phase="ended")
    env.load()
    lib = tmp_path / "lib"
    lib.mkdir()
    import core.archive_install as ai

    def boom(*args, **kwargs):
        raise ai.ArchiveInstallError("simulated failure")

    monkeypatch.setattr(ai, "check_symlinks_inside_staging", boom)
    with pytest.raises(ArchiveInstallError):
        run(folder / "data", lib)
    # Flattening already ran; the wrapper folder comes back too.
    assert (game / "Game.exe").is_file() and (game / "sub" / "readme.txt").is_file()
    assert [p.name for p in (folder / "data").iterdir()] == ["Game-0.8-pc"]


def test_folder_update_can_be_retried_with_the_chosen_launch_file(tmp_path, env):
    from core.archive_install import NoLauncherChosenError
    import core.database as database

    dest = tmp_path / "lib" / "DDB"
    dest.mkdir(parents=True)
    (dest / "Game.exe").write_text("old")
    game_id = database.add_game("DDB", str(dest / "Game.exe"), version="1.0")
    # Two wrapper levels: one is flattened, the next is still a single folder.
    folder, game = make_download(
        tmp_path, wrapper="Wrap/Game", phase="ended",
        files={"PC/DDB/Game.exe": b"n", "Mac/DDB/Game.exe": b"m"},
    )
    env.load()
    options = {"library_folder": str(tmp_path / "lib"), "pattern": "{title}", "title": "Other", "version": "2.0",
               "game_id": game_id, "source_kind": "folder"}
    with pytest.raises(NoLauncherChosenError) as info:
        install_archive(str(folder / "data"), **options)
    chosen = next(c for c in info.value.candidates if c.endswith("PC/DDB/Game.exe"))
    assert (game / "PC" / "DDB" / "Game.exe").is_file()

    result = install_archive(str(folder / "data"), exe_relpath=chosen, **options)
    assert result["success"] is True, result
    assert str(result["exe_path"]).endswith("PC/DDB/Game.exe")


def test_seeding_archive_survives_install_even_with_delete_ticked(tmp_path, env):
    import zipfile

    folder = tmp_path / "data" / "downloads" / "torrents" / ("cd" * 20)
    (folder / "data").mkdir(parents=True)
    archive = folder / "data" / "Game-0.8.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Game.exe", b"x")
    (folder / "state.json").write_text(json.dumps({"job_id": "j2", "game_id": 1, "size": 5, "phase": "seeding", "updated": time.time()}))
    env.load()
    lib = tmp_path / "lib"
    lib.mkdir()
    result = install_archive(str(archive), library_folder=str(lib), pattern="{title}/{version}", title="Game", version="0.8", delete_archive=True)
    assert result["success"] is True and archive.is_file()


def test_ended_torrent_archive_deleted_when_install_releases_it(tmp_path, env):
    import zipfile

    folder = tmp_path / "data" / "downloads" / "torrents" / ("ef" * 20)
    (folder / "data").mkdir(parents=True)
    archive = folder / "data" / "Game-0.8.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Game.exe", b"x")
    (folder / "state.json").write_text(json.dumps({"job_id": "j3", "game_id": 1, "size": 5, "phase": "ended", "updated": time.time()}))
    env.load()
    env.mark_open(str(archive))
    lib = tmp_path / "lib"
    lib.mkdir()
    assert install_archive(str(archive), library_folder=str(lib), pattern="{title}/{version}", title="Game", version="0.8")["success"]
    assert folder.exists()
    env.release(str(archive), installed=True)
    assert not folder.exists()


def test_regular_archive_still_deleted_on_request(tmp_path):
    import zipfile

    archive = tmp_path / "Game-0.8.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Game.exe", b"x")
    lib = tmp_path / "lib"
    lib.mkdir()
    install_archive(str(archive), library_folder=str(lib), pattern="{title}/{version}", title="Game", version="0.8", delete_archive=True)
    assert not archive.exists()


def test_api_install_holds_then_releases_the_download(tmp_path, env):
    from core.api import Api

    folder, game = make_download(tmp_path, phase="seeding")
    env.load()
    lib = tmp_path / "lib"
    lib.mkdir()
    from core.database import update_setting

    update_setting("library_folder", str(lib))
    api = Api()
    info = api.inspect_archive(str(folder / "data"))
    assert info["success"] is True and info["source_kind"] == "folder" and info["torrent_seeding"] is True
    assert info["tool_error"] == ""
    assert api.inspect_archive(str(tmp_path))["success"] is False
    result = api.install_archive({
        "archive_path": str(folder / "data"), "source_kind": "folder", "library_folder": str(lib),
        "pattern": "{title}/{version}", "title": "Game", "version": "0.8",
    })
    assert result["success"] is True, result
    assert (lib / "Game" / "0.8" / "Game.exe").is_file() and (game / "Game.exe").is_file()
    assert not env._open  # pyright: ignore[reportPrivateUsage]

