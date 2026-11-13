# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared tracked launch and play-session bookkeeping."""
from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from typing import TypedDict

from core import gamescope
from core.database import normalize_launch_mode
from core.gamescope import GamescopeOptions
from core.launcher import Launcher
from core.web_launch import resolve_web_player as resolve_web_player

class _OptionalLaunchOptions(TypedDict, total=False):
    html_player: str
    gamescope: GamescopeOptions


def _normalize_compare_path(value: object) -> str:
    raw = str(value or "")
    if not raw.strip():
        return ""
    expanded = os.path.expanduser(raw)
    try:
        resolved = os.path.realpath(os.path.abspath(expanded))
    except (OSError, ValueError):
        resolved = os.path.abspath(expanded)
    return os.path.normcase(resolved)


def request_from_game(game: Mapping[str, object]) -> dict[str, object]:
    """Build a launch request from the persisted game settings."""
    path = str(game.get("exe_path") or "").strip()
    if not path:
        raise ValueError("Wishlisted games cannot be launched.")
    mode = normalize_launch_mode(game.get("launch_mode"))
    from core.database import html_builtin_player_enabled

    if resolve_web_player(path, mode, game.get("html_player"), html_builtin_player_enabled()) == "browser":
        raise ValueError("Browser games cannot be launched by the runner.")
    return {
        "exe_path": path,
        "require_tracked": True,
        "command_line_args": str(game.get("command_line_args") or ""),
        "run_japanese_locale": bool(game.get("run_japanese_locale")),
        "run_wayland": bool(game.get("run_wayland")),
        "auto_inject_ce": bool(game.get("auto_inject_ce")),
        "custom_prefix": str(game.get("custom_prefix") or ""),
        "proton_version": str(game.get("proton_version") or ""),
        "launch_mode": mode,
    }


