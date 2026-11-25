# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from core.host_platform import get_cache_dir

NWJS_LIBRARY = "libnw.so"
NWJS_LIBRARY_FOLDERS = ("", "lib")
ANGLE_LIBRARY = "libEGL.so"
TUNABLES_VARIABLE = "GLIBC_TUNABLES"
EXECSTACK_TUNABLE_NAME = "glibc.rtld.execstack"
EXECSTACK_ALLOWED = f"{EXECSTACK_TUNABLE_NAME}=2"
CACHE_HOME_VARIABLE = "XDG_CACHE_HOME"
NWJS_CACHE_FOLDER = "nwjs"
HOST_GPU_ARGUMENT = "--use-gl=desktop"
GPU_ARGUMENT_NAMES = frozenset(
    {"--use-gl", "--use-angle", "--disable-gpu", "--in-process-gpu"}
)


def _nwjs_library(game_dir: str) -> str | None:
    for folder in NWJS_LIBRARY_FOLDERS:
        path = os.path.join(game_dir, folder, NWJS_LIBRARY)
        if os.path.isfile(path):
            return path
    return None


def _tunables_allowing_execstack(current: str) -> str:
    entries = [entry for entry in current.split(":") if entry]
    if any(entry.startswith(f"{EXECSTACK_TUNABLE_NAME}=") for entry in entries):
        return current
    return ":".join([*entries, EXECSTACK_ALLOWED])


def _build_cache_dir(library: str) -> str:
    """Each NW.js build gets its own cache: an old build's fontconfig cannot
    read the font cache a newer build wrote (0.29 aborts after 0.48 ran) and
    the bad cache stays. The size of libnw.so tells the builds apart."""
    return os.path.join(
        get_cache_dir(), NWJS_CACHE_FOLDER, f"build-{os.path.getsize(library)}"
    )


def nwjs_host_env(
    game_dir: str, env: Mapping[str, str], user_env: Mapping[str, str]
) -> dict[str, str]:
    """glibc refuses the bundled libnode.so, which asks for an executable stack,
    unless execstack=2 (glibc 2.42+), and NW.js's built-in fontconfig finds no
    font in a cache that a current fontconfig wrote."""
    library = _nwjs_library(game_dir)
    if library is None:
        return {}
    overrides = {
        TUNABLES_VARIABLE: _tunables_allowing_execstack(env.get(TUNABLES_VARIABLE, ""))
    }
    if CACHE_HOME_VARIABLE not in user_env:
        overrides[CACHE_HOME_VARIABLE] = _build_cache_dir(library)
    return overrides


def nwjs_gpu_arguments(game_dir: str, args: Sequence[str]) -> list[str]:
    """Old builds without ANGLE (Chromium 65, NW.js 0.29) fall back to
    SwiftShader on a current Mesa stack and draw the whole game on the CPU.
    Builds that ship libEGL.so beside libnw.so already use the GPU, and from
    Chromium 110 (NW.js 0.73) --use-gl=desktop forces SwiftShader or turns
    WebGL off, so they get no flag."""
    library = _nwjs_library(game_dir)
    if library is None:
        return []
    if os.path.isfile(os.path.join(os.path.dirname(library), ANGLE_LIBRARY)):
        return []
    if any(arg.split("=", 1)[0] in GPU_ARGUMENT_NAMES for arg in args):
        return []
    return [HOST_GPU_ARGUMENT]
