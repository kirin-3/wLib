# SPDX-License-Identifier: GPL-3.0-or-later
"""Gamescope options: validation, per-game resolution, argv and install hints.

Pure functions only (no database access), so the settings API, the database
normalizers and the launcher all use the same rules.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Mapping
from typing import TypedDict

from core import steam_env

WINDOW_MODES = ("fullscreen", "borderless", "windowed")
SCALERS = ("auto", "integer", "fit", "fill", "stretch")
FILTERS = ("linear", "nearest", "fsr", "nis", "pixel")
MAX_RESOLUTION_SIDE = 16384
MAX_FPS_LIMIT = 1000

DEFAULT_WINDOW_MODE = "fullscreen"
DEFAULT_SCALER = "auto"
DEFAULT_FILTER = "linear"

# Global settings keys with their defaults (stored as text).
SETTINGS_DEFAULTS: dict[str, str] = {
    "gamescope_enabled": "false",
    "gamescope_window_mode": DEFAULT_WINDOW_MODE,
    "gamescope_output_resolution": "",
    "gamescope_game_resolution": "",
    "gamescope_scaler": DEFAULT_SCALER,
    "gamescope_filter": DEFAULT_FILTER,
    "gamescope_fps_limit": "",
    "gamescope_args": "",
}

# Per-game override columns (besides the `gamescope` default|on|off column).
GAME_OVERRIDE_COLUMNS = (
    "gamescope_resolution",
    "gamescope_scaler",
    "gamescope_filter",
    "gamescope_args",
)

_RESOLUTION_RE = re.compile(r"^\s*(\d+)\s*[xX×]\s*(\d+)\s*$")


class GamescopeOptions(TypedDict):
    window_mode: str
    output_resolution: str
    game_resolution: str
    scaler: str
    filter: str
    fps_limit: str
    args: list[str]


class InstallHint(TypedDict):
    command: str
    package_manager: str
    nixos: bool


def parse_resolution(value: object) -> tuple[int, int] | None:
    """`WxH` with each side from 1 to 16384, else None."""
    match = _RESOLUTION_RE.match(str(value or ""))
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if not (1 <= width <= MAX_RESOLUTION_SIDE and 1 <= height <= MAX_RESOLUTION_SIDE):
        return None
    return width, height


def resolution_error(value: object) -> str:
    """'' when the value is empty (Auto) or a valid resolution."""
    if not str(value or "").strip():
        return ""
    if parse_resolution(value) is None:
        return (
            "Enter the resolution as width x height, each a whole number "
            f"from 1 to {MAX_RESOLUTION_SIDE}."
        )
    return ""


def normalize_resolution(value: object) -> str:
    """Canonical `WxH`, or '' (Auto) for an empty or invalid value."""
    parsed = parse_resolution(value)
    return f"{parsed[0]}x{parsed[1]}" if parsed else ""


def fps_limit_error(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.isdigit() or not (1 <= int(text) <= MAX_FPS_LIMIT):
        return f"Enter the FPS limit as a whole number from 1 to {MAX_FPS_LIMIT}."
    return ""


def normalize_fps_limit(value: object) -> str:
    text = str(value or "").strip()
    return str(int(text)) if text and not fps_limit_error(text) else ""


def normalize_choice(value: object, choices: tuple[str, ...], default: str = "") -> str:
    text = str(value or "").strip().lower()
    return text if text in choices else default


def normalize_window_mode(value: object) -> str:
    return normalize_choice(value, WINDOW_MODES, DEFAULT_WINDOW_MODE)


def normalize_scaler(value: object, default: str = "") -> str:
    return normalize_choice(value, SCALERS, default)


def normalize_filter(value: object, default: str = "") -> str:
    return normalize_choice(value, FILTERS, default)


def split_args(value: object) -> list[str]:
    """Split extra arguments as a POSIX shell does.

    Raises ValueError with a user-facing reason for unclosed quotes or a
    standalone `--` (wLib adds the separator itself).
    """
    text = str(value or "")
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:
        raise ValueError("The quoting is not closed.") from None
    if "--" in parts:
        raise ValueError("`--` is not allowed; wLib adds it before the game command.")
    return parts


def args_error(value: object) -> str:
    try:
        _ = split_args(value)
    except ValueError as exc:
        return str(exc)
    return ""


def normalize_args(value: object) -> str:
    text = str(value or "").strip()
    return text if not args_error(text) else ""


def settings_error(key: str, value: object) -> str:
    """Why a global gamescope setting value cannot be saved ('' when it can)."""
    if key in ("gamescope_output_resolution", "gamescope_game_resolution"):
        return resolution_error(value)
    if key == "gamescope_fps_limit":
        return fps_limit_error(value)
    if key == "gamescope_args":
        return args_error(value)
    if key == "gamescope_window_mode" and str(value or "").strip().lower() not in WINDOW_MODES:
        return "Choose Fullscreen, Borderless or Window."
    if key == "gamescope_scaler" and str(value or "").strip().lower() not in SCALERS:
        return "Choose a scaler from the list."
    if key == "gamescope_filter" and str(value or "").strip().lower() not in FILTERS:
        return "Choose a filter from the list."
    return ""


def normalize_setting(key: str, value: object) -> str:
    """Stored form of a global gamescope setting; invalid values get the default."""
    if key == "gamescope_enabled":
        return "true" if str(value).strip().lower() in ("1", "true", "on", "yes") else "false"
    if key == "gamescope_window_mode":
        return normalize_window_mode(value)
    if key in ("gamescope_output_resolution", "gamescope_game_resolution"):
        return normalize_resolution(value)
    if key == "gamescope_scaler":
        return normalize_scaler(value, DEFAULT_SCALER)
    if key == "gamescope_filter":
        return normalize_filter(value, DEFAULT_FILTER)
    if key == "gamescope_fps_limit":
        return normalize_fps_limit(value)
    if key == "gamescope_args":
        return normalize_args(value)
    return str(value or "")


def normalize_game_field(column: str, value: object) -> str:
    """Stored form of a per-game override; invalid values become '' (use global)."""
    if column == "gamescope_resolution":
        return normalize_resolution(value)
    if column == "gamescope_scaler":
        return normalize_scaler(value)
    if column == "gamescope_filter":
        return normalize_filter(value)
    if column == "gamescope_args":
        return normalize_args(value)
    return str(value or "")


def resolve_options(
    global_settings: Mapping[str, object], game: Mapping[str, object]
) -> GamescopeOptions:
    """Per-game overrides replace global values; per-game args follow global ones."""

    def setting(key: str) -> str:
        return normalize_setting(key, global_settings.get(key, SETTINGS_DEFAULTS[key]))

    def game_value(column: str) -> str:
        return normalize_game_field(column, game.get(column))

    global_args = split_args(setting("gamescope_args"))
    game_args = split_args(game_value("gamescope_args"))
    return {
        "window_mode": setting("gamescope_window_mode"),
        "output_resolution": setting("gamescope_output_resolution"),
        "game_resolution": game_value("gamescope_resolution")
        or setting("gamescope_game_resolution"),
        "scaler": game_value("gamescope_scaler") or setting("gamescope_scaler"),
        "filter": game_value("gamescope_filter") or setting("gamescope_filter"),
        "fps_limit": setting("gamescope_fps_limit"),
        "args": [*global_args, *game_args],
    }


def gamescope_argv(
    options: GamescopeOptions,
    screen_size: tuple[int, int] | None = None,
    binary: str = "gamescope",
) -> list[str]:
    """`gamescope <options>` without the trailing `--`."""
    argv = [binary]
    mode = normalize_window_mode(options.get("window_mode"))
    if mode == "fullscreen":
        argv.append("-f")
    elif mode == "borderless":
        argv.append("-b")
    output = parse_resolution(options.get("output_resolution"))
    game = parse_resolution(options.get("game_resolution"))
    if output is None and game is None and screen_size is not None:
        # Nested gamescope renders at 1280x720 by default; match the screen.
        output = game = screen_size
    elif game is None:
        game = output
    if output is not None:
        argv += ["-W", str(output[0]), "-H", str(output[1])]
    if game is not None:
        argv += ["-w", str(game[0]), "-h", str(game[1])]
    scaler = normalize_scaler(options.get("scaler"))
    if scaler:
        argv += ["-S", scaler]
    filter_name = normalize_filter(options.get("filter"))
    if filter_name:
        argv += ["-F", filter_name]
    fps = normalize_fps_limit(options.get("fps_limit"))
    if fps:
        argv += ["-r", fps]
    argv += [str(arg) for arg in options.get("args", [])]
    return argv


# rpm-ostree first: immutable Fedora also ships dnf.
INSTALL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("rpm-ostree", "rpm-ostree install gamescope"),
    ("pacman", "sudo pacman -S gamescope"),
    ("apt", "sudo apt install gamescope"),
    ("dnf", "sudo dnf install gamescope"),
    ("zypper", "sudo zypper install gamescope"),
    ("xbps-install", "sudo xbps-install gamescope"),
)
NIXOS_INSTALL = "programs.gamescope.enable = true;"


def install_hint(which: Callable[[str], str | None], nixos: bool) -> InstallHint:
    """The install command for the first package manager `which` finds."""
    if nixos:
        return {"command": NIXOS_INSTALL, "package_manager": "nixos", "nixos": True}
    for manager, command in INSTALL_COMMANDS:
        if which(manager):
            return {"command": command, "package_manager": manager, "nixos": False}
    return {"command": "", "package_manager": "", "nixos": False}


def in_gamescope_session() -> bool:
    """True when wLib itself runs inside a gamescope session (Steam Deck Game Mode)."""
    return steam_env.gaming_mode()


def args_start_gamescope(args: list[str]) -> bool:
    """True when the game's own arguments already run gamescope."""
    return any(_basename(arg) == "gamescope" for arg in args)


def _basename(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1]
