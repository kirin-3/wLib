# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import zipfile
from typing import cast

import pytest

from core.archive_install import (
    ArchiveInstallError,
    ArchiveSafetyError,
    apply_execute_bits,
    build_destination,
    can_remove_old_folder,
    check_archive_entry_safe,
    check_symlinks_inside_staging,
    cleanup_stale_staging,
    copy_infolder_saves,
    create_staging_dir,
    cleanup_staging_dir,
    detect_archive_tool,
    ensure_free_space,
    extract_with_tool,
    extract_zip,
    flatten_single_wrapper,
    infolder_save_relpaths,
    relocate_launch_targets,
)


def _join(*parts: str) -> str:
    return os.path.join(*parts)


def test_default_pattern():
    dest = build_destination(
        "/games", "{creator}/{title}/{version}", "Caribdis", "Eternum", "0.8", ""
    )
    assert dest == _join(os.path.abspath("/games"), "Caribdis", "Eternum", "0.8")


def test_missing_creator_omitted():
    dest = build_destination(
        "/games", "{creator}/{title}/{version}", "", "Eternum", "0.8", ""
    )
    assert dest == _join(os.path.abspath("/games"), "Eternum", "0.8")


def test_invalid_characters_sanitized():
    dest = build_destination("/games", "{title}", "", "Re:Birth?", "1", "")
    name = os.path.basename(dest)
    assert "?" not in name and ":" not in name
    assert name == "Re_Birth_"


def test_reserved_name_prefixed():
    dest = build_destination("/games", "{title}", "", "CON", "1", "")
    assert dest.endswith("_CON")


def test_trailing_dots_stripped():
    dest = build_destination("/games", "{title}", "", "Game...   ", "1", "")
    assert dest.endswith("Game")


def test_all_empty_pattern_falls_back():
    dest = build_destination("/games", "{creator}/{version}", "", "", "", "")
    assert dest == _join(os.path.abspath("/games"), "game")


def _which_stub(mapping):
    def _which(name):
        return mapping.get(name)

    return _which


def test_tool_detection_zip_needs_no_tool(monkeypatch):
    monkeypatch.setattr("shutil.which", _which_stub({}))
    tool, error = detect_archive_tool("game.zip")
    assert tool is None and error is None


def test_tool_detection_rar_with_only_unrar(monkeypatch):
    monkeypatch.setattr("shutil.which", _which_stub({"unrar": "/usr/bin/unrar"}))
    tool, error = detect_archive_tool("game.rar", platform="linux")
    assert tool == "/usr/bin/unrar" and error is None


def test_tool_detection_7z_with_only_bsdtar(monkeypatch):
    monkeypatch.setattr("shutil.which", _which_stub({"bsdtar": "/usr/bin/bsdtar"}))
    tool, error = detect_archive_tool("game.7z", platform="linux")
    assert tool == "/usr/bin/bsdtar" and error is None


def test_tool_detection_windows_program_files(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_stub({}))
    seven_zip = tmp_path / "7-Zip" / "7z.exe"
    seven_zip.parent.mkdir(parents=True)
    seven_zip.write_bytes(b"x")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    tool, error = detect_archive_tool("game.7z", platform="win32")
    assert tool == str(seven_zip) and error is None


def test_tool_detection_nothing_available(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_stub({}))
    monkeypatch.setattr("os.path.isfile", lambda _p: False)
    _tool, error = detect_archive_tool("game.7z", platform="linux")
    assert error and "7-Zip" in error or "bsdtar" in error


@pytest.mark.parametrize("extension", ["7z", "rar"])
@pytest.mark.parametrize(
    "flatpak,bundled,archive_visible,library_writable",
    [
        (True, True, True, True),
        (True, False, True, True),
        (False, True, True, True),
        (True, True, False, True),
        (True, True, True, False),
    ],
)
def test_tool_detection_bundled_and_host_fallback(
    monkeypatch, tmp_path, extension, flatpak, bundled, archive_visible, library_writable
):
    import core.archive_install as ai

    _simulate_flatpak_archive(monkeypatch, enabled=flatpak)
    bundled_tool = tmp_path / "7zz"
    monkeypatch.setattr(ai, "BUNDLED_7ZZ", str(bundled_tool))
    if bundled:
        bundled_tool.touch()
    archive = tmp_path / f"game.{extension}"
    if archive_visible:
        archive.touch()
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(ai.os, "access", lambda path, mode: library_writable)
    searched = []

    def host_which(name):
        searched.append(name)
        return "/usr/bin/bsdtar" if name == "bsdtar" else None

    monkeypatch.setattr(ai, "host_which", host_which)
    tool, error = ai.detect_archive_tool(str(archive), library_folder=str(library))
    if flatpak and bundled and archive_visible and library_writable:
        assert tool == str(bundled_tool)
        assert searched == []
    else:
        assert tool == "/usr/bin/bsdtar"
        assert searched == ["7zz", "7z", "7za", "bsdtar"]
    assert error is None


def _make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_zip_extract_normal(tmp_path):
    archive = tmp_path / "game.zip"
    _make_zip(str(archive), {"Game.exe": b"x", "readme.txt": b"hi"})
    staging = tmp_path / "staging"
    count = extract_zip(str(archive), str(staging))
    assert count == 2
    assert (staging / "Game.exe").is_file()


def test_zip_extract_traversal_aborts(tmp_path):
    for evil in ("../evil.txt", "/abs.txt", "a/../../evil.txt"):
        archive = tmp_path / "evil.zip"
        _make_zip(str(archive), {evil: b"x", "ok.txt": b"y"})
        staging = tmp_path / f"staging-{abs(hash(evil)) % 100000}"
        staging.mkdir()
        with pytest.raises(ArchiveSafetyError):
            extract_zip(str(archive), str(staging))
        assert not list(staging.iterdir())
        # Nothing written outside staging.
        assert not (tmp_path / "evil.txt").exists()


def test_zip_extract_restores_mode(tmp_path):
    archive = tmp_path / "mode.zip"
    info = zipfile.ZipInfo("run.sh")
    info.external_attr = 0o755 << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, "#!/bin/sh\n")
    staging = tmp_path / "staging-mode"
    extract_zip(str(archive), str(staging))
    if os.name != "nt":
        assert os.stat(staging / "run.sh").st_mode & 0o111


def test_zip_extract_ignores_mode_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    archive = tmp_path / "ro.zip"
    info = zipfile.ZipInfo("data.txt")
    info.external_attr = 0o444 << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, "x")
    staging = tmp_path / "staging-ro"
    extract_zip(str(archive), str(staging))
    assert os.access(staging / "data.txt", os.W_OK)


def test_zip_symlink_entry_written_as_file(tmp_path):
    archive = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link")
    info.external_attr = (0o120777) << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, "/etc/passwd")
    staging = tmp_path / "staging-zlink"
    extract_zip(str(archive), str(staging))
    assert not os.path.islink(staging / "link")
    assert (staging / "link").read_text() == "/etc/passwd"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_staging_dir_is_world_readable(tmp_path):
    staging = create_staging_dir(str(tmp_path))
    assert os.stat(staging).st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="symlinks need Linux")
