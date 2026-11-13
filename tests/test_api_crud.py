# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.api import Api
from core.database import add_game, get_all_games, init_db, update_setting
from api_patching import patch_api_name


def test_added_game_metadata_saves_first_post_links(monkeypatch):
    from threading import Thread

    from core.database import get_game_links

    api = Api()
    links = [
        {
            "source": "dlsite",
            "id": "RJ123",
            "url": "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html",
        }
    ]
    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda _: {"success": True, "store_links": links},
    )
    # Execute this add's worker synchronously so the test observes persistence,
    # without polling a daemon thread or sleeping.
    monkeypatch.setattr(Thread, "start", lambda self: self.run())
    result = api.add_game("Store game", "", "https://f95zone.to/threads/123/")
    assert result["success"]
    assert get_game_links(result["id"]) == links
    assert api._metadata_pending == set()


@pytest.mark.parametrize("metadata", [{}, {"store_links": []}, {"store_links": None}])
def test_metadata_without_visible_links_preserves_stored_links(metadata):
    from core.database import get_game_links, replace_game_links

    url = "https://f95zone.to/threads/123/"
    game_id = add_game("Store game", "", url)
    replace_game_links(game_id, "f95_post", [{"url": "https://vndb.org/v123"}])
    before = get_game_links(game_id)
    assert Api()._update_thread_edit_metadata_for_url(url, metadata) == []
    assert get_game_links(game_id) == before


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    init_db()


@pytest.mark.parametrize("title", [None, "", "   ", [], {"name": "x"}])
def test_add_game_rejects_invalid_title(title):
    result = Api().add_game(title=title, exe_path="game.exe")
    assert result["success"] is False
    assert result["error_code"] == "invalid_title"


@pytest.mark.parametrize("default", ["false", "true"])
def test_manual_add_uses_sandbox_default(default):
    update_setting("sandbox_new_games", default)
    assert Api().add_game("Manual game", "/game")["success"]
    assert get_all_games()[0]["sandbox_enabled"] == int(default == "true")


def test_sandbox_status_endpoints(monkeypatch):
    calls = []
    status = {
        "status": "ready",
        "tool": "bwrap",
        "path": "/usr/bin/bwrap",
        "version": "0.13",
        "reason": "",
    }

    def fake_status(**kwargs):
        calls.append(kwargs)
        return status

    monkeypatch.setattr("core.game_sandbox.get_sandbox_status", fake_status)
    api = Api()
    assert api.get_sandbox_status() == status
    assert api.recheck_sandbox_status() == status
    assert calls == [{}, {"recheck": True}]
    assert api.get_platform_capabilities()["sandbox"] in ("bwrap", "sandboxie")


@pytest.mark.parametrize("default", ["false", "true"])
def test_extension_add_uses_sandbox_default(default, monkeypatch):
    update_setting("sandbox_new_games", default)
    api = Api()
    monkeypatch.setattr(api, "_fetch_added_game_metadata", lambda *_: None)
    # The extension opens the add form, which sends its metadata through Api.add_game.
    result = api.add_game(
        "Extension game",
        "",
        "https://f95zone.to/threads/game.123/",
        version="1.0",
        tags="RPG",
        developer="Developer",
        engine="RPGM",
    )
    assert result["success"]
    assert get_all_games()[0]["sandbox_enabled"] == int(default == "true")


def test_tag_color_set_change_clear():
    api = Api()
    assert api.set_tag_color(" Sandbox ", "green")["tag_colors"] == {"sandbox": "green"}
    assert api.set_tag_color("SANDBOX", "blue")["tag_colors"] == {"sandbox": "blue"}
    assert api.set_tag_color("other", "pink")["success"]
    assert not api.set_tag_color("Sandbox", "magenta")["success"]
    assert api.get_settings()["tag_colors"] == {"sandbox": "blue", "other": "pink"}
    assert api.set_tag_color("sandbox", None)["tag_colors"] == {"other": "pink"}
    assert api.set_tag_color("OTHER", "")["tag_colors"] == {}
    assert not api.set_tag_color(" ", "blue")["success"]


