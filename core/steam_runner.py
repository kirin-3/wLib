# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-process records for headless Steam launches."""
from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from typing import cast

from core.host_platform import get_data_dir


def _path(game_id: int, data_dir: str | None = None) -> Path:
    if game_id <= 0:
        raise ValueError('Invalid game ID')
    return Path(data_dir or get_data_dir()) / 'runs' / f'{game_id}.json'


def runner_pid(game_id: int, data_dir: str | None = None, proc: Path | None = None) -> int | None:
    path = _path(game_id, data_dir)
    try:
        record = cast(dict[str, object], json.loads(path.read_text(encoding='utf-8')))
        raw_pid = record['pid']
        if not isinstance(raw_pid, int):
            raise ValueError('Invalid pid')
        pid = raw_pid
        if pid <= 1:
            raise ValueError('Invalid pid')
        cmdline = ((proc or Path('/proc')) / str(pid) / 'cmdline').read_bytes().split(b'\0')
        if b'--launch' not in cmdline or cmdline.index(b'--launch') + 1 >= len(cmdline) or cmdline[cmdline.index(b'--launch') + 1] != str(game_id).encode():
            raise ValueError('Not the expected runner')
        return pid
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        # A newly-created file may still be getting its contents written.
        try:
            if time.time() - path.stat().st_mtime > 5:
                path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def running_games(data_dir: str | None = None, proc: Path | None = None) -> list[int]:
    directory = Path(data_dir or get_data_dir()) / 'runs'
    if not directory.is_dir():
        return []
    result: list[int] = []
    for path in directory.glob('*.json'):
        if path.stem.isdecimal() and int(path.stem) > 0 and runner_pid(int(path.stem), data_dir, proc):
            result.append(int(path.stem))
    return sorted(result)


def create_record(game_id: int, data_dir: str | None = None) -> bool:
    path = _path(game_id, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Clean up old records, but do not remove a fresh file another runner is writing.
    _ = runner_pid(game_id, data_dir)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'pid': os.getpid(), 'started_at': time.time(), 'version': 1}, stream)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True


def remove_record(game_id: int, data_dir: str | None = None) -> None:
    path = _path(game_id, data_dir)
    try:
        record = cast(dict[str, object], json.loads(path.read_text(encoding='utf-8')))
        if record.get('pid') == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError, AttributeError):
        pass


def stop_runner(game_id: int) -> dict[str, object]:
    pid = runner_pid(game_id)
    if pid is None:
        return {'success': False, 'code': 'not_running', 'error': 'Game is not running'}
    try:
        os.kill(pid, signal.SIGTERM)
        return {'success': True}
    except OSError as exc:
        return {'success': False, 'error': str(exc)}


def _window_request(path: str, payload: dict[str, object] | None = None) -> dict[str, object] | None:
    from http.client import HTTPResponse
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    body = json.dumps(payload).encode() if payload is not None else None
    request = Request('http://127.0.0.1:8183' + path, data=body,
                      headers={'X-wLib-Extension': '1', 'Content-Type': 'application/json'})
    try:
        with cast(HTTPResponse, urlopen(request, timeout=2)) as response:
            return cast(dict[str, object], json.loads(response.read()))
    except (OSError, URLError, ValueError):
        return None


def report_error(game_id: int, message: str) -> None:
    import subprocess

    print(f"[wLib] Launch error: {message}")
    response = _window_request('/api/runner-event', {'gameId': game_id, 'state': 'error', 'error': message})
    if response and response.get('success'):
        from core.desktop_notify import send_notification

        send_notification('Game launch failed', message)
        return
    from core.host_platform import is_flatpak
    from core.steam_script import ensure_launch_script

    try:
        env = dict(os.environ)
        _ = env.pop('LD_PRELOAD', None)
        env['WLIB_LAUNCH_ERROR'] = message
        # The script runs `flatpak run`, which is unavailable inside the sandbox.
        if is_flatpak():
            command = 'wlib'
        else:
            script = Path(get_data_dir()) / 'steam/wlib-steam'
            command = str(script if script.is_file() else ensure_launch_script())
        _ = subprocess.run([command, '--show-launch-error', str(game_id)], env=env, check=False)
    except OSError as exc:
        print(f'[wLib] Could not show launch error: {exc}')


def run(game_id: int) -> int:
    """Launch a stored game and remain alive through its final playtime tick."""
    import threading

    from core.database import get_game, init_db
    from core.game_launch import launch_tracked, request_from_game
    from core.launcher import Launcher

    for name in ('LD_PRELOAD', 'SteamAppId', 'SteamGameId'):
        host_value = os.environ.pop('WLIB_HOST_' + name, None)
        if host_value:
            os.environ[name] = host_value
    try:
        init_db()
        game = get_game(game_id)
    except Exception as exc:
        report_error(game_id, f"Could not open the library: {exc}")
        return 1
    if game is None:
        report_error(game_id, f'Game {game_id} was not found.')
        return 2
    window = _window_request('/api/running')
    if window and game_id in cast(list[object], window.get('games', [])):
        report_error(game_id, f'{game.get("title") or "Game"} is already running.')
        return 3
    if not create_record(game_id):
        report_error(game_id, f'{game.get("title") or "Game"} is already running.')
        return 3
    launcher = Launcher()
    done = threading.Event()
    stop_requested = threading.Event()
    previous = [(sig, signal.getsignal(sig)) for sig in (signal.SIGTERM, signal.SIGINT)]
    try:
        def stop(_signum: int, _frame: object) -> None:
            stop_requested.set()
            _ = launcher.stop(game_id)

        for sig, _handler in previous:
            _ = signal.signal(sig, stop)
        try:
            request = request_from_game(game)
        except ValueError as exc:
            remove_record(game_id)
            report_error(game_id, f"{game.get('title') or 'Game'}: {exc}")
            return 1
        _ = _window_request('/api/runner-event', {'gameId': game_id, 'state': 'started'})
        result = launch_tracked(launcher, game_id, request,
                                lambda tick: done.set() if tick.get('isFinal') else None)
        if not result.get('success') or result.get('tracks_playtime') is False:
            remove_record(game_id)
            report_error(game_id, f"{game.get('title') or 'Game'}: {result.get('error', 'Cannot track this game')}")
            return 1
        if stop_requested.is_set() and not done.is_set():
            _ = launcher.stop(game_id)
        stop_deadline: float | None = None
        while not done.wait(0.5):
            if stop_requested.is_set():
                if stop_deadline is None:
                    stop_deadline = time.monotonic() + 15
                elif time.monotonic() >= stop_deadline:
                    launcher.shutdown()
                    if not done.is_set():
                        report_error(game_id, "Timed out stopping the game.")
                        return 1
        return 0
    finally:
        remove_record(game_id)
        _ = _window_request('/api/runner-event', {'gameId': game_id, 'state': 'ended'})
        for sig, handler in previous:
            _ = signal.signal(sig, handler)