def test_symlink_escape_rejected(tmp_path):
    staging = tmp_path / "staging-link"
    staging.mkdir()
    (staging / "evil-link").symlink_to("/etc/passwd")
    with pytest.raises(ArchiveSafetyError):
        check_symlinks_inside_staging(str(staging))


def test_external_tool_traversal_rejected_before_run(monkeypatch, tmp_path):
    import core.archive_install as ai

    monkeypatch.setattr(
        ai, "list_tool_entries", lambda _t, _a: ["ok.txt", "../../evil.txt"]
    )
    called = []

    def _fail(cmd, **kwargs):
        called.append(cmd)
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(ai.subprocess, "run", _fail)
    staging = tmp_path / "staging-ext"
    with pytest.raises(ArchiveSafetyError):
        extract_with_tool("/usr/bin/7z", "game.7z", str(staging))
    assert called == []


def test_external_tool_nonzero_exit_fails(monkeypatch, tmp_path):
    import core.archive_install as ai

    monkeypatch.setattr(ai, "list_tool_entries", lambda _t, _a: ["ok.txt"])

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(ai.subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(Exception, match="boom"):
        extract_with_tool("/usr/bin/7z", "game.7z", str(tmp_path / "s"))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_execute_bits_for_sh_and_elf(tmp_path):
    sh = tmp_path / "Game.sh"
    sh.write_text("#!/bin/sh\n")
    sh.chmod(0o644)
    elf = tmp_path / "game.bin"
    elf.write_bytes(b"\x7fELF" + b"\x00" * 100)
    elf.chmod(0o644)
    txt = tmp_path / "notes.txt"
    txt.write_text("hi")
    txt.chmod(0o644)
    changed = apply_execute_bits(str(tmp_path))
    assert os.access(sh, os.X_OK)
    assert os.access(elf, os.X_OK)
    assert not os.access(txt, os.X_OK)
    assert changed >= 2


def test_flatten_wrapper_vs_multi(tmp_path):
    single = tmp_path / "single"
    single.mkdir()
    wrapper = single / "Game-1.0-pc"
    wrapper.mkdir()
    (wrapper / "Game.exe").write_text("x")
    assert flatten_single_wrapper(str(single)) is True
    assert (single / "Game.exe").is_file()

    multi = tmp_path / "multi"
    multi.mkdir()
    (multi / "a.txt").write_text("a")
    (multi / "b").mkdir()
    assert flatten_single_wrapper(str(multi)) is False


def test_staging_cleanup_and_stale(tmp_path):
    staging = create_staging_dir(str(tmp_path))
    assert os.path.isdir(staging)
    cleanup_staging_dir(staging)
    assert not os.path.exists(staging)
    stale = tmp_path / ".wlib-staging-old"
    stale.mkdir()
    assert cleanup_stale_staging(str(tmp_path)) >= 1
    assert not stale.exists()


def test_free_space_check(monkeypatch, tmp_path):
    class _Usage:
        total = 100
        used = 90
        free = 5

    monkeypatch.setattr("shutil.disk_usage", lambda _p: _Usage())
    with pytest.raises(Exception, match="free space"):
        ensure_free_space(str(tmp_path), 1000)


def test_infolder_saves_carry_over_and_overwrite(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    renpy_saves = old / "game" / "saves"
    renpy_saves.mkdir(parents=True)
    (renpy_saves / "1-1-LT1.save").write_text("old-save")
    rpgm_saves = old / "www" / "save"
    rpgm_saves.mkdir(parents=True)
    (rpgm_saves / "file1.rpgsave").write_text("old-rpgm")
    (old / "notes.dat").write_text("data-should-not-copy")
    (new / "game" / "saves").mkdir(parents=True)
    (new / "game" / "saves" / "1-1-LT1.save").write_text("shipped")
    copied = copy_infolder_saves(str(old), str(new))
    assert (new / "game" / "saves" / "1-1-LT1.save").read_text() == "old-save"
    assert (new / "www" / "save" / "file1.rpgsave").read_text() == "old-rpgm"
    assert not (new / "notes.dat").exists()
    assert any("game" in p and "saves" in p for p in copied)
    # .dat loose files are excluded from the helper.
    assert "notes.dat" not in infolder_save_relpaths(str(old))


def test_infolder_saves_found_when_install_path_has_brackets(tmp_path):
    root = tmp_path / "Game [v1.0]"
    root.mkdir()
    (root / "slot1.sav").write_text("save")
    assert infolder_save_relpaths(str(root)) == ["slot1.sav"]



def test_can_remove_old_folder_guards(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    new_dest = lib / "Game" / "2.0"
    new_dest.mkdir(parents=True)
    old = lib / "Game" / "1.0"
    old.mkdir(parents=True)
    (old / "Game.exe").write_text("x")
    other_exe = lib / "Other" / "Other.exe"
    other_exe.parent.mkdir(parents=True)
    other_exe.write_text("x")

    allowed = can_remove_old_folder(
        str(old),
        str(new_dest),
        str(lib),
        str(tmp_path / "Downloads"),
        [str(other_exe)],
        str(old / "Game.exe"),
    )
    assert allowed == {"removable": True, "reason": ""}

    assert can_remove_old_folder("/", str(new_dest))["removable"] is False
    assert (
        can_remove_old_folder(
            str(
                tmp_path.home()
                if hasattr(tmp_path, "home")
                else os.path.expanduser("~")
            ),
            str(new_dest),
        )["removable"]
        is False
    )
    assert (
        can_remove_old_folder(str(lib), str(new_dest), str(lib))["removable"] is False
    )
    downloads = tmp_path / "Downloads"
    downloads.mkdir(exist_ok=True)
    assert (
        can_remove_old_folder(str(downloads), str(new_dest), str(lib), str(downloads))[
            "removable"
        ]
        is False
    )
    assert (
        can_remove_old_folder(str(lib / "Game"), str(new_dest), str(lib))["removable"]
        is False
    )
    shared = tmp_path / "shared"
    shared.mkdir(exist_ok=True)
    (shared / "Game.exe").write_text("x")
    (shared / "Other.exe").write_text("x")
    assert (
        can_remove_old_folder(
            str(shared),
            str(new_dest),
            str(lib),
            "",
            [str(shared / "Other.exe")],
            str(shared / "Game.exe"),
        )["removable"]
        is False
    )
    assert (
        can_remove_old_folder(
            str(old), str(new_dest), str(lib), "", [], str(old / "Missing.exe")
        )["removable"]
        is False
    )


def test_can_remove_old_folder_refuses_a_mounted_drive_root(tmp_path, monkeypatch):
    import core.archive_install as ai

    drive = tmp_path / "drive"
    drive.mkdir()
    old_exe = drive / "Game.exe"
    old_exe.write_text("x")
    # A mounted filesystem's root (an external drive, a tmpfs) is a filesystem
    # root for that drive even though it is not the OS root.
    monkeypatch.setattr(
        ai.os.path, "ismount", lambda value: os.path.abspath(value) == str(drive)
    )

    result = can_remove_old_folder(
        str(drive),
        str(tmp_path / "lib" / "Game" / "2.0"),
        str(tmp_path / "lib"),
        "",
        [],
        str(old_exe),
    )

    assert result["removable"] is False
    assert "mount" in str(result["reason"]).lower()


def test_relocate_launch_targets(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    (old / "sub").mkdir(parents=True)
    (new / "sub").mkdir(parents=True)
    (old / "sub" / "Config.exe").write_text("x")
    (new / "sub" / "Config.exe").write_text("x")
    targets = [
        {"label": "Config", "exe_path": str(old / "sub" / "Config.exe")},
        {"label": "Missing", "exe_path": str(old / "sub" / "Gone.exe")},
        {"label": "Outside", "exe_path": "/usr/bin/other"},
    ]
    updated, unmoved = relocate_launch_targets(str(old), str(new), targets)
    assert updated[0]["exe_path"] == str(new / "sub" / "Config.exe")
    assert updated[1]["exe_path"] == str(old / "sub" / "Gone.exe")
    assert updated[2]["exe_path"] == "/usr/bin/other"
    assert [t["label"] for t in unmoved] == ["Missing"]


def _isolated_db(tmp_path, monkeypatch):
    import core.database as database

    db_file = tmp_path / "archive-install-test.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    database.init_db()
    return database


def _write_game_zip(path, files):
    import zipfile as _zf

    with _zf.ZipFile(str(path), "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_install_new_game(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    lib.mkdir()
    archive = tmp_path / "Eternum-0.8-pc.zip"
    _write_game_zip(archive, {"Eternum.exe": b"x"})
    result = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}/{version}",
        title="Eternum",
        version="0.8",
    )
    assert result["success"] is True
    assert os.path.isfile(str(result["exe_path"]))
    games = db.get_all_games()
    assert len(games) == 1 and games[0]["version"] == "0.8"


def test_install_prefers_title_executable_over_alphabetically_earlier_tool(
    tmp_path, monkeypatch
):
    # The destination folder is named by version, so the title has to come from
    # the install request; otherwise "Config.exe" wins over "Eternum.exe".
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    archive = tmp_path / "Eternum-0.8-pc.zip"
    _write_game_zip(archive, {"Config.exe": b"config", "Eternum.exe": b"game"})
    result = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}/{version}",
        title="Eternum",
        version="0.8",
    )
    expected = str(lib / "Eternum" / "0.8" / "Eternum.exe")
    assert result["exe_path"] == expected
    assert db.get_all_games()[0]["exe_path"] == expected



def test_install_update_removes_old_and_keeps_when_unticked(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    old_dir = lib / "Game" / "1.0"
    old_dir.mkdir(parents=True)
    old_exe = old_dir / "Game.exe"
    old_exe.write_text("old")
    game_id = db.add_game("Game", str(old_exe), version="1.0")
    assert game_id is not None

    db.update_game(game_id, {"skipped_version": "2.0"})

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
    assert not old_dir.exists()
    assert db.get_all_games()[0]["version"] == "2.0"
    assert db.get_all_games()[0]["skipped_version"] == ""

    # Keep-old path.
    old_dir2 = lib / "Game" / "2.0"
    archive2 = tmp_path / "Game-3.0.zip"
    _write_game_zip(archive2, {"Game.exe": b"newer"})
    result2 = install_archive(
        str(archive2),
        library_folder=str(lib),
        pattern="{title}/{version}",
        title="Game",
        version="3.0",
        game_id=int(game_id),
        remove_old=False,
    )
    assert result2["success"] is True
    assert old_dir2.exists()


def test_install_same_folder_pattern_replaces_safely(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    dest = lib / "Game"
    dest.mkdir(parents=True)
    old_exe = dest / "Game.exe"
    old_exe.write_text("old")
    (dest / "game" / "saves").mkdir(parents=True)
    (dest / "game" / "saves" / "1.save").write_text("keep")
    game_id = db.add_game("Game", str(old_exe), version="1.0")
    assert game_id is not None
    archive = tmp_path / "Game-2.0.zip"
    _write_game_zip(archive, {"Game.exe": b"new"})
    result = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}",
        title="Game",
        version="2.0",
        game_id=int(game_id),
    )
    assert result["success"] is True
    assert (dest / "Game.exe").read_bytes() == b"new"
    assert (dest / "game" / "saves" / "1.save").read_text() == "keep"
    # The renamed previous contents are removed, not left as .wlib-old-*.
    assert result["removed_old"] is True
    assert result["kept_old_folder_reason"] == ""
    assert [p.name for p in lib.iterdir()] == ["Game"]


def test_install_same_folder_pattern_keeps_backup_when_unticked(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    dest = lib / "Game"
    dest.mkdir(parents=True)
    old_exe = dest / "Game.exe"
    old_exe.write_text("old")
    game_id = db.add_game("Game", str(old_exe), version="1.0")
    assert game_id is not None
    archive = tmp_path / "Game-2.0.zip"
    _write_game_zip(archive, {"Game.exe": b"new"})
    result = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}",
        title="Game",
        version="2.0",
        game_id=int(game_id),
        remove_old=False,
    )
    backups = [p for p in lib.iterdir() if ".wlib-old-" in p.name]
    assert result["removed_old"] is False
    assert len(backups) == 1
    assert (backups[0] / "Game.exe").read_text() == "old"
    assert str(backups[0]) in str(result["kept_old_folder_reason"])


