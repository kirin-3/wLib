# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.api import Api
from core.database import add_game, get_all_games, init_db, update_setting


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
    game_id = add_game("Sandbox game", "/game.exe", sandbox_enabled=True)
    api = Api()
    captured = {}

    def launch(*args, **kwargs):
        captured.update(kwargs)
        return {"success": True, "tracks_playtime": False}

    monkeypatch.setattr(api.launcher, "launch", launch)
    assert api.launch_game(game_id, exe_path="/extra.exe")["success"]
    assert captured["sandbox_enabled"] is True
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
    monkeypatch.setattr("core.api.is_linux", lambda: True)
    monkeypatch.setattr("core.api.is_windows", lambda: False)
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

    monkeypatch.setattr("core.api.threading.Thread", InlineThread)
    assert api.install_sandbox_tool("bwrap")["success"]
    progress = api.get_sandbox_install_status()
    assert not progress["running"] and "cancelled" in progress["error"]
    assert calls == [{"recheck": True}]
    assert api.install_sandbox_tool("sandboxie")["code"] == "unsupported_platform"
