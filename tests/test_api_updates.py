# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import io
import json
import os
import ssl
import tarfile
import zipfile
from pathlib import Path
from typing import cast
from urllib.error import URLError
from unittest.mock import MagicMock

import pytest

from core.api import DEFAULT_PLAYWRIGHT_BROWSERS_PATH, Api
from core.database import (
    RPGMAKER_LINUX_RUNNER_SETTING,
    init_db,
    add_game,
    add_game_launch_target,
    get_all_games,
    get_setting,
    update_game,
    update_setting,
)


class _FakeDownloadResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        _ = (exc_type, exc_value, traceback)
        return None


def test_runner_discovery_native_flatpak_and_dedup(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr("core.api.get_proton_dir", lambda: str(tmp_path / "wlib-proton"))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/wine" if name == "wine" else None)
    locations = {
        "wlib-proton/Managed/proton": "Managed",
        ".steam/steam/compatibilitytools.d/SteamGE/proton": "SteamGE (Steam)",
        ".local/share/Steam/steamapps/common/Proton 9/proton": "Proton 9 (Steam)",
        ".var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d/FlatGE/proton": "FlatGE (Steam)",
        ".var/app/com.valvesoftware.Steam/.local/share/Steam/compatibilitytools.d/OtherGE/proton": "OtherGE (Steam)",
        ".local/share/lutris/runners/wine/wine-ge/bin/wine": "wine-ge (Lutris)",
        ".var/app/net.lutris.Lutris/data/lutris/runners/wine/flat-wine/bin/wine": "flat-wine (Lutris)",
    }
    for relative in locations:
        path = tmp_path / relative
        path.parent.mkdir(parents=True)
        path.touch()
    result = Api().get_available_runners()
    runners = result["runners"]
    assert {runner["name"] for runner in runners} == {"System Wine", *locations.values()}
    assert {runner["path"] for runner in runners} == {"wine", *(str(tmp_path / relative) for relative in locations)}
    assert len({os.path.realpath(runner["path"]) for runner in runners}) == len(runners)
    try:
        (tmp_path / ".steam/root").symlink_to(tmp_path / ".local/share/Steam", target_is_directory=True)
    except OSError:
        # A Windows junction resolves the same way without symlink privileges.
        import subprocess
        if os.name != "nt":
            raise
        result = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(tmp_path / ".steam/root"), str(tmp_path / ".local/share/Steam")],
            capture_output=True, check=False,
        )
        assert result.returncode == 0
    runners = Api().get_available_runners()["runners"]
    assert len(runners) == len(locations) + 1


def test_api_running_and_stop_bridge(monkeypatch):
    api = Api()
    monkeypatch.setattr(api.launcher, "get_running_games", lambda: [1, 3])
    monkeypatch.setattr(api.launcher, "stop", lambda game_id: {"success": game_id == 3})
    assert api.get_running_games() == [1, 3]
    assert api.stop_game(3) == {"success": True}


def test_custom_status_settings_validation_and_removal():
    api = Api()
    game_id = add_game("Backlog", "/tmp/game.exe")
    assert game_id is not None
    assert api.save_settings({"custom_play_statuses": [" Backlog "]})["success"]
    assert api.get_settings()["custom_play_statuses"] == ["Backlog"]
    update_game(game_id, {"play_status": "Backlog"})
    for invalid in (["backlog", "Backlog"], ["playing"], ["Replaying"], ["in_progress"], [""], ["x" * 41], [7], "Backlog"):
        assert api.save_settings({"custom_play_statuses": invalid, "proton_path": "must-not-save"})["success"] is False
        assert api.get_settings()["custom_play_statuses"] == ["Backlog"]
        assert get_setting("proton_path") != "must-not-save"
    assert api.save_settings({"custom_play_statuses": []})["success"]
    assert get_all_games()[0]["play_status"] == "Not Started"


def test_urm_install_overwrite_remove_and_errors(tmp_path):
    folder = tmp_path / "RenPy"
    (folder / "renpy").mkdir(parents=True)
    (folder / "game").mkdir()
    target = folder / "game.exe"
    target.touch()
    game_id = add_game("RenPy", str(target))
    assert game_id is not None
    api = Api()
    assert api.get_urm_status(game_id) == {"success": True, "renpy": True, "installed": False, "source_configured": False}
    assert api.set_urm_installed(game_id, True)["success"] is False
    source = tmp_path / "mod.rpa"
    source.write_bytes(b"first")
    assert api.save_settings({"urm_rpa_path": str(source)})["success"]
    assert api.get_settings()["urm_rpa_path"] == str(source)
    installed = folder / "game" / "0x52_URM.rpa"
    assert api.set_urm_installed(game_id, True)["installed"]
    assert installed.read_bytes() == b"first"
    source.write_bytes(b"second")
    assert api.set_urm_installed(game_id, True)["installed"]
    assert installed.read_bytes() == b"second"
    source.unlink()
    assert api.set_urm_installed(game_id, True)["success"] is False
    assert installed.read_bytes() == b"second"
    assert api.set_urm_installed(game_id, False)["installed"] is False
    assert not installed.exists()
    assert api.set_urm_installed(game_id, False)["success"]
    other_id = add_game("Other", str(tmp_path / "game.exe"))
    assert other_id is not None
    assert api.get_urm_status(other_id)["renpy"] is False
    assert api.set_urm_installed(other_id, True)["success"] is False
    assert api.get_urm_status(999)["success"] is False
    assert api.set_urm_installed(999, True)["success"] is False


def test_urm_failed_copy_preserves_existing_mod(monkeypatch, tmp_path):
    folder = tmp_path / "RenPy"
    (folder / "renpy").mkdir(parents=True)
    (folder / "game").mkdir()
    installed = folder / "game" / "0x52_URM.rpa"
    installed.write_bytes(b"old")
    source = tmp_path / "mod.rpa"
    source.touch()
    update_setting("urm_rpa_path", str(source))
    game_id = add_game("RenPy", str(folder / "game.exe"))
    monkeypatch.setattr("shutil.copyfile", MagicMock(side_effect=OSError("copy failed")))
    result = Api().set_urm_installed(game_id, True)
    assert result["success"] is False
    assert installed.read_bytes() == b"old"
    assert list((folder / "game").iterdir()) == [installed]


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_wlib_api.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


@pytest.fixture(autouse=True)
def _linux_api_by_default(monkeypatch):
    monkeypatch.setattr("core.api.is_linux", lambda: True)
    monkeypatch.setattr("core.api.is_windows", lambda: False)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _redirect_cheat_engine_dir(monkeypatch, tmp_path):
    ce_dir = tmp_path / "home" / ".local" / "share" / "wLib" / "CheatEngine"
    monkeypatch.setattr("core.api.get_cheat_engine_dir", lambda: str(ce_dir))
    return ce_dir


def _redirect_extension_dir(monkeypatch, persistent_dir):
    monkeypatch.setattr(
        "core.api.get_extension_dir", lambda: str(persistent_dir)
    )


def _write_existing_cheat_engine(ce_dir, content: bytes = b"old engine"):
    executable = ce_dir / "Lunar Engine" / "lunarengine-x86_64.exe"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(content)
    return executable


@pytest.mark.parametrize(
    "member_name, checksum_ok",
    [("GE-Proton-test/proton", True), ("../escaped", True), ("GE-Proton-test/proton", False)],
)
def test_download_proton_ge_streams_and_filters_tar(
    monkeypatch, tmp_path, member_name, checksum_ok
):
    payload = b"proton executable"
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo(member_name)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    class ChunkedResponse(io.BytesIO):
        length = len(archive_buffer.getvalue())

        def read(self, size=-1):
            assert size > 0, "tarball reads must be bounded"
            return super().read(min(size, 32))

    release = {
        "tag_name": "GE-Proton-test",
        "assets": [
            {
                "name": "GE-Proton-test.tar.gz",
                "browser_download_url": "https://example.com/GE-Proton-test.tar.gz",
            },
            {
                "name": "GE-Proton-test.sha512sum",
                "browser_download_url": "https://example.com/GE-Proton-test.sha512sum",
            },
        ],
    }
    digest = hashlib.sha512(archive_buffer.getvalue() if checksum_ok else b"other").hexdigest()
    responses = iter(
        [
            io.BytesIO(json.dumps(release).encode()),
            io.BytesIO(f"{digest}  GE-Proton-test.tar.gz".encode()),
            ChunkedResponse(archive_buffer.getvalue()),
        ]
    )
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_args, **_kwargs: next(responses)
    )
    install_dir = tmp_path / "proton"
    monkeypatch.setattr("core.api.get_proton_dir", lambda: str(install_dir))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    api = Api()
    stages = []
    original_set_stage = api._set_proton_download_stage

    def record_stage(stage, done=0, total=0):
        stages.append((stage, done, total, api.get_proton_download_status()["running"]))
        original_set_stage(stage, done, total)

    monkeypatch.setattr(api, "_set_proton_download_stage", record_stage)

    result = api.download_proton_ge()

    size = len(archive_buffer.getvalue())
    assert ("Downloading GE-Proton-test", size, size, True) in stages
    assert api.get_proton_download_status()["running"] is False

    if not checksum_ok:
        assert result["success"] is False
        assert "SHA-512" in str(result["error"])
        assert not (install_dir / member_name).exists()
        assert not (tmp_path / "GE-Proton-test.tar.gz").exists()
        assert get_setting("proton_path") == ""
    elif member_name == "../escaped":
        assert result["success"] is False
        assert "outside" in str(result["error"])
        assert not (tmp_path / "escaped").exists()
        assert get_setting("proton_path") == ""
    else:
        executable = install_dir / member_name
        assert result == {"success": True, "path": str(executable)}
        assert executable.read_bytes() == payload
        assert get_setting("proton_path") == str(executable)
        assert not (tmp_path / "GE-Proton-test.tar.gz").exists()