def test_install_save_copy_failure_leaves_db_untouched(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    import core.archive_install as ai

    lib = tmp_path / "lib"
    old_dir = lib / "Game" / "1.0"
    old_dir.mkdir(parents=True)
    old_exe = old_dir / "Game.exe"
    old_exe.write_text("old")
    game_id = db.add_game("Game", str(old_exe), version="1.0")
    assert game_id is not None
    archive = tmp_path / "Game-2.0.zip"
    _write_game_zip(archive, {"Game.exe": b"new"})

    def _boom(_old, _new):
        raise ai.ArchiveInstallError("save copy boom")

    monkeypatch.setattr(ai, "copy_infolder_saves", _boom)
    with pytest.raises(ai.ArchiveInstallError):
        ai.install_archive(
            str(archive),
            library_folder=str(lib),
            pattern="{title}/{version}",
            title="Game",
            version="2.0",
            game_id=int(game_id),
        )
    assert old_exe.read_text() == "old"
    assert db.get_all_games()[0]["version"] == "1.0"
    assert not (lib / "Game" / "2.0").exists()


def test_install_rmtree_failure_reports_but_update_stands(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    import core.archive_install as ai

    lib = tmp_path / "lib"
    old_dir = lib / "Game" / "1.0"
    old_dir.mkdir(parents=True)
    old_exe = old_dir / "Game.exe"
    old_exe.write_text("old")
    game_id = db.add_game("Game", str(old_exe), version="1.0")
    assert game_id is not None
    archive = tmp_path / "Game-2.0.zip"
    _write_game_zip(archive, {"Game.exe": b"new"})
    monkeypatch.setattr(ai, "remove_tree_collect", lambda _p: ["busy"])
    result = ai.install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}/{version}",
        title="Game",
        version="2.0",
        game_id=int(game_id),
        remove_old=True,
    )
    assert result["success"] is True
    assert db.get_all_games()[0]["version"] == "2.0"
    assert result["kept_old_folder_reason"]