def test_diagnostics_payloads_and_log_folder_error(tmp_path, monkeypatch):
    import json
    from core import diagnostics

    api = Api()
    monkeypatch.setattr(diagnostics, "host_which", lambda name: None)
    monkeypatch.setattr(api, "get_available_runners", lambda: {"runners": []})
    monkeypatch.setattr(
        api,
        "get_extension_service_status",
        lambda: {"success": True, "reachable": False},
    )
    for method in (
        api.get_diagnostics,
        api.get_diagnostics_tools,
        api.get_diagnostics_report,
    ):
        result = method()
        assert result["success"], result
        json.dumps(result)
    monkeypatch.setattr("core.host_platform.get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        api,
        "_open_path_with_system_handler",
        lambda path: {"success": False, "error": "No file manager"},
    )
    result = api.open_log_folder()
    assert not result["success"]
    assert str(tmp_path / "logs") in result["error"]
    assert (tmp_path / "logs").is_dir()


def test_add_game_strips_title_and_reports_duplicate_url():
    api = Api()
    api._fetch_added_game_metadata = lambda *args: None
    url = "https://f95zone.to/threads/game.1/"
    first = api.add_game(title="  Game  ", exe_path="a.exe", f95_url=url)
    assert first["success"] is True
    assert first["title"] == "Game"
    second = api.add_game(title="Other", exe_path="b.exe", f95_url=url)
    assert second["error_code"] == "duplicate_url"


def test_update_game_validation():
    api = Api()
    game_id = add_game(title="Game", exe_path="game.exe")
    assert api.update_game(999999, {"title": "Ghost"})["error_code"] == "game_not_found"
    assert api.update_game(game_id, 123)["error_code"] == "invalid_fields"
    for title in ("   ", []):
        assert (
            api.update_game(game_id, {"title": title})["error_code"] == "invalid_title"
        )
    assert api.update_game(game_id, {"title": " Renamed "})["success"] is True


def test_delete_game_clears_metadata_pending():
    api = Api()
    game_id = add_game(title="Game", exe_path="game.exe")
    api._metadata_pending.add(game_id)
    assert api.delete_game(game_id)["success"] is True
    assert game_id not in api._metadata_pending


def test_check_path_rejects_folders_for_file_kinds(tmp_path):
    api = Api()
    assert api.check_path(str(tmp_path), "runner")["reason"] == "Expected a file"
    assert (
        api.check_path(str(tmp_path / "missing") + "/", "parent")["reason"]
        == "Expected a file"
    )
    assert api.check_path(str(tmp_path / "save.json"), "parent")["ok"] is True


# ── Game saves folder ──


@pytest.fixture
def saves_api(tmp_path, monkeypatch):
    # expanduser("~") reads USERPROFILE on Windows and HOME elsewhere.
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path / "home"))
    root = tmp_path / "save-backups"
    api = Api()
    monkeypatch.setattr(Api, "_save_backups_root", staticmethod(lambda: str(root)))
    return api, root


def _saves_game(tmp_path, **kwargs):
    game_id = add_game("Saves Game", str(tmp_path / "game" / "run.exe"), **kwargs)
    assert game_id is not None
    return game_id


