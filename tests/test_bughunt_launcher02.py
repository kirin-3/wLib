# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for four launcher bugs (Steam runner, shortcuts, umu)."""

import json
import os
import subprocess
import sys
import threading
import time
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import extension_server as server_module
from core import steam_runner, umu
from core.launcher import Launcher, RunningGame
from core.steam_shortcuts import (
    ShortcutsChanged,
    atomic_write,
    read_vdf,
    write_vdf,
)

REPO = Path(__file__).resolve().parent.parent
# Prefix claims use flock, POSIX file modes and /proc: Linux only, like the runner.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX-only")
TOKEN = "t" * 64


@pytest.fixture
def runner_token(monkeypatch):
    monkeypatch.setattr(steam_runner, "runner_token", lambda: TOKEN)


# --- Bug 1: a duplicate Steam launch must not free the running game's prefix ---


class _WindowApi:
    """The window's reservation methods, on a real Launcher."""

    def __init__(self):
        from core.api_mixins.launch import LaunchMixin

        self.mixin = LaunchMixin
        self.launcher = Launcher()
        self._status_lock = threading.Lock()
        self._prefix_tool_conflict = lambda _prefix, _game_id: None

    def reserve_runner_launch(self, *args, **kwargs):
        return self.mixin.reserve_runner_launch(self, *args, **kwargs)

    def release_runner_launch(self, *args, **kwargs):
        return self.mixin.release_runner_launch(self, *args, **kwargs)


def _post(path, payload):
    handler = server_module.ExtensionRequestHandler.__new__(
        server_module.ExtensionRequestHandler
    )
    handler.path = path
    headers = Message()
    body = json.dumps(payload).encode()
    for key, value in {
        "X-wLib-Extension": "1",
        "X-wLib-Runner": TOKEN,
        "Content-Length": str(len(body)),
    }.items():
        headers[key] = value
    handler.headers = headers
    handler.__dict__["wfile"] = BytesIO()
    handler.__dict__["rfile"] = BytesIO(body)
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.do_POST()
    return handler


def test_duplicate_launch_error_keeps_the_original_reservation(
    monkeypatch, runner_token
):
    api = _WindowApi()
    monkeypatch.setattr(server_module, "api_ref", api)
    monkeypatch.setattr(server_module, "window_ref", MagicMock())
    claim = {
        "gameId": 12,
        "launchId": "original",
        "prefix": "/p",
        "sandboxed": True,
        "title": "Running",
    }
    assert json.loads(_post("/api/launch-check", claim).wfile.getvalue()) == {
        "success": True
    }
    assert api.launcher.game_using_prefix("/p") == "Running"
    # The duplicate exits with "already running" and reports an error.
    duplicate = {
        "gameId": 12,
        "state": "error",
        "error": "Running is already running.",
        "launchId": "duplicate",
    }
    assert _post("/api/runner-event", duplicate).send_response.call_args.args == (200,)
    assert api.launcher.game_using_prefix("/p") == "Running"
    assert "Running" in (
        api.launcher._prefix_conflict_locked(RunningGame(None, None, prefix="/p"), 13)
        or ""
    )
    # The runner that made the reservation still releases it, by error or at its end.
    for state in ("error", "ended"):
        _ = api.launcher.reserve_external(
            12, RunningGame(None, None, prefix="/p", launch_token="original")
        )
        _ = _post(
            "/api/runner-event", {**duplicate, "state": state, "launchId": "original"}
        )
        assert api.launcher.game_using_prefix("/p") is None


