# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from collections.abc import Mapping

from core.host_platform import get_cache_root

NWJS_LIBRARY = "libnw.so"
NWJS_LIBRARY_FOLDERS = ("", "lib")
TUNABLES_VARIABLE = "GLIBC_TUNABLES"
EXECSTACK_TUNABLE_NAME = "glibc.rtld.execstack"
EXECSTACK_ALLOWED = f"{EXECSTACK_TUNABLE_NAME}=2"
CACHE_HOME_VARIABLE = "XDG_CACHE_HOME"
WLIB_CACHE_FOLDER = "wLib"
NWJS_CACHE_FOLDER = "nwjs"


def is_nwjs_bundle(game_dir: str) -> bool:
    return any(
        os.path.isfile(os.path.join(game_dir, folder, NWJS_LIBRARY))
        for folder in NWJS_LIBRARY_FOLDERS
    )


def _tunables_allowing_execstack(current: str) -> str:
    entries = [entry for entry in current.split(":") if entry]
    if any(entry.startswith(f"{EXECSTACK_TUNABLE_NAME}=") for entry in entries):
        return current
    return ":".join([*entries, EXECSTACK_ALLOWED])


def nwjs_host_env(
    game_dir: str, env: Mapping[str, str], user_env: Mapping[str, str]
) -> dict[str, str]:
    """glibc refuses the bundled libnode.so, which asks for an executable stack,
    unless execstack=2 (glibc 2.42+), and NW.js's built-in fontconfig finds no
    font in a cache that a current fontconfig wrote."""
    if not is_nwjs_bundle(game_dir):
        return {}
    overrides = {
        TUNABLES_VARIABLE: _tunables_allowing_execstack(env.get(TUNABLES_VARIABLE, "")),
        CACHE_HOME_VARIABLE: os.path.join(
            get_cache_root(), WLIB_CACHE_FOLDER, NWJS_CACHE_FOLDER
        ),
    }
    return {name: value for name, value in overrides.items() if name not in user_env}