@pytest.mark.parametrize("mode", ["native", "custom"])
def test_api_add_game_persists_launch_mode(mode):
    api = Api()

    result = api.add_game("Native", "/tmp/native.sh", launch_mode=mode, command_line_args="xsystem35")

    assert result["id"] is not None
    games = get_all_games()
    assert games[0]["launch_mode"] == mode
    assert games[0]["command_line_args"] == "xsystem35"


def test_api_launch_game_passes_normalized_launch_mode(monkeypatch):
    api = Api()
    captured: dict[str, object] = {}

    def fake_launch(
        exe_path,
        command_line_args="",
        run_japanese_locale=False,
        run_wayland=False,
        auto_inject_ce=False,
        custom_prefix="",
        proton_version="",
        launch_mode="auto",
        on_exit_callback=None,
        game_id=None,
    ):
        captured["exe_path"] = exe_path
        captured["launch_mode"] = launch_mode
        captured["on_exit_callback"] = on_exit_callback
        captured["game_id"] = game_id
        return {"success": True}

    monkeypatch.setattr(api.launcher, "launch", fake_launch)

    result = api.launch_game(1, "/tmp/game.exe", launch_mode="unknown")

    assert result["success"] is True
    assert captured["exe_path"] == "/tmp/game.exe"
    assert captured["launch_mode"] == "auto"
    assert captured["game_id"] == 1


def test_api_launch_game_passes_rpgmaker_linux_launch_mode(monkeypatch):
    api = Api()
    captured: dict[str, object] = {}

    def fake_launch(
        exe_path,
        command_line_args="",
        run_japanese_locale=False,
        run_wayland=False,
        auto_inject_ce=False,
        custom_prefix="",
        proton_version="",
        launch_mode="auto",
        on_exit_callback=None,
        game_id=None,
    ):
        captured["exe_path"] = exe_path
        captured["launch_mode"] = launch_mode
        captured["on_exit_callback"] = on_exit_callback
        return {"success": True}

    monkeypatch.setattr(api.launcher, "launch", fake_launch)

    result = api.launch_game(1, "/tmp/game.exe", launch_mode="rpgmaker_linux")

    assert result["success"] is True
    assert captured["exe_path"] == "/tmp/game.exe"
    assert captured["launch_mode"] == "rpgmaker_linux"


def test_api_launch_target_crud_and_reorder():
    api = Api()
    game_id = add_game(title="Multi Part", exe_path="/tmp/main.exe")
    other_id = add_game(title="Other", exe_path="/tmp/other.exe")
    assert game_id is not None
    assert other_id is not None

    first = api.create_launch_target(game_id, "Part 1", "/tmp/part1.exe")
    second = api.create_launch_target(game_id, "Part 2", "/tmp/part2.exe")
    other = api.create_launch_target(other_id, "Other", "/tmp/other-part.exe")

    assert first["success"] is True
    assert second["success"] is True
    assert other["success"] is True

    first_target = cast(dict[str, object], first["target"])
    second_target = cast(dict[str, object], second["target"])
    other_target = cast(dict[str, object], other["target"])

    update = api.update_launch_target(
        int(str(second_target["id"])),
        {"label": "Season 2", "exe_path": "/tmp/s2.exe"},
    )
    assert update["success"] is True
    updated_target = cast(dict[str, object], update["target"])
    assert updated_target["label"] == "Season 2"
    assert updated_target["exe_path"] == "/tmp/s2.exe"

    invalid_reorder = api.reorder_launch_targets(
        game_id, [first_target["id"], other_target["id"]]
    )
    assert invalid_reorder["success"] is False
    assert invalid_reorder["error_code"] == "invalid_target_order"

    reorder = api.reorder_launch_targets(
        game_id, [second_target["id"], first_target["id"]]
    )
    assert reorder["success"] is True
    reordered_targets = cast(list[dict[str, object]], reorder["targets"])
    assert [target["label"] for target in reordered_targets] == [
        "Season 2",
        "Part 1",
    ]

    listed = api.get_launch_targets(game_id)
    assert [target["label"] for target in listed] == ["Season 2", "Part 1"]

    delete = api.delete_launch_target(int(str(first_target["id"])))
    assert delete["success"] is True
    assert [target["label"] for target in api.get_launch_targets(game_id)] == [
        "Season 2"
    ]


def test_api_launch_target_validation_errors():
    api = Api()
    game_id = add_game(title="Invalid Target", exe_path="/tmp/main.exe")
    assert game_id is not None

    blank_label = api.create_launch_target(game_id, " ", "/tmp/part.exe")
    blank_path = api.create_launch_target(game_id, "Part", " ")
    missing_parent = api.create_launch_target(9999, "Part", "/tmp/part.exe")

    assert blank_label["success"] is False
    assert blank_label["error_code"] == "invalid_target"
    assert blank_path["success"] is False
    assert blank_path["error_code"] == "invalid_target"
    assert missing_parent["success"] is False
    assert missing_parent["error_code"] == "game_not_found"

    not_found_update = api.update_launch_target(9999, {"label": "Missing"})
    not_found_delete = api.delete_launch_target(9999)
    assert not_found_update["error_code"] == "target_not_found"
    assert not_found_delete["error_code"] == "target_not_found"


def test_download_cheat_engine_preserves_existing_install_on_download_failure(
    monkeypatch, tmp_path
):
    ce_dir = _redirect_cheat_engine_dir(monkeypatch, tmp_path)
    existing_executable = _write_existing_cheat_engine(ce_dir)

    def failing_urlopen(*_args, **_kwargs):
        raise URLError("network down")

    monkeypatch.setattr("urllib.request.urlopen", failing_urlopen)

    result = Api().download_cheat_engine()

    assert result["success"] is False
    assert "network down" in str(result["error"])
    assert existing_executable.read_bytes() == b"old engine"
    assert not list(ce_dir.parent.glob("CheatEngine-download-*"))


def test_download_cheat_engine_preserves_existing_install_when_verification_fails(
    monkeypatch, tmp_path
):
    ce_dir = _redirect_cheat_engine_dir(monkeypatch, tmp_path)
    existing_executable = _write_existing_cheat_engine(ce_dir)
    archive_data = _zip_bytes({"readme.txt": b"missing executable"})
    monkeypatch.setattr(
        "core.api.LUNAR_ENGINE_SHA256", hashlib.sha256(archive_data).hexdigest()
    )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _FakeDownloadResponse(archive_data),
    )

    result = Api().download_cheat_engine()

    assert result["success"] is False
    assert "lunarengine-x86_64.exe" in str(result["error"])
    assert existing_executable.read_bytes() == b"old engine"
    assert not list(ce_dir.parent.glob("CheatEngine-download-*"))


def test_download_cheat_engine_rejects_archive_with_wrong_checksum(
    monkeypatch, tmp_path
):
    ce_dir = _redirect_cheat_engine_dir(monkeypatch, tmp_path)
    existing_executable = _write_existing_cheat_engine(ce_dir)
    archive_data = _zip_bytes(
        {"Lunar Engine/lunarengine-x86_64.exe": b"tampered engine"}
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _FakeDownloadResponse(archive_data),
    )

    result = Api().download_cheat_engine()

    assert result["success"] is False
    assert "SHA-256" in str(result["error"])
    assert existing_executable.read_bytes() == b"old engine"
    assert not list(ce_dir.parent.glob("CheatEngine-download-*"))