def test_runner_without_a_launch_id_still_releases(monkeypatch, runner_token):
    """A runner from before launch IDs sends none; its events keep working."""
    api = _WindowApi()
    monkeypatch.setattr(server_module, "api_ref", api)
    monkeypatch.setattr(server_module, "window_ref", MagicMock())
    _ = api.launcher.reserve_external(5, RunningGame(None, None, prefix="/q"))
    _ = _post("/api/runner-event", {"gameId": 5, "state": "error", "error": "x"})
    assert api.launcher.game_using_prefix("/q") is None
    _ = api.launcher.reserve_external(
        5, RunningGame(None, None, prefix="/q", launch_token="new")
    )
    _ = _post("/api/runner-event", {"gameId": 5, "state": "ended"})
    assert api.launcher.game_using_prefix("/q") is None


def _run_with(monkeypatch, launch):
    """steam_runner.run(8) with the window and launcher faked; returns what it sent."""
    from core import database, game_launch
    from core import launcher as launcher_module

    sent = []
    monkeypatch.setattr(database, "init_db", lambda: None)
    monkeypatch.setattr(database, "get_game", lambda _: {"id": 8, "exe_path": "/g"})
    monkeypatch.setattr(game_launch, "request_from_game", lambda _game: {})
    monkeypatch.setattr(steam_runner, "create_record", lambda _: True)
    monkeypatch.setattr(steam_runner, "remove_record", lambda _: None)
    monkeypatch.setattr(
        steam_runner,
        "_window_request",
        lambda path, payload=None: sent.append((path, payload)) or {"success": True},
    )
    monkeypatch.setattr("core.desktop_notify.send_notification", lambda *_: None)
    monkeypatch.setattr(launcher_module, "Launcher", lambda: SimpleNamespace())
    monkeypatch.setattr(game_launch, "launch_tracked", launch)
    return sent


def test_each_runner_invocation_names_itself_to_the_window(monkeypatch):
    checks = []
    monkeypatch.setattr(
        steam_runner, "launch_check", lambda payload: checks.append(payload) or None
    )
    monkeypatch.setattr(steam_runner, "claim_prefix", lambda *_args: None)

    def failing_launch(launcher, game_id, _request, _callback):
        assert (
            launcher.external_guard(RunningGame(None, None, prefix="/p"), game_id)
            is None
        )
        return {"success": False, "error": "no"}

    sent = _run_with(monkeypatch, failing_launch)
    assert steam_runner.run(8) == 1
    first = [payload["launchId"] for _path, payload in sent if payload]
    assert len(first) == 3  # started, error, ended
    assert len(set(first)) == 1 and first[0]
    assert checks[0]["launchId"] == first[0]
    sent.clear()
    assert steam_runner.run(8) == 1
    second = {payload["launchId"] for _path, payload in sent if payload}
    assert len(second) == 1 and second != set(first)


def test_refused_duplicate_runner_reports_its_own_launch_id(monkeypatch):
    from core import database

    reported = []
    monkeypatch.setattr(database, "init_db", lambda: None)
    monkeypatch.setattr(database, "get_game", lambda _: {"id": 8, "exe_path": "/g"})
    monkeypatch.setattr(steam_runner, "_window_request", lambda *_: None)
    monkeypatch.setattr(steam_runner, "create_record", lambda _: False)
    monkeypatch.setattr(
        steam_runner, "report_error", lambda *args: reported.append(args)
    )
    assert steam_runner.run(8) == 3
    assert steam_runner.run(8) == 3
    assert reported[0][0] == 8 and reported[0][2]
    assert reported[0][2] != reported[1][2]


def test_startup_failure_still_releases_its_own_reservation(monkeypatch, runner_token):
    api = _WindowApi()
    monkeypatch.setattr(server_module, "api_ref", api)
    monkeypatch.setattr(server_module, "window_ref", MagicMock())
    window = MagicMock()
    monkeypatch.setattr(server_module, "window_ref", window)
    _ = _post(
        "/api/launch-check",
        {"gameId": 3, "launchId": "mine", "prefix": "/z", "sandboxed": True},
    )
    assert api.launcher.game_using_prefix("/z")
    _ = _post(
        "/api/runner-event",
        {"gameId": 3, "state": "error", "error": "boom", "launchId": "mine"},
    )
    assert api.launcher.game_using_prefix("/z") is None
    assert any(
        "wlib-launch-error" in call.args[0]
        for call in window.evaluate_js.call_args_list
    )