def test_find_download_offers_match_and_filters(tmp_path, monkeypatch):
    import time as _time

    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import dismiss_download_offer, find_download_offers

    downloads = tmp_path / "dl"
    downloads.mkdir()
    db.update_setting("downloads_folder", str(downloads))
    db.update_setting("download_detection_enabled", "true")
    game_id = db.add_game("Eternum", "/games/Eternum/0.7/Eternum.exe", version="0.7")
    assert game_id is not None
    db.update_game(int(game_id), {"latest_version": "0.8"})
    # A game with no pending update never produces offers.
    quiet_id = db.add_game("Quiet Game", "/games/Quiet/1.0/Game.exe", version="1.0")
    assert quiet_id is not None

    def _touch(name, age=60):
        p = downloads / name
        p.write_bytes(b"x" * 16)
        old = _time.time() - age
        os.utime(p, (old, old))
        return p

    match = _touch("Eternum-0.8-pc.zip")
    _touch("Eternum-0.8-pc.zip.crdownload")
    fresh = downloads / "Eternum-0.8-fresh.zip"
    fresh.write_bytes(b"y" * 16)
    _touch("Unrelated-9.9.zip")
    _touch("Game-1.0.part2.rar")
    _touch("Quiet-Game-2.0.zip")
    offers = find_download_offers()
    assert [o["archive_path"] for o in offers] == [str(match)]

    # Same version as installed is dropped.
    same = _touch("Eternum-0.7-pc.zip")
    offers = find_download_offers()
    assert str(match) in [o["archive_path"] for o in offers]
    assert str(same) not in [o["archive_path"] for o in offers]

    # Dismissed file is hidden; replacing it re-offers.
    dismiss_download_offer(offers[0]["key"])
    assert str(match) not in [o["archive_path"] for o in find_download_offers()]
    match.write_bytes(b"z" * 32)
    old = _time.time() - 60
    os.utime(match, (old, old))
    assert str(match) in [o["archive_path"] for o in find_download_offers()]

    # Missing folder → empty list.
    db.update_setting("downloads_folder", str(tmp_path / "nope"))
    assert find_download_offers() == []


def test_find_download_offers_skips_skipped_update_and_wishlisted_game(
    tmp_path, monkeypatch
):
    import time as _time

    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import find_download_offers

    downloads = tmp_path / "dl"
    downloads.mkdir()
    db.update_setting("downloads_folder", str(downloads))

    skipped_id = db.add_game(
        "Eternum", "/games/Eternum/0.7/Eternum.exe", version="0.7"
    )
    assert skipped_id is not None
    db.update_game(int(skipped_id), {"latest_version": "0.8", "skipped_version": "v0.8"})
    wishlist_id = db.add_game("Wanted", "", version="")
    assert wishlist_id is not None
    db.update_game(int(wishlist_id), {"latest_version": "1.0"})

    old = _time.time() - 60
    for name in ("Eternum-0.8-pc.zip", "Wanted-1.0.zip"):
        file = downloads / name
        file.write_bytes(b"x" * 16)
        os.utime(file, (old, old))

    # Neither game has an available update, so no archive is offered for them.
    assert find_download_offers() == []

    # The same archive is offered once the skip is cleared.
    db.update_game(int(skipped_id), {"skipped_version": ""})
    offers = find_download_offers()
    assert [offer["game_id"] for offer in offers] == [int(skipped_id)]


def test_find_download_offers_keeps_non_latin_titles_apart(tmp_path, monkeypatch):
    import time as _time

    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import find_download_offers

    downloads = tmp_path / "dl"
    downloads.mkdir()
    db.update_setting("downloads_folder", str(downloads))
    for title in ("エターナム", "別のゲーム"):
        game_id = db.add_game(title, f"/games/{title}/old/Game.exe", version="0.7")
        assert game_id is not None
        db.update_game(int(game_id), {"latest_version": "0.8"})

    old = _time.time() - 60

    def _touch(name):
        file = downloads / name
        file.write_bytes(b"x" * 16)
        os.utime(file, (old, old))
        return file

    # Every non-Latin title used to squash to the same empty key, so an
    # unrelated archive matched all of them.
    _touch("全然関係ない-9.9.zip")
    assert find_download_offers() == []

    match = _touch("エターナム-0.8-pc.zip")
    offers = find_download_offers()
    assert [offer["archive_path"] for offer in offers] == [str(match)]
    assert [offer["game_title"] for offer in offers] == ["エターナム"]


def test_7z_listing_skips_archive_self_entry(monkeypatch, tmp_path):
    import core.archive_install as ai

    archive = str(tmp_path / "game.7z")

    class _Listing:
        returncode = 0
        stderr = ""

        def __init__(self, archive_path: str) -> None:
            self.stdout = (
                f"Path = {archive_path}\nType = 7z\n"
                "Path = Game-1.0\nFolder = +\n"
                "Path = Game-1.0/Game.exe\n"
            )

    class _Extract:
        returncode = 0
        stdout = ""
        stderr = ""

    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[1] == "l":
            return _Listing(archive)
        return _Extract()

    monkeypatch.setattr(ai.subprocess, "run", _run)
    entries = ai.list_tool_entries("/usr/bin/7z", archive)
    assert entries == ["Game-1.0", "Game-1.0/Game.exe"]
    staging = tmp_path / "staging-7z-self"
    assert ai.extract_with_tool("/usr/bin/7z", archive, str(staging)) == 2
    # list-entries ("l") runs once directly and once inside extract ("l", "x").
    assert [c[1] for c in calls] == ["l", "l", "x"]


def test_check_archive_entry_safe_drive_relative_and_reserved_names():
    for bad in ("D:evil.txt", "c:secret.txt", "D:\\evil.txt", "d:/evil.txt"):
        with pytest.raises(ArchiveSafetyError):
            check_archive_entry_safe(bad)

    for reserved in (
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM9",
        "LPT1",
        "LPT9",
        "con.txt",
        "nul.tar",
        "CON ",
        "NUL .txt",
        "dir/aux",
    ):
        with pytest.raises(ArchiveSafetyError):
            check_archive_entry_safe(reserved)

    for invalid in ("", "   ", "evil\x00file.txt", "sub/\x00/file"):
        with pytest.raises(ArchiveSafetyError):
            check_archive_entry_safe(invalid)


