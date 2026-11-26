# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core.storage_cleanup actions, protections and report updates."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import cast

from core import storage_usage
from core.storage_cleanup import (
    ACTION_ORDER,
    ACTION_TIERS,
    PREVIEW_TTL_SECONDS,
    CleanupContext,
    StorageCleanupService,
    measure_all_actions,
)
from core.storage_usage import MeasureBudget, PlanInput, StorageScanService


def _make_services(
    tmp_path: Path,
    ctx_factory,
    games: list[dict[str, object]] | None = None,
    clock=None,
) -> tuple[StorageScanService, StorageCleanupService]:
    data_dir = tmp_path / "data"
    lib = tmp_path / "Library"
    lib.mkdir(exist_ok=True)

    scan = StorageScanService(
        running_games=lambda: ctx_factory().running_games(),
        load_plan_input=lambda: PlanInput(
            games=games or [],
            library_folders=[str(lib)],
            pattern="{creator}/{title}/{version}",
            wine_prefix_path="",
            data_dir=str(data_dir),
            picked_roots={},
            home_dir=str(tmp_path / "Home"),
        ),
        measure_actions=lambda budget: measure_all_actions(ctx_factory(), budget),
        data_dir=lambda: str(data_dir),
    )
    cleanup = StorageCleanupService(scan, ctx_factory, clock=clock or (lambda: 1000.0))
    return scan, cleanup


def test_cleanup_expired_preview_target_left_list_symlink_and_scan_lock(
    tmp_path: Path, monkeypatch
):
    data_dir = tmp_path / "data"
    removed_dir = data_dir / "sandbox" / "removed" / "10_2026-01-01_00-00-00"
    removed_dir.mkdir(parents=True)
    _ = (removed_dir / "save.dat").write_bytes(b"s" * 8192)

    # Symlink inside the removed sandbox pointing to an outside Documents folder.
    docs = tmp_path / "Documents"
    docs.mkdir()
    doc_file = docs / "important.txt"
    _ = doc_file.write_text("keep me", encoding="utf-8")
    os.symlink(docs, removed_dir / "docs_link")

    now = [1000.0]
    ctx = CleanupContext(data_dir=lambda: str(data_dir))
    scan, cleanup = _make_services(tmp_path, lambda: ctx, clock=lambda: now[0])

    # 1. Expired preview (> 15 min) is refused and deletes nothing.
    prev = cleanup.preview("removed_game_data")
    assert prev["success"] is True
    assert prev["count"] == 1
    assert "saves" in str(prev["warning"]).lower()

    now[0] += PREVIEW_TTL_SECONDS + 1.0
    expired_res = cleanup.run(str(prev["preview_id"]))
    assert expired_res["success"] is False
    assert expired_res["code"] == "preview_expired"
    assert removed_dir.is_dir()

    # 2. Target that became protected between preview and run is skipped.
    dl_dir = data_dir / "downloads"
    dl_dir.mkdir(parents=True)
    a1 = dl_dir / "a1.zip"
    a2 = dl_dir / "a2.zip"
    _ = a1.write_bytes(b"1" * 4096)
    _ = a2.write_bytes(b"2" * 4096)
    open_set: set[str] = set()
    ctx2 = CleanupContext(
        data_dir=lambda: str(data_dir),
        open_archives=lambda: open_set,
    )
    _scan2, cleanup2 = _make_services(tmp_path, lambda: ctx2, clock=lambda: now[0])
    prev_dl = cleanup2.preview("download_archives")
    assert prev_dl["count"] == 2
    open_set.add(str(a1))
    run_dl = cleanup2.run(str(prev_dl["preview_id"]))
    assert run_dl["success"] is True
    assert run_dl["removed"] == 1
    assert run_dl["skipped"] == 1
    assert a1.is_file()
    assert not a2.exists()

    # 3. Symbolic link inside removed sandbox is unlinked; Documents stay untouched.
    prev_rem = cleanup.preview("removed_game_data")
    run_rem = cleanup.run(str(prev_rem["preview_id"]))
    assert run_rem["success"] is True
    assert run_rem["removed"] == 1
    assert not removed_dir.exists()
    assert doc_file.read_text(encoding="utf-8") == "keep me"

    # 4. Cleanup refused while a scan is running.
    lib = tmp_path / "Library"
    g_exe = lib / "G" / "g.exe"
    g_exe.parent.mkdir(parents=True)
    _ = g_exe.write_bytes(b"g" * 4096)
    entered = threading.Event()
    release = threading.Event()
    real_measure = storage_usage.measure

    def slow_measure(path: str | os.PathLike[str], budget: MeasureBudget | None = None):
        entered.set()
        _ = release.wait(timeout=2.0)
        return real_measure(path, budget)

    scan_busy, cleanup_busy = _make_services(
        tmp_path,
        lambda: ctx,
        games=[{"id": 1, "title": "G", "exe_path": str(g_exe)}],
    )
    monkeypatch.setattr(storage_usage, "measure", slow_measure)
    assert scan_busy.start_scan()["success"] is True
    assert entered.wait(timeout=2.0)
    refused = cleanup_busy.preview("removed_game_data")
    assert refused["success"] is False
    assert refused["code"] == "task_running"
    release.set()
    assert scan_busy.wait_idle(timeout=5.0)
    monkeypatch.setattr(storage_usage, "measure", real_measure)