# --- Bug 2: headless runners share prefix claims ---


def _proc_for(tmp_path, pid, game_id):
    folder = tmp_path / "proc" / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cmdline").write_bytes(f"wlib\0--launch\0{game_id}\0".encode())


def _fake_runner(tmp_path, game_id, pid):
    """A live run record owned by this process, as claim_prefix requires."""
    assert steam_runner.create_record(game_id, str(tmp_path))
    path = tmp_path / "runs" / f"{game_id}.json"
    record = json.loads(path.read_text())
    record["pid"] = pid
    path.write_text(json.dumps(record))
    _proc_for(tmp_path, pid, game_id)


@posix_only
def test_claims_refuse_a_shared_proton_prefix_and_release(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    entry = RunningGame(None, None, prefix="/pfx", umu=True, title="Alpha")
    _fake_runner(tmp_path, 1, os.getpid())
    assert steam_runner.claim_prefix(1, entry, str(tmp_path), proc) is None
    # Another runner's record, other game, same prefix: refused.
    monkeypatch.setattr(os, "getpid", lambda: 4242)
    _fake_runner(tmp_path, 2, 4242)
    second = RunningGame(None, None, prefix="/pfx", umu=True, title="Beta")
    refusal = steam_runner.claim_prefix(2, second, str(tmp_path), proc)
    assert refusal and "Alpha" in refusal
    # A separate prefix is fine.
    other = RunningGame(None, None, prefix="/other", umu=True, title="Beta")
    assert steam_runner.claim_prefix(2, other, str(tmp_path), proc) is None
    # Releasing the record frees the prefix.
    steam_runner.remove_record(2, str(tmp_path))
    _fake_runner(tmp_path, 3, 4242)
    assert steam_runner.claim_prefix(3, second, str(tmp_path), proc) is not None
    monkeypatch.undo()
    steam_runner.remove_record(1, str(tmp_path))
    monkeypatch.setattr(os, "getpid", lambda: 4242)
    assert steam_runner.claim_prefix(3, second, str(tmp_path), proc) is None


@posix_only
def test_compatible_wine_games_can_share_a_claimed_prefix(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    _fake_runner(tmp_path, 1, os.getpid())
    plain = RunningGame(None, None, prefix="/pfx", title="Alpha")
    assert steam_runner.claim_prefix(1, plain, str(tmp_path), proc) is None
    monkeypatch.setattr(os, "getpid", lambda: 4242)
    _fake_runner(tmp_path, 2, 4242)
    assert steam_runner.claim_prefix(2, plain, str(tmp_path), proc) is None
    # A network-blocked game cannot join a networked one, as inside one launcher.
    monkeypatch.setattr(os, "getpid", lambda: 4343)
    _fake_runner(tmp_path, 3, 4343)
    blocked = RunningGame(
        None, None, prefix="/pfx", network_blocked=True, title="Gamma"
    )
    assert steam_runner.claim_prefix(3, blocked, str(tmp_path), proc)


@posix_only
def test_claim_without_prefix_and_dead_runner_claims(tmp_path):
    proc = tmp_path / "proc"
    _fake_runner(tmp_path, 1, os.getpid())
    assert (
        steam_runner.claim_prefix(1, RunningGame(None, None), str(tmp_path), proc)
        is None
    )
    assert steam_runner.running_claims(str(tmp_path), proc) == {}
    claimed = RunningGame(None, None, prefix="/pfx", umu=True, title="Alpha")
    assert steam_runner.claim_prefix(1, claimed, str(tmp_path), proc) is None
    assert list(steam_runner.running_claims(str(tmp_path), proc)) == [1]
    # The runner died without removing its record: the claim goes.
    for path in (proc / str(os.getpid())).iterdir():
        path.unlink()
    (proc / str(os.getpid())).rmdir()
    assert steam_runner.running_claims(str(tmp_path), proc) == {}
    mode = (tmp_path / "runs/1.json").stat().st_mode & 0o777
    assert mode == 0o600
    assert not list((tmp_path / "runs").glob(".*.tmp"))


@posix_only
def test_window_sees_runner_claims(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    _fake_runner(tmp_path, 1, os.getpid())
    claim = RunningGame(None, None, prefix="/pfx", umu=True, title="Alpha")
    assert steam_runner.claim_prefix(1, claim, str(tmp_path), proc) is None
    launcher = Launcher()
    launcher.external_claims = lambda: steam_runner.running_claims(str(tmp_path), proc)
    assert launcher.game_using_prefix("/pfx") == "Alpha"
    refused = launcher._prefix_conflict_locked(
        RunningGame(None, None, prefix="/pfx"), 2
    )
    assert refused and "Alpha" in refused
    assert (
        launcher._prefix_conflict_locked(RunningGame(None, None, prefix="/x"), 2)
        is None
    )
    launcher.shutdown()


def test_runner_checks_the_window_before_claiming(monkeypatch):
    calls = []
    monkeypatch.setattr(
        steam_runner,
        "launch_check",
        lambda _payload: calls.append("window") or "window busy",
    )
    monkeypatch.setattr(
        steam_runner,
        "claim_prefix",
        lambda *_args: calls.append("claim") or "runner busy",
    )
    guards = []

    def launch(launcher, game_id, _request, _callback):
        entry = RunningGame(None, None, prefix="/p")
        guards.append(launcher.external_guard(entry, game_id))
        monkeypatch.setattr(steam_runner, "launch_check", lambda _p: None)
        guards.append(launcher.external_guard(entry, game_id))
        return {"success": False, "error": "x"}

    _ = _run_with(monkeypatch, launch)
    assert steam_runner.run(8) == 1
    assert guards == ["window busy", "runner busy"]
    assert calls == ["window", "claim"]


CHILD = """
import sys
from core import steam_runner
from core.launcher import RunningGame

data, prefix, title, umu = sys.argv[3:7]
game = int(sys.argv[2])
assert steam_runner.create_record(game, data)
entry = RunningGame(None, None, prefix=prefix, umu=umu == "1", title=title)
print(steam_runner.claim_prefix(game, entry, data) or "ok", flush=True)
sys.stdin.read()
steam_runner.remove_record(game, data)
"""


def _child(data, game, prefix, title, umu):
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            CHILD,
            "--launch",
            str(game),
            str(data),
            prefix,
            title,
            umu,
        ],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO), "QT_QPA_PLATFORM": "offscreen"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    return process, process.stdout.readline().strip()


def _end(process):
    assert process.stdin is not None
    process.stdin.close()
    _ = process.wait(timeout=30)


@posix_only
def test_separate_runner_processes_acquire_refuse_and_release(tmp_path):
    first, answer = _child(tmp_path, 1, "/pfx", "Alpha", "1")
    try:
        assert answer == "ok"
        second, answer = _child(tmp_path, 2, "/pfx", "Beta", "1")
        try:
            assert "Alpha" in answer and "already running" in answer
        finally:
            _end(second)
        elsewhere, answer = _child(tmp_path, 3, "/other", "Gamma", "1")
        try:
            assert answer == "ok"
        finally:
            _end(elsewhere)
    finally:
        _end(first)
    third, answer = _child(tmp_path, 4, "/pfx", "Delta", "1")
    try:
        assert answer == "ok"
    finally:
        _end(third)


# --- Bug 3: shortcut sync must not overwrite a newer Steam edit ---


def _steam_fixture(tmp_path, monkeypatch):
    from core import database, steam_env

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "library.db"))
    database.init_db()
    game_file = tmp_path / "game"
    game_file.write_text("")
    _ = database.add_game("Example", str(game_file))
    database.update_setting("steam_games_mode", "all-installed")
    root = tmp_path / "Steam"
    config = root / "userdata/123/config"
    config.mkdir(parents=True)
    path = config / "shortcuts.vdf"
    path.write_bytes(
        write_vdf({"shortcuts": {"0": {"appid": 7, "AppName": "Old name"}}})
    )
    old = time.time() - 60
    os.utime(path, (old, old))
    return root, path, steam_env