def test_download_cheat_engine_replaces_install_after_verified_download(
    monkeypatch, tmp_path
):
    ce_dir = _redirect_cheat_engine_dir(monkeypatch, tmp_path)
    old_executable = _write_existing_cheat_engine(ce_dir)
    old_marker = ce_dir / "old-marker.txt"
    old_marker.write_text("old install", encoding="utf-8")
    archive_data = _zip_bytes(
        {"Lunar Engine/lunarengine-x86_64.exe": b"new engine"}
    )
    monkeypatch.setattr(
        "core.api.LUNAR_ENGINE_SHA256", hashlib.sha256(archive_data).hexdigest()
    )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _FakeDownloadResponse(archive_data),
    )

    result = Api().download_cheat_engine()

    assert result["success"] is True
    installed_path = str(result["path"])
    assert Path(installed_path).name == "lunarengine-x86_64.exe"
    assert Path(installed_path).parent.name == "Lunar Engine"
    assert os.path.exists(installed_path)
    assert old_executable.read_bytes() == b"new engine"
    assert not old_marker.exists()
    assert not list(ce_dir.parent.glob("CheatEngine-download-*"))
    assert not list(ce_dir.parent.glob("CheatEngine-backup-*"))


def test_api_settings_persist_rpgmaker_linux_runner_path(tmp_path):
    runner_path = tmp_path / "rpgmaker-linux"
    runner_path.write_text("#!/bin/sh\n", encoding="utf-8")
    runner_path.chmod(0o755)

    api = Api()
    first_save = api.save_settings(
        {
            "proton_path": "/opt/proton",
            "wine_prefix_path": "/tmp/wlib-prefix",
            "enable_logging": True,
            "playwright_browsers_path": "/tmp/ms-playwright",
        }
    )
    assert first_save["success"] is True

    result = api.save_settings({"rpgmaker_linux_runner_path": str(runner_path)})

    assert result["success"] is True
    assert get_setting("proton_path") == "/opt/proton"
    assert get_setting("wine_prefix_path") == "/tmp/wlib-prefix"
    assert get_setting("enable_logging") == "true"
    assert get_setting("playwright_browsers_path") == "/tmp/ms-playwright"
    assert get_setting(RPGMAKER_LINUX_RUNNER_SETTING) == str(runner_path)

    settings = api.get_settings()
    status = cast(dict[str, object], settings["rpgmaker_linux_runner_status"])
    assert settings["rpgmaker_linux_runner_path"] == str(runner_path)
    assert status["configured_path"] == str(runner_path)
    if status["available"]:
        assert status["path"] == str(runner_path)
    if status["available"]:
        assert status["source"] == "configured"


def test_api_launch_game_uses_selected_target_path_and_parent_playtime(monkeypatch):
    api = Api()
    game_id = add_game(title="Multi Part", exe_path="/tmp/main.exe")
    assert game_id is not None
    target = add_game_launch_target(game_id, "Part 2", "/tmp/part2.exe")
    captured: dict[str, object] = {}

    def fake_launch(
        exe_path,
        command_line_args="",
        run_japanese_locale=False,
        run_wayland=False,
        auto_inject_ce=False,
        custom_prefix="",
        proton_version="",
        launch_mode="auto",
        on_exit_callback=None,
        game_id=None,
    ):
        captured["exe_path"] = exe_path
        captured["command_line_args"] = command_line_args
        captured["run_japanese_locale"] = run_japanese_locale
        captured["run_wayland"] = run_wayland
        captured["auto_inject_ce"] = auto_inject_ce
        captured["custom_prefix"] = custom_prefix
        captured["proton_version"] = proton_version
        captured["launch_mode"] = launch_mode
        captured["on_exit_callback"] = on_exit_callback
        return {"success": True}

    monkeypatch.setattr(api.launcher, "launch", fake_launch)

    result = api.launch_game(
        game_id,
        target["exe_path"],
        "--fullscreen",
        True,
        True,
        True,
        "/tmp/prefix",
        "/tmp/proton",
        "wine_proton",
    )

    assert result["success"] is True
    assert captured["exe_path"] == "/tmp/part2.exe"
    assert captured["command_line_args"] == "--fullscreen"
    assert captured["run_japanese_locale"] is True
    assert captured["run_wayland"] is True
    assert captured["auto_inject_ce"] is True
    assert captured["custom_prefix"] == "/tmp/prefix"
    assert captured["proton_version"] == "/tmp/proton"
    assert captured["launch_mode"] == "wine_proton"

    on_exit = captured["on_exit_callback"]
    assert callable(on_exit)
    on_exit(42, True)

    game = get_all_games()[0]
    assert game["playtime_seconds"] == 42
    assert game["last_played"]


def test_check_for_updates_rejects_unknown_version(monkeypatch):
    api = Api()
    add_game(
        title="Demo",
        exe_path="/tmp/demo.exe",
        f95_url="https://f95zone.to/threads/demo.123/",
        version="1.0",
    )

    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": True,
            "title": "Demo [Unknown]",
            "version": "Unknown",
        },
    )

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is False
    assert result["error_code"] == "extract_failed"


def test_check_for_updates_propagates_structured_scraper_error(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": False,
            "code": "blocked",
            "error": "Blocked by anti-bot challenge while loading thread",
        },
    )

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is False
    assert result["error_code"] == "blocked"
    assert "Blocked" in str(result.get("error", ""))


def test_check_for_updates_succeeds_with_actionable_version(monkeypatch):
    api = Api()
    add_game(
        title="Demo",
        exe_path="/tmp/demo.exe",
        f95_url="https://f95zone.to/threads/demo.123/",
        version="1.0",
    )

    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": True,
            "title": "Demo [v1.1]",
            "version": "1.1",
        },
    )

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is True
    assert result["version"] == "1.1"
    assert result["has_update"] is True


@pytest.mark.parametrize("local_version, has_update", [("b12", True), ("Final", False)])
def test_check_for_updates_non_numeric_version(monkeypatch, local_version, has_update):
    api = Api()
    url = "https://f95zone.to/threads/demo.123/"
    add_game(title="Demo", exe_path="/tmp/demo.exe", f95_url=url, version=local_version)
    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": True,
            "version": api.scraper._extract_version_from_title("Demo [Final] [Dev]"),
        },
    )
    result = api.check_for_updates(url)
    assert result["success"] is True
    assert result["version"] == "Final"
    assert result["has_update"] is has_update


def test_check_for_updates_retries_blocked_with_headed_mode(monkeypatch):
    api = Api()
    add_game(
        title="Demo",
        exe_path="/tmp/demo.exe",
        f95_url="https://f95zone.to/threads/demo.123/",
        version="1.0",
    )

    calls = []

    def fake_get_thread_version(
        _url,
        headless=True,
        timeout_ms=60000,
        hold_open_seconds=0,
        include_metadata=False,
    ):
        calls.append((headless, timeout_ms, hold_open_seconds, include_metadata))
        if len(calls) == 1:
            return {
                "success": False,
                "code": "blocked",
                "error": "Blocked by anti-bot challenge while loading thread",
            }
        return {"success": True, "title": "Demo [v1.1]", "version": "1.1"}

    monkeypatch.setattr(api.scraper, "get_thread_version", fake_get_thread_version)

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is True
    assert result["version"] == "1.1"
    assert calls[0] == (True, 60000, 0, True)
    assert calls[1] == (False, 180000, 20, True)


def test_check_for_updates_backfills_missing_metadata(monkeypatch):
    api = Api()
    add_game(
        title="Demo",
        exe_path="/tmp/demo.exe",
        f95_url="https://f95zone.to/threads/demo.123/",
        version="1.0",
        tags="",
        engine="",
        cover_image="",
    )

    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": True,
            "title": "Demo [v1.1]",
            "version": "1.1",
            "engine": "Ren'Py",
            "tags": ["3dcg", "sandbox"],
            "cover_image": "https://img.example/cover.jpg",
        },
    )

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is True
    assert result["metadata_updated"] == 1

    game = get_all_games()[0]
    assert game["engine"] == "Ren'Py"
    assert game["tags"] == "3dcg, sandbox"
    assert game["cover_image_path"] == "https://img.example/cover.jpg"