def _write_save(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_saves_api_rejects_unknown_game_and_bad_backup_names(saves_api, tmp_path):
    api, root = saves_api
    for call in (
        lambda: api.get_game_saves(999),
        lambda: api.set_game_save_folder(999, str(tmp_path)),
        lambda: api.backup_game_saves(999),
        lambda: api.import_game_saves(999, str(tmp_path)),
        lambda: api.restore_game_saves_backup(999, "2026-10-23_12-00-00"),
        lambda: api.open_game_save_backup(999, "2026-10-23_12-00-00"),
        lambda: api.get_game_saves("nope"),
    ):
        assert call()["success"] is False

    game_id = _saves_game(tmp_path)
    (root / str(game_id + 1) / "2026-10-23_12-00-00").mkdir(parents=True)
    for bad in ("../2/2026-10-23_12-00-00", "..", "", "2026-10-23_12-00-00"):
        assert api.restore_game_saves_backup(game_id, bad)["success"] is False
        assert api.open_game_save_backup(game_id, bad)["success"] is False


def test_saves_api_override_backup_import_restore_flow(saves_api, tmp_path):
    api, root = saves_api
    game_id = _saves_game(tmp_path)
    saves = tmp_path / "my-saves"
    _write_save(saves / "1.save", "one")
    _write_save(saves / "2.save", "two-old")

    result = api.set_game_save_folder(game_id, str(saves))
    assert result["success"] is True and result["override_exists"] is True
    info = api.get_game_saves(game_id)
    assert info["override"] == str(saves)
    assert info["effective"] == str(saves)
    assert info["detected"] == []
    assert info["backups"] == []

    first = api.backup_game_saves(game_id)
    assert first["success"] is True
    assert [b["name"] for b in first["backups"]] == [first["backup"]]

    source = tmp_path / "incoming"
    _write_save(source / "2.save", "two-new")
    _write_save(source / "3.save", "three")
    merged = api.import_game_saves(game_id, str(source))
    assert merged["success"] is True and merged["backup"]
    assert (saves / "1.save").read_text() == "one"
    assert (saves / "2.save").read_text() == "two-new"
    assert (saves / "3.save").read_text() == "three"
    assert len(api.get_game_saves(game_id)["backups"]) == 2

    restored = api.restore_game_saves_backup(game_id, first["backup"])
    assert restored["success"] is True
    assert (saves / "2.save").read_text() == "two-old"
    assert (saves / "3.save").read_text() == "three"  # merge keeps other files

    assert api.set_game_save_folder(game_id, "")["override"] == ""
    assert api.get_game_saves(game_id)["override"] == ""


def test_saves_api_rejects_invalid_overrides(saves_api, tmp_path):
    api, root = saves_api
    game_id = _saves_game(tmp_path)
    good = tmp_path / "good"
    assert api.set_game_save_folder(game_id, str(good))["success"] is True
    for bad in ("relative/dir", str(root / "1"), 5):
        assert api.set_game_save_folder(game_id, bad)["success"] is False
    assert api.get_game_saves(game_id)["override"] == str(good)
    assert api.get_game_saves(game_id)["override_exists"] is False


def test_update_game_validates_save_override_like_the_saves_api(saves_api, tmp_path):
    api, root = saves_api
    game_id = _saves_game(tmp_path)
    for bad in ("relative/dir", str(root / "1"), 5):
        result = api.update_game(game_id, {"save_path_override": bad})
        assert result["success"] is False
    assert api.get_game_saves(game_id)["override"] == ""

    assert api.update_game(game_id, {"save_path_override": "~/saves"})["success"]
    assert api.get_game_saves(game_id)["override"] == str(tmp_path / "home" / "saves")
    assert api.update_game(game_id, {"save_path_override": ""})["success"]
    assert api.get_game_saves(game_id)["override"] == ""


def test_saves_api_backup_needs_a_folder(saves_api, tmp_path):

    api, _root = saves_api
    game_id = _saves_game(tmp_path)
    assert api.backup_game_saves(game_id)["success"] is False
    assert api.import_game_saves(game_id, str(tmp_path))["success"] is False
    api.set_game_save_folder(game_id, str(tmp_path / "missing"))
    result = api.backup_game_saves(game_id)
    assert result["success"] is False and "nothing to back up" in str(result["error"])


def test_saves_api_detects_single_folder_and_ignores_exe_folder(saves_api, tmp_path):

    api, _root = saves_api
    game_id = _saves_game(tmp_path)
    _write_save(tmp_path / "game" / "slot.save")
    info = api.get_game_saves(game_id)
    assert info["effective"] == ""
    assert [d["eligible"] for d in info["detected"]] == [False]

    _write_save(tmp_path / "game" / "game" / "saves" / "1.save")
    info = api.get_game_saves(game_id)
    assert info["effective"] == str(tmp_path / "game" / "game" / "saves")


def test_saves_api_busy_guard(saves_api, tmp_path):

    api, _root = saves_api
    game_id = _saves_game(tmp_path)
    saves = tmp_path / "saves"
    _write_save(saves / "a.save")
    api.set_game_save_folder(game_id, str(saves))
    source = tmp_path / "src"
    _write_save(source / "b.save")

    api._saves_busy.add(game_id)
    for result in (
        api.backup_game_saves(game_id),
        api.import_game_saves(game_id, str(source)),
    ):
        assert result["success"] is False
        assert "already running" in str(result["error"])
    assert not (saves / "b.save").exists()
    api._saves_busy.discard(game_id)

    assert api.backup_game_saves(game_id)["success"] is True
    assert game_id not in api._saves_busy


def test_saves_api_busy_guard_released_after_exception(saves_api, tmp_path):

    api, _root = saves_api
    with pytest.raises(RuntimeError):
        with api._save_operation(7):
            assert 7 in api._saves_busy
            raise RuntimeError("boom")
    assert 7 not in api._saves_busy


def test_launch_uses_stored_sandbox_flag_for_extra_target(monkeypatch):
    game_id = add_game("Sandbox game", "/game.exe", sandbox_enabled=True, block_network=True)
    api = Api()
    captured = {}

    def launch(*args, **kwargs):
        captured.update(kwargs)
        return {"success": True, "tracks_playtime": False}

    monkeypatch.setattr(api.launcher, "launch", launch)
    assert api.launch_game(game_id, exe_path="/extra.exe")["success"]
    assert captured["sandbox_enabled"] is True
    assert captured["block_network"] is True
    assert captured["game_title"] == "Sandbox game"


def test_sandbox_bulk_api_and_custom_path_validation():
    api = Api()
    add_game("One", "/one")
    assert api.set_sandbox_for_all_games(True) == {"success": True, "changed": 1}
    assert api.set_sandbox_for_all_games(True)["changed"] == 0
    assert not api.set_sandbox_for_all_games("yes")["success"]
    assert api.save_settings({"sandboxie_custom_path": r"C:\Sandboxie\Start.exe"})[
        "success"
    ]
    assert not api.save_settings(
        {"sandboxie_custom_path": r"C:\Sandboxie\SandMan.exe"}
    )["success"]
    assert api.get_settings()["sandboxie_custom_path"] == r"C:\Sandboxie\Start.exe"
    assert api.save_settings({"sandbox_new_games": True})["success"]
    assert api.get_settings()["sandbox_new_games"] is True


def test_sandbox_install_failure_is_reported_and_rechecked(monkeypatch):
    from core import game_sandbox

    api = Api()
    patch_api_name(monkeypatch, "is_linux", lambda: True)
    patch_api_name(monkeypatch, "is_windows", lambda: False)
    calls = []

    def status(**kwargs):
        calls.append(kwargs)
        return {"status": "missing", "reason": "Bubblewrap is not installed."}

    def failed(*args):
        raise RuntimeError("Authentication cancelled")

    monkeypatch.setattr(game_sandbox, "get_sandbox_status", status)
    monkeypatch.setattr(game_sandbox, "install_sandbox_tool", failed)

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("threading.Thread", InlineThread)
    assert api.install_sandbox_tool("bwrap")["success"]
    progress = api.get_sandbox_install_status()
    assert not progress["running"] and "cancelled" in progress["error"]
    assert calls == [{"recheck": True}]
    assert api.install_sandbox_tool("sandboxie")["code"] == "unsupported_platform"


def test_add_links_duplicate_attach_and_conflicting_thread(monkeypatch):
    from core.database import get_game_links, get_game

    api = Api()
    calls = []
    monkeypatch.setattr(
        api, "_fetch_added_game_metadata", lambda *args: calls.append(args)
    )
    links = [{"url": "https://www.dlsite.com/maniax/work/=/product_id/RJ123.html"}]
    first = api.add_game("Work", "", links=links)
    assert first["success"]
    assert get_game_links(first["id"])[0]["id"] == "RJ123"
    assert api.add_game("Duplicate", "", links=links)["error_code"] == "duplicate_url"
    attached = api.add_game(
        "Thread", "", "https://f95zone.to/threads/123/", links=links
    )
    assert attached["success"] and attached["attached"]
    assert attached["id"] == first["id"]
    assert get_game(first["id"])["f95_url"] == "https://f95zone.to/threads/123/"
    assert (
        api.add_game(
            "Other thread", "", "https://f95zone.to/threads/456/", links=links
        )["error_code"]
        == "duplicate_url"
    )
    assert len(get_all_games()) == 1


def test_attaching_with_an_executable_installs_the_wishlisted_match(monkeypatch):
    from core.database import get_game

    api = Api()
    monkeypatch.setattr(api, "_fetch_added_game_metadata", lambda *args: None)
    links = [{"url": "https://vndb.org/v123"}]
    wishlisted = api.add_game("Work", "", links=links)
    attached = api.add_game(
        "Thread",
        "/games/work/game.exe",
        "https://f95zone.to/threads/123/",
        version="1.0",
        links=links,
    )
    assert attached["attached"] and attached["id"] == wishlisted["id"]
    game = get_game(wishlisted["id"])
    assert game["exe_path"] == "/games/work/game.exe"
    assert game["version"] == "1.0"
    assert len(get_all_games()) == 1


def test_ambiguous_add_does_not_attach_or_change_links(monkeypatch):
    from core.database import replace_game_links

    api = Api()
    links = [{"url": "https://vndb.org/v123"}]
    for name in ("One", "Two"):
        replace_game_links(add_game(name, ""), "page", links)
    before = get_all_games()
    assert (
        api.add_game("Thread", "", "https://f95zone.to/threads/123/", links=links)[
            "error_code"
        ]
        == "duplicate_url"
    )
    assert get_all_games() == before


def test_cover_cache_api_validity_and_delete(tmp_path, monkeypatch):
    from core import database, game_covers

    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    api = Api()
    url = "https://attachments.f95zone.to/image.png"
    game_id = add_game("Cache", "", "", cover_image=url)
    name = game_covers.store_cached_cover(b"\x89PNG\r\n\x1a\nimage")
    assert database.set_game_cover_cache(game_id, url, name)
    assert "cover_cache_url" not in api.get_games()[0]
    api.library_sync.server_available = True
    assert api.get_games()[0]["cover_cache_url"].endswith("/covers/cache/" + name)
    database.update_game(game_id, {"cover_image_path": url + "new"})
    assert "cover_cache_url" not in api.get_games()[0]
    database.update_game(game_id, {"cover_image_path": url})
    game_covers.delete_cached_cover(name)
    assert "cover_cache_url" not in api.get_games()[0]
    name = game_covers.store_cached_cover(b"\x89PNG\r\n\x1a\nimage")
    assert database.set_game_cover_cache(game_id, url, name)
    assert api.delete_game(game_id)["success"]
    assert not game_covers.cached_cover_path(name).exists()


def test_performance_settings_round_trip():
    api = Api()
    assert api.save_settings({"gamemode_enabled": True, "mangohud_enabled": True})["success"]
    settings = api.get_settings()
    assert settings["gamemode_enabled"] is True
    assert settings["mangohud_enabled"] is True


def test_web_player_devtools_setting_round_trip():
    api = Api()
    assert api.get_settings()["web_player_devtools"] is True
    assert api.save_settings({"web_player_devtools": False})["success"]
    from core.database import get_setting

    assert get_setting("web_player_devtools") == "false"
    assert api.get_settings()["web_player_devtools"] is False
    assert api.save_settings({"web_player_devtools": True})["success"]
    assert api.get_settings()["web_player_devtools"] is True


def test_performance_tool_detection_cache(monkeypatch):
    api = Api()
    calls = []
    patch_api_name(monkeypatch, "is_windows", lambda: False)
    patch_api_name(monkeypatch, "host_which", lambda name: calls.append(name) or ("/bin/mangohud" if name == "mangohud" else None))
    expected = {"gamemode": False, "mangohud": True, "gamescope": False}
    assert api.get_performance_tools() == expected
    assert api.get_performance_tools() == expected
    assert len(calls) == 3
    api.get_performance_tools(True)
    assert len(calls) == 6


def test_gamescope_settings_defaults_and_round_trip():
    api = Api()
    settings = api.get_settings()
    assert settings["gamescope_enabled"] is False
    assert settings["gamescope_window_mode"] == "fullscreen"
    assert settings["gamescope_scaler"] == "auto"
    assert settings["gamescope_filter"] == "linear"
    assert settings["gamescope_output_resolution"] == ""
    assert settings["gamescope_fps_limit"] == ""
    result = api.save_settings(
        {
            "gamescope_enabled": True,
            "gamescope_window_mode": "borderless",
            "gamescope_output_resolution": "2560 x 1440",
            "gamescope_game_resolution": "",
            "gamescope_scaler": "Integer",
            "gamescope_filter": "nearest",
            "gamescope_fps_limit": 60,
            "gamescope_args": "--hdr-enabled",
        }
    )
    assert result["success"]
    settings = api.get_settings()
    assert settings["gamescope_enabled"] is True
    assert settings["gamescope_window_mode"] == "borderless"
    assert settings["gamescope_output_resolution"] == "2560x1440"
    assert settings["gamescope_scaler"] == "integer"
    assert settings["gamescope_fps_limit"] == "60"
    assert settings["gamescope_args"] == "--hdr-enabled"


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("gamescope_output_resolution", "0x600", "1 to 16384"),
        ("gamescope_fps_limit", "0", "1 to 1000"),
        ("gamescope_args", '--hdr-enabled "unclosed', "quoting is not closed"),
        ("gamescope_args", "-f -- foo", "`--` is not allowed"),
        ("gamescope_scaler", "bogus", "scaler"),
    ],
)
def test_gamescope_settings_reject_bad_values(key, value, message):
    from core.database import get_setting

    api = Api()
    before = get_setting(key)
    result = api.save_settings({key: value, "gamemode_enabled": True})
    assert result["success"] is False
    assert result["field"] == key
    assert message in result["error"]
    assert get_setting(key) == before
    assert get_setting("gamemode_enabled") != "true"


