import json
import os
import signal
import sys
import time
import pytest

from core import steam_runner


@pytest.fixture(autouse=True)
def isolated_library(tmp_path, monkeypatch):
    from core import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path))
    database.init_db()




def test_records_are_exclusive_and_stale_records_are_removed(tmp_path):
    assert steam_runner.create_record(7, str(tmp_path))
    assert not steam_runner.create_record(7, str(tmp_path))
    path = tmp_path / 'runs/7.json'
    os.utime(path, (time.time() - 10, time.time() - 10))
    assert steam_runner.runner_pid(7, str(tmp_path), tmp_path / 'proc') is None
    assert not path.exists()
    assert steam_runner.create_record(7, str(tmp_path))
    steam_runner.remove_record(7, str(tmp_path))
    assert not path.exists()


def test_running_record_validation_and_stop(tmp_path, monkeypatch):
    runs = tmp_path / 'runs'
    runs.mkdir()
    (runs / '3.json').write_text(json.dumps({'pid': 42}))
    proc = tmp_path / 'proc/42'
    proc.mkdir(parents=True)
    (proc / 'cmdline').write_bytes(b'python\0main.py\0--launch\x003\0')
    assert steam_runner.running_games(str(tmp_path), tmp_path / 'proc') == [3]
    monkeypatch.setattr(steam_runner, 'runner_pid', lambda _: 42)
    called = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: called.append((pid, sig)))
    assert steam_runner.stop_runner(3)['success']
    assert called == [(42, signal.SIGTERM)]


def test_window_merges_external_runs_and_refuses_second_launch(monkeypatch):
    from core.api import Api

    api = Api()
    monkeypatch.setattr('core.api.is_linux', lambda: True)
    monkeypatch.setattr(api.launcher, 'get_running_games', lambda: [1])
    monkeypatch.setattr(steam_runner, 'running_games', lambda: [2])
    monkeypatch.setattr(steam_runner, 'runner_pid', lambda _: 42)
    assert api.get_running_games() == [1, 2]
    assert api.launch_game(2, '/game')['code'] == 'already_running'


def test_runner_exit_codes_and_signal(monkeypatch):
    from core import database, game_launch, launcher

    monkeypatch.setattr(database, 'init_db', lambda: None)
    monkeypatch.setattr(steam_runner, '_window_request', lambda *_: None)
    monkeypatch.setattr(steam_runner, 'report_error', lambda *_: None)
    monkeypatch.setattr(database, 'get_game', lambda _: None)
    assert steam_runner.run(8) == 2
    game = {'id': 8, 'exe_path': '/game'}
    monkeypatch.setattr(database, 'get_game', lambda _: game)
    monkeypatch.setattr(steam_runner, 'create_record', lambda _: False)
    assert steam_runner.run(8) == 3
    monkeypatch.setattr(steam_runner, 'create_record', lambda _: True)
    monkeypatch.setattr(steam_runner, 'remove_record', lambda _: None)
    stops = []
    class FakeLauncher:
        def stop(self, game_id):
            stops.append(game_id)
            return {'success': True}
    monkeypatch.setattr(launcher, 'Launcher', FakeLauncher)
    monkeypatch.setenv('WLIB_HOST_SteamAppId', '42')
    def launch(_launcher, _game_id, _request, callback):
        assert os.environ['SteamAppId'] == '42'
        signal.raise_signal(signal.SIGTERM)
        callback({'isFinal': True})
        return {'success': True}
    monkeypatch.setattr(game_launch, 'launch_tracked', launch)
    assert steam_runner.run(8) == 0
    assert stops == [8]
    monkeypatch.setattr(game_launch, 'launch_tracked', lambda *_: {'success': False, 'error': 'no'})
    assert steam_runner.run(8) == 1


def test_error_reporting_window_and_child(monkeypatch, tmp_path):
    import subprocess
    from core import desktop_notify, steam_script

    notifications = []
    monkeypatch.setattr(desktop_notify, 'send_notification', lambda title, body: notifications.append((title, body)))
    monkeypatch.setattr(steam_runner, '_window_request', lambda *_: {'success': True})
    steam_runner.report_error(8, 'broken')
    assert notifications == [('Game launch failed', 'broken')]
    monkeypatch.setattr(steam_runner, '_window_request', lambda *_: None)
    monkeypatch.setattr(steam_script, 'ensure_launch_script', lambda: tmp_path / 'wlib-steam')
    called = []
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: called.append((args, kwargs)))
    monkeypatch.setenv('LD_PRELOAD', 'overlay')
    steam_runner.report_error(8, 'missing')
    args, kwargs = called[0]
    assert args[0][1:] == ['--show-launch-error', '8']
    assert 'LD_PRELOAD' not in kwargs['env']
    assert kwargs['env']['WLIB_LAUNCH_ERROR'] == 'missing'
    from core import host_platform
    monkeypatch.setattr(host_platform, 'is_flatpak', lambda: True)
    steam_runner.report_error(8, 'missing')
    assert called[1][0][0] == ['wlib', '--show-launch-error', '8']


@pytest.mark.skipif(sys.platform == 'win32', reason='Windows uppercases environment names')
def test_flatpak_host_command_forwards_steam_environment(monkeypatch):
    from core import host_platform
    monkeypatch.setattr(host_platform, 'is_flatpak', lambda: True)
    monkeypatch.setenv('LD_PRELOAD', 'overlay.so')
    monkeypatch.setenv('SteamAppId', '42')
    monkeypatch.setenv('SteamGameId', '99')
    command = host_platform.host_command(['game'], env=dict(os.environ))
    assert '--env=LD_PRELOAD=overlay.so' in command
    assert '--env=SteamAppId=42' in command
    assert '--env=SteamGameId=99' in command


@pytest.mark.skipif(sys.platform == 'win32', reason='flock is POSIX-only')
def test_launch_lock_is_exclusive_until_released():
    from core.game_run_lock import acquire_launch_lock
    first = acquire_launch_lock(1)
    assert first is not None
    try:
        assert acquire_launch_lock(1) is None
    finally:
        os.close(first)
    second = acquire_launch_lock(1)
    assert second is not None
    os.close(second)