def test_check_for_updates_refreshes_thread_edit_metadata_without_overwriting_existing_fields(
    monkeypatch,
):
    api = Api()
    add_game(
        title="Demo",
        exe_path="/tmp/demo.exe",
        f95_url="https://f95zone.to/threads/demo.123/",
        version="1.0",
        tags="existing",
        engine="Unity",
        cover_image="https://img.example/existing.jpg",
    )

    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": True,
            "title": "Demo [v1.1]",
            "version": "1.1",
            "engine": "Ren'Py",
            "tags": ["3dcg", "sandbox"],
            "cover_image": "https://img.example/new-cover.jpg",
            "thread_main_post_last_edit_at": "2026-03-06T22:59:58+0300",
            "thread_main_post_checked_at": "2026-03-07T01:00:00",
        },
    )

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is True

    game = get_all_games()[0]
    assert game["engine"] == "Unity"
    assert game["tags"] == "existing"
    assert game["cover_image_path"] == "https://img.example/existing.jpg"
    assert game["thread_main_post_last_edit_at"] == "2026-03-06T22:59:58+0300"
    assert game["thread_main_post_checked_at"] == "2026-03-07T01:00:00"


class _InlineThread:
    def __init__(self, target, args=(), daemon=None):
        _ = daemon
        self._run = lambda: target(*args)

    def start(self):
        self._run()


class _DeferredThread(_InlineThread):
    def start(self):
        pass


def test_add_game_returns_before_metadata_fetch(monkeypatch):
    api = Api()
    monkeypatch.setattr("core.api.threading.Thread", _DeferredThread)

    result = api.add_game(
        title="Pending", exe_path="/tmp/pending.exe", f95_url="https://f95zone.to/threads/p.1/"
    )
    no_url = api.add_game(title="Local", exe_path="/tmp/local.exe")

    assert result["success"] is True and result["metadata_pending"] is True
    assert no_url["metadata_pending"] is False
    pending = {game["title"]: game.get("metadata_pending") for game in api.get_games()}
    assert pending == {"Pending": True, "Local": None}


def test_add_game_backfills_missing_metadata(monkeypatch):
    api = Api()
    monkeypatch.setattr("core.api.threading.Thread", _InlineThread)

    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda *_args, **_kwargs: {
            "success": True,
            "engine": "Unity",
            "tags": ["3d", "adventure"],
            "cover_image": "https://img.example/new-cover.jpg",
        },
    )

    result = api.add_game(
        title="Manual Add",
        exe_path="/tmp/manual.exe",
        f95_url="https://f95zone.to/threads/manual.321/",
        tags="",
        engine="",
        cover_image="",
    )

    assert result["id"] is not None
    assert "metadata_pending" not in api.get_games()[0]

    game = get_all_games()[0]
    assert game["engine"] == "Unity"
    assert game["tags"] == "3d, adventure"
    assert game["cover_image_path"] == "https://img.example/new-cover.jpg"


def test_add_game_updates_thread_edit_metadata_without_overwriting_existing_fields(
    monkeypatch,
):
    api = Api()
    monkeypatch.setattr("core.api.threading.Thread", _InlineThread)

    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda *_args, **_kwargs: {
            "success": True,
            "engine": "Ren'Py",
            "tags": ["sandbox"],
            "cover_image": "https://img.example/other.jpg",
            "thread_main_post_last_edit_at": "2026-03-06T22:59:58+0300",
            "thread_main_post_checked_at": "2026-03-07T01:00:00",
        },
    )

    result = api.add_game(
        title="Manual Add",
        exe_path="/tmp/manual.exe",
        f95_url="https://f95zone.to/threads/manual.321/",
        tags="existing",
        engine="Unity",
        cover_image="https://img.example/existing.jpg",
    )

    assert result["id"] is not None

    game = get_all_games()[0]
    assert game["engine"] == "Unity"
    assert game["tags"] == "existing"
    assert game["cover_image_path"] == "https://img.example/existing.jpg"
    assert game["thread_main_post_last_edit_at"] == "2026-03-06T22:59:58+0300"
    assert game["thread_main_post_checked_at"] == "2026-03-07T01:00:00"


def test_get_executable_modified_time_reports_file_timestamp(tmp_path):
    api = Api()
    executable = tmp_path / "demo.exe"
    executable.write_text("demo")

    result = api.get_executable_modified_time(str(executable))

    assert result["success"] is True
    assert isinstance(result["modified_at"], str)


def test_get_executable_modified_time_handles_missing_file(tmp_path):
    api = Api()

    result = api.get_executable_modified_time(str(tmp_path / "missing.exe"))

    assert result["success"] is False
    assert result["modified_at"] is None


def test_add_game_rejects_duplicate_f95_url(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda *_args, **_kwargs: {"success": False},
    )

    first = api.add_game(
        title="First",
        exe_path="/tmp/first.exe",
        f95_url="https://f95zone.to/threads/dupe.777/",
    )
    second = api.add_game(
        title="Second",
        exe_path="/tmp/second.exe",
        f95_url="https://f95zone.to/threads/dupe.777/",
    )

    assert first["id"] is not None
    assert second["success"] is False
    assert second["error_code"] == "duplicate_url"


def test_add_game_rejects_duplicate_thread_url_variants(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda *_args, **_kwargs: {"success": False},
    )

    first = api.add_game(
        title="First",
        exe_path="/tmp/first.exe",
        f95_url="https://f95zone.to/threads/original-slug.777/",
    )
    second = api.add_game(
        title="Second",
        exe_path="/tmp/second.exe",
        f95_url="https://f95zone.to/threads/renamed-slug.777/page-2?latest=1#post-5",
    )

    assert first["id"] is not None
    assert second["success"] is False
    assert second["error_code"] == "duplicate_url"


def test_update_game_rejects_duplicate_thread_url_variants(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda *_args, **_kwargs: {"success": False},
    )

    first = api.add_game(
        title="First",
        exe_path="/tmp/first.exe",
        f95_url="https://f95zone.to/threads/original-slug.777/",
    )
    second = api.add_game(
        title="Second",
        exe_path="/tmp/second.exe",
        f95_url="https://f95zone.to/threads/different-slug.888/",
    )

    result = api.update_game(
        cast(int, second["id"]),
        {"f95_url": "https://f95zone.to/threads/renamed-slug.777/page-3#post-42"},
    )

    assert first["id"] is not None
    assert result["success"] is False
    assert result["error_code"] == "duplicate_url"


def test_get_settings_includes_playwright_path_default():
    api = Api()

    settings = api.get_settings()

    assert settings["playwright_browsers_path"] == DEFAULT_PLAYWRIGHT_BROWSERS_PATH


def test_save_settings_persists_playwright_path():
    api = Api()

    api.save_settings(
        {
            "proton_path": "",
            "wine_prefix_path": "",
            "enable_logging": False,
            "playwright_browsers_path": "/tmp/custom-playwright-cache",
        }
    )

    assert get_setting("playwright_browsers_path") == "/tmp/custom-playwright-cache"


def test_browse_file_uses_linux_fallback_order(monkeypatch, tmp_path):
    api = Api()
    selected_file = tmp_path / "game.exe"
    selected_file.write_text("demo")
    calls = []

    monkeypatch.setattr("core.api.sys.platform", "linux")
    monkeypatch.setattr(
        api,
        "_build_linux_browse_backends",
        lambda *_args, **_kwargs: [
            {"name": "portal", "command": ["portal"], "env": {"GTK_USE_PORTAL": "1"}},
            {"name": "zenity", "command": ["zenity"], "env": {}},
        ],
    )

    def fake_run_picker(command, env=None):
        calls.append((tuple(command), dict(env or {})))
        if command[0] == "portal":
            return {"success": False, "cancelled": False, "error": "portal unavailable"}
        return {"success": True, "cancelled": False, "path": str(selected_file)}

    monkeypatch.setattr(api, "_run_picker_command", fake_run_picker)
    monkeypatch.setattr(api, "_browse_qt_dialog", lambda *_args, **_kwargs: "")

    result = api.browse_file()

    assert result == str(selected_file)
    assert [command[0] for command, _env in calls] == ["portal", "zenity"]


def test_browse_file_falls_back_after_portal_error_like_cancel(monkeypatch, tmp_path):
    api = Api()
    selected_file = tmp_path / "game.exe"
    selected_file.write_text("demo")
    calls = []

    monkeypatch.setattr("core.api.sys.platform", "linux")
    monkeypatch.setattr(
        api,
        "_build_linux_browse_backends",
        lambda *_args, **_kwargs: [
            {
                "name": "portal",
                "command": ["portal"],
                "env": {"GTK_USE_PORTAL": "1"},
                "continue_on_cancel_error": True,
                "cancel_error_markers": ("failed to talk to portal",),
            },
            {"name": "zenity", "command": ["zenity"], "env": {}},
        ],
    )

    def fake_run_picker(command, env=None):
        calls.append((tuple(command), dict(env or {})))
        if command[0] == "portal":
            return {
                "success": False,
                "cancelled": True,
                "error": "Failed to talk to portal",
                "stderr": "Failed to talk to portal",
                "returncode": 1,
            }
        return {
            "success": True,
            "cancelled": False,
            "path": str(selected_file),
            "stderr": "",
            "returncode": 0,
        }

    monkeypatch.setattr(api, "_run_picker_command", fake_run_picker)
    monkeypatch.setattr(api, "_browse_qt_dialog", lambda *_args, **_kwargs: "")

    result = api.browse_file()

    assert result == str(selected_file)
    assert [command[0] for command, _env in calls] == ["portal", "zenity"]


