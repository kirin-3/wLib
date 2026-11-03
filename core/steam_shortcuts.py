# SPDX-License-Identifier: GPL-3.0-or-later
"""Steam's binary shortcut format and safe file operations."""
from __future__ import annotations

import os
import shutil
import struct
import tempfile
import zlib
from pathlib import Path
from typing import TypeAlias

VdfValue: TypeAlias = str | int | dict[str, 'VdfValue']


def read_vdf(data: bytes) -> dict[str, VdfValue]:
    offset = 0

    def cstring() -> str:
        nonlocal offset
        end = data.find(b'\0', offset)
        if end < 0:
            raise ValueError('Unterminated VDF string')
        value = data[offset:end].decode('utf-8', errors='surrogateescape')
        offset = end + 1
        return value

    def mapping() -> dict[str, VdfValue]:
        nonlocal offset
        result: dict[str, VdfValue] = {}
        while True:
            if offset >= len(data):
                raise ValueError('Truncated VDF')
            kind = data[offset]
            offset += 1
            if kind == 8:
                return result
            key = cstring()
            if key in result:
                raise ValueError('Duplicate VDF key')
            if kind == 0:
                result[key] = mapping()
            elif kind == 1:
                result[key] = cstring()
            elif kind == 2:
                if offset + 4 > len(data):
                    raise ValueError('Truncated VDF integer')
                result[key] = struct.unpack_from('<I', data, offset)[0]
                offset += 4
            else:
                raise ValueError(f'Unsupported VDF type: {kind}')

    result = mapping()
    if offset != len(data):
        raise ValueError('Trailing VDF data')
    return result


def write_vdf(values: dict[str, VdfValue]) -> bytes:
    output = bytearray()

    def cstring(value: str) -> None:
        if '\0' in value:
            raise ValueError('NUL in VDF string')
        output.extend(value.encode('utf-8', errors='surrogateescape'))
        output.append(0)

    def mapping(items: dict[str, VdfValue]) -> None:
        for key, value in items.items():
            if isinstance(value, dict):
                output.append(0)
                cstring(key)
                mapping(value)
            elif isinstance(value, str):
                output.append(1)
                cstring(key)
                cstring(value)
            else:
                output.append(2)
                cstring(key)
                output.extend(struct.pack('<I', value & 0xffffffff))
        output.append(8)

    mapping(values)
    return bytes(output)


def shortcut_appid(exe: str, name: str) -> int:
    return zlib.crc32((exe + name).encode('utf-8')) | 0x80000000


def rungameid(appid: int) -> int:
    return ((appid & 0xffffffff) << 32) | 0x02000000


