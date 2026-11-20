"""Prefix tools and the RTP installer use the prefix the game launches in."""

import os
import sys

import pytest

from core.api import Api
from core.database import add_game, update_game, update_setting

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Wine prefix tools are Linux only"
)


def test_launch_env_overrides_match_the_launcher_rules():
    from core.launcher import launch_env_overrides

    assert launch_env_overrides("WINEPREFIX=/p A=1 %command% -x", "auto") == {
        "WINEPREFIX": "/p",
        "A": "1",
    }
    assert launch_env_overrides("WINEPREFIX=/p game.exe", "custom") == {"WINEPREFIX": "/p"}
    # Without %command% or Custom Command, the words are plain arguments.
    assert launch_env_overrides("WINEPREFIX=/p -x", "auto") == {}
    assert launch_env_overrides('"unclosed', "custom") == {}


def test_launch_argument_prefix_wins_for_runtime_tools(tmp_path):
    api = Api()
    update_setting("wine_prefix_path", str(tmp_path / "global"))
    update_setting("proton_path", "")
    launch_prefix = tmp_path / "launch-prefix"
    launch_prefix.mkdir()
    game_id = add_game("Game", "/games/game.exe")
    assert game_id is not None
    update_game(
        game_id,
        {"command_line_args": f"WINEPREFIX={launch_prefix} %command%", "launch_mode": "auto"},
    )

    target = api._resolve_runtime_install_target(str(tmp_path / "custom"), "/usr/bin/wine", game_id)
    assert target["base_prefix"] == str(launch_prefix)
    assert target["resolved_prefix"] == str(launch_prefix)

    # A game without the override keeps the custom prefix field.
    plain = add_game("Plain", "/games/plain.exe")
    assert plain is not None
    custom = api._resolve_runtime_install_target(str(tmp_path / "custom"), "/usr/bin/wine", plain)
    assert custom["base_prefix"] == os.path.abspath(str(tmp_path / "custom"))
