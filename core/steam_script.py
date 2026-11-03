# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable entry point for Steam shortcuts."""
from __future__ import annotations

import os
import shlex
import sys
import tempfile
from pathlib import Path

from core.host_platform import get_data_dir, is_flatpak


def render_launch_script(*, flatpak: bool = False, appimage: str = '', launcher: str = '', python: str = '', main: str = '') -> str:
    if flatpak:
        command = ('flatpak run --env="WLIB_HOST_LD_PRELOAD=${LD_PRELOAD-}" '
                   '--env="WLIB_HOST_SteamAppId=${SteamAppId-}" '
                   '--env="WLIB_HOST_SteamGameId=${SteamGameId-}" io.github.kirin_3.wLib')
    elif appimage or launcher:
        command = shlex.quote(appimage or launcher)
    else:
        command = f'{shlex.quote(python)} {shlex.quote(main)}'
    return f'''#!/bin/sh
case "${{1-}}" in
    --*) exec {command} "$@" ;;
    ?*) exec {command} --launch "$1" ;;
esac
exec {command}
'''


def ensure_launch_script(data_dir: str | None = None) -> Path:
    path = Path(data_dir or get_data_dir()) / 'steam/wlib-steam'
    path.parent.mkdir(parents=True, exist_ok=True)
    script = render_launch_script(flatpak=is_flatpak(), appimage=os.environ.get('APPIMAGE', ''),
                                  launcher=os.environ.get('WLIB_LAUNCHER', ''), python=sys.executable,
                                  main=str(Path(__file__).resolve().parent.parent / 'main.py'))
    fd, temp = tempfile.mkstemp(prefix='.wlib-steam.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            _ = stream.write(script)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o755)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return path