def test_browse_file_stops_on_user_cancel_even_with_portal_stderr(monkeypatch):
    api = Api()
    calls = []

    monkeypatch.setattr("core.api.sys.platform", "linux")
    monkeypatch.setattr(
        api,
        "_build_linux_browse_backends",
        lambda *_args, **_kwargs: [
            {
                "name": "portal",
                "command": ["portal"],
                "env": {"GTK_USE_PORTAL": "1"},
                "continue_on_cancel_error": True,
                "cancel_error_markers": ("failed to talk to portal",),
            },
            {"name": "zenity", "command": ["zenity"], "env": {}},
        ],
    )

    def fake_run_picker(command, env=None):
        calls.append((tuple(command), dict(env or {})))
        return {
            "success": False,
            "cancelled": True,
            "error": "Dialog cancelled",
            "stderr": "GtkDialog mapped without transient parent",
            "returncode": 1,
        }

    monkeypatch.setattr(api, "_run_picker_command", fake_run_picker)
    monkeypatch.setattr(
        api,
        "_browse_qt_dialog",
        lambda *_args, **_kwargs: "/tmp/should-not-open",
    )

    result = api.browse_file()

    assert result == ""
    assert [command[0] for command, _env in calls] == ["portal"]


def test_build_linux_browse_backends_skips_portal_when_unavailable(monkeypatch):
    api = Api()

    def fake_which(command):
        mapping = {
            "zenity": "/usr/bin/zenity",
            "kdialog": "/usr/bin/kdialog",
        }
        return mapping.get(command)

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(api, "_desktop_portal_available", lambda _env: False)
    monkeypatch.setattr(api, "_build_host_open_env", lambda: {})
    monkeypatch.setattr(api, "_coerce_browse_directory", lambda _path: "/home/tester")

    backends = api._build_linux_browse_backends("file", "")

    assert [backend["name"] for backend in backends] == ["zenity", "kdialog"]


def test_build_linux_browse_backends_uses_custom_file_filters(monkeypatch):
    api = Api()

    def fake_which(command):
        mapping = {
            "zenity": "/usr/bin/zenity",
            "kdialog": "/usr/bin/kdialog",
        }
        return mapping.get(command)

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(api, "_desktop_portal_available", lambda _env: False)
    monkeypatch.setattr(api, "_build_host_open_env", lambda: {})
    monkeypatch.setattr(api, "_coerce_browse_directory", lambda _path: "/home/tester")

    backends = api._build_linux_browse_backends(
        "file", "", file_types=("Runner Binaries (*)",)
    )

    assert backends[0]["command"][-1] == "--file-filter=Runner Binaries | *"
    assert backends[1]["command"][-1] == "Runner Binaries (*)"


def test_browse_directory_returns_empty_string_when_linux_picker_cancelled(monkeypatch):
    api = Api()
    qt_calls = []

    monkeypatch.setattr("core.api.sys.platform", "linux")
    monkeypatch.setattr(
        api,
        "_build_linux_browse_backends",
        lambda *_args, **_kwargs: [
            {"name": "portal", "command": ["portal"], "env": {}}
        ],
    )
    monkeypatch.setattr(
        api,
        "_run_picker_command",
        lambda *_args, **_kwargs: {
            "success": False,
            "cancelled": True,
            "error": "Picker cancelled",
        },
    )
    monkeypatch.setattr(
        api,
        "_browse_qt_dialog",
        lambda *_args, **_kwargs: qt_calls.append(True) or "/tmp/should-not-open",
    )

    result = api.browse_directory()

    assert result == ""
    assert qt_calls == []


def test_browse_runner_file_uses_runner_filter_on_linux(monkeypatch):
    api = Api()
    recorded: dict[str, object] = {}

    monkeypatch.setattr("core.api.sys.platform", "linux")

    def fake_browse_linux_dialog(dialog_kind, directory="", file_types=()):
        recorded["dialog_kind"] = dialog_kind
        recorded["directory"] = directory
        recorded["file_types"] = file_types
        return "/tmp/proton"

    monkeypatch.setattr(api, "_browse_linux_dialog", fake_browse_linux_dialog)

    result = api.browse_runner_file("/tmp")

    assert result == "/tmp/proton"
    assert recorded == {
        "dialog_kind": "file",
        "directory": "/tmp",
        "file_types": ("Runner Binaries (*)",),
    }


def test_open_scraper_login_session_delegates_to_scraper(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "open_login_session",
        lambda login_url: {"success": True, "login_url": login_url},
    )

    result = api.open_scraper_login_session()

    assert result["success"] is True
    assert result["login_url"] == "https://f95zone.to/login/"


def test_reset_scraper_session_delegates_to_scraper(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "reset_browser_session",
        lambda: {"success": True, "message": "reset"},
    )

    result = api.reset_scraper_session()

    assert result["success"] is True
    assert result["message"] == "reset"


def test_open_folder_uses_host_env_outside_appimage_runtime(monkeypatch, tmp_path):
    api = Api()
    target_dir = tmp_path / "folder"
    target_dir.mkdir()

    popen_mock = MagicMock()
    monkeypatch.setenv("APPIMAGE", "/tmp/wLib.AppImage")
    monkeypatch.setenv("APPDIR", "/tmp/.mount_wLib")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_wLib/usr/bin/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib:/lib")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/xdg-open" if cmd == "xdg-open" else None
    )
    monkeypatch.setattr("subprocess.Popen", popen_mock)

    result = api.open_folder(str(target_dir))

    assert result["success"] is True
    popen_mock.assert_called_once()
    args, kwargs = popen_mock.call_args
    assert args[0] == ["/usr/bin/xdg-open", str(target_dir)]
    assert kwargs["env"]["LD_LIBRARY_PATH"] == "/usr/lib:/lib"
    assert kwargs["env"]["DISPLAY"] == ":0"
    assert kwargs["env"]["WAYLAND_DISPLAY"] == "wayland-0"
    assert "APPIMAGE" not in kwargs["env"]
    assert "APPDIR" not in kwargs["env"]
    assert kwargs["start_new_session"] is True


def test_open_in_browser_uses_host_env_outside_appimage_runtime(monkeypatch):
    api = Api()
    popen_mock = MagicMock()

    monkeypatch.setenv("APPIMAGE", "/tmp/wLib.AppImage")
    monkeypatch.setenv("APPDIR", "/tmp/.mount_wLib")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_wLib/usr/bin/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib:/lib")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/xdg-open" if cmd == "xdg-open" else None
    )
    monkeypatch.setattr("subprocess.Popen", popen_mock)

    result = api.open_in_browser("https://example.com")

    assert result["success"] is True
    popen_mock.assert_called_once()
    args, kwargs = popen_mock.call_args
    assert args[0] == ["/usr/bin/xdg-open", "https://example.com"]
    assert kwargs["env"]["LD_LIBRARY_PATH"] == "/usr/lib:/lib"
    assert kwargs["env"]["DISPLAY"] == ":0"
    assert kwargs["env"]["WAYLAND_DISPLAY"] == "wayland-0"
    assert "APPIMAGE" not in kwargs["env"]
    assert "APPDIR" not in kwargs["env"]
    assert kwargs["start_new_session"] is True


def test_open_in_browser_returns_error_when_no_opener_found(monkeypatch):
    api = Api()

    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    result = api.open_in_browser("https://example.com")

    assert result == {"success": False, "error": "No browser opener found"}


@pytest.mark.parametrize(
    "url",
    [
        r"C:\Users\Public\evil.exe",
        r"\\attacker\share\evil.exe",
        "/usr/bin/xterm",
        "file:///C:/evil.exe",
        "javascript:alert(1)",
    ],
)
def test_open_in_browser_rejects_non_web_urls(monkeypatch, url):
    api = Api()
    opener = MagicMock()
    monkeypatch.setattr(api, "_open_with_system_handler", opener)

    result = api.open_in_browser(url)

    assert result["success"] is False
    opener.assert_not_called()