def test_gamescope_status_install_hint(monkeypatch):
    api = Api()
    patch_api_name(monkeypatch, "is_windows", lambda: False)
    patch_api_name(monkeypatch, "host_which", lambda name: "/usr/bin/pacman" if name == "pacman" else None)
    import core.host_platform as host_platform

    monkeypatch.setattr(host_platform, "host_system_layout", lambda: (False, {}))
    assert api.get_gamescope_status(True) == {
        "installed": False,
        "command": "sudo pacman -S gamescope",
        "package_manager": "pacman",
        "nixos": False,
    }
    monkeypatch.setattr(host_platform, "host_system_layout", lambda: (True, {}))
    assert api.get_gamescope_status(True)["command"] == "programs.gamescope.enable = true;"
    patch_api_name(monkeypatch, "host_which", lambda name: "/usr/bin/gamescope" if name == "gamescope" else None)
    assert api.get_gamescope_status(True) == {
        "installed": True,
        "command": "",
        "package_manager": "",
        "nixos": False,
    }


def test_performance_overrides_passed_to_launcher(monkeypatch):
    api = Api()
    game_id = add_game("Tools", "/game")
    from core.database import update_game, update_setting
    update_game(game_id, {"gamemode": "off", "mangohud": "on"})
    update_setting("gamemode_enabled", "true")
    update_setting("mangohud_enabled", "false")
    captured = {}
    def launch(*args, **kwargs):
        captured.update(kwargs)
        return {"success": True, "tracks_playtime": False}
    monkeypatch.setattr(api.launcher, "launch", launch)
    assert api.launch_game(game_id, "/game")["success"]
    assert captured["gamemode"] is False
    assert captured["mangohud"] is True