def _steam_saves_while_wlib_works(steam_env, monkeypatch, path):
    """Steam is closed at every check; it saves its newer file during the second one."""
    calls = []

    def steam_running(_root):
        calls.append(1)
        if len(calls) == 2:
            newer = write_vdf(
                {
                    "shortcuts": {
                        "0": {"appid": 7, "AppName": "Steam edited this"},
                        "1": {"appid": 8, "AppName": "Added in Steam"},
                    }
                }
            )
            path.write_bytes(newer)
            old = time.time() - 60
            os.utime(path, (old, old))
        return False

    monkeypatch.setattr(steam_env, "steam_running", steam_running)


def test_sync_does_not_revert_a_steam_save_made_during_sync(tmp_path, monkeypatch):
    from core.steam_shortcuts import sync_games

    root, path, steam_env = _steam_fixture(tmp_path, monkeypatch)
    _steam_saves_while_wlib_works(steam_env, monkeypatch, path)
    result = sync_games(root, "123", tmp_path / "wlib-steam")
    assert result["success"], result
    shortcuts = read_vdf(path.read_bytes())["shortcuts"]
    assert isinstance(shortcuts, dict)
    assert shortcuts["0"]["AppName"] == "Steam edited this"  # type: ignore[index]
    assert shortcuts["1"]["AppName"] == "Added in Steam"  # type: ignore[index]
    assert result["added"] == 1
    assert any(
        isinstance(v, dict) and v.get("LaunchOptions") == "1"
        for v in shortcuts.values()
    )
    # The version Steam saved is what the backup of wLib's own write holds.
    backup = read_vdf((path.parent / "shortcuts.vdf.wlib-bak").read_bytes())[
        "shortcuts"
    ]
    assert backup["0"]["AppName"] == "Steam edited this"  # type: ignore[index]