def test_download_archives_and_unpack_leftovers_protections(tmp_path: Path):
    data_dir = tmp_path / "data"
    dl_dir = data_dir / "downloads"
    dl_dir.mkdir(parents=True)

    part_file = dl_dir / "resume-abc.part"
    open_archive = dl_dir / "open.zip"
    ready_archive = dl_dir / "ready.zip"
    stale_archive = dl_dir / "stale.zip"
    runner_archive = dl_dir / "rpgm-runner-abc123"
    for f in (part_file, open_archive, ready_archive, stale_archive, runner_archive):
        _ = f.write_bytes(b"z" * 4096)

    torrent_file = dl_dir / "torrents" / "deadbeef" / "payload.bin"
    torrent_file.parent.mkdir(parents=True)
    _ = torrent_file.write_bytes(b"t" * 8192)

    scratch_child = data_dir / "install-scratch" / "wlib-stage-1"
    scratch_child.mkdir(parents=True)
    _ = (scratch_child / "tmp.bin").write_bytes(b"u" * 4096)

    install_running = [False]
    runner_prep = [False]

    ctx = CleanupContext(
        data_dir=lambda: str(data_dir),
        open_archives=lambda: [str(open_archive)],
        ready_queue_archives=lambda: [str(ready_archive)],
        archive_install_running=lambda: install_running[0],
        rpgm_runner_preparing=lambda: runner_prep[0],
    )
    _scan, cleanup = _make_services(tmp_path, lambda: ctx)

    # A runner install unpacks its archive from the download folder.
    runner_prep[0] = True
    prev = cleanup.preview("download_archives")
    assert prev["success"] is True
    assert prev["count"] == 1
    run = cleanup.run(str(prev["preview_id"]))
    assert run["success"] is True
    assert run["removed"] == 1
    assert not stale_archive.exists()
    assert runner_archive.is_file()
    runner_prep[0] = False
    assert part_file.is_file()
    assert open_archive.is_file()
    assert ready_archive.is_file()
    assert torrent_file.is_file()

    # Unpack leftovers blocked while archive install or RPGM runner preparation runs.
    install_running[0] = True
    blocked1 = cleanup.preview("unpack_leftovers")
    assert blocked1["success"] is False
    assert blocked1["code"] == "action_blocked"

    install_running[0] = False
    runner_prep[0] = True
    blocked2 = cleanup.preview("unpack_leftovers")
    assert blocked2["success"] is False
    assert blocked2["code"] == "action_blocked"

    runner_prep[0] = False
    prev_sc = cleanup.preview("unpack_leftovers")
    assert prev_sc["success"] is True
    assert prev_sc["count"] == 1
    run_sc = cleanup.run(str(prev_sc["preview_id"]))
    assert run_sc["success"] is True
    assert not scratch_child.exists()


