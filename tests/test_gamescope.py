# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core import gamescope
from core.gamescope import (
    GamescopeOptions,
    args_error,
    gamescope_argv,
    install_hint,
    normalize_resolution,
    parse_resolution,
    resolve_options,
    settings_error,
    split_args,
)


def options(**overrides: object) -> GamescopeOptions:
    base: GamescopeOptions = {
        "window_mode": "fullscreen",
        "output_resolution": "",
        "game_resolution": "",
        "scaler": "",
        "filter": "",
        "fps_limit": "",
        "args": [],
    }
    base.update(overrides)  # pyright: ignore[reportCallIssue, reportArgumentType]
    return base


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("816x624", (816, 624)),
        (" 1280 X 720 ", (1280, 720)),
        ("16384x1", (16384, 1)),
        ("0x600", None),
        ("16385x100", None),
        ("800", None),
        ("axb", None),
        ("", None),
    ],
)
def test_parse_resolution(value: str, expected: tuple[int, int] | None):
    assert parse_resolution(value) == expected


def test_normalize_resolution_and_errors():
    assert normalize_resolution(" 816 x 624 ") == "816x624"
    assert normalize_resolution("bad") == ""
    assert settings_error("gamescope_output_resolution", "") == ""
    assert "1 to 16384" in settings_error("gamescope_output_resolution", "0x600")


@pytest.mark.parametrize(("value", "ok"), [("", True), ("60", True), ("1000", True), ("0", False), ("1001", False), ("6.5", False)])
def test_fps_limit(value: str, ok: bool):
    assert (settings_error("gamescope_fps_limit", value) == "") is ok


def test_split_args_rules():
    assert split_args('--hdr-enabled "a b"') == ["--hdr-enabled", "a b"]
    with pytest.raises(ValueError, match="quoting is not closed"):
        _ = split_args('--hdr-enabled "unclosed')
    with pytest.raises(ValueError, match="`--` is not allowed"):
        _ = split_args("-f -- foo")
    assert args_error("--mangoapp") == ""


@pytest.mark.parametrize(
    ("mode", "flag"), [("fullscreen", ["-f"]), ("borderless", ["-b"]), ("windowed", [])]
)
def test_window_modes(mode: str, flag: list[str]):
    assert gamescope_argv(options(window_mode=mode)) == ["gamescope", *flag]


def test_fullscreen_pixel_art_game():
    argv = gamescope_argv(
        options(game_resolution="816x624", scaler="integer", filter="nearest")
    )
    assert argv == ["gamescope", "-f", "-w", "816", "-h", "624", "-S", "integer", "-F", "nearest"]


def test_output_set_game_auto_uses_output_size():
    argv = gamescope_argv(options(output_resolution="2560x1440"), (3840, 2160))
    assert argv == ["gamescope", "-f", "-W", "2560", "-H", "1440", "-w", "2560", "-h", "1440"]


def test_both_auto_uses_screen_size():
    argv = gamescope_argv(options(), (3840, 2160))
    assert argv == ["gamescope", "-f", "-W", "3840", "-H", "2160", "-w", "3840", "-h", "2160"]


def test_both_auto_unknown_screen_gives_no_size_flags():
    assert gamescope_argv(options(), None) == ["gamescope", "-f"]


def test_fps_and_extra_args_come_last():
    argv = gamescope_argv(options(fps_limit="60", args=["--hdr-enabled"]))
    assert argv == ["gamescope", "-f", "-r", "60", "--hdr-enabled"]


GLOBAL = {
    "gamescope_window_mode": "borderless",
    "gamescope_output_resolution": "2560x1440",
    "gamescope_game_resolution": "1280x720",
    "gamescope_scaler": "fit",
    "gamescope_filter": "fsr",
    "gamescope_fps_limit": "60",
    "gamescope_args": "--hdr-enabled",
}


def test_resolve_options_per_game_override():
    resolved = resolve_options(
        GLOBAL,
        {
            "gamescope_resolution": "816x624",
            "gamescope_scaler": "integer",
            "gamescope_filter": "",
            "gamescope_args": "--force-grab-cursor",
        },
    )
    assert resolved == {
        "window_mode": "borderless",
        "output_resolution": "2560x1440",
        "game_resolution": "816x624",
        "scaler": "integer",
        "filter": "fsr",
        "fps_limit": "60",
        "args": ["--hdr-enabled", "--force-grab-cursor"],
    }


def test_resolve_options_empty_override_uses_global():
    resolved = resolve_options(GLOBAL, {})
    assert resolved["game_resolution"] == "1280x720"
    assert resolved["scaler"] == "fit"
    assert resolved["filter"] == "fsr"
    assert resolved["args"] == ["--hdr-enabled"]


def test_resolve_options_defaults_when_global_missing():
    resolved = resolve_options({}, {})
    assert resolved == {
        "window_mode": "fullscreen",
        "output_resolution": "",
        "game_resolution": "",
        "scaler": "auto",
        "filter": "linear",
        "fps_limit": "",
        "args": [],
    }


def test_resolve_options_ignores_invalid_stored_args():
    resolved = resolve_options({"gamescope_args": "-- bad"}, {"gamescope_args": '"open'})
    assert resolved["args"] == []


@pytest.mark.parametrize(
    ("found", "command"),
    [
        ({"pacman"}, "sudo pacman -S gamescope"),
        ({"apt"}, "sudo apt install gamescope"),
        ({"dnf"}, "sudo dnf install gamescope"),
        ({"dnf", "rpm-ostree"}, "rpm-ostree install gamescope"),
        ({"zypper"}, "sudo zypper install gamescope"),
        ({"xbps-install"}, "sudo xbps-install gamescope"),
    ],
)
def test_install_hint_package_managers(found: set[str], command: str):
    hint = install_hint(lambda name: f"/usr/bin/{name}" if name in found else None, False)
    assert hint["command"] == command
    assert hint["nixos"] is False


def test_install_hint_nixos_and_unknown():
    assert install_hint(lambda _name: "/x", True) == {
        "command": "programs.gamescope.enable = true;",
        "package_manager": "nixos",
        "nixos": True,
    }
    assert install_hint(lambda _name: None, False) == {
        "command": "",
        "package_manager": "",
        "nixos": False,
    }


def test_in_gamescope_session(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GAMESCOPE_WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert gamescope.in_gamescope_session() is False
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "gamescope")
    assert gamescope.in_gamescope_session() is True


def test_args_start_gamescope():
    assert gamescope.args_start_gamescope(["/usr/bin/gamescope", "-f", "--", "%command%"])
    assert not gamescope.args_start_gamescope(["%command%", "-gamescope-like"])