def _mark_running(api, game_id, prefix=""):
    from core.launcher import RunningGame

    api.launcher._running[game_id] = RunningGame(None, None, prefix=prefix, title="Busy game")


def test_save_restore_refused_while_game_runs(saves_api, tmp_path):

    api, _root = saves_api
    game_id = _saves_game(tmp_path)
    saves = tmp_path / "my-saves"
    _write_save(saves / "1.save", "one")
    assert api.set_game_save_folder(game_id, str(saves))["success"] is True
    backup = api.backup_game_saves(game_id)
    _write_save(saves / "1.save", "newer")
    _mark_running(api, game_id)
    # Backups only read the folder, so they stay allowed while playing.
    assert api.backup_game_saves(game_id)["success"] is True
    restored = api.restore_game_saves_backup(game_id, backup["backup"])
    assert restored["success"] is False
    assert "Close the game" in restored["error"]
    assert (saves / "1.save").read_text() == "newer"


def test_launch_and_install_refused_while_game_files_are_busy(tmp_path):
    api = Api()
    game_id = add_game("Busy", str(tmp_path / "run.exe"))
    assert game_id is not None
    api._tool_status[game_id] = {"running": True, "action": "extract"}
    launched = api.launch_game(game_id, str(tmp_path / "run.exe"))
    assert launched["success"] is False and launched["code"] == "game_busy"
    import zipfile

    with zipfile.ZipFile(tmp_path / "x.zip", "w") as zf:
        zf.writestr("run.exe", b"MZ")
    (tmp_path / "lib").mkdir()
    update_setting("library_folder", str(tmp_path / "lib"))
    installed = api.install_archive({"archive_path": str(tmp_path / "x.zip"), "game_id": game_id, "title": "Busy"})
    assert installed["success"] is False and installed["error_code"] == "game_busy"
    assert api._archive_install_status["running"] is False
    _ = api._tool_status.pop(game_id)
    with api._saves_busy_lock:
        api._saves_writing.add(game_id)
    assert api.launch_game(game_id, str(tmp_path / "run.exe"))["code"] == "game_busy"