def test_save_backups_keep_3_and_keep_all(tmp_path: Path):
    data_dir = tmp_path / "data"
    game_backups = data_dir / "save-backups" / "7"
    stamps = [
        "2026-01-01_10-00-00",
        "2026-01-02_10-00-00",
        "2026-01-03_10-00-00",
        "2026-01-04_10-00-00",
        "2026-01-05_10-00-00",
    ]
    for stamp in stamps:
        folder = game_backups / stamp
        folder.mkdir(parents=True)
        _ = (folder / "save.sav").write_bytes(b"s" * 4096)

    keep_val: list[int | None] = [None]
    ctx = CleanupContext(
        data_dir=lambda: str(data_dir),
        save_backup_keep=lambda: keep_val[0],
    )
    _scan, cleanup = _make_services(tmp_path, lambda: ctx)

    # Keep all -> blocked and names the setting.
    keep_all_res = cleanup.preview("save_backups")
    assert keep_all_res["success"] is False
    assert "Keep all" in str(keep_all_res["error"])

    # Keep 3 with 5 backups -> removes the 2 oldest backups.
    keep_val[0] = 3
    prev = cleanup.preview("save_backups")
    assert prev["success"] is True
    assert prev["count"] == 2
    run = cleanup.run(str(prev["preview_id"]))
    assert run["success"] is True
    assert run["removed"] == 2
    remaining = sorted(p.name for p in game_backups.iterdir())
    assert remaining == stamps[2:]


def test_proton_cover_cache_and_bepinex_actions(tmp_path: Path):
    data_dir = tmp_path / "data"

    # Proton builds: one used by a game, one default, one unused.
    proton_root = data_dir / "proton"
    used_game = proton_root / "GE-Proton8-1"
    used_default = proton_root / "GE-Proton9-1"
    unused = proton_root / "GE-Proton7-1"
    for p in (used_game, used_default, unused):
        p.mkdir(parents=True)
        _ = (p / "proton").write_bytes(b"p" * 8192)

    # Covers: local cover stays, cached cover deleted via purge callback.
    covers_dir = data_dir / "covers"
    cache_dir = covers_dir / "cache"
    cache_dir.mkdir(parents=True)
    local_cover = covers_dir / "0123456789abcdef0123456789abcdef.jpg"
    cached_cover = cache_dir / "fedcba9876543210fedcba9876543210.jpg"
    _ = local_cover.write_bytes(b"L" * 4096)
    _ = cached_cover.write_bytes(b"C" * 8192)

    # BepInEx cache.
    bep_dir = data_dir / "bepinex-cache"
    bep_dir.mkdir(parents=True)
    bep_zip = bep_dir / "BepInEx.zip"
    _ = bep_zip.write_bytes(b"B" * 8192)

    running_games: list[int] = [1]
    bep_running = [True]
    purge_called = [0]

    def fake_purge() -> dict[str, object]:
        purge_called[0] += 1
        for child in cache_dir.iterdir():
            child.unlink()
        return {"success": True}

    ctx = CleanupContext(
        data_dir=lambda: str(data_dir),
        running_games=lambda: running_games,
        default_proton_path=lambda: str(used_default / "proton"),
        game_proton_versions=lambda: [str(used_game)],
        bepinex_running=lambda: bep_running[0],
        purge_cover_cache=fake_purge,
    )
    _scan, cleanup = _make_services(tmp_path, lambda: ctx)

    # Proton blocked while game runs.
    p_blocked = cleanup.preview("proton_builds")
    assert p_blocked["success"] is False
    assert p_blocked["code"] == "action_blocked"

    running_games.clear()
    p_prev = cleanup.preview("proton_builds")
    assert p_prev["success"] is True
    assert p_prev["count"] == 1
    p_run = cleanup.run(str(p_prev["preview_id"]))
    assert p_run["success"] is True
    assert p_run["removed"] == 1
    assert used_game.is_dir()
    assert used_default.is_dir()
    assert not unused.exists()

    # Cover cache calls purge_cover_cache() and keeps local cover.
    c_prev = cleanup.preview("cover_cache")
    assert c_prev["success"] is True
    assert c_prev["count"] == 1
    c_run = cleanup.run(str(c_prev["preview_id"]))
    assert c_run["success"] is True
    assert purge_called[0] == 1
    assert not cached_cover.exists()
    assert local_cover.is_file()

    # BepInEx blocked while BepInEx install runs, then succeeds.
    b_blocked = cleanup.preview("bepinex_cache")
    assert b_blocked["success"] is False
    bep_running[0] = False
    b_prev = cleanup.preview("bepinex_cache")
    assert b_prev["success"] is True
    assert b_prev["count"] == 1
    b_run = cleanup.run(str(b_prev["preview_id"]))
    assert b_run["success"] is True
    assert not bep_zip.exists()