def test_zip_extract_drive_relative_and_reserved_traversal_aborts(tmp_path):
    for evil in ("D:evil.txt", "CON", "NUL"):
        archive = tmp_path / f"evil_{abs(hash(evil)) % 10000}.zip"
        _make_zip(str(archive), {evil: b"x", "ok.txt": b"y"})
        staging = tmp_path / f"staging_{abs(hash(evil)) % 10000}"
        staging.mkdir()
        with pytest.raises(ArchiveSafetyError):
            extract_zip(str(archive), str(staging))
        assert not list(staging.iterdir())


def test_zip_encrypted_archive_raises_install_error(tmp_path):
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("secret.txt", b"secret data")
    raw = bytearray(buf.getvalue())
    cd_idx = raw.find(b"PK\x01\x02")
    assert cd_idx != -1
    raw[cd_idx + 8] |= 0x1

    archive = tmp_path / "encrypted.zip"
    archive.write_bytes(raw)

    staging = tmp_path / "staging_enc"
    staging.mkdir()
    with pytest.raises(ArchiveInstallError, match=r"encrypted|password"):
        extract_zip(str(archive), str(staging))


def test_zip_extract_cp437_utf8_filename(tmp_path):
    import io

    # Write a zip where UTF-8 bytes were written into an archive with bit 11 cleared.
    ascii_placeholder = b"cafe__game.exe"
    utf8_target = "café_game.exe".encode("utf-8")
    assert len(ascii_placeholder) == len(utf8_target)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(ascii_placeholder.decode("ascii"), b"game payload")

    raw = bytearray(buf.getvalue().replace(ascii_placeholder, utf8_target))
    archive = tmp_path / "cp437.zip"
    archive.write_bytes(raw)

    staging = tmp_path / "staging_cp437"
    staging.mkdir()
    count = extract_zip(str(archive), str(staging))
    assert count == 1
    assert (staging / "café_game.exe").is_file()


def test_hardlink_escape_detected(tmp_path):
    staging = tmp_path / "staging_hardlink"
    staging.mkdir()
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("outside data")
    try:
        os.link(str(outside), str(staging / "link.txt"))
    except (OSError, NotImplementedError):
        pytest.skip("Hard links not supported in this environment")

    with pytest.raises(ArchiveSafetyError, match=r"outside the destination"):
        check_symlinks_inside_staging(str(staging))


def test_uncompressed_size_7z_with_tool(monkeypatch, tmp_path):
    import core.archive_install as ai

    archive = str(tmp_path / "game.7z")
    (tmp_path / "game.7z").touch()

    monkeypatch.setattr(
        ai, "detect_archive_tool", lambda _p, **_kw: ("/usr/bin/7z", None)
    )

    class _Listing:
        returncode = 0
        stdout = (
            "Path = game.7z\n"
            "Type = 7z\n"
            "----\n"
            "Path = Game.exe\n"
            "Size = 10485760\n"
            "Path = data.bin\n"
            "Size = 5242880\n"
        )
        stderr = ""

    monkeypatch.setattr(ai.subprocess, "run", lambda *a, **k: _Listing())
    size = ai.uncompressed_size(archive)
    assert size == 10485760 + 5242880


def test_7z_tool_non_interactive_flags_passed(monkeypatch, tmp_path):
    import core.archive_install as ai

    archive = str(tmp_path / "game.7z")
    staging = tmp_path / "staging_tool"
    staging.mkdir()

    calls: list[dict[str, object]] = []

    def _mock_run(cmd, **kwargs):
        calls.append({"cmd": list(cmd), "kwargs": kwargs})

        class _Res:
            returncode = 0
            stdout = "Path = game.7z\nType = 7z\nPath = Game.exe\n"
            stderr = ""

        return _Res()

    monkeypatch.setattr(ai.subprocess, "run", _mock_run)
    ai.extract_with_tool("/usr/bin/7z", archive, str(staging))

    extract_call = next(c for c in calls if "x" in c["cmd"])
    assert "-p" in extract_call["cmd"]
    assert "-y" in extract_call["cmd"]
    assert extract_call["kwargs"].get("stdin") == ai.subprocess.DEVNULL


def test_flatten_single_wrapper_ignores_macosx(tmp_path):
    staging = tmp_path / "staging_mac"
    staging.mkdir()
    game_dir = staging / "Game-1.0"
    game_dir.mkdir()
    (game_dir / "Game.exe").write_text("game payload")

    macosx_dir = staging / "__MACOSX"
    macosx_dir.mkdir()
    (macosx_dir / "._Game-1.0").write_text("metadata")

    (staging / ".DS_Store").write_text("ds_store")

    assert flatten_single_wrapper(str(staging)) is True
    assert (staging / "Game.exe").is_file()
    assert not (staging / "__MACOSX").exists()


def _simulate_flatpak_archive(monkeypatch, enabled=True):
    import core.host_platform as host_platform

    real_isfile = os.path.isfile

    def _isfile(path):
        if path == host_platform.FLATPAK_INFO_PATH:
            return enabled
        try:
            return real_isfile(path)
        except OSError:
            return False

    monkeypatch.setattr(os.path, "isfile", _isfile)
    monkeypatch.setattr(host_platform.os.path, "isfile", _isfile)


def test_extract_with_tool_flatpak_argv_and_default_path(monkeypatch, tmp_path):
    import core.archive_install as ai

    staging = tmp_path / "staging-flatpak"
    staging.mkdir()
    archive = str(tmp_path / "game.7z")

    monkeypatch.setattr(ai, "list_tool_entries", lambda _t, _a: ["Game.exe"])

    calls: list[list[str]] = []

    class _Res:
        returncode = 0
        stdout = ""
        stderr = ""

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return _Res()

    monkeypatch.setattr(ai.subprocess, "run", _run)

    # Default path is unchanged outside Flatpak.
    _simulate_flatpak_archive(monkeypatch, enabled=False)
    assert ai.extract_with_tool("/usr/bin/7z", archive, str(staging)) == 1
    assert calls[-1][0] == "/usr/bin/7z"

    # Inside Flatpak the extractor runs on the host.
    _simulate_flatpak_archive(monkeypatch, enabled=True)
    assert ai.extract_with_tool("/usr/bin/7z", archive, str(staging)) == 1
    assert calls[-1][:3] == ["flatpak-spawn", "--host", "--watch-bus"]
    sep = calls[-1].index("--")
    inner = calls[-1][sep + 1 :]
    assert inner[0] == "/usr/bin/7z"
    assert inner[1] == "x"
    assert inner[2] == archive