def test_install_archive_into_wishlisted_game_with_same_thread_url(tmp_path):
    import zipfile

    url = "https://f95zone.to/threads/game.123/"
    game_id = add_game("Game", "", f95_url=url)
    assert game_id is not None
    with zipfile.ZipFile(tmp_path / "Game-1.0.zip", "w") as zf:
        zf.writestr("Game.exe", b"MZ")
    (tmp_path / "lib").mkdir()
    update_setting("library_folder", str(tmp_path / "lib"))
    update_setting("library_folders", f'["{tmp_path / "lib"}"]')
    api = Api()
    installed = api.install_archive(
        {
            "archive_path": str(tmp_path / "Game-1.0.zip"),
            "game_id": game_id,
            "title": "Game",
            "f95_url": url,
        }
    )
    assert installed["success"] is True, installed
    games = get_all_games()
    assert len(games) == 1
    assert str(games[0]["exe_path"]).endswith("Game.exe")


def test_mod_tool_refused_while_archive_update_runs(tmp_path):
    from core.game_tools import GameToolError

    api = Api()
    api._archive_install_status["running"] = True
    api._archive_install_game_id = 7
    with pytest.raises(GameToolError, match="being updated"):
        with api._tool_run(7, "extract"):
            pass
    api._set_archive_install_status(running=False)
    assert api._archive_install_game_id is None
    with api._tool_run(7, "extract"):
        pass