def test_open_in_browser_returns_launch_error(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/xdg-open" if cmd == "xdg-open" else None
    )

    def raise_popen(*_args, **_kwargs):
        raise OSError("launch failed")

    monkeypatch.setattr("subprocess.Popen", raise_popen)

    result = api.open_in_browser("https://example.com")

    assert result["success"] is False
    assert "launch failed" in result.get("error", "")


def test_open_extension_folder_uses_host_env_outside_appimage_runtime(
    monkeypatch, tmp_path
):
    api = Api()
    popen_mock = MagicMock()
    persistent_dir = tmp_path / "extension"
    _redirect_extension_dir(monkeypatch, persistent_dir)

    monkeypatch.setenv("APPIMAGE", "/tmp/wLib.AppImage")
    monkeypatch.setenv("APPDIR", "/tmp/.mount_wLib")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_wLib/usr/bin/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib:/lib")
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/bin/xdg-open" if cmd == "xdg-open" else None,
    )
    monkeypatch.setattr("subprocess.Popen", popen_mock)
    result = api.open_extension_folder()

    assert result["success"] is True
    chrome_manifest_path = persistent_dir / "chrome" / "manifest.json"
    firefox_xpi_path = persistent_dir / "firefox" / "wLib.xpi"

    assert chrome_manifest_path.is_file()
    assert firefox_xpi_path.is_file()

    chrome_manifest = json.loads(chrome_manifest_path.read_text())
    assert chrome_manifest["background"]["service_worker"] == "background.js"
    assert "scripts" not in chrome_manifest["background"]

    with zipfile.ZipFile(firefox_xpi_path) as firefox_xpi:
        firefox_manifest = json.loads(firefox_xpi.read("manifest.json").decode("utf-8"))

    assert firefox_manifest["background"]["service_worker"] == "background.js"
    assert firefox_manifest["background"]["scripts"] == ["background.js"]

    popen_mock.assert_called_once()
    args, kwargs = popen_mock.call_args
    assert args[0] == ["/usr/bin/xdg-open", str(persistent_dir)]
    assert kwargs["env"]["LD_LIBRARY_PATH"] == "/usr/lib:/lib"
    assert "APPIMAGE" not in kwargs["env"]
    assert "APPDIR" not in kwargs["env"]


def test_sync_extension_files_replaces_outdated_install(monkeypatch, tmp_path):
    api = Api()
    persistent_dir = tmp_path / "extension"
    chrome_dir = persistent_dir / "chrome"
    firefox_dir = persistent_dir / "firefox"
    _redirect_extension_dir(monkeypatch, persistent_dir)

    chrome_dir.mkdir(parents=True)
    firefox_dir.mkdir(parents=True)
    (chrome_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "version": "1.0.1",
                "background": {
                    "service_worker": "background.js",
                },
            }
        )
    )
    (chrome_dir / "content.js").write_text("old-content")
    (firefox_dir / "wLib.xpi").write_text("outdated")

    result = api.sync_extension_files()

    assert result["success"] is True
    assert result.get("updated") is True

    chrome_manifest = json.loads((chrome_dir / "manifest.json").read_text())
    chrome_content = (chrome_dir / "content.js").read_text()

    assert chrome_manifest["version"] == "1.0.7"
    assert "scripts" not in chrome_manifest["background"]
    assert (
        "*://f95zone.to/sam/latest_alpha*"
        in chrome_manifest["content_scripts"][0]["matches"]
    )
    assert "checkGameInWLib" in chrome_content
    assert "resource-tile_link" in chrome_content
    assert "wlib-library-badge" in chrome_content
    assert "hashchange" in chrome_content
    assert (firefox_dir / "wLib.xpi").is_file()


def test_sync_extension_files_copies_bundled_signed_firefox_xpi(
    monkeypatch, tmp_path
):
    api = Api()
    bundled_dir = tmp_path / "bundled-extension"
    persistent_dir = tmp_path / "extension"
    chrome_dir = persistent_dir / "chrome"
    firefox_dir = persistent_dir / "firefox"
    signed_xpi = b"signed-firefox-xpi"
    _redirect_extension_dir(monkeypatch, persistent_dir)

    bundled_dir.mkdir()
    (bundled_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "wLib",
                "version": "2.0.0",
                "background": {
                    "service_worker": "background.js",
                    "scripts": ["background.js"],
                    "type": "module",
                },
            }
        )
    )
    (bundled_dir / "background.js").write_text("")
    (bundled_dir / "content.js").write_text("signed-bundle-content")
    (bundled_dir / "firefox").mkdir()
    (bundled_dir / "firefox" / "wLib.xpi").write_bytes(signed_xpi)

    monkeypatch.setattr(api, "_get_bundled_extension_dir", lambda: str(bundled_dir))
    result = api.sync_extension_files()

    assert result["success"] is True
    assert result.get("updated") is True
    assert (firefox_dir / "wLib.xpi").read_bytes() == signed_xpi
    assert not (chrome_dir / "firefox").exists()

    chrome_manifest = json.loads((chrome_dir / "manifest.json").read_text())
    assert "scripts" not in chrome_manifest["background"]


def test_sync_extension_files_replaces_changed_bundled_signed_firefox_xpi(
    monkeypatch, tmp_path
):
    api = Api()
    bundled_dir = tmp_path / "bundled-extension"
    persistent_dir = tmp_path / "extension"
    chrome_dir = persistent_dir / "chrome"
    firefox_dir = persistent_dir / "firefox"
    _redirect_extension_dir(monkeypatch, persistent_dir)

    bundled_dir.mkdir()
    (bundled_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "wLib",
                "version": "2.0.0",
                "background": {
                    "service_worker": "background.js",
                    "scripts": ["background.js"],
                    "type": "module",
                },
            }
        )
    )
    (bundled_dir / "background.js").write_text("")
    (bundled_dir / "content.js").write_text("updated-signed-bundle-content")
    (bundled_dir / "firefox").mkdir()
    (bundled_dir / "firefox" / "wLib.xpi").write_bytes(b"new-signed-xpi")

    chrome_dir.mkdir(parents=True)
    firefox_dir.mkdir(parents=True)
    (chrome_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "version": "2.0.0",
                "background": {
                    "service_worker": "background.js",
                },
            }
        )
    )
    (chrome_dir / "content.js").write_text("old-content")
    (firefox_dir / "wLib.xpi").write_bytes(b"old-xpi")

    monkeypatch.setattr(api, "_get_bundled_extension_dir", lambda: str(bundled_dir))
    result = api.sync_extension_files()

    assert result["success"] is True
    assert result.get("updated") is True
    assert result.get("reason") == "signed-firefox-xpi-changed"
    assert (firefox_dir / "wLib.xpi").read_bytes() == b"new-signed-xpi"
    assert (chrome_dir / "content.js").read_text() == "updated-signed-bundle-content"


def test_sync_extension_files_generates_unsigned_firefox_fallback(
    monkeypatch, tmp_path
):
    api = Api()
    bundled_dir = tmp_path / "bundled-extension"
    persistent_dir = tmp_path / "extension"
    chrome_dir = persistent_dir / "chrome"
    firefox_dir = persistent_dir / "firefox"
    _redirect_extension_dir(monkeypatch, persistent_dir)

    bundled_dir.mkdir()
    (bundled_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "wLib",
                "version": "2.0.0",
                "background": {
                    "service_worker": "background.js",
                    "scripts": ["background.js"],
                    "type": "module",
                },
            }
        )
    )
    (bundled_dir / "background.js").write_text("")
    (bundled_dir / "content.js").write_text("fallback-bundle-content")
    (bundled_dir / "firefox").mkdir()
    (bundled_dir / "firefox" / "ignored.txt").write_text("not packaged")

    monkeypatch.setattr(api, "_get_bundled_extension_dir", lambda: str(bundled_dir))
    result = api.sync_extension_files()

    assert result["success"] is True
    assert result.get("updated") is True
    assert not (chrome_dir / "firefox").exists()

    with zipfile.ZipFile(firefox_dir / "wLib.xpi") as firefox_xpi:
        xpi_names = set(firefox_xpi.namelist())
        firefox_manifest = json.loads(firefox_xpi.read("manifest.json").decode())

    assert "manifest.json" in xpi_names
    assert "firefox/ignored.txt" not in xpi_names
    assert firefox_manifest["background"]["scripts"] == ["background.js"]