@pytest.mark.parametrize("bundled", [True, False])
def test_flatpak_tool_execution_for_listing_extraction_and_size(
    monkeypatch, tmp_path, bundled
):
    import core.archive_install as ai

    _simulate_flatpak_archive(monkeypatch)
    tool = ai.BUNDLED_7ZZ if bundled else "/usr/bin/7zz"
    monkeypatch.setattr(ai, "detect_archive_tool", lambda _p, **_kw: (tool, None))
    if bundled:
        def unexpected_host_env():
            raise AssertionError("Bundled tool must inherit the sandbox environment")
        monkeypatch.setattr(ai, "build_host_open_env", unexpected_host_env)
    else:
        monkeypatch.setattr(ai, "build_host_open_env", lambda: {"WLIB_TEST": "host"})
    archive = str(tmp_path / "game.7z")
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return ai.subprocess.CompletedProcess(
            cmd, 0, stdout=f"Path = {archive}\nPath = Game.exe\nSize = 42\n", stderr=""
        )

    monkeypatch.setattr(ai.subprocess, "run", run)
    assert ai.list_tool_entries(tool, archive) == ["Game.exe"]
    assert ai.extract_with_tool(tool, archive, str(tmp_path / "staging")) == 1
    assert ai.uncompressed_size(archive) == 42
    assert len(calls) == 4  # listing, extraction pre-check, extraction, size listing
    for argv, kwargs in calls:
        if bundled:
            assert argv[0] == tool
            assert "flatpak-spawn" not in argv
            assert kwargs["env"] is None
        else:
            assert argv[:3] == ["flatpak-spawn", "--host", "--watch-bus"]
            assert argv[argv.index("--") + 1] == tool
            assert "--env=WLIB_TEST=host" in argv
            assert kwargs["env"] == {"WLIB_TEST": "host"}


def test_install_checks_destination_when_selecting_tool(monkeypatch, tmp_path):
    import core.archive_install as ai

    _isolated_db(tmp_path, monkeypatch)
    library = tmp_path / "library"
    archive = tmp_path / "game.7z"
    archive.touch()
    checked_folders = []

    def detect(path, *, library_folder):
        checked_folders.append(library_folder)
        return "/usr/bin/7z", None

    def extract(tool, path, staging):
        from pathlib import Path

        (Path(staging) / "Game.exe").write_bytes(b"game")
        return 1

    monkeypatch.setattr(ai, "detect_archive_tool", detect)
    monkeypatch.setattr(ai, "extract_with_tool", extract)
    monkeypatch.setattr(
        ai.subprocess, "run",
        lambda *a, **kw: ai.subprocess.CompletedProcess(a[0], 0, stdout="Size = 4\n"),
    )
    result = ai.install_archive(
        str(archive), library_folder=str(library), pattern="{title}", title="Game"
    )
    assert result["success"] is True
    assert checked_folders == [str(library), str(library)]


def test_dot_entries_rejected(tmp_path):
    for evil in (".", "./", "/"):
        with pytest.raises(ArchiveSafetyError):
            check_archive_entry_safe(evil)
    archive = tmp_path / "dot.zip"
    _make_zip(str(archive), {".": b"x", "ok.txt": b"y"})
    staging = tmp_path / "staging-dot"
    staging.mkdir()
    with pytest.raises(ArchiveSafetyError):
        extract_zip(str(archive), str(staging))
    assert not list(staging.iterdir())


def test_duplicate_zip_entries_rejected(tmp_path):
    archive = tmp_path / "dup.zip"
    with zipfile.ZipFile(str(archive), "w") as zf:
        zf.writestr("a.txt", b"first")
        zf.writestr("a.txt", b"second")
    staging = tmp_path / "staging-dup"
    staging.mkdir()
    with pytest.raises(ArchiveSafetyError, match="duplicate"):
        extract_zip(str(archive), str(staging))
    assert not list(staging.iterdir())


@pytest.mark.parametrize("alias", ["./a.txt", "dir//a.txt", "dir/./a.txt"])
def test_dot_segment_aliases_collide(alias):
    from core.archive_install import check_archive_entries_no_collisions

    first = "a.txt" if alias.startswith("./") else "dir/a.txt"
    with pytest.raises(ArchiveSafetyError, match="duplicate"):
        check_archive_entries_no_collisions([first, alias])


def test_case_insensitive_collision_rejected(tmp_path):
    archive = tmp_path / "case.zip"
    _make_zip(str(archive), {"Game.exe": b"upper", "game.exe": b"lower"})
    staging = tmp_path / "staging-case"
    staging.mkdir()
    with pytest.raises(ArchiveSafetyError, match="[Cc]ollide|duplicate"):
        extract_zip(str(archive), str(staging))
    assert not list(staging.iterdir())


def test_external_tool_duplicate_precheck(monkeypatch, tmp_path):
    import core.archive_install as ai

    monkeypatch.setattr(ai, "list_tool_entries", lambda _t, _a: ["a.txt", "a.txt"])
    called = []

    def _fail(cmd, **kwargs):
        called.append(cmd)
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(ai.subprocess, "run", _fail)
    with pytest.raises(ArchiveSafetyError, match="duplicate"):
        ai.extract_with_tool("/usr/bin/7z", "game.7z", str(tmp_path / "s"))
    assert called == []


def test_external_tool_case_collision_precheck(monkeypatch, tmp_path):
    import core.archive_install as ai

    monkeypatch.setattr(ai, "list_tool_entries", lambda _t, _a: ["Game.exe", "game.exe"])

    def _fail(cmd, **kwargs):
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(ai.subprocess, "run", _fail)
    with pytest.raises(ArchiveSafetyError, match="[Cc]ollide"):
        ai.extract_with_tool("/usr/bin/7z", "game.7z", str(tmp_path / "s2"))


def test_install_same_folder_preserves_custom_exe(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    dest = lib / "MyAdventure"
    dest.mkdir(parents=True)
    (dest / "Custom.exe").write_text("old")
    (dest / "MyAdventure.exe").write_text("old-title")
    game_id = db.add_game("MyAdventure", str(dest / "Custom.exe"), version="1.0")
    assert game_id is not None
    archive = tmp_path / "upd.zip"
    _write_game_zip(archive, {"Custom.exe": b"new", "MyAdventure.exe": b"newtitle"})
    result = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}",
        title="MyAdventure",
        version="2.0",
        game_id=int(game_id),
    )
    assert result["success"] is True
    assert str(result["exe_path"]).endswith("Custom.exe")


def test_install_same_folder_reports_dropped_target(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    dest = lib / "MyAdventure"
    dest.mkdir(parents=True)
    (dest / "MyAdventure.exe").write_text("old")
    (dest / "Extra.exe").write_text("old")
    game_id = db.add_game("MyAdventure", str(dest / "MyAdventure.exe"), version="1.0")
    assert game_id is not None
    _ = db.add_game_launch_target(int(game_id), "Extra", str(dest / "Extra.exe"))
    archive = tmp_path / "upd.zip"
    _write_game_zip(archive, {"MyAdventure.exe": b"new"})
    result = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}",
        title="MyAdventure",
        version="2.0",
        game_id=int(game_id),
    )
    assert result["success"] is True
    unmoved = cast(list[dict[str, object]], result["unmoved_targets"])
    assert [t["label"] for t in unmoved] == ["Extra"]


