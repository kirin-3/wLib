"""Updates carry saves kept behind an in-folder symlink before old-folder cleanup."""

import os
import zipfile

import pytest

from core.archive_install import copy_infolder_saves

pytestmark = pytest.mark.skipif(
    not hasattr(os, "symlink") or os.name == "nt", reason="needs POSIX symlinks"
)


def _isolated_db(tmp_path, monkeypatch):
    import core.database as database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "h4.db"))
    database.init_db()
    return database


def _write_game_zip(path, files):
    with zipfile.ZipFile(str(path), "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_copy_follows_a_save_link_that_stays_inside_the_old_install(tmp_path):
    old = tmp_path / "old"
    (old / "profile").mkdir(parents=True)
    (old / "profile" / "1.save").write_text("keep")
    (old / "game").mkdir()
    os.symlink(old / "profile", old / "game" / "saves")
    new = tmp_path / "new"
    new.mkdir()

    copied = copy_infolder_saves(str(old), str(new))

    assert os.path.join("game", "saves") in copied
    dst = new / "game" / "saves"
    assert not dst.is_symlink()
    assert (dst / "1.save").read_text() == "keep"


def test_copy_reports_a_save_link_to_the_install_folder_itself(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "data.sav").write_text("keep")
    os.symlink(old, old / "saves")
    new = tmp_path / "new"
    new.mkdir()
    skipped: list[str] = []

    copied = copy_infolder_saves(str(old), str(new), skipped=skipped)

    assert "saves" not in copied
    assert skipped == ["saves"]
    assert not (new / "saves").exists()


def test_copy_leaves_a_save_link_that_points_outside_the_old_install(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "1.save").write_text("outside")
    old = tmp_path / "old"
    old.mkdir()
    os.symlink(outside, old / "saves")
    new = tmp_path / "new"
    new.mkdir()
    skipped: list[str] = []

    copied = copy_infolder_saves(str(old), str(new), skipped=skipped)

    # Cleanup removes only the link, so the target is not at risk.
    assert copied == []
    assert skipped == []
    assert not (new / "saves").exists()


def test_update_carries_symlinked_saves_before_removing_the_old_folder(
    tmp_path, monkeypatch
):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    old_dir = lib / "Game" / "1.0"
    (old_dir / "profile").mkdir(parents=True)
    (old_dir / "profile" / "1-1-LT1.save").write_text("only copy")
    (old_dir / "game").mkdir()
    os.symlink(old_dir / "profile", old_dir / "game" / "saves")
    old_exe = old_dir / "Game.exe"
    old_exe.write_text("old")
    game_id = db.add_game("Game", str(old_exe), version="1.0")
    assert game_id is not None

    archive = tmp_path / "Game-2.0.zip"
    _write_game_zip(archive, {"Game.exe": b"new"})
    result = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}/{version}",
        title="Game",
        version="2.0",
        game_id=int(game_id),
        remove_old=True,
    )

    assert result["success"] is True
    assert result["removed_old"] is True
    assert not old_dir.exists()
    saved = lib / "Game" / "2.0" / "game" / "saves" / "1-1-LT1.save"
    assert saved.read_text() == "only copy"


def test_update_keeps_the_old_folder_when_a_save_link_cannot_be_copied(
    tmp_path, monkeypatch
):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    old_dir = lib / "Game" / "1.0"
    old_dir.mkdir(parents=True)
    (old_dir / "progress.dat").write_text("unknown save")
    os.symlink(old_dir, old_dir / "saves")
    old_exe = old_dir / "Game.exe"
    old_exe.write_text("old")
    game_id = db.add_game("Game", str(old_exe), version="1.0")
    assert game_id is not None

    archive = tmp_path / "Game-2.0.zip"
    _write_game_zip(archive, {"Game.exe": b"new"})
    result = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}/{version}",
        title="Game",
        version="2.0",
        game_id=int(game_id),
        remove_old=True,
    )

    assert result["success"] is True
    assert result["removed_old"] is False
    assert "saves" in str(result["kept_old_folder_reason"])
    assert (old_dir / "progress.dat").read_text() == "unknown save"