def atomic_write(path: Path, data: bytes) -> None:
    """Replace a shortcut file atomically after saving its previous contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _ = shutil.copy2(path, path.with_name(path.name + '.wlib-bak'))
    fd, temp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def plan(
    desired: dict[int, dict[str, object]],
    links: dict[int, dict[str, object]],
    records: dict[int, dict[str, object]],
    steam_running: bool,
    *,
    retry_removed: bool = False,
) -> dict[int, str]:
    """Return reconciliation actions without changing files or stored state."""
    actions: dict[int, str] = {}
    for game_id in sorted(desired.keys() | links.keys() | records.keys()):
        wanted = desired.get(game_id)
        link = links.get(game_id)
        record = records.get(game_id)
        state = str(link.get('state') or '') if link else ''
        if wanted is None:
            if record is not None:
                actions[game_id] = 'pending_removal' if steam_running else 'remove'
            elif link is not None:
                actions[game_id] = 'forget'
            continue
        if state == 'user_removed' and not retry_removed:
            continue
        if record is None:
            if state == 'linked' and not retry_removed:
                actions[game_id] = 'user_removed'
            else:
                actions[game_id] = 'pending_add' if steam_running else 'add'
            continue
        changed = any(record.get(key) != wanted.get(key) for key in ('AppName', 'Exe', 'StartDir', 'LaunchOptions'))
        if changed:
            actions[game_id] = 'pending_update' if steam_running else 'update'
        elif state != 'linked':
            actions[game_id] = 'linked'
    return actions


def desired_games(games: list[dict[str, object]], mode: str, script: Path) -> dict[int, dict[str, object]]:
    """Build shortcut fields for installed games selected by the local mode."""
    desired: dict[int, dict[str, object]] = {}
    if mode == 'off':
        return desired
    for game in games:
        game_id = game.get('id')
        exe = os.path.expanduser(str(game.get('exe_path') or '').strip())
        launch_mode = str(game.get('launch_mode') or 'auto')
        if not isinstance(game_id, int) or not exe or not os.path.isfile(exe):
            continue
        if mode == 'marked' and not game.get('steam_shortcut'):
            continue
        if Path(exe).suffix.lower() in ('.html', '.htm') and launch_mode in ('auto', 'native'):
            continue
        desired[game_id] = {'AppName': str(game.get('title') or ''),
                            'Exe': f'"{script}"', 'StartDir': f'"{Path(exe).parent}"',
                            'LaunchOptions': str(game_id)}
    return desired


def sync_games(root: Path, account: str, script: Path, *, retry_removed: bool = False) -> dict[str, object]:
    """Reconcile games for one account, never writing while Steam is running."""
    import time

    from core import database, steam_env

    if account not in steam_env.accounts(root):
        return {'success': False, 'error': 'Steam account not found'}
    path = root / 'userdata' / account / 'config/shortcuts.vdf'
    try:
        data: dict[str, VdfValue] = read_vdf(path.read_bytes()) if path.exists() else {'shortcuts': {}}
        shortcuts = data.get('shortcuts')
        if not isinstance(shortcuts, dict):
            raise ValueError('Invalid shortcuts root')
        records: dict[int, dict[str, object]] = {}
        keys: dict[int, str] = {}
        for key, value in shortcuts.items():
            if not isinstance(value, dict):
                continue
            exe = str(value.get('Exe') or '').strip('"')
            arg = str(value.get('LaunchOptions') or '')
            if exe == str(script) and arg.isdecimal() and int(arg) > 0:
                game_id = int(arg)
                if game_id in records:
                    raise ValueError('Duplicate wLib shortcut for a game')
                records[game_id] = dict(value)
                keys[game_id] = key
        links = {int(str(link['game_id'])): link for link in database.list_steam_shortcuts(account)}
        mode = steam_env.effective_mode(database.get_setting('steam_games_mode'))
        games = database.get_all_games()
        existing_ids = {game.get('id') for game in games}
        desired = desired_games(games, mode, script)
        running = steam_env.steam_running(root)
        actions = plan(desired, links, records, running, retry_removed=retry_removed)
        # Steam can rewrite the file immediately after it exits. Wait for a stable mtime.
        if not running and path.exists() and time.time() - path.stat().st_mtime < 5:
            running = True
            actions = plan(desired, links, records, True, retry_removed=retry_removed)
        counts = {'added': 0, 'updated': 0, 'removed': 0}
        link_updates: list[tuple[int, int | None, str, str]] = []
        forget: list[int] = []
        removed_art: list[int] = []
        for game_id, action in actions.items():
            link = links.get(game_id, {})
            app_id_value = link.get('app_id') or records.get(game_id, {}).get('appid')
            app_id = int(str(app_id_value)) if app_id_value is not None else None
            art_key = str(link.get('art_key') or '')
            if action == 'forget':
                forget.append(game_id)
                continue
            if action == 'remove':
                del shortcuts[keys[game_id]]
                if app_id is not None:
                    removed_art.append(app_id)
                counts['removed'] += 1
                forget.append(game_id)
                continue
            if action in ('add', 'update'):
                fields = desired[game_id]
                if action == 'add':
                    occupied = {int(str(v['appid'])) for v in shortcuts.values() if isinstance(v, dict) and isinstance(v.get('appid'), int)}
                    app_id = shortcut_appid(str(fields['Exe']), str(fields['AppName']))
                    # Games with the same title share Steam's default ID; salt it with the game ID.
                    salt = 0
                    while app_id in occupied:
                        salt += 1
                        app_id = shortcut_appid(str(fields['Exe']), f"{fields['AppName']}\0{game_id}:{salt}")
                    key = str(max((int(k) for k in shortcuts if k.isdecimal()), default=-1) + 1)
                    shortcuts[key] = {'appid': app_id, 'AppName': '', 'Exe': '', 'StartDir': '',
                                      'icon': '', 'ShortcutPath': '', 'LaunchOptions': '',
                                      'IsHidden': 0, 'AllowDesktopConfig': 1, 'AllowOverlay': 1,
                                      'OpenVR': 0, 'Devkit': 0, 'DevkitGameID': '',
                                      'DevkitOverrideAppID': 0, 'LastPlayTime': 0, 'FlatpakAppID': '', 'tags': {}}
                    counts['added'] += 1
                else:
                    key = keys[game_id]
                    counts['updated'] += 1
                entry = shortcuts[key]
                if not isinstance(entry, dict):
                    raise ValueError('Invalid shortcut entry')
                for field, value in fields.items():
                    entry[field] = str(value)
                link_updates.append((game_id, app_id, 'linked', art_key))
            elif game_id in existing_ids:
                link_updates.append((game_id, app_id, action, art_key))
        if any(counts.values()):
            if steam_env.steam_running(root):
                return {'success': False, 'error': 'Steam started during synchronization; retry after it closes'}
            atomic_write(path, write_vdf(data))
        grid = path.parent / 'grid'
        for app_id in removed_art:
            for suffix in ('p.png', '.png', '_hero.png'):
                (grid / f'{app_id}{suffix}').unlink(missing_ok=True)
        for game_id in forget:
            database.delete_steam_shortcut(account, game_id)
        for game_id, app_id, state, art_key in link_updates:
            database.put_steam_shortcut(account, game_id, app_id, state, art_key)
        from core.game_covers import cached_cover_path, cover_path

        updated_links = {int(str(link['game_id'])): link for link in database.list_steam_shortcuts(account)}
        convert: list[str] = []
        used_converted: set[str] = set()
        converted_dir = converted_cover_path(Path('x')).parent
        for game in games:
            game_id = game.get('id')
            if not isinstance(game_id, int) or game_id not in desired:
                continue
            link = updated_links.get(game_id)
            if not link or not isinstance(link.get('app_id'), int):
                continue
            app_id = int(str(link['app_id']))
            source = cover_path(game.get('cover_local'))
            if source is None or not source.is_file():
                source = cached_cover_path(game.get('cover_cache'))
            if (source is None or not source.is_file()) and link.get('art_key'):
                for suffix in ('p.png', '.png', '_hero.png'):
                    (grid / f'{app_id}{suffix}').unlink(missing_ok=True)
            if source is not None and source.is_file() and not qt_can_read(source):
                # Qt lacks some formats (AVIF); the window converts them to PNG for us.
                converted = converted_cover_path(source)
                if not converted.is_file() or converted.stat().st_mtime_ns < source.stat().st_mtime_ns:
                    convert.append(str(source))
                    continue
                source = converted
            if source is not None and source.parent == converted_dir:
                used_converted.add(source.name)
            try:
                key = write_art(grid, app_id, source, str(link.get('art_key') or ''))
            except (OSError, ValueError) as exc:
                # One unreadable cover must not block the other games' art.
                print(f'[wLib] Steam artwork for game {game_id} failed: {exc}')
                continue
            if key != str(link.get('art_key') or ''):
                database.put_steam_shortcut(account, game_id, app_id, str(link['state']), key)
        if converted_dir.is_dir() and not convert:
            for stale in converted_dir.iterdir():
                if stale.is_file() and stale.name not in used_converted:
                    stale.unlink(missing_ok=True)
        return {'success': True, **counts, 'convert': convert,
                'pending': sum(1 for action in actions.values() if action.startswith('pending_'))}
    except (OSError, ValueError, TypeError) as exc:
        return {'success': False, 'error': str(exc)}


def qt_can_read(path: Path) -> bool:
    from PyQt6.QtGui import QImageReader

    return QImageReader(str(path)).canRead()


def converted_cover_path(source: Path) -> Path:
    """Device-local PNG copy of a cover that Qt cannot decode."""
    from core.game_covers import cache_covers_dir

    return cache_covers_dir() / 'steam-png' / f'{source.parent.name}-{source.name}.png'


def write_art(grid: Path, app_id: int, source: Path | None, old_key: str = '') -> str:
    """Generate Steam capsule and hero art from a cached or local cover."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QImage, QPainter

    targets = (grid / f'{app_id}p.png', grid / f'{app_id}.png', grid / f'{app_id}_hero.png')
    if source is None or not source.is_file():
        return ''
    key = f'{source.name}:{source.stat().st_mtime_ns}'
    if key == old_key and all(path.is_file() for path in targets):
        return key
    image = QImage(str(source))
    if image.isNull():
        raise ValueError('Could not read cover image')
    grid.mkdir(parents=True, exist_ok=True)

    def fill(width: int, height: int) -> QImage:
        scaled = image.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
        return scaled.copy((scaled.width() - width) // 2, (scaled.height() - height) // 2, width, height)

    portrait = fill(600, 900)
    painter = QPainter(portrait)
    painter.fillRect(0, 0, 600, 900, QColor(0, 0, 0, 155))
    fit = image.scaled(600, 900, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    painter.drawImage((600 - fit.width()) // 2, (900 - fit.height()) // 2, fit)
    _ = painter.end()
    for target, output in zip(targets, (portrait, fill(920, 430), fill(1920, 620)), strict=True):
        fd, temp = tempfile.mkstemp(prefix='.wlib-art-', suffix='.png', dir=grid)
        os.close(fd)
        try:
            if not output.save(temp, 'PNG'):
                raise OSError('Could not write Steam artwork')
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return key


def add_wlib(root: Path, account: str, script: Path, icon: Path) -> dict[str, object]:
    """Add the GUI shortcut, or persist a request until Steam has exited."""
    import time
    from core import database, steam_env

    if account not in steam_env.accounts(root):
        return {'success': False, 'error': 'Steam account not found'}
    path = root / 'userdata' / account / 'config/shortcuts.vdf'
    pending_key = f'steam_wlib_pending_{account}'
    try:
        data: dict[str, VdfValue] = read_vdf(path.read_bytes()) if path.exists() else {'shortcuts': {}}
        shortcuts = data.get('shortcuts')
        if not isinstance(shortcuts, dict):
            raise ValueError('Invalid shortcuts root')
        for record in shortcuts.values():
            if isinstance(record, dict) and str(record.get('Exe') or '').strip('"') == str(script) and record.get('LaunchOptions') == 'LD_PRELOAD= %command%':
                database.update_setting(pending_key, '')
                return {'success': True, 'already_exists': True}
        if steam_env.steam_running(root) or (path.exists() and time.time() - path.stat().st_mtime < 5):
            database.update_setting(pending_key, 'true')
            return {'success': True, 'pending': True}
        exe = f'"{script}"'
        app_id = shortcut_appid(exe, 'wLib')
        occupied = {int(str(v['appid'])) for v in shortcuts.values() if isinstance(v, dict) and isinstance(v.get('appid'), int)}
        if app_id in occupied:
            raise ValueError('Steam shortcut app ID collision')
        key = str(max((int(k) for k in shortcuts if k.isdecimal()), default=-1) + 1)
        shortcuts[key] = {'appid': app_id, 'AppName': 'wLib', 'Exe': exe,
                          'StartDir': f'"{script.parent}"', 'icon': '', 'ShortcutPath': '',
                          'LaunchOptions': 'LD_PRELOAD= %command%', 'IsHidden': 0,
                          'AllowDesktopConfig': 1, 'AllowOverlay': 1, 'OpenVR': 0,
                          'Devkit': 0, 'DevkitGameID': '', 'DevkitOverrideAppID': 0,
                          'LastPlayTime': 0, 'FlatpakAppID': '', 'tags': {}}
        if steam_env.steam_running(root):
            database.update_setting(pending_key, 'true')
            return {'success': True, 'pending': True}
        atomic_write(path, write_vdf(data))
        if icon.is_file():
            _ = write_art(path.parent / 'grid', app_id, icon)
        database.update_setting(pending_key, '')
        return {'success': True, 'added': True}
    except (OSError, ValueError, TypeError) as exc:
        return {'success': False, 'error': str(exc)}