def test_install_unknown_game_id_rejected(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    lib.mkdir()
    archive = tmp_path / "g.zip"
    _write_game_zip(archive, {"Game.exe": b"x"})
    with pytest.raises(ArchiveInstallError, match="not found"):
        install_archive(
            str(archive),
            library_folder=str(lib),
            pattern="{title}/{version}",
            title="Game",
            version="1.0",
            game_id=9999,
        )
    assert not (lib / "Game").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need Linux")
def test_flatten_relative_symlink_rechecked(tmp_path, monkeypatch):
    import core.archive_install as ai

    _isolated_db(tmp_path, monkeypatch)
    lib = tmp_path / "lib"
    lib.mkdir()
    archive = tmp_path / "wrap.zip"
    _write_game_zip(archive, {"Game/Game.exe": b"x"})
    real_extract = ai.extract_zip

    def _plant_symlink(archive_path, staging_dir):
        _ = real_extract(archive_path, staging_dir)
        os.symlink("../evil", os.path.join(staging_dir, "Game", "link"))
        return 2

    monkeypatch.setattr(ai, "extract_zip", _plant_symlink)
    with pytest.raises(ArchiveSafetyError, match="outside"):
        ai.install_archive(
            str(archive),
            library_folder=str(lib),
            pattern="{title}",
            title="Game",
            version="1.0",
        )
    assert not (lib / "Game").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFOs need Linux")
def test_apply_execute_bits_skips_fifo(tmp_path):
    import concurrent.futures

    fifo = tmp_path / "blocker"
    os.mkfifo(str(fifo))
    (tmp_path / "run.sh").write_text("#!/bin/sh\n")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(apply_execute_bits, str(tmp_path))
        changed = future.result(timeout=5)
    assert isinstance(changed, int)


def _override_game(tmp_path, monkeypatch, override_rel=None, override_abs=None):
    db = _isolated_db(tmp_path, monkeypatch)
    lib = tmp_path / "lib"
    old_dir = lib / "Game" / "1.0"
    old_dir.mkdir(parents=True)
    old_exe = old_dir / "Game.exe"
    old_exe.write_text("old")
    game_id = db.add_game("Game", str(old_exe), version="1.0")
    assert game_id is not None
    override = override_abs or (str(old_dir / override_rel) if override_rel else "")
    if override:
        db.update_game(int(game_id), {"save_path_override": override})
    archive = tmp_path / "Game-2.0.zip"
    _write_game_zip(archive, {"Game.exe": b"new"})
    return db, lib, old_dir, int(game_id), archive


def _update(archive, lib, game_id, **kwargs):
    from core.archive_install import install_archive

    return install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}/{version}",
        title="Game",
        version="2.0",
        game_id=game_id,
        remove_old=False,
        **kwargs,
    )


def test_install_carries_override_inside_old_root_and_repoints(tmp_path, monkeypatch):
    db, lib, old_dir, game_id, archive = _override_game(
        tmp_path, monkeypatch, override_rel="MySaves"
    )
    (old_dir / "MySaves" / "sub").mkdir(parents=True)
    (old_dir / "MySaves" / "a.save").write_text("user")
    (old_dir / "MySaves" / "sub" / "b.save").write_text("deep")

    result = _update(archive, lib, game_id)

    new_dir = lib / "Game" / "2.0"
    assert result["success"] is True
    assert (new_dir / "MySaves" / "a.save").read_text() == "user"
    assert (new_dir / "MySaves" / "sub" / "b.save").read_text() == "deep"
    assert db.get_all_games()[0]["save_path_override"] == str(new_dir / "MySaves")


def test_install_user_override_files_win_over_shipped_ones(tmp_path, monkeypatch):
    db, lib, old_dir, game_id, archive = _override_game(
        tmp_path, monkeypatch, override_rel="saves-x"
    )
    (old_dir / "saves-x").mkdir()
    (old_dir / "saves-x" / "a.save").write_text("user")
    _write_game_zip(archive, {"Game.exe": b"new", "saves-x/a.save": b"shipped"})

    assert _update(archive, lib, game_id)["success"] is True

    assert (lib / "Game" / "2.0" / "saves-x" / "a.save").read_text() == "user"


def test_install_leaves_override_outside_old_root_alone(tmp_path, monkeypatch):
    outside = tmp_path / "elsewhere" / "saves"
    outside.mkdir(parents=True)
    (outside / "a.save").write_text("keep")
    db, lib, _old, game_id, archive = _override_game(
        tmp_path, monkeypatch, override_abs=str(outside)
    )

    assert _update(archive, lib, game_id)["success"] is True

    assert db.get_all_games()[0]["save_path_override"] == str(outside)
    assert (outside / "a.save").read_text() == "keep"
    assert not (lib / "Game" / "2.0" / "saves").exists()


def test_install_override_missing_on_disk_is_still_repointed(tmp_path, monkeypatch):
    db, lib, _old, game_id, archive = _override_game(
        tmp_path, monkeypatch, override_rel="NotCreatedYet"
    )

    assert _update(archive, lib, game_id)["success"] is True

    assert db.get_all_games()[0]["save_path_override"] == str(
        lib / "Game" / "2.0" / "NotCreatedYet"
    )


def test_install_override_copy_failure_leaves_game_on_old_version(
    tmp_path, monkeypatch
):
    db, lib, old_dir, game_id, archive = _override_game(
        tmp_path, monkeypatch, override_rel="MySaves"
    )
    (old_dir / "MySaves").mkdir()
    (old_dir / "MySaves" / "a.save").write_text("user")
    import core.archive_install as ai
    import core.game_saves as gs

    def _boom(_src, _dst, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(gs.shutil, "copy2", _boom)
    with pytest.raises(ai.ArchiveInstallError, match="carry saves"):
        _update(archive, lib, game_id)

    row = db.get_all_games()[0]
    assert row["version"] == "1.0"
    assert row["save_path_override"] == str(old_dir / "MySaves")
    assert (old_dir / "MySaves" / "a.save").read_text() == "user"
    assert not (lib / "Game" / "2.0").exists()


def test_install_nested_game_folder_carries_saves_targets_and_override(
    tmp_path, monkeypatch
):
    # A readme beside the game folder keeps the wrapper from being flattened,
    # so the executable sits one level below the archive root. Saves, the
    # saves override and launch targets are relative to the executable's
    # folder, not to the archive root.
    from pathlib import Path

    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    archive = tmp_path / "Game-1.0.zip"
    _write_game_zip(
        archive,
        {"readme.txt": b"hi", "Game/Game.exe": b"v1", "Game/Config.exe": b"cfg1"},
    )
    first = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}/{version}",
        title="Game",
        version="1.0",
    )
    old_dir = Path(str(first["exe_path"])).parent
    game_id = int(str(first["game_id"]))
    assert old_dir == lib / "Game" / "1.0" / "Game"
    assert db.add_game_launch_target(game_id, "Config", str(old_dir / "Config.exe"))
    db.update_game(game_id, {"save_path_override": str(old_dir / "MySaves")})
    renpy_saves = old_dir / "game" / "saves"
    renpy_saves.mkdir(parents=True)
    (renpy_saves / "1-1-LT1.save").write_text("mysave")
    (old_dir / "slot1.sav").write_text("loose")
    (old_dir / "MySaves").mkdir()
    (old_dir / "MySaves" / "a.save").write_text("user")

    update = tmp_path / "Game-2.0.zip"
    _write_game_zip(
        update,
        {"readme.txt": b"hi", "Game/Game.exe": b"v2", "Game/Config.exe": b"cfg2"},
    )
    result = install_archive(
        str(update),
        library_folder=str(lib),
        pattern="{title}/{version}",
        title="Game",
        version="2.0",
        game_id=game_id,
    )
    new_dir = Path(str(result["exe_path"])).parent
    assert result["success"] is True
    assert new_dir == lib / "Game" / "2.0" / "Game"
    assert result["unmoved_targets"] == []
    assert not old_dir.exists()
    assert (new_dir / "game" / "saves" / "1-1-LT1.save").read_text() == "mysave"
    assert (new_dir / "slot1.sav").read_text() == "loose"
    assert (new_dir / "MySaves" / "a.save").read_text() == "user"
    row = db.get_game(game_id)
    assert row["save_path_override"] == str(new_dir / "MySaves")
    targets = db.list_game_launch_targets(game_id)
    assert [target["exe_path"] for target in targets] == [str(new_dir / "Config.exe")]
    assert (new_dir / "Config.exe").is_file()


