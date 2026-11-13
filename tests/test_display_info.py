# SPDX-License-Identifier: GPL-3.0-or-later
from core import display_info
from core.gamescope import gamescope_argv


def test_unknown_screen_size_gives_no_size_flags():
    display_info.set_screen_size(None, None)
    assert display_info.screen_size() is None
    argv = gamescope_argv(
        {"window_mode": "fullscreen", "output_resolution": "", "game_resolution": "", "scaler": "", "filter": "", "fps_limit": "", "args": []},
        display_info.screen_size(),
    )
    assert argv == ["gamescope", "-f"]


def test_known_screen_size_is_used_when_both_auto():
    display_info.set_screen_size(3840, 2160)
    try:
        assert display_info.screen_size() == (3840, 2160)
        argv = gamescope_argv(
            {"window_mode": "fullscreen", "output_resolution": "", "game_resolution": "", "scaler": "", "filter": "", "fps_limit": "", "args": []},
            display_info.screen_size(),
        )
        assert argv == ["gamescope", "-f", "-W", "3840", "-H", "2160", "-w", "3840", "-h", "2160"]
    finally:
        display_info.set_screen_size(None, None)


def test_invalid_sizes_clear_the_value():
    display_info.set_screen_size(0, 1080)
    assert display_info.screen_size() is None
    display_info.set_screen_size("x", 1080)
    assert display_info.screen_size() is None
