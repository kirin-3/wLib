# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-process records for headless Steam launches."""

from __future__ import annotations

import json
import os
import signal
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

from core.host_platform import get_data_dir

if TYPE_CHECKING:
    from core.launcher import RunningGame


RUNNER_HEADER = "X-wLib-Runner"


def runner_token(data_dir: str | None = None) -> str:
    """Shared secret for the runner endpoints of the local server.

    The file is private to the user and outside every sandbox, so a game
    that can reach 127.0.0.1 still cannot pose as the runner."""
    import secrets

    path = Path(data_dir or get_data_dir()) / "runner-token"
    try:
        token = path.read_text(encoding="ascii").strip()
        if len(token) >= 32:
            return token
    except (OSError, UnicodeDecodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    temporary = path.with_name(f".runner-token.{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        _ = stream.write(token)
    os.replace(temporary, path)
    return token


def _path(game_id: int, data_dir: str | None = None) -> Path:
    if game_id <= 0:
        raise ValueError("Invalid game ID")
    return Path(data_dir or get_data_dir()) / "runs" / f"{game_id}.json"


def runner_pid(
    game_id: int, data_dir: str | None = None, proc: Path | None = None
) -> int | None:
    path = _path(game_id, data_dir)
    try:
        record = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        raw_pid = record["pid"]
        if not isinstance(raw_pid, int):
            raise ValueError("Invalid pid")
        pid = raw_pid
        if pid <= 1:
            raise ValueError("Invalid pid")
        cmdline = (
            ((proc or Path("/proc")) / str(pid) / "cmdline").read_bytes().split(b"\0")
        )
        if (
            b"--launch" not in cmdline
            or cmdline.index(b"--launch") + 1 >= len(cmdline)
            or cmdline[cmdline.index(b"--launch") + 1] != str(game_id).encode()
        ):
            raise ValueError("Not the expected runner")
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
    directory = Path(data_dir or get_data_dir()) / "runs"
    if not directory.is_dir():
        return []
    result: list[int] = []
    for path in directory.glob("*.json"):
        if (
            path.stem.isdecimal()
            and int(path.stem) > 0
            and runner_pid(int(path.stem), data_dir, proc)
        ):
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
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {"pid": os.getpid(), "started_at": time.time(), "version": 1}, stream
            )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True


def _claim_lock(directory: Path):
    """Exclusive cross-process lock for prefix claims; closing the file releases it."""
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / ".claims.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _record_claim(record: dict[str, object], title: str = "") -> RunningGame | None:
    from core.launcher import RunningGame

    claim = record.get("claim")
    if not isinstance(claim, dict):
        return None
    values = cast(dict[str, object], claim)
    prefix = values.get("prefix")
    if not isinstance(prefix, str) or not prefix:
        return None
    return RunningGame(
        None,
        None,
        prefix=prefix,
        sandboxed=values.get("sandboxed") is True,
        umu=values.get("umu") is True,
        network_blocked=values.get("blocked") is True,
        title=str(values.get("title") or title or "Another game"),
    )


def running_claims(
    data_dir: str | None = None, proc: Path | None = None
) -> dict[int, RunningGame]:
    """Prefix claims of the live runners, by game ID.

    A runner claims its Wine prefix in its run record before the game starts and
    holds it until the record goes, so the open window and other runners see it."""
    directory = Path(data_dir or get_data_dir()) / "runs"
    claims: dict[int, RunningGame] = {}
    if not directory.is_dir():
        return claims
    for path in directory.glob("*.json"):
        if not path.stem.isdecimal() or int(path.stem) <= 0:
            continue
        game_id = int(path.stem)
        if runner_pid(game_id, data_dir, proc) is None:
            continue
        try:
            record = cast(dict[str, object], json.loads(path.read_text("utf-8")))
            entry = _record_claim(record, f"Game {game_id}")
        except (OSError, ValueError, AttributeError):
            continue
        if entry is not None:
            claims[game_id] = entry
    return claims


def claim_prefix(
    game_id: int,
    entry: RunningGame,
    data_dir: str | None = None,
    proc: Path | None = None,
) -> str | None:
    """Hold entry's Wine prefix in this runner's record, or say why it can't be held.

    The check and the claim happen under one cross-process lock, so two runners
    starting on the same prefix cannot both pass. Another runner's live claim
    follows the same sharing rules as a game in one launcher."""
    from core.launcher import prefix_sharing_refused

    if not entry.prefix:
        return None
    directory = Path(data_dir or get_data_dir()) / "runs"
    path = _path(game_id, data_dir)
    fd = _claim_lock(directory)
    try:
        for other_id, other in running_claims(data_dir, proc).items():
            if (
                other_id != game_id
                and other.prefix == entry.prefix
                and prefix_sharing_refused(entry, other)
            ):
                return (
                    f"{other.title} is already running on this Wine prefix. "
                    + "Use a separate custom prefix to run both games."
                )
        try:
            record = cast(dict[str, object], json.loads(path.read_text("utf-8")))
        except (OSError, ValueError, AttributeError):
            return "This launch lost its run record. Try again."
        if record.get("pid") != os.getpid():
            return "Another runner owns this game's record. Try again."
        record["claim"] = {
            "prefix": entry.prefix,
            "sandboxed": entry.sandboxed,
            "umu": entry.umu,
            "blocked": entry.network_blocked,
            "title": entry.title,
        }
        temporary = path.with_name(f".{path.stem}.{os.getpid()}.tmp")
        stream_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(stream_fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return None
    finally:
        os.close(fd)


def remove_record(game_id: int, data_dir: str | None = None) -> None:
    path = _path(game_id, data_dir)
    try:
        record = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        if record.get("pid") == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError, AttributeError):
        pass


def stop_runner(game_id: int) -> dict[str, object]:
    pid = runner_pid(game_id)
    if pid is None:
        return {"success": False, "code": "not_running", "error": "Game is not running"}
    try:
        os.kill(pid, signal.SIGTERM)
        return {"success": True}
    except OSError as exc:
        return {"success": False, "error": str(exc)}


def _window_request(
    path: str, payload: dict[str, object] | None = None
) -> dict[str, object] | None:
    from http.client import HTTPResponse
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    body = json.dumps(payload).encode() if payload is not None else None
    try:
        token = runner_token()
    except OSError:
        token = ""
    request = Request(
        "http://127.0.0.1:8183" + path,
        data=body,
        headers={
            "X-wLib-Extension": "1",
            RUNNER_HEADER: token,
            "Content-Type": "application/json",
        },
    )
    try:
        with cast(HTTPResponse, urlopen(request, timeout=2)) as response:
            return cast(dict[str, object], json.loads(response.read()))
    except (OSError, URLError, ValueError):
        return None


LAUNCH_CHECK_UNANSWERED = (
    "wLib is open but did not confirm that this game can start. Try again in a moment."
)


def launch_check(payload: dict[str, object]) -> str | None:
    """Ask an open wLib window to check and hold this launch.

    None when the window allows it, or when no wLib listens (connection
    refused: nothing to clash with). Any other failure, such as a timeout,
    an error status or a bad answer, refuses the launch: a busy window may
    be installing a runtime on the same Wine prefix."""
    from http.client import HTTPResponse
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    try:
        token = runner_token()
    except OSError:
        token = ""
    request = Request(
        "http://127.0.0.1:8183/api/launch-check",
        data=json.dumps(payload).encode(),
        headers={
            "X-wLib-Extension": "1",
            RUNNER_HEADER: token,
            "Content-Type": "application/json",
        },
    )
    try:
        # Longer than other runner requests: the window may wait on its
        # status lock while an install step finishes.
        with cast(HTTPResponse, urlopen(request, timeout=10)) as response:
            answer = cast(object, json.loads(response.read()))
    except HTTPError as exc:
        try:
            answer = cast(object, json.loads(exc.read()))
        except (OSError, ValueError):
            answer = None
        if isinstance(answer, dict) and cast(dict[str, object], answer).get("error"):
            return str(cast(dict[str, object], answer)["error"])
        return LAUNCH_CHECK_UNANSWERED
    except URLError as exc:
        return (
            None
            if isinstance(exc.reason, ConnectionRefusedError)
            else LAUNCH_CHECK_UNANSWERED
        )
    except ConnectionRefusedError:
        return None
    except (OSError, ValueError):
        return LAUNCH_CHECK_UNANSWERED
    if not isinstance(answer, dict):
        return LAUNCH_CHECK_UNANSWERED
    checked = cast(dict[str, object], answer)
    if checked.get("success") is True:
        return None
    return str(checked.get("error") or "This game cannot start right now.")


def report_error(game_id: int, message: str, launch_id: str = "") -> None:
    import subprocess

    print(f"[wLib] Launch error: {message}")
    response = _window_request(
        "/api/runner-event",
        {"gameId": game_id, "state": "error", "error": message, "launchId": launch_id},
    )
    if response and response.get("success"):
        from core.desktop_notify import send_notification

        send_notification("Game launch failed", message)
        return
    from core.host_platform import is_flatpak
    from core.steam_script import ensure_launch_script

    try:
        env = dict(os.environ)
        _ = env.pop("LD_PRELOAD", None)
        env["WLIB_LAUNCH_ERROR"] = message
        # The script runs `flatpak run`, which is unavailable inside the sandbox.
        if is_flatpak():
            command = "wlib"
        else:
            script = Path(get_data_dir()) / "steam/wlib-steam"
            command = str(script if script.is_file() else ensure_launch_script())
        _ = subprocess.run(
            [command, "--show-launch-error", str(game_id)], env=env, check=False
        )
    except OSError as exc:
        print(f"[wLib] Could not show launch error: {exc}")


def run(game_id: int) -> int:
    """Launch a stored game and remain alive through its final playtime tick."""
    import threading

    from core.database import get_game, init_db
    from core.game_launch import launch_tracked, request_from_game
    from core.launcher import Launcher

    # Names this invocation to the open window. It releases a game's prefix
    # reservation only for the invocation that made it, so a duplicate launch
    # that is refused cannot free the running game's prefix.
    launch_id = uuid.uuid4().hex
    for name in ("LD_PRELOAD", "SteamAppId", "SteamGameId"):
        host_value = os.environ.pop("WLIB_HOST_" + name, None)
        if host_value:
            os.environ[name] = host_value
    try:
        init_db()
        game = get_game(game_id)
    except Exception as exc:
        report_error(game_id, f"Could not open the library: {exc}", launch_id)
        return 1
    if game is None:
        report_error(game_id, f"Game {game_id} was not found.", launch_id)
        return 2
    window = _window_request("/api/running")
    if window and game_id in cast(list[object], window.get("games", [])):
        report_error(
            game_id, f"{game.get('title') or 'Game'} is already running.", launch_id
        )
        return 3
    if not create_record(game_id):
        report_error(
            game_id, f"{game.get('title') or 'Game'} is already running.", launch_id
        )
        return 3
    launcher = Launcher()
    # This process sees only its own game. The wLib window or another runner
    # may run a game on the same Wine prefix, so never end the whole prefix.
    launcher.prefix_owner_uncertain = True

    def ask_window(entry: object, checked_id: int | None) -> str | None:
        # The open wLib window knows about installs, mod tools, prefix tools
        # and the games it runs; without a window there is nothing to clash with.
        from core.launcher import RunningGame

        assert isinstance(entry, RunningGame)
        refused = launch_check(
            {
                "gameId": checked_id,
                "launchId": launch_id,
                "prefix": entry.prefix,
                "sandboxed": entry.sandboxed,
                "umu": entry.umu,
                "blocked": entry.network_blocked,
                "title": entry.title,
            }
        )
        if refused or checked_id is None:
            return refused
        # Other runners know nothing of this one, and a closed window cannot
        # tell them; the run records hold each runner's prefix claim.
        return claim_prefix(checked_id, entry)

    launcher.external_guard = ask_window
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
            report_error(game_id, f"{game.get('title') or 'Game'}: {exc}", launch_id)
            return 1
        _ = _window_request(
            "/api/runner-event",
            {"gameId": game_id, "state": "started", "launchId": launch_id},
        )
        result = launch_tracked(
            launcher,
            game_id,
            request,
            lambda tick: done.set() if tick.get("isFinal") else None,
        )
        if not result.get("success") or result.get("tracks_playtime") is False:
            remove_record(game_id)
            report_error(
                game_id,
                f"{game.get('title') or 'Game'}: {result.get('error', 'Cannot track this game')}",
                launch_id,
            )
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
                        report_error(game_id, "Timed out stopping the game.", launch_id)
                        return 1
        return 0
    finally:
        remove_record(game_id)
        _ = _window_request(
            "/api/runner-event",
            {"gameId": game_id, "state": "ended", "launchId": launch_id},
        )
        for sig, handler in previous:
            _ = signal.signal(sig, handler)