def test_cleanup_report_update_matches_fresh_scan(tmp_path: Path):
    data_dir = tmp_path / "data"
    lib = tmp_path / "Library"
    g_exe = lib / "Hero" / "hero.exe"
    g_exe.parent.mkdir(parents=True)
    _ = g_exe.write_bytes(b"h" * 16384)

    # Save backups: 4 backups, keep 2 -> 2 removed.
    saves_dir = data_dir / "save-backups" / "1"
    for stamp in (
        "2026-01-01_00-00-00",
        "2026-01-02_00-00-00",
        "2026-01-03_00-00-00",
        "2026-01-04_00-00-00",
    ):
        d = saves_dir / stamp
        d.mkdir(parents=True)
        _ = (d / "slot.sav").write_bytes(b"s" * 8192)

    dl_dir = data_dir / "downloads"
    dl_dir.mkdir(parents=True)
    _ = (dl_dir / "old.zip").write_bytes(b"d" * 12288)

    games: list[dict[str, object]] = [
        {"id": 1, "title": "Hero", "exe_path": str(g_exe)}
    ]
    ctx = CleanupContext(
        data_dir=lambda: str(data_dir),
        save_backup_keep=lambda: 2,
    )
    scan, cleanup = _make_services(tmp_path, lambda: ctx, games=games)

    assert scan.start_scan()["success"] is True
    assert scan.wait_idle(timeout=5.0)

    # Clean save backups and download archives.
    p1 = cleanup.preview("save_backups")
    r1 = cleanup.run(str(p1["preview_id"]))
    assert r1["success"] is True
    p2 = cleanup.preview("download_archives")
    r2 = cleanup.run(str(p2["preview_id"]))
    assert r2["success"] is True

    updated = scan.get_report()
    assert updated is not None

    # Compare updated report against a fresh full scan of the exact same tree.
    assert scan.start_scan()["success"] is True
    assert scan.wait_idle(timeout=5.0)
    fresh = scan.get_report()
    assert fresh is not None

    assert updated["total_bytes"] == fresh["total_bytes"]
    assert updated["total_files"] == fresh["total_files"]
    assert updated["categories"] == fresh["categories"]
    assert updated["games"]["1"]["parts"] == fresh["games"]["1"]["parts"]
    assert updated["games"]["1"]["total_bytes"] == fresh["games"]["1"]["total_bytes"]
    assert updated["actions"] == fresh["actions"]
    assert cast(int, r1["freed_bytes"]) > 0
    assert cast(int, r2["freed_bytes"]) > 0


def test_cover_cache_targets_skip_local_cover_cards(tmp_path: Path):
    from core.storage_cleanup import list_action_targets

    data_dir = tmp_path / "data"
    covers = data_dir / "covers"
    (covers / "cache").mkdir(parents=True)
    (covers / "cards").mkdir(parents=True)
    _ = (covers / "local1.png").write_bytes(b"l" * 4096)
    _ = (covers / "cache" / "remote1.jpg").write_bytes(b"r" * 4096)
    for name in (
        "local1.still.webp",
        "remote1.still.webp",
        "orphan1.anim.webp",
        ".card-tmp.webp",
    ):
        _ = (covers / "cards" / name).write_bytes(b"c" * 4096)

    ctx = CleanupContext(data_dir=lambda: str(data_dir))
    names = sorted(Path(t.path).name for t in list_action_targets("cover_cache", ctx))
    assert names == ["orphan1.anim.webp", "remote1.jpg", "remote1.still.webp"]