def test_sync_gives_up_when_steam_keeps_changing_the_file(tmp_path, monkeypatch):
    from core.steam_shortcuts import sync_games

    root, path, steam_env = _steam_fixture(tmp_path, monkeypatch)
    count = [0]

    def steam_running(_root):
        count[0] += 1
        if count[0] % 2 == 0:
            path.write_bytes(
                write_vdf({"shortcuts": {"0": {"AppName": str(count[0])}}})
            )
            old = time.time() - 60
            os.utime(path, (old, old))
        return False

    monkeypatch.setattr(steam_env, "steam_running", steam_running)
    result = sync_games(root, "123", tmp_path / "wlib-steam")
    assert result["success"] is False
    assert "retry" in str(result["error"])
    shortcuts = read_vdf(path.read_bytes())["shortcuts"]
    assert isinstance(shortcuts, dict)
    assert not any(
        isinstance(v, dict) and v.get("LaunchOptions") == "1"
        for v in shortcuts.values()
    )


def test_add_wlib_does_not_revert_a_steam_save_made_during_the_check(
    tmp_path, monkeypatch
):
    from core.steam_shortcuts import add_wlib

    root, path, steam_env = _steam_fixture(tmp_path, monkeypatch)
    _steam_saves_while_wlib_works(steam_env, monkeypatch, path)
    result = add_wlib(root, "123", tmp_path / "script", tmp_path / "no-icon")
    assert result["success"], result
    shortcuts = read_vdf(path.read_bytes())["shortcuts"]
    assert isinstance(shortcuts, dict)
    assert shortcuts["0"]["AppName"] == "Steam edited this"  # type: ignore[index]
    assert shortcuts["1"]["AppName"] == "Added in Steam"  # type: ignore[index]
    assert any(
        isinstance(v, dict) and v.get("AppName") == "wLib" for v in shortcuts.values()
    )


