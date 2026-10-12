# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import zipfile

import pytest

from core.archive_install import (
    ArchiveSafetyError,
    apply_execute_bits,
    build_destination,
    can_remove_old_folder,
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