def launch_tracked(
    launcher: Launcher,
    game_id: int | None,
    request: Mapping[str, object],
    on_tick: Callable[[dict[str, object]], None] | None = None,
) -> Mapping[str, object]:
    exe_path = str(request.get("exe_path") or "")
    command_line_args = str(request.get("command_line_args") or "")
    run_japanese_locale = bool(request.get("run_japanese_locale", False))
    run_wayland = bool(request.get("run_wayland", False))
    auto_inject_ce = bool(request.get("auto_inject_ce", False))
    custom_prefix = str(request.get("custom_prefix") or "")
    proton_version = str(request.get("proton_version") or "")
    launch_mode = str(request.get("launch_mode") or "auto")
    target_path = os.path.expanduser((exe_path or "").strip())
    if game_id is not None and not (target_path and os.path.exists(target_path)):
        # Missing/empty path: fall back to the default exe, then other targets.
        # Only the default launch (empty request or the canonical path
        # itself) may fall back. An explicit launch-target path that was
        # deleted must error instead of silently launching a different file.
        # When nothing else exists either, pass the request through so the
        # launcher reports the missing file (and mocked launchers in tests
        # still see the requested path).
        from core.database import get_all_games, list_game_launch_targets

        canonical_path = next(
            (
                str(g.get("exe_path") or "").strip()
                for g in get_all_games()
                if g.get("id") == game_id
            ),
            "",
        )
        candidates = [
            os.path.expanduser(canonical_path),
            *[
                os.path.expanduser(str(t.get("exe_path") or "").strip())
                for t in list_game_launch_targets(game_id)
            ],
        ]
        existing = [
            candidate
            for candidate in candidates
            if candidate and os.path.exists(candidate)
        ]
        is_default_request = not target_path or (
            canonical_path
            and _normalize_compare_path(target_path)
            == _normalize_compare_path(canonical_path)
        )
        if not is_default_request and existing:
            return {
                "success": False,
                "error": f"Executable not found at {target_path}",
            }
        target_path = next(
            iter(existing),
            target_path or os.path.expanduser(canonical_path),
        )

    print(
        f"Launching {target_path} (Args: {command_line_args}, JP Locale: {run_japanese_locale}, Wayland: {run_wayland}, Auto Inject CE: {auto_inject_ce}, Custom Prefix: {custom_prefix}, Proton Version: {proton_version}, Launch Mode: {launch_mode})"
    )

    from core.database import (
        get_all_games,
        record_play_tick,
        start_play_session,
        update_playtime,
    )

    empty_game: dict[str, object] = {}
    stored_game = (
        next(
            (game for game in get_all_games() if game.get("id") == game_id),
            empty_game,
        )
        if game_id is not None
        else empty_game
    )
    from core.database import get_setting, normalize_tool_override

    def tool_enabled(name: str) -> bool:
        override = normalize_tool_override(stored_game.get(name))
        return override == "on" or (override == "default" and get_setting(f"{name}_enabled") == "true")

    gamescope_options: GamescopeOptions | None = None
    if tool_enabled("gamescope"):
        global_gamescope = {
            key: value
            for key in gamescope.SETTINGS_DEFAULTS
            if (value := get_setting(key)) is not None
        }
        gamescope_options = gamescope.resolve_options(global_gamescope, stored_game)

    from core.database import html_builtin_player_enabled

    if request.get("require_tracked") and resolve_web_player(
        target_path, launch_mode, stored_game.get("html_player"), html_builtin_player_enabled()
    ) == "browser":
        return {"success": False, "error": "Browser games cannot be launched by the runner."}

    from core.host_platform import is_linux

    lock_fd: int | None = None
    if game_id is not None and game_id > 0 and is_linux():
        from core.game_run_lock import acquire_launch_lock

        lock_fd = acquire_launch_lock(game_id)
        if lock_fd is None:
            return {"success": False, "code": "already_running", "error": "Game is already running"}

    def release_lock() -> None:
        nonlocal lock_fd
        if lock_fd is not None:
            os.close(lock_fd)
            lock_fd = None

    launch_version = str(stored_game.get("version") or "")
    session_id: int | None = None
    session_failed = False
    session_lock = threading.Lock()
    pending_ticks: list[tuple[int, bool]] = []

    def save_tick(delta: int, is_final: bool) -> None:
        if game_id is None:
            return
        last_played_version: str | None = None
        if session_id is None:
            # No journal row could be created; still count the playtime.
            update_playtime(game_id, delta)
        else:
            last_played_version = record_play_tick(
                game_id, session_id, delta, is_final
            )
        if on_tick is not None:
            on_tick({
                "gameId": game_id,
                "delta": max(int(delta), 0),
                "isFinal": bool(is_final),
                "lastPlayedVersion": last_played_version,
                "sessionId": session_id,
            })

    def on_exit(delta: int, is_final: bool = True) -> None:
        if game_id is None:
            return
        try:
            with session_lock:
                if session_id is None and not session_failed:
                    pending_ticks.append((delta, is_final))
                else:
                    save_tick(delta, is_final)
        except Exception as e:
            print(f"Failed to update playtime for game {game_id}: {e}")
        finally:
            if is_final:
                release_lock()

    optional_options: _OptionalLaunchOptions = {}
    if stored_game.get("html_player"):
        optional_options["html_player"] = str(stored_game["html_player"])
    if gamescope_options is not None:
        optional_options["gamescope"] = gamescope_options

    try:
        result = launcher.launch(
            target_path,
            command_line_args,
            run_japanese_locale,
            run_wayland,
            auto_inject_ce,
            custom_prefix,
            proton_version,
            normalize_launch_mode(launch_mode),
            on_exit_callback=on_exit,
            game_id=game_id,
            sandbox_enabled=bool(stored_game.get("sandbox_enabled")),
            block_network=bool(stored_game.get("block_network")),
            game_title=str(stored_game.get("title") or ""),
            gamemode=tool_enabled("gamemode"),
            mangohud=tool_enabled("mangohud"),
            # Only passed when set, so launchers without the option keep working.
            **optional_options,
        )
    except Exception as exc:
        # Launcher setup can still fail outside its own error paths (for
        # example creating the default Wine prefix). Keep the bridge
        # contract: report a failure payload instead of rejecting the call.
        print(f"Failed to launch game {game_id}: {exc}")
        release_lock()
        return {"success": False, "error": str(exc) or "Could not launch the game."}
    if (
        result.get("success")
        and result.get("tracks_playtime", True)
        and game_id is not None
    ):
        with session_lock:
            try:
                session_id = start_play_session(game_id, launch_version)
            except Exception as exc:
                session_failed = True
                print(f"Failed to start play session for game {game_id}: {exc}")
            for delta, is_final in pending_ticks:
                try:
                    save_tick(delta, is_final)
                except Exception as exc:
                    print(f"Failed to update playtime for game {game_id}: {exc}")
            pending_ticks.clear()
    if not result.get("success") or not result.get("tracks_playtime", True):
        release_lock()
    return result

