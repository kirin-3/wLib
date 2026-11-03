# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover the local Steam installation and session."""
from __future__ import annotations

import os
import re
from pathlib import Path


def steam_root(home: Path | None = None) -> Path | None:
    home = home or Path.home()
    for path in (home / '.steam/steam', home / '.local/share/Steam'):
        if path.is_dir():
            return path.resolve()
    return None


def flatpak_steam_only(home: Path | None = None) -> bool:
    home = home or Path.home()
    return steam_root(home) is None and (home / '.var/app/com.valvesoftware.Steam').is_dir()


def accounts(root: Path) -> list[str]:
    userdata = root / 'userdata'
    if not userdata.is_dir():
        return []
    return sorted(p.name for p in userdata.iterdir() if p.name.isdecimal() and (p / 'config').is_dir())


def most_recent_account(root: Path) -> str | None:
    try:
        text = (root / 'config/loginusers.vdf').read_text(encoding='utf-8')
    except OSError:
        return None
    # Each user is a single block in loginusers.vdf. Match quoted strings,
    # allowing Steam's whitespace and comments between entries.
    for match in re.finditer(r'"(\d{17,})"\s*\{([^{}]*)\}', text, re.DOTALL):
        if re.search(r'"MostRecent"\s*"1"', match.group(2), re.IGNORECASE):
            account = str(int(match.group(1)) & 0xffffffff)
            if account in accounts(root):
                return account
    return None


def steam_running(root: Path, proc: Path | None = None, home: Path | None = None) -> bool:
    proc = proc or Path('/proc')
    # Native Steam normally writes ~/.steam/steam.pid outside the resolved root.
    for pid_file in ((home or Path.home()) / '.steam/steam.pid', root / 'steam.pid'):
        try:
            pid = int(pid_file.read_text().strip())
            if pid > 0 and (proc / str(pid) / 'comm').read_text().strip() == 'steam':
                return True
        except (OSError, ValueError):
            continue
    return False


def is_deck(os_release: Path | None = None) -> bool:
    os_release = os_release or Path('/etc/os-release')
    override = os.environ.get('WLIB_STEAM_DECK')
    if override in ('0', '1'):
        return override == '1'
    try:
        fields = dict(line.split('=', 1) for line in os_release.read_text().splitlines() if '=' in line and not line.startswith('#'))
    except OSError:
        return False
    return fields.get('ID', '').strip('"\'').lower() == 'steamos' or fields.get('VARIANT_ID', '').strip('"\'').lower() == 'steamdeck'


def gaming_mode() -> bool:
    return ('gamescope' in os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
            or bool(os.environ.get('GAMESCOPE_WAYLAND_DISPLAY'))
            or any(key.startswith('STEAM_GAMESCOPE_') for key in os.environ))


def effective_mode(value: str | None, deck: bool | None = None) -> str:
    if value in ('off', 'all-installed', 'marked'):
        return value
    return 'all-installed' if (is_deck() if deck is None else deck) else 'off'