def test_delete_game_keeps_its_save_backups_aside(tmp_path, monkeypatch):
    root = tmp_path / "save-backups"
    monkeypatch.setattr(Api, "_save_backups_root", staticmethod(lambda: str(root)))
    api = Api()
    game_id = add_game(title="Game", exe_path="game.exe")
    other_id = add_game(title="Other", exe_path="other.exe")
    (root / str(game_id) / "20260101-000000").mkdir(parents=True)
    (root / str(other_id)).mkdir()
    assert api.delete_game(game_id)["success"] is True
    assert not (root / str(game_id)).exists()
    kept = list((root / "removed").iterdir())
    assert len(kept) == 1 and kept[0].name.startswith(f"{game_id}_")
    assert (kept[0] / "20260101-000000").is_dir()
    assert (root / str(other_id)).is_dir()


def test_delete_game_refuses_running_or_busy_game(monkeypatch):
    api = Api()
    game_id = add_game(title="Game", exe_path="game.exe")
    monkeypatch.setattr(api, "get_running_games", lambda: [game_id])
    assert api.delete_game(game_id)["error_code"] == "game_running"
    monkeypatch.setattr(api, "get_running_games", lambda: [])
    with api._tool_run(game_id, "extract"):
        assert api.delete_game(game_id)["error_code"] == "game_busy"
    assert api.delete_game(game_id)["success"] is True