def test_install_same_folder_swap_keeps_override(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    lib = tmp_path / "lib"
    dest = lib / "Game"
    dest.mkdir(parents=True)
    old_exe = dest / "Game.exe"
    old_exe.write_text("old")
    (dest / "MySaves").mkdir()
    (dest / "MySaves" / "a.save").write_text("user")
    game_id = db.add_game("Game", str(old_exe), version="1.0")
    assert game_id is not None
    db.update_game(int(game_id), {"save_path_override": str(dest / "MySaves")})
    archive = tmp_path / "Game-2.0.zip"
    _write_game_zip(archive, {"Game.exe": b"new"})

    result = install_archive(
        str(archive),
        library_folder=str(lib),
        pattern="{title}",
        title="Game",
        version="2.0",
        game_id=int(game_id),
    )

    assert result["success"] is True
    assert db.get_all_games()[0]["save_path_override"] == str(dest / "MySaves")
    assert (dest / "MySaves" / "a.save").read_text() == "user"


def test_install_override_equal_to_old_root_is_repointed_not_copied(
    tmp_path, monkeypatch
):
    db, lib, old_dir, game_id, archive = _override_game(
        tmp_path, monkeypatch, override_abs=""
    )
    db.update_game(game_id, {"save_path_override": str(old_dir)})
    (old_dir / "slot.sav").write_text("loose")

    assert _update(archive, lib, game_id)["success"] is True

    new_dir = lib / "Game" / "2.0"
    assert db.get_all_games()[0]["save_path_override"] == str(new_dir)
    assert (new_dir / "Game.exe").read_bytes() == b"new"  # not clobbered by the old copy


@pytest.mark.parametrize("known_engine", [True, False])
def test_install_detects_engine_before_destination(tmp_path, monkeypatch, known_engine):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    archive = tmp_path / "Game.zip"
    files = {"Wrapper/Game.exe": b"x", "Wrapper/readme.txt": b"hi"}
    if known_engine:
        files["Wrapper/renpy/__init__.py"] = b""
    _write_game_zip(archive, files)
    result = install_archive(
        str(archive),
        library_folder=str(tmp_path / "lib"),
        pattern="{engine}/{title}/{version}",
        title="Game",
        version="1.0",
    )
    engine = "Ren'Py" if known_engine else ""
    expected = tmp_path / "lib"
    if engine:
        expected /= engine
    assert result["destination"] == str(expected / "Game/1.0")
    assert db.get_all_games()[0]["engine"] == engine


def test_detected_destination_collision_cleans_staging(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import ArchiveInstallError, install_archive

    destination = tmp_path / "lib/Ren'Py/Game/1.0"
    destination.mkdir(parents=True)
    (destination / "keep.txt").write_text("keep")
    archive = tmp_path / "Game.zip"
    _write_game_zip(archive, {"Wrapper/Game.exe": b"x", "Wrapper/renpy/init.py": b""})
    with pytest.raises(ArchiveInstallError, match="already exists"):
        install_archive(
            str(archive),
            library_folder=str(tmp_path / "lib"),
            pattern="{engine}/{title}/{version}",
            title="Game",
            version="1.0",
        )
    assert (destination / "keep.txt").read_text() == "keep"
    assert not list((tmp_path / "lib").glob(".wlib-staging-*"))
    assert db.get_all_games() == []


def test_install_metadata_only_applied_to_new_games(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    archive = tmp_path / "Game.zip"
    _write_game_zip(archive, {"Game.exe": b"x"})
    options = dict(
        library_folder=str(tmp_path / "lib"), pattern="{title}/{version}", title="Game"
    )
    first = install_archive(
        str(archive),
        **options,
        version="1",
        cover_image="cover.jpg",
        tags=["3d"],
        rating="4.5",
    )
    install_archive(
        str(archive),
        **options,
        game_id=first["game_id"],
        version="2",
        cover_image="other.jpg",
        tags="other",
        rating="1",
    )
    game = db.get_all_games()[0]
    assert game["cover_image_path"] == "cover.jpg"
    assert game["tags"] == "3d"
    assert game["rating"] == "4.5"


def test_update_without_engine_keeps_stored_engine(tmp_path, monkeypatch):
    db = _isolated_db(tmp_path, monkeypatch)
    from core.archive_install import install_archive

    first_archive = tmp_path / "Game-1.zip"
    _write_game_zip(first_archive, {"Game.exe": b"x"})
    first = install_archive(
        str(first_archive),
        library_folder=str(tmp_path / "lib"),
        pattern="{engine}/{title}/{version}",
        title="Game",
        version="1",
        engine="RPGM",
    )
    update_archive = tmp_path / "Game-2.zip"
    _write_game_zip(update_archive, {"Game.exe": b"y", "renpy/__init__.py": b""})
    result = install_archive(
        str(update_archive),
        library_folder=str(tmp_path / "lib"),
        pattern="{engine}/{title}/{version}",
        title="Game",
        version="2",
        game_id=first["game_id"],
    )
    assert result["destination"] == str(tmp_path / "lib/RPGM/Game/2")
    assert db.get_all_games()[0]["engine"] == "RPGM"


def test_bundled_tool_used_when_library_folder_not_created_yet(monkeypatch, tmp_path):
    import core.archive_install as ai

    bundled = tmp_path / "7zz"
    bundled.write_bytes(b"")
    archive = tmp_path / "Game.7z"
    archive.write_bytes(b"")
    monkeypatch.setattr(ai, "BUNDLED_7ZZ", str(bundled))
    monkeypatch.setattr(ai, "is_flatpak", lambda: True)
    monkeypatch.setattr(ai, "host_which", lambda _name: None)

    tool, error = ai.detect_archive_tool(
        str(archive), library_folder=str(tmp_path / "missing" / "library")
    )
    assert (tool, error) == (str(bundled), None)