def test_atomic_write_checks_the_version_it_replaces(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    atomic_write(path, b"first", None)  # creating a file that did not exist
    assert path.read_bytes() == b"first"
    assert not (tmp_path / "shortcuts.vdf.wlib-bak").exists()
    atomic_write(path, b"second", b"first")
    assert (tmp_path / "shortcuts.vdf.wlib-bak").read_bytes() == b"first"
    with pytest.raises(ShortcutsChanged):
        atomic_write(path, b"third", b"first")
    assert path.read_bytes() == b"second"
    assert (tmp_path / "shortcuts.vdf.wlib-bak").read_bytes() == b"first"
    path.unlink()
    with pytest.raises(ShortcutsChanged):
        atomic_write(path, b"fourth", b"second")
    assert not path.exists()
    assert not list(tmp_path.glob(".shortcuts.vdf.*"))


# --- Bug 4: umu runtime status follows the effective runner ---


def _umu_proton_build(tmp_path, monkeypatch, appid="1628350"):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    build = tmp_path / "UMU-Proton-9.0-4"
    build.mkdir()
    (build / "toolmanifest.vdf").write_text(f'"require_tool_appid" "{appid}"')
    (build / "proton").write_text("")
    monkeypatch.setattr(umu, "resolve_codename", lambda _runner: str(build))
    return build


@pytest.mark.parametrize("runner", ["wine", "/usr/bin/wine", "", "UMU-Proton"])
def test_wine_default_reports_the_umu_proton_runtime(tmp_path, monkeypatch, runner):
    _ = _umu_proton_build(tmp_path, monkeypatch)
    # Preparing (and launching) with a Wine runner leaves PROTONPATH unset.
    assert umu.proton_path_value(runner) == ""
    status = umu.runtime_status(runner)
    assert status["runtime"] == "steamrt3"
    assert status["path"] == str(tmp_path / "umu" / "steamrt3")
    assert status == umu.runtime_status("UMU-Proton")


def test_explicit_proton_runner_keeps_its_own_runtime(tmp_path, monkeypatch):
    _ = _umu_proton_build(tmp_path, monkeypatch)
    custom = tmp_path / "Proton-Custom"
    custom.mkdir()
    (custom / "proton").write_text("")
    (custom / "toolmanifest.vdf").write_text('"require_tool_appid" "4183110"')
    assert umu.runtime_status(str(custom / "proton"))["runtime"] == "steamrt4"
    assert umu.runtime_status(str(custom))["runtime"] == "steamrt4"
    assert umu.runtime_status("wine")["runtime"] == "steamrt3"


def test_wine_default_preparation_watches_the_runtime_it_installs(
    tmp_path, monkeypatch
):
    _ = _umu_proton_build(tmp_path, monkeypatch)
    watched = []
    environments = []

    class Process:
        returncode = 0

        def poll(self):
            return 0

    def popen(_command, env, **_kwargs):
        environments.append(env)
        return Process()

    monkeypatch.setattr(umu.subprocess, "Popen", popen)
    monkeypatch.setattr(umu, "host_command", lambda command, **_kwargs: command)
    monkeypatch.setattr(umu, "_folder_size", lambda path: watched.append(path) or 0)
    monkeypatch.setattr(umu, "_runtime_stamp", lambda path: watched.append(path))
    prepare = umu.UmuRuntimePrepare()
    prepare._run("/usr/bin/umu-run", str(tmp_path / "prefix"), "wine")
    assert "PROTONPATH" not in environments[0]
    assert set(watched) == {str(tmp_path / "umu" / "steamrt3")}
    assert prepare.status()["error"] == ""