def test_sync_extension_files_skips_copy_when_versions_match(monkeypatch, tmp_path):
    api = Api()
    persistent_dir = tmp_path / "extension"
    chrome_dir = persistent_dir / "chrome"
    firefox_dir = persistent_dir / "firefox"
    _redirect_extension_dir(monkeypatch, persistent_dir)

    chrome_dir.mkdir(parents=True)
    firefox_dir.mkdir(parents=True)
    (chrome_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "version": "1.0.7",
                "background": {
                    "service_worker": "background.js",
                },
            }
        )
    )
    sentinel = "kept-existing-files"
    (chrome_dir / "content.js").write_text(sentinel)
    (firefox_dir / "wLib.xpi").write_text("existing")

    result = api.sync_extension_files()

    assert result["success"] is True
    assert result.get("updated") is False
    assert result.get("reason") == "up-to-date"
    assert result.get("installed_version") == "1.0.7"
    assert (chrome_dir / "content.js").read_text() == sentinel


def test_startup_extension_sync_status_defaults_and_updates():
    api = Api()

    initial = api.get_startup_extension_sync_status()
    assert initial["success"] is True
    assert initial.get("updated") is False

    api.set_startup_extension_sync_status(
        {
            "success": True,
            "updated": True,
            "installed_version": "1.0.5",
            "bundled_version": "1.0.5",
            "reason": "version-changed",
        }
    )

    updated = api.get_startup_extension_sync_status()
    assert updated.get("updated") is True
    assert updated.get("installed_version") == "1.0.5"
    assert updated.get("reason") == "version-changed"


def test_get_extension_service_status_reports_reachable(monkeypatch):
    api = Api()
    captured = {}

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"exists": false}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["origin"] = request.headers.get("Origin")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = api.get_extension_service_status()

    assert result == {"success": True, "reachable": True}
    assert captured["url"] == "http://127.0.0.1:8183/api/check?url=__ping__"
    assert captured["timeout"] == 2
    assert captured["origin"] is None


def test_get_extension_service_status_reports_unreachable(monkeypatch):
    api = Api()

    def fake_urlopen(_request, timeout=0):
        raise URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = api.get_extension_service_status()

    assert result["success"] is True
    assert result["reachable"] is False
    assert "connection refused" in str(result.get("error", ""))


def test_resolve_runtime_install_target_prefers_proton_pfx(tmp_path):
    api = Api()
    base_prefix = tmp_path / "compat-prefix"
    pfx_path = base_prefix / "pfx"
    pfx_path.mkdir(parents=True)

    target = api._resolve_runtime_install_target(
        str(base_prefix), "/tmp/GE-Proton/proton"
    )

    assert target["base_prefix"] == str(base_prefix)
    assert target["resolved_prefix"] == str(pfx_path)
    assert target["is_proton"] is True


def test_get_install_status_derives_from_requested_prefix(tmp_path):
    api = Api()
    resolved_prefix = tmp_path / "prefix"

    required_paths = [
        resolved_prefix / "drive_c" / "windows" / "Fonts" / "arial.ttf",
        resolved_prefix / "drive_c" / "windows" / "system32" / "d3dcompiler_47.dll",
        resolved_prefix / "drive_c" / "windows" / "system32" / "msvcr120.dll",
        resolved_prefix / "drive_c" / "windows" / "system32" / "vcruntime140.dll",
        resolved_prefix
        / "drive_c"
        / "windows"
        / "Microsoft.NET"
        / "Framework"
        / "v4.0.30319",
        resolved_prefix / "drive_c" / "windows" / "system32" / "RGSS301.dll",
        resolved_prefix / "drive_c" / "windows" / "system32" / "RGSS202E.dll",
        resolved_prefix / "drive_c" / "windows" / "system32" / "RGSS104E.dll",
        resolved_prefix
        / "drive_c"
        / "Program Files"
        / "Common Files"
        / "ASCII"
        / "RPG2003",
    ]

    for path in required_paths:
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("installed")
        else:
            path.mkdir(parents=True, exist_ok=True)

    status = api.get_install_status(str(resolved_prefix), "")

    assert status["dlls_installed"] is True
    assert status["rtps_installed"] is True


def test_get_install_status_requires_all_rtp_sentinels(tmp_path):
    api = Api()
    resolved_prefix = tmp_path / "prefix"
    partial_rtp_paths = [
        resolved_prefix / "drive_c" / "windows" / "system32" / "RGSS301.dll",
        resolved_prefix / "drive_c" / "windows" / "system32" / "RGSS202E.dll",
        resolved_prefix / "drive_c" / "windows" / "system32" / "RGSS104E.dll",
    ]

    for path in partial_rtp_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("installed")

    partial_status = api.get_install_status(str(resolved_prefix), "")
    assert partial_status["rtps_installed"] is False

    final_path = (
        resolved_prefix
        / "drive_c"
        / "Program Files"
        / "Common Files"
        / "ASCII"
        / "RPG2003"
    )
    final_path.mkdir(parents=True, exist_ok=True)

    full_status = api.get_install_status(str(resolved_prefix), "")
    assert full_status["rtps_installed"] is True


def test_get_install_status_detects_rpgmaker_2003_in_user_appdata(tmp_path):
    api = Api()
    resolved_prefix = tmp_path / "prefix"
    required_paths = [
        resolved_prefix / "drive_c" / "windows" / "system32" / "RGSS301.dll",
        resolved_prefix / "drive_c" / "windows" / "system32" / "RGSS202E.dll",
        resolved_prefix / "drive_c" / "windows" / "system32" / "RGSS104E.dll",
        resolved_prefix
        / "drive_c"
        / "users"
        / "steamuser"
        / "AppData"
        / "Roaming"
        / "KADOKAWA"
        / "Common"
        / "RPG Maker 2003 RTP",
    ]

    for path in required_paths:
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("installed")
        else:
            path.mkdir(parents=True, exist_ok=True)

    status = api.get_install_status(str(resolved_prefix), "")

    assert status["rtps_installed"] is True


def test_install_rpgmaker_dependencies_fails_fast_without_winetricks(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        "shutil.which",
        lambda command: None if command == "winetricks" else "/usr/bin/wine",
    )

    result = api.install_rpgmaker_dependencies("/tmp/prefix", "")

    assert result["success"] is False
    assert "Winetricks is not installed" in str(result["error"])


def test_install_rpgmaker_dependencies_reports_background_failure(
    monkeypatch, tmp_path
):
    api = Api()
    prefix_path = tmp_path / "prefix"

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            self._target = target
            self.daemon = daemon

        def start(self):
            if self._target is not None:
                self._target()

    class FailedRunResult:
        returncode = 1

    monkeypatch.setattr(
        "shutil.which",
        lambda command: (
            "/usr/bin/winetricks" if command == "winetricks" else "/usr/bin/wine"
        ),
    )
    monkeypatch.setattr("core.api.threading.Thread", FakeThread)
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: FailedRunResult())

    result = api.install_rpgmaker_dependencies(str(prefix_path), "")
    status = api.get_install_status(str(prefix_path), "")
    deps_status = cast(dict[str, object], status["deps"])

    assert result["success"] is True
    assert deps_status["running"] is False
    assert "corefonts" in str(deps_status["error"])
    assert status["dlls_installed"] is False


