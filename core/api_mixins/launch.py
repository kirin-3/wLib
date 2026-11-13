# SPDX-License-Identifier: GPL-3.0-or-later
"""Launch, stop, Steam, sandbox, launch targets and companion tools."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, cast

from core import database
from core.host_platform import (
    host_command,
    host_which,
    is_linux,
    is_windows,
    unsupported_on_host,
)
from core.bridge_exposure import hide_from_js, python_only
from core.api_mixins.base import ApiBase

if TYPE_CHECKING:
    from core.steam_sync import SteamSync


class LaunchMixin(ApiBase):
    def open_steam_keyboard(self) -> dict[str, object]:
        from core import steam_env

        if not is_linux() or not steam_env.gaming_mode():
            return {"success": False, "code": "unsupported"}
        try:
            _ = subprocess.Popen(
                host_command(["steam", "steam://open/keyboard"]),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True}

    def get_sandbox_status(self) -> dict[str, object]:
        from core.game_sandbox import get_sandbox_status

        return dict(get_sandbox_status())

    def recheck_sandbox_status(self) -> dict[str, object]:
        from core.game_sandbox import get_sandbox_status

        return dict(get_sandbox_status(recheck=True))

    def get_sandbox_install_info(self) -> dict[str, object]:
        from core.game_sandbox import get_sandbox_install_info

        return get_sandbox_install_info()

    def get_sandbox_install_status(self) -> dict[str, object]:
        with self._status_lock:
            return dict(self._sandbox_install_status)

    def install_sandbox_tool(self, tool: str = "") -> dict[str, object]:
        from core import game_sandbox

        expected = "bwrap" if is_linux() else "sandboxie" if is_windows() else ""
        if not expected or (tool and tool != expected):
            return {
                "success": False,
                "code": "unsupported_platform",
                "error": "This sandbox installer is unavailable on this host.",
            }
        with self._status_lock:
            if self._sandbox_install_status["running"]:
                return {
                    "success": False,
                    "error": "Sandbox installation is already running.",
                }
            self._sandbox_install_status: dict[str, object] = {
                "running": True,
                "done": 0,
                "total": 1,
                "current": "Installing sandbox tool",
                "error": "",
            }

        def install() -> None:
            error = ""
            try:
                game_sandbox.install_sandbox_tool(expected)
            except Exception as exc:
                error = str(exc)
            try:
                status = game_sandbox.get_sandbox_status(recheck=True)
                if not error and status["status"] != "ready":
                    error = status["reason"]
            except Exception as exc:
                error = error or str(exc)
            with self._status_lock:
                self._sandbox_install_status = {
                    "running": False,
                    "done": 0 if error else 1,
                    "total": 1,
                    "current": "",
                    "error": error,
                }

        threading.Thread(target=install, daemon=True).start()
        return {"success": True}

    def set_sandbox_for_all_games(self, enabled: object) -> dict[str, object]:
        from core.database import set_sandbox_for_all_games

        if not isinstance(enabled, bool):
            return {"success": False, "error": "Sandbox preference must be a boolean."}
        return {"success": True, "changed": set_sandbox_for_all_games(enabled)}

    def open_game_sandbox_folder(self, game_id: int) -> dict[str, object]:
        from core.database import get_game
        from core.game_sandbox import sandbox_folder

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or get_game(coerced_id) is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        folder = sandbox_folder(coerced_id)
        try:
            os.makedirs(folder, exist_ok=True)
            return dict(self._open_path_with_system_handler(folder))
        except OSError as exc:
            return {"success": False, "error": str(exc)}

    def list_companion_tools(self) -> dict[str, object]:
        return {"success": True, "tools": database.list_companion_tools()}

    def save_companion_tool(self, tool: object = None) -> dict[str, object]:
        import sqlite3

        if not isinstance(tool, Mapping):
            return {"success": False, "error": "Tool definition must be an object"}
        fields = dict(cast(Mapping[str, object], tool))
        if "id" in fields:
            fields["id"] = self._coerce_db_id(fields["id"])
            if fields["id"] is None:
                return {"success": False, "error": "Tool ID is invalid"}
        try:
            return {"success": True, "tool": database.save_companion_tool(fields)}
        except (ValueError, TypeError, sqlite3.Error) as exc:
            return {"success": False, "error": f"{fields.get('name', 'Tool')}: {exc}"}

    def delete_companion_tool(self, tool_id: object = None) -> dict[str, object]:
        tool_id = self._coerce_db_id(tool_id)
        if tool_id is None or not database.delete_companion_tool(tool_id):
            return {"success": False, "error": "Tool was not found"}
        return {"success": True}

    def get_game_companion_tools(self, game_id: object = None) -> dict[str, object]:
        game_id = self._coerce_db_id(game_id)
        if game_id is None or database.get_game(game_id) is None:
            return {"success": False, "error": "Game was not found"}
        return {"success": True, "tools": database.get_game_companion_tools(game_id)}

    def set_game_companion_tool_override(
        self, game_id: object = None, tool_id: object = None, visible: object = None
    ) -> dict[str, object]:
        game_id = self._coerce_db_id(game_id)
        tool_id = self._coerce_db_id(tool_id)
        tool = database.get_companion_tool(tool_id) if tool_id is not None else None
        name = tool["name"] if tool else "Tool"
        if game_id is None or database.get_game(game_id) is None:
            return {"success": False, "error": f"{name}: game was not found"}
        if tool is None or tool_id is None:
            return {"success": False, "error": "Tool was not found"}
        if visible is not None and not isinstance(visible, bool):
            return {
                "success": False,
                "error": f"{name}: visible must be true, false or null",
            }
        database.set_game_companion_tool_override(game_id, tool_id, visible)
        return {"success": True}

    def list_launchable_companion_tools(
        self, game_id: object = None
    ) -> dict[str, object]:
        if not is_linux():
            return unsupported_on_host("Custom tools")
        game_id = self._coerce_db_id(game_id)
        if game_id is None or database.get_game(game_id) is None:
            return {"success": False, "error": "Game was not found"}
        tools: list[database.CompanionTool] = [
            tool
            for tool in database.get_game_companion_tools(game_id)
            if tool["visible"]
        ]
        return {
            "success": True,
            "tools": self.launcher.list_launchable_tools(game_id, tools),
        }

    def launch_companion_tool(
        self, game_id: object = None, tool_id: object = None
    ) -> dict[str, object]:
        game_id = self._coerce_db_id(game_id)
        tool_id = self._coerce_db_id(tool_id)
        tool = database.get_companion_tool(tool_id) if tool_id is not None else None
        name = tool["name"] if tool else f"Tool {tool_id}"
        if not is_linux():
            return unsupported_on_host(name)
        if tool is None:
            return {"success": False, "error": f"{name}: tool was not found"}
        if game_id is None or database.get_game(game_id) is None:
            return {"success": False, "error": f"{name}: game was not found"}
        if not any(
            entry["id"] == tool_id and entry["visible"]
            for entry in database.get_game_companion_tools(game_id)
        ):
            return {
                "success": False,
                "error": f"{name}: tool is not shown for this game",
            }
        return self.launcher.launch_companion_tool(game_id, tool)

    def get_launch_targets(self, game_id: object = None) -> list[dict[str, object]]:
        import sqlite3

        from core.database import list_game_launch_targets

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return []

        list_targets_fn = cast(
            Callable[[int], list[dict[str, object]]], list_game_launch_targets
        )

        try:
            return list_targets_fn(coerced_id)
        except (sqlite3.Error, TypeError, OverflowError) as exc:
            print(f"Failed to list launch targets for game {coerced_id}: {exc}")
            return []

    def create_launch_target(
        self,
        game_id: object = None,
        label: object = "",
        exe_path: object = "",
        sort_order: object | None = None,
    ) -> dict[str, object]:
        import sqlite3

        from core.database import add_game_launch_target

        coerced_game_id = self._coerce_db_id(game_id)
        if coerced_game_id is None:
            return {
                "success": False,
                "error": "Parent game was not found",
                "error_code": "game_not_found",
            }

        add_target_fn = cast(
            Callable[[int, object, object, object | None], dict[str, object]],
            add_game_launch_target,
        )

        try:
            target = add_target_fn(coerced_game_id, label, exe_path, sort_order)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_code": "invalid_target"}
        except sqlite3.IntegrityError:
            return {
                "success": False,
                "error": "Parent game was not found",
                "error_code": "game_not_found",
            }
        except (sqlite3.Error, TypeError, OverflowError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not create launch target",
                "error_code": "invalid_target",
            }

        self._schedule_steam_sync()
        return {"success": True, "target": target}

    def update_launch_target(
        self, target_id: object = None, fields: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import update_game_launch_target

        coerced_id = self._coerce_db_id(target_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Launch target was not found",
                "error_code": "target_not_found",
            }
        if fields is not None and not isinstance(fields, Mapping):
            return {
                "success": False,
                "error": "Fields must be an object",
                "error_code": "invalid_target",
            }

        update_target_fn = cast(
            Callable[[int, Mapping[str, object]], dict[str, object] | None],
            update_game_launch_target,
        )

        try:
            target = update_target_fn(
                coerced_id, dict(cast(Mapping[str, object], fields or {}))
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_code": "invalid_target"}
        except (sqlite3.Error, TypeError, OverflowError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not update launch target",
                "error_code": "invalid_target",
            }

        if target is None:
            return {
                "success": False,
                "error": "Launch target was not found",
                "error_code": "target_not_found",
            }

        self._schedule_steam_sync()
        return {"success": True, "target": target}

    def delete_launch_target(self, target_id: object = None) -> dict[str, object]:
        import sqlite3

        from core.database import delete_game_launch_target

        coerced_id = self._coerce_db_id(target_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Launch target was not found",
                "error_code": "target_not_found",
            }

        delete_target_fn = cast(Callable[[int], bool], delete_game_launch_target)

        try:
            deleted = delete_target_fn(coerced_id)
        except (sqlite3.Error, TypeError, OverflowError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not delete launch target",
                "error_code": "target_not_found",
            }
        if not deleted:
            return {
                "success": False,
                "error": "Launch target was not found",
                "error_code": "target_not_found",
            }

        self._schedule_steam_sync()
        return {"success": True}

    def reorder_launch_targets(
        self, game_id: object = None, target_ids: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import reorder_game_launch_targets

        coerced_game_id = self._coerce_db_id(game_id)
        if coerced_game_id is None:
            return {
                "success": False,
                "error": "Parent game was not found",
                "error_code": "game_not_found",
            }

        reorder_targets_fn = cast(
            Callable[[int, Sequence[int]], list[dict[str, object]]],
            reorder_game_launch_targets,
        )

        if isinstance(target_ids, str) or not isinstance(target_ids, Sequence):
            return {
                "success": False,
                "error": "Launch target order must be a list of target IDs",
                "error_code": "invalid_target_order",
            }

        try:
            normalized_ids = [int(str(target_id)) for target_id in target_ids]
            targets = reorder_targets_fn(coerced_game_id, normalized_ids)
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "error": str(exc),
                "error_code": "invalid_target_order",
            }
        except (sqlite3.Error, OverflowError) as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not reorder launch targets",
                "error_code": "invalid_target_order",
            }

        self._schedule_steam_sync()
        return {"success": True, "targets": targets}

    def get_performance_tools(self, refresh: bool = False) -> dict[str, bool]:
        if self._performance_tools is None or refresh:
            self._performance_tools: dict[str, bool] | None = {name: bool(not is_windows() and host_which(binary)) for name, binary in (("gamemode", "gamemoderun"), ("mangohud", "mangohud"), ("gamescope", "gamescope"))}
        return dict(self._performance_tools)

    def get_gamescope_status(self, refresh: bool = False) -> dict[str, object]:
        """Whether gamescope is on the host, and the install command when it is not."""
        installed = self.get_performance_tools(refresh).get("gamescope", False)
        if installed or is_windows():
            return {"installed": installed, "command": "", "package_manager": "", "nixos": False}
        from core import gamescope
        from core.host_platform import host_system_layout

        nixos, _links = host_system_layout()
        hint = gamescope.install_hint(host_which, nixos)
        return {"installed": False, **hint}

    def launch_game(
        self,
        game_id: int | None = None,
        exe_path: str = "",
        command_line_args: str = "",
        run_japanese_locale: bool = False,
        run_wayland: bool = False,
        auto_inject_ce: bool = False,
        custom_prefix: str = "",
        proton_version: str = "",
        launch_mode: str = "auto",
    ) -> Mapping[str, object]:
        """
        Uses the Launcher class to execute the game via Proton/Wine.
        """
        from core.game_launch import launch_tracked
        if game_id is not None and is_linux():
            from core.steam_runner import runner_pid

            if runner_pid(game_id) is not None:
                return {"success": False, "code": "already_running", "error": "Game is already running"}
        if game_id is not None:
            with self._status_lock:
                busy = self._game_busy_reason(game_id)
            if busy:
                return {"success": False, "code": "game_busy", "error": busy}

        def on_tick(detail: dict[str, object]) -> None:
            # evaluate_js can block forever once the window is gone at exit.
            if self.window and not self.launcher.shutting_down:
                self.window.evaluate_js(
                    f"window.dispatchEvent(new CustomEvent('wlib-playtime-tick', {{ detail: {json.dumps(detail)} }}))"
                )

        return launch_tracked(self.launcher, game_id, {
            "exe_path": exe_path,
            "command_line_args": command_line_args,
            "run_japanese_locale": run_japanese_locale,
            "run_wayland": run_wayland,
            "auto_inject_ce": auto_inject_ce,
            "custom_prefix": custom_prefix,
            "proton_version": proton_version,
            "launch_mode": launch_mode,
        }, on_tick)

    def _start_steam_sync(self, icon: str) -> None:
        if not is_linux():
            return
        from pathlib import Path
        from core.steam_sync import SteamSync

        self.steam_sync: SteamSync | None = hide_from_js(
            SteamSync(Path(icon), on_conversions=self._notify_steam_covers)
        )
        self.steam_sync.schedule()

    def _notify_steam_covers(self) -> None:
        if self.window and not self.launcher.shutting_down:
            self.window.evaluate_js("window.dispatchEvent(new Event('wlib-steam-covers'))")

    def get_steam_cover_conversions(self) -> dict[str, object]:
        """Covers Qt cannot decode, for the window to convert to PNG."""
        if not is_linux():
            return {"success": False, "code": "unsupported", "error": "Steam integration is Linux only"}
        if self.steam_sync is None:
            return {"success": True, "covers": []}
        import base64

        from core.game_covers import COVER_TYPES, MAX_COVER_SIZE

        covers: list[dict[str, str]] = []
        for token, source in self.steam_sync.conversions().items():
            try:
                data = source.read_bytes()
                kind = COVER_TYPES[source.suffix[1:]]
                if len(data) > MAX_COVER_SIZE:
                    raise ValueError("Cover is too large")
            except (OSError, KeyError, ValueError):
                _ = self.steam_sync.finish_conversion(token, None)
                continue
            covers.append({"token": token, "type": kind, "data": base64.b64encode(data).decode("ascii")})
        return {"success": True, "covers": covers}

    def save_steam_cover_png(self, token: object = None, data: object = None) -> dict[str, object]:
        """Store a converted cover; empty data marks the conversion as failed."""
        if not is_linux():
            return {"success": False, "code": "unsupported", "error": "Steam integration is Linux only"}
        if self.steam_sync is None or not isinstance(token, str) or not isinstance(data, str):
            return {"success": False, "error": "Invalid cover"}
        import base64
        import binascii

        png: bytes | None = None
        if data:
            try:
                png = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                png = None
            if png is None or not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > 32 * 1024 * 1024:
                _ = self.steam_sync.finish_conversion(token, None)
                return {"success": False, "error": "Invalid PNG"}
        try:
            if not self.steam_sync.finish_conversion(token, png):
                return {"success": False, "error": "Cover is not waiting for conversion"}
        except OSError as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True}

    def get_steam_status(self) -> dict[str, object]:
        if not is_linux():
            return {"success": False, "code": "unsupported", "error": "Steam integration is Linux only"}
        from core.steam_sync import environment

        result = environment()
        result["success"] = True
        result["sync"] = self.steam_sync.status() if self.steam_sync else {"running": False}
        return result

    def add_wlib_to_steam(self) -> dict[str, object]:
        if not is_linux():
            return {"success": False, "code": "unsupported", "error": "Steam integration is Linux only"}
        if self.steam_sync is None:
            return {"success": False, "error": "Steam integration is not ready"}
        self.steam_sync.queue_add_wlib()
        return {"success": True, "queued": True}

    def set_steam_games_mode(self, mode: object = None) -> dict[str, object]:
        if not is_linux():
            return {"success": False, "code": "unsupported", "error": "Steam integration is Linux only"}
        if mode not in ("off", "all-installed", "marked"):
            return {"success": False, "error": "Invalid Steam games mode"}
        database.update_setting("steam_games_mode", str(mode))
        self._schedule_steam_sync()
        return {"success": True}

    def set_steam_account(self, account: object = None) -> dict[str, object]:
        if not is_linux():
            return {"success": False, "code": "unsupported", "error": "Steam integration is Linux only"}
        from core.steam_sync import environment

        if not isinstance(account, str) or account not in cast(list[str], environment()["accounts"]):
            return {"success": False, "error": "Steam account not found"}
        database.update_setting("steam_account_id", account)
        self._schedule_steam_sync()
        return {"success": True}

    def sync_steam_shortcuts(self) -> dict[str, object]:
        if not is_linux():
            return {"success": False, "code": "unsupported", "error": "Steam integration is Linux only"}
        if self.steam_sync is None:
            return {"success": False, "error": "Steam integration is not ready"}
        self.steam_sync.schedule(0, retry_removed=True)
        return {"success": True}

    def set_game_steam_shortcut(self, game_id: object = None, enabled: object = None) -> dict[str, object]:
        if not is_linux():
            return {"success": False, "code": "unsupported", "error": "Steam integration is Linux only"}
        from core.steam_sync import environment

        game_id = self._coerce_db_id(game_id)
        if game_id is None or database.get_game(game_id) is None or not isinstance(enabled, bool):
            return {"success": False, "error": "Invalid game or mark"}
        _ = database.update_game(game_id, {"steam_shortcut": enabled})
        account = str(environment()["account_id"])
        if enabled and account:
            links = database.list_steam_shortcuts(account)
            if any(link["game_id"] == game_id and link["state"] == "user_removed" for link in links):
                database.delete_steam_shortcut(account, game_id)
        self._schedule_steam_sync()
        return {"success": True}

    def get_game_steam_state(self, game_id: object = None) -> dict[str, object]:
        if not is_linux():
            return {"success": False, "code": "unsupported", "error": "Steam integration is Linux only"}
        from core.steam_sync import environment

        game_id = self._coerce_db_id(game_id)
        if game_id is None or (game := database.get_game(game_id)) is None:
            return {"success": False, "error": "Game was not found"}
        env = environment()
        from core.steam_shortcuts import desired_games
        from pathlib import Path

        eligible = game_id in desired_games([game], "all-installed", Path("/wlib-steam"))
        exe = os.path.expanduser(str(game.get("exe_path") or "").strip())
        reason = ""
        if not eligible:
            reason = "wishlisted" if not exe else "missing_file" if not os.path.isfile(exe) else "browser_game"
        links = database.list_steam_shortcuts(str(env["account_id"]))
        state = next((str(link["state"]) for link in links if link["game_id"] == game_id), "not_in_steam")
        return {"success": True, "state": state, "marked": bool(game.get("steam_shortcut")), "mode": env["mode"], "eligible": eligible, "reason": reason}

    def get_startup_launch_error(self) -> dict[str, str]:
        return {"error": os.environ.pop("WLIB_LAUNCH_ERROR", "") if "--show-launch-error" in sys.argv else ""}

    def get_game_play_sessions(self, game_id: int) -> dict[str, object]:
        from core.database import get_play_sessions

        try:
            return {"success": True, "sessions": get_play_sessions(game_id)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def set_window_focused(self, focused: bool) -> dict[str, bool]:
        self._window_focused: bool = bool(focused)
        return {"success": True}

    def stop_game(self, game_id: int) -> dict[str, object]:
        if game_id in self.launcher.get_running_games():
            return self.launcher.stop(game_id)
        if is_linux():
            from core.steam_runner import stop_runner

            return stop_runner(game_id)
        return self.launcher.stop(game_id)

    @python_only
    def reserve_runner_launch(
        self,
        game_id: int,
        prefix: str,
        *,
        sandboxed: bool,
        umu: bool,
        blocked: bool,
        title: str,
    ) -> str | None:
        """Check a Steam-shortcut launch against this app's busy games, prefix
        tools and running games, and hold its prefix until it ends.

        Returns why the launch must be refused, or None."""
        from core.launcher import RunningGame

        with self._status_lock:
            reason = self._prefix_tool_conflict(prefix, game_id)
            if reason:
                return reason
            entry = RunningGame(
                None,
                None,
                prefix=prefix,
                sandboxed=sandboxed,
                umu=umu,
                network_blocked=blocked,
                title=title or f"Game {game_id}",
            )
            return self.launcher.reserve_external(game_id, entry)

    @python_only
    def release_runner_launch(self, game_id: int) -> None:
        self.launcher.release_external(game_id)