def test_cover_change_reports_locked_database(tmp_path, monkeypatch):
    import sqlite3

    api = Api()
    game_id = add_game(title="Game", exe_path="game.exe")

    def locked(*_args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("core.game_covers.copy_cover_file", lambda _path: "x.png")
    monkeypatch.setattr("core.database.set_game_local_cover", locked)
    monkeypatch.setattr("core.database.clear_game_local_cover", locked)
    image = tmp_path / "cover.png"
    image.write_bytes(b"x")
    assert api.set_game_cover_file(game_id, str(image)) == {
        "success": False,
        "error": "database is locked",
    }
    assert api.clear_game_cover(game_id) == {
        "success": False,
        "error": "database is locked",
    }


def test_delete_game_moves_its_sandbox_aside(tmp_path, monkeypatch):
    import os

    from core import game_sandbox

    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    api = Api()
    game_id = add_game(title="Game", exe_path="game.exe")
    home = game_sandbox.prepare_sandbox_home(game_id)
    (tmp_path / "data" / "sandbox" / str(game_id) / "home" / "save.dat").write_text("x")
    assert api.delete_game(game_id)["success"] is True
    # A later game with the same ID must not inherit this home or prefix.
    assert not os.path.exists(home)
    removed = tmp_path / "data" / "sandbox" / "removed"
    (kept,) = removed.iterdir()
    assert kept.name.startswith(f"{game_id}_")
    assert (kept / "home" / "save.dat").read_text() == "x"
    # Kept for 30 days, then pruned.
    assert game_sandbox.prune_removed_sandboxes() == 0
    later = kept.stat().st_mtime + game_sandbox.REMOVED_SANDBOX_KEEP_SECONDS + 1
    assert game_sandbox.prune_removed_sandboxes(now=later) == 1
    assert not kept.exists()


def test_cancel_archive_install_only_before_the_move(monkeypatch):
    api = Api()
    assert api.cancel_archive_install()["error_code"] == "not_running"
    api._archive_install_status = {"running": True, "stage": "extracting", "done": 0, "total": 0, "cancellable": True}
    assert api.cancel_archive_install()["success"] is True
    assert api._archive_install_cancel.is_set()
    assert api.get_archive_install_status()["stage"] == "cancelling"
    # A later stage report keeps showing the cancel in progress.
    api._set_archive_install_status(running=True, stage="fixing permissions")
    assert api.get_archive_install_status()["stage"] == "cancelling"
    api._archive_install_cancel.clear()
    api._set_archive_install_status(running=True, stage="moving into place", done=5, total=9)
    status = api.get_archive_install_status()
    assert status["cancellable"] is False and status["done"] == 5
    assert api.cancel_archive_install()["error_code"] == "not_cancellable"


def test_delete_game_moves_web_player_storage_aside_and_prunes_it(tmp_path, monkeypatch):
    import os

    from core import game_sandbox

    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    api = Api()
    game_id = add_game(title="Game", exe_path="index.html")
    storage = game_sandbox.web_player_folder(game_id)
    os.makedirs(storage)
    with open(os.path.join(storage, "save.dat"), "w") as handle:
        handle.write("x")
    assert api.delete_game(game_id)["success"] is True
    assert not os.path.exists(storage)
    (kept,) = (tmp_path / "data" / "web-player" / "removed").iterdir()
    assert kept.name.startswith(f"{game_id}_") and (kept / "save.dat").read_text() == "x"
    assert game_sandbox.prune_removed_sandboxes() == 0
    later = kept.stat().st_mtime + game_sandbox.REMOVED_SANDBOX_KEEP_SECONDS + 1
    assert game_sandbox.prune_removed_sandboxes(now=later) == 1
    assert not kept.exists()


def test_builtin_player_saves_are_detected_and_refuse_changes_while_running(saves_api, tmp_path, monkeypatch):
    from core import game_sandbox

    from core.database import update_game

    api, _root = saves_api
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    html = tmp_path / "web" / "index.html"
    _write_save(html)
    game_id = add_game("Web", str(html))
    assert game_id is not None
    storage = game_sandbox.web_player_folder(game_id)
    _write_save(__import__("pathlib").Path(storage) / "data" / "state.bin", "s")
    info = api.get_game_saves(game_id)
    assert info["effective"] == storage and info["detected"][0]["type"] == "Web Player"
    # The system browser keeps its saves elsewhere.
    update_game(game_id, {"html_player": "browser"})
    assert api.get_game_saves(game_id)["effective"] == ""
    update_game(game_id, {"html_player": ""})
    monkeypatch.setattr(api, "get_running_games", lambda: [game_id])
    source = tmp_path / "incoming"
    _write_save(source / "a.save", "a")
    refused = api.import_game_saves(game_id, str(source))
    assert refused["success"] is False and "Close the game" in str(refused["error"])