def test_installs_refuse_to_run_in_parallel(monkeypatch, tmp_path):
    api = Api()
    prefix_path = str(tmp_path / "prefix")
    thread = MagicMock()  # never starts, so the first install stays "running"
    monkeypatch.setattr("shutil.which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr("core.api.threading.Thread", thread)

    assert api.install_rpgmaker_dependencies(prefix_path, "")["success"] is True
    for install in (api.install_rpgmaker_dependencies, api.install_rpgmaker_rtp):
        result = install(prefix_path, "")
        assert result["success"] is False
        assert "already running" in str(result["error"])
    thread.assert_called_once()


def test_open_url_with_targeted_tls_fallback_retries_komodo_with_intermediate(
    monkeypatch,
):
    api = Api()
    created_contexts: list[object] = []
    used_contexts: list[object] = []

    class FakeContext:
        def __init__(self):
            self.loaded_cadata: list[str] = []

        def load_verify_locations(self, cafile=None, capath=None, cadata=None):
            if cadata:
                self.loaded_cadata.append(str(cadata))

    def fake_create_default_context(*_args, **_kwargs):
        context = FakeContext()
        created_contexts.append(context)
        return context

    def fake_urlopen(request, context=None, timeout=0):
        used_contexts.append(context)
        if len(used_contexts) == 1:
            raise URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        return {"ok": True, "request": request, "timeout": timeout}

    monkeypatch.setattr("ssl.create_default_context", fake_create_default_context)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = cast(
        dict[str, object],
        cast(
            object,
            api._open_url_with_targeted_tls_fallback(
                "https://dl.komodo.jp/rpgmakerweb/run-time-packages/RPGVXAce_RTP.zip",
                timeout=15,
            ),
        ),
    )

    assert result["ok"] is True
    assert len(created_contexts) == 2
    assert cast(FakeContext, created_contexts[0]).loaded_cadata == []
    assert cast(FakeContext, created_contexts[1]).loaded_cadata
    assert (
        "BEGIN CERTIFICATE" in cast(FakeContext, created_contexts[1]).loaded_cadata[0]
    )


def test_install_rpgmaker_rtp_reports_manual_guidance_when_tls_fallback_fails(
    monkeypatch, tmp_path
):
    api = Api()
    prefix_path = tmp_path / "prefix"
    rtp_dir = tmp_path / "rtp-cache"

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            self._target = target
            self.daemon = daemon

        def start(self):
            if self._target is not None:
                self._target()

    monkeypatch.setattr(
        "shutil.which", lambda command: "/usr/bin/wine" if command == "wine" else None
    )
    monkeypatch.setattr("core.api.threading.Thread", FakeThread)
    monkeypatch.setattr(
        api,
        "_open_url_with_targeted_tls_fallback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        ),
    )
    monkeypatch.setattr("core.api.get_rtp_dir", lambda: str(rtp_dir))

    result = api.install_rpgmaker_rtp(str(prefix_path), "")
    status = api.get_install_status(str(prefix_path), "")
    rtp_status = cast(dict[str, object], status["rtps"])
    error_text = str(rtp_status["error"])

    assert result["success"] is True
    assert "official RPG Maker file host" in error_text
    assert "https://www.rpgmakerweb.com/run-time-package" in error_text


def test_install_rpgmaker_rtp_recovers_from_interrupted_download(monkeypatch, tmp_path):
    api = Api()
    rtp_dir = tmp_path / "rtp"
    rtp_dir.mkdir()
    # What an interrupted download used to leave behind, forever.
    (rtp_dir / "vxace_rtp.zip").write_bytes(b"")
    (rtp_dir / "VX_Ace").mkdir()  # half-extracted folder without the installer
    packages = api._get_rtp_packages()[:1]
    downloads: list[str] = []

    class RunInline:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    def fake_open(request, timeout=30):
        downloads.append(request.full_url)
        return io.BytesIO(_zip_bytes({"Setup.exe": b"MZ"}))

    monkeypatch.setattr("core.api.get_rtp_dir", lambda: str(rtp_dir))
    monkeypatch.setattr("core.api.threading.Thread", RunInline)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/wine")
    monkeypatch.setattr("subprocess.run", MagicMock())
    monkeypatch.setattr(api, "_get_rtp_packages", lambda: packages)
    monkeypatch.setattr(api, "_open_url_with_targeted_tls_fallback", fake_open)

    _ = api.install_rpgmaker_rtp(str(tmp_path / "prefix"), "")  # drops the stale folder
    _ = api.install_rpgmaker_rtp(str(tmp_path / "prefix"), "")

    assert len(downloads) == 1
    assert zipfile.is_zipfile(rtp_dir / "vxace_rtp.zip")
    assert (rtp_dir / "VX_Ace" / "Setup.exe").is_file()
    assert not list(rtp_dir.glob("*.part"))


def test_runtime_install_env_targets_proton_pfx_with_protons_wine(tmp_path):
    api = Api()
    proton = tmp_path / "GE-Proton" / "proton"
    (proton.parent / "files" / "bin").mkdir(parents=True)
    (proton.parent / "files" / "bin" / "wine").write_text("")
    proton.write_text("")
    fresh = tmp_path / "fresh"
    plain_wine = tmp_path / "plain"
    (plain_wine / "drive_c").mkdir(parents=True)

    env, target = api._build_runtime_install_env(str(fresh), str(proton))
    # A fresh prefix gets Proton's layout, so the launcher later runs in the same pfx.
    assert env["WINEPREFIX"] == target["resolved_prefix"] == str(fresh / "pfx")
    assert env["STEAM_COMPAT_DATA_PATH"] == str(fresh)
    assert env["WINE"] == str(proton.parent / "files" / "bin" / "wine")

    env, _ = api._build_runtime_install_env(str(plain_wine), str(proton))
    # Same isolation rule as the launcher for an existing plain Wine prefix.
    assert env["STEAM_COMPAT_DATA_PATH"] == str(plain_wine / "proton_compat")
    assert env["WINEPREFIX"] == str(plain_wine / "proton_compat" / "pfx")


def test_check_all_updates_stays_running_until_cancelled_worker_exits(monkeypatch):
    import threading
    import time

    api = Api()
    _ = add_game(
        title="Game",
        exe_path="/tmp/game.sh",
        f95_url="https://f95zone.to/threads/game.1/",
    )
    entered = threading.Event()
    release = threading.Event()
    callback_results: list[bool] = []

    def fake_batch(urls, headless, delay, include_metadata, callback):
        _ = (headless, delay, include_metadata)
        entered.set()
        assert release.wait(5)
        callback_results.append(callback(urls[0], {"success": True, "version": "2.0"}))
        return {}

    monkeypatch.setattr(api.scraper, "get_multiple_thread_versions", fake_batch)

    assert api.check_all_updates()["success"] is True
    assert entered.wait(5)
    # Closing the app now must not count as a finished check.
    assert api.get_auto_check_setting()["last_check"] == ""
    assert api.cancel_update_check()["success"] is True

    status = api.get_update_status()
    assert status["running"] is True
    assert status["cancelling"] is True
    # The old worker still owns the browser profile, so a restart must wait.
    assert api.check_all_updates()["success"] is False

    release.set()
    deadline = time.monotonic() + 5
    while api.get_update_status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)

    status = api.get_update_status()
    assert status["running"] is False
    assert status["cancelling"] is False
    assert callback_results == [False]
    assert api.get_auto_check_setting()["last_check"] == ""


def test_check_all_updates_current_label_names_game_being_checked(monkeypatch):
    import time

    api = Api()
    titles_by_url = {
        f"https://f95zone.to/threads/game-{n}.{n}/": f"Game {n}" for n in (1, 2, 3)
    }
    for url, title in titles_by_url.items():
        _ = add_game(title=title, exe_path="/tmp/game.sh", f95_url=url)
    seen: list[tuple[str, str]] = []

    def fake_batch(urls, headless, delay, include_metadata, callback):
        _ = (headless, delay, include_metadata)
        for url in urls:
            seen.append((titles_by_url[url], str(api.get_update_status()["current"])))
            _ = callback(url, {"success": True, "version": "2.0"})
        seen.append(("", str(api.get_update_status()["current"])))
        return {}

    monkeypatch.setattr(api.scraper, "get_multiple_thread_versions", fake_batch)

    assert api.check_all_updates()["success"] is True
    deadline = time.monotonic() + 5
    while api.get_update_status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(seen) == 4
    assert all(expected == current for expected, current in seen)
    assert api.get_auto_check_setting()["last_check"]


@pytest.mark.parametrize("outcome", ["busy", "failed", "empty", "partial", "success"])
def test_update_check_timestamp_requires_successful_completion(monkeypatch, outcome):
    import time

    api = Api()
    for number in (1, 2):
        add_game(f"Game {number}", "", f95_url=f"https://f95zone.to/threads/game.{number}/")
    previous = "2020-01-01T00:00:00"
    update_setting("last_update_check", previous)

    def fake_batch(urls, headless, delay, include_metadata, callback):
        if outcome == "busy":
            return {"__batch_error__": {"error": "Browser busy", "code": "browser_busy"}}
        if outcome == "empty":
            return {}
        for index, url in enumerate(urls):
            if outcome == "failed" or (outcome == "partial" and index == 1):
                callback(url, {"success": False})
            else:
                callback(url, {"success": True, "version": "2.0"})
        return {}

    monkeypatch.setattr(api.scraper, "get_multiple_thread_versions", fake_batch)
    assert api.check_all_updates()["success"] is True
    deadline = time.monotonic() + 5
    while api.get_update_status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert api.get_update_status()["running"] is False
    if outcome == "success":
        assert get_setting("last_update_check") != previous
    else:
        assert get_setting("last_update_check") == previous


def test_check_app_updates_fetches_github_once_per_session(monkeypatch):
    api = Api()
    calls: list[str] = []

    class FakeResponse(io.BytesIO):
        status = 200

    def fake_urlopen(request, timeout=0):
        _ = timeout
        calls.append(request.full_url)
        if len(calls) == 1:
            raise URLError("offline")
        return FakeResponse(json.dumps({"tag_name": "v9.9.9", "assets": []}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert api.check_app_updates()["success"] is False  # failures are retried
    assert api.check_app_updates()["version"] == "v9.9.9"
    assert api.check_app_updates()["version"] == "v9.9.9"
    assert len(calls) == 2