def test_every_action_has_a_risk_tier_in_statuses_and_preview(tmp_path: Path):
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "covers" / "cache"
    cache_dir.mkdir(parents=True)
    _ = (cache_dir / "1.jpg").write_bytes(b"c" * 4096)
    ctx = CleanupContext(data_dir=lambda: str(data_dir))
    _scan, cleanup = _make_services(tmp_path, lambda: ctx)

    assert set(ACTION_TIERS) == set(ACTION_ORDER)
    # Caches and leftovers that come back by themselves are safe; files that
    # can hold saves are risky; files that need a new download need a review.
    assert ACTION_TIERS == {
        "download_archives": "review",
        "unpack_leftovers": "safe",
        "removed_game_data": "risky",
        "save_backups": "risky",
        "proton_builds": "review",
        "cover_cache": "safe",
        "bepinex_cache": "safe",
    }

    statuses = cleanup.action_statuses(None)
    assert {str(s["id"]): s["tier"] for s in statuses} == ACTION_TIERS

    prev = cleanup.preview("cover_cache")
    assert prev["success"] is True
    assert prev["tier"] == "safe"


def test_save_backup_copy_uses_real_plurals(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    def statuses_for(keep: int | None) -> dict[str, dict[str, object]]:
        ctx = CleanupContext(
            data_dir=lambda: str(data_dir), save_backup_keep=lambda: keep
        )
        _scan, cleanup = _make_services(tmp_path, lambda: ctx)
        return {str(s["id"]): s for s in cleanup.action_statuses(None)}

    assert statuses_for(1)["save_backups"]["what_stays"] == (
        "The newest backup for each game stays."
    )
    assert statuses_for(3)["save_backups"]["what_stays"] == (
        "The newest 3 backups for each game stay."
    )
    assert statuses_for(None)["save_backups"]["what_stays"] == "All save backups stay."
    assert statuses_for(3)["unpack_leftovers"]["label"] == "Temporary install files"


def test_preview_names_the_largest_items_for_the_confirmation(tmp_path: Path):
    data_dir = tmp_path / "data"
    sandbox = data_dir / "sandbox" / "removed" / "12_2026-11-20_10-30-00"
    player = data_dir / "web-player" / "removed" / "12_2026-11-20_10-30-00"
    odd = data_dir / "sandbox" / "removed" / "leftover"
    for folder, size in ((sandbox, 16384), (player, 8192), (odd, 4096)):
        folder.mkdir(parents=True)
        _ = (folder / "data.bin").write_bytes(b"x" * size)
    backups = data_dir / "save-backups" / "7"
    for name in (
        "2026-11-01_09-00-00",
        "2026-11-02_09-00-00",
        "2026-11-03_09-00-00-1",
    ):
        (backups / name).mkdir(parents=True)
        _ = (backups / name / "save.dat").write_bytes(b"s" * 4096)
    downloads = data_dir / "downloads"
    downloads.mkdir()
    for index in range(10):
        _ = (downloads / f"game-{index}.zip").write_bytes(b"z" * (4096 * (index + 1)))
    cache = data_dir / "covers" / "cache"
    cache.mkdir(parents=True)
    _ = (cache / "3f2a9c.jpg").write_bytes(b"c" * 4096)

    ctx = CleanupContext(
        data_dir=lambda: str(data_dir),
        save_backup_keep=lambda: 1,
        game_title=lambda gid: "Midnight Harbor" if gid == 7 else "",
    )
    _scan, cleanup = _make_services(tmp_path, lambda: ctx)

    removed = cleanup.preview("removed_game_data")
    assert [
        item["label"] for item in cast(list[dict[str, object]], removed["items"])
    ] == [
        "Sandbox of a game removed 2026-11-20 10:30",
        "Built-in player data of a game removed 2026-11-20 10:30",
        "Sandbox: leftover",
    ]
    assert removed["more_items"] == 0

    saves = cleanup.preview("save_backups")
    labels = sorted(
        str(item["label"]) for item in cast(list[dict[str, object]], saves["items"])
    )
    assert labels == [
        "Midnight Harbor: backup from 2026-11-01 09:00",
        "Midnight Harbor: backup from 2026-11-02 09:00",
    ]

    archives = cleanup.preview("download_archives")
    items = cast(list[dict[str, object]], archives["items"])
    assert len(items) == 8
    assert items[0]["label"] == "game-9.zip"
    assert [int(cast(int, item["bytes"])) for item in items] == sorted(
        (int(cast(int, item["bytes"])) for item in items), reverse=True
    )
    assert archives["more_items"] == 2

    # Hashed cache names mean nothing to the user: counts only.
    covers = cleanup.preview("cover_cache")
    assert covers["items"] == []
    assert covers["more_items"] == 0
