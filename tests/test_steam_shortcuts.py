from pathlib import Path

import pytest

from core.steam_shortcuts import atomic_write, read_vdf, rungameid, shortcut_appid, write_vdf
from api_patching import patch_api_name


def test_round_trip_and_preserve_other_shortcuts():
    # Sanitized structure from a real Steam shortcut, including tags.
    fixture = (Path(__file__).parent / 'fixtures/steam/shortcuts.vdf').read_bytes()
    records = read_vdf(fixture)
    assert write_vdf(records) == fixture
    shortcuts = records['shortcuts']
    assert isinstance(shortcuts, dict)
    original = shortcuts['0']
    shortcuts['1'] = {'AppName': 'wLib', 'appid': 123}
    assert read_vdf(write_vdf(records))['shortcuts'] == shortcuts
    del shortcuts['1']
    assert shortcuts['0'] == original
    assert write_vdf(records) == fixture


def test_invalid_vdf_is_rejected():
    with pytest.raises(ValueError):
        read_vdf(b'\x01key\x00unterminated')
    with pytest.raises(ValueError):
        read_vdf(b'\x03key\x00')


def test_ids_and_atomic_backup(tmp_path):
    assert shortcut_appid('"/usr/bin/game"', 'Game') == 0x98783974
    assert rungameid(0x98783974) == 0x9878397402000000
    path = tmp_path / 'shortcuts.vdf'
    path.write_bytes(b'old')
    atomic_write(path, b'new')
    assert path.read_bytes() == b'new'
    assert (tmp_path / 'shortcuts.vdf.wlib-bak').read_bytes() == b'old'


@pytest.mark.parametrize('wanted,link,record,running,retry,expected', [
    (True, None, None, False, False, 'add'),
    (True, None, None, True, False, 'pending_add'),
    (True, 'pending_add', None, False, False, 'add'),
    (True, 'linked', None, False, False, 'user_removed'),
    (True, 'user_removed', None, False, False, None),
    (True, 'user_removed', None, False, True, 'add'),
    (False, 'linked', 'same', True, False, 'pending_removal'),
    (False, 'pending_removal', 'same', False, False, 'remove'),
    (False, 'pending_add', None, True, False, 'forget'),
    (True, 'linked', 'changed', True, False, 'pending_update'),
    (True, 'pending_update', 'changed', False, False, 'update'),
    (True, 'pending_update', 'same', False, False, 'linked'),
    (True, 'linked', 'same', False, False, None),
])
def test_planner(wanted, link, record, running, retry, expected):
    from core.steam_shortcuts import plan

    fields = {'AppName': 'Game', 'Exe': '/script', 'StartDir': '/games', 'LaunchOptions': '1'}
    desired = {1: fields} if wanted else {}
    links = {1: {'state': link}} if link else {}
    records = {1: {**fields, 'AppName': 'Old' if record == 'changed' else 'Game'}} if record else {}
    assert plan(desired, links, records, running, retry_removed=retry).get(1) == expected


def test_desired_games_excludes_wishlist_missing_and_browser(tmp_path, monkeypatch):
    monkeypatch.setattr('core.database.get_setting', lambda key: None)
    from core.steam_shortcuts import desired_games
    game = tmp_path / 'game'
    game.write_text('')
    html = tmp_path / 'index.html'
    html.write_text('')
    swf = tmp_path / 'game.swf'
    swf.write_text('')
    games = [
        {'id': 1, 'title': 'Installed', 'exe_path': str(game), 'steam_shortcut': 1},
        {'id': 2, 'title': 'Wishlist', 'exe_path': ''},
        {'id': 3, 'title': 'Missing', 'exe_path': str(tmp_path / 'missing')},
        {'id': 4, 'title': 'Browser', 'exe_path': str(html), 'html_player': 'browser'},
        {'id': 5, 'title': 'Unmarked', 'exe_path': str(game)},
        {'id': 6, 'title': 'Built-in player', 'exe_path': str(html), 'html_player': 'builtin'},
        {'id': 7, 'title': 'Default player', 'exe_path': str(html)},
        {'id': 8, 'title': 'Flash', 'exe_path': str(swf), 'html_player': 'browser'},
    ]
    assert set(desired_games(games, 'all-installed', tmp_path / 'script')) == {1, 5, 6, 7, 8}
    assert set(desired_games(games, 'marked', tmp_path / 'script')) == {1}
    assert not desired_games(games, 'off', tmp_path / 'script')


def test_sync_queues_and_applies_after_steam_exit(tmp_path, monkeypatch):
    import os
    import time
    from core import database, steam_env
    from core.steam_shortcuts import sync_games

    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'library.db'))
    database.init_db()
    game_file = tmp_path / 'game'
    game_file.write_text('')
    game_id = database.add_game('Example', str(game_file))
    database.update_setting('steam_games_mode', 'all-installed')
    root = tmp_path / 'Steam'
    config = root / 'userdata/123/config'
    config.mkdir(parents=True)
    script = tmp_path / 'wlib-steam'
    monkeypatch.setattr(steam_env, 'steam_running', lambda _: True)
    result = sync_games(root, '123', script)
    assert result['pending'] == 1
    assert not (config / 'shortcuts.vdf').exists()
    assert database.list_steam_shortcuts('123')[0]['state'] == 'pending_add'
    monkeypatch.setattr(steam_env, 'steam_running', lambda _: False)
    result = sync_games(root, '123', script)
    assert result['added'] == 1
    assert database.list_steam_shortcuts('123')[0]['state'] == 'linked'
    path = config / 'shortcuts.vdf'
    os.utime(path, (time.time() - 10, time.time() - 10))
    database.update_game(game_id, {'title': 'Renamed'})
    assert sync_games(root, '123', script)['updated'] == 1
    os.utime(path, (time.time() - 10, time.time() - 10))
    database.update_setting('steam_games_mode', 'off')
    monkeypatch.setattr(steam_env, 'steam_running', lambda _: True)
    assert sync_games(root, '123', script)['pending'] == 1
    monkeypatch.setattr(steam_env, 'steam_running', lambda _: False)
    assert sync_games(root, '123', script)['removed'] == 1
    assert database.list_steam_shortcuts('123') == []


def test_artwork_dimensions_and_unchanged_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QColor, QGuiApplication, QImage
    app = QGuiApplication.instance() or QGuiApplication([])
    assert app is not None
    from core.steam_shortcuts import write_art
    banner = tmp_path / 'banner.png'
    image = QImage(200, 60, QImage.Format.Format_RGB32)
    image.fill(QColor('blue'))
    assert image.save(str(banner))
    grid = tmp_path / 'grid'
    key = write_art(grid, 123, banner)
    files = [grid / '123p.png', grid / '123.png', grid / '123_hero.png']
    assert [(QImage(str(p)).width(), QImage(str(p)).height()) for p in files] == [(600, 900), (920, 430), (1920, 620)]
    times = [p.stat().st_mtime_ns for p in files]
    assert write_art(grid, 123, banner, key) == key
    assert [p.stat().st_mtime_ns for p in files] == times
    assert write_art(grid, 124, None) == ''


def test_add_wlib_queued_and_idempotent(tmp_path, monkeypatch):
    import os
    import time
    from core import database, steam_env
    from core.steam_shortcuts import add_wlib

    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'library.db'))
    database.init_db()
    root = tmp_path / 'steam'
    config = root / 'userdata/123/config'
    config.mkdir(parents=True)
    script = tmp_path / 'script'
    icon = tmp_path / 'no-icon'
    monkeypatch.setattr(steam_env, 'steam_running', lambda _: True)
    assert add_wlib(root, '123', script, icon)['pending']
    assert database.get_setting('steam_wlib_pending_123') == 'true'
    assert not (config / 'shortcuts.vdf').exists()
    monkeypatch.setattr(steam_env, 'steam_running', lambda _: False)
    assert add_wlib(root, '123', script, icon)['added']
    os.utime(config / 'shortcuts.vdf', (time.time() - 10, time.time() - 10))
    assert add_wlib(root, '123', script, icon)['already_exists']
    contents = read_vdf((config / 'shortcuts.vdf').read_bytes())
    assert len(contents['shortcuts']) == 1


def test_sync_creates_and_reuses_cover_art(tmp_path, monkeypatch):
    import os
    import time
    from PyQt6.QtGui import QColor, QImage
    from core import database, steam_env
    from core.steam_shortcuts import sync_games

    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'library.db'))
    monkeypatch.setattr(database, 'DATA_DIR', str(tmp_path))
    database.init_db()
    executable = tmp_path / 'game'
    executable.write_text('')
    game_id = database.add_game('Example', str(executable))
    covers = tmp_path / 'covers'
    covers.mkdir()
    cover = covers / ('a' * 32 + '.png')
    image = QImage(200, 60, QImage.Format.Format_RGB32)
    image.fill(QColor('blue'))
    assert image.save(str(cover))
    database.set_game_local_cover(game_id, cover.name)
    database.update_setting('steam_games_mode', 'all-installed')
    root = tmp_path / 'steam'
    config = root / 'userdata/123/config'
    config.mkdir(parents=True)
    monkeypatch.setattr(steam_env, 'steam_running', lambda _: False)
    assert sync_games(root, '123', tmp_path / 'script')['added'] == 1
    link = database.list_steam_shortcuts('123')[0]
    art = config / 'grid' / f"{link['app_id']}p.png"
    before = art.stat().st_mtime_ns
    os.utime(config / 'shortcuts.vdf', (time.time() - 10, time.time() - 10))
    assert sync_games(root, '123', tmp_path / 'script')['success']
    assert art.stat().st_mtime_ns == before


def test_games_with_the_same_title_get_distinct_app_ids(tmp_path, monkeypatch):
    from core import database, steam_env
    from core.steam_shortcuts import read_vdf, sync_games

    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'library.db'))
    database.init_db()
    for name in ('a', 'b'):
        (tmp_path / name).write_text('')
        database.add_game('Same', str(tmp_path / name))
    database.update_setting('steam_games_mode', 'all-installed')
    config = tmp_path / 'Steam/userdata/123/config'
    config.mkdir(parents=True)
    monkeypatch.setattr(steam_env, 'steam_running', lambda _: False)
    assert sync_games(tmp_path / 'Steam', '123', tmp_path / 'wlib-steam')['added'] == 2
    shortcuts = read_vdf((config / 'shortcuts.vdf').read_bytes())['shortcuts']
    assert isinstance(shortcuts, dict)
    assert len({entry['appid'] for entry in shortcuts.values() if isinstance(entry, dict)}) == 2


def test_unreadable_cover_is_converted_by_the_window(tmp_path, monkeypatch):
    import base64
    import os
    import time
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage
    from core import database, steam_env, steam_sync
    from core.api import Api
    from core.steam_shortcuts import sync_games

    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'library.db'))
    monkeypatch.setattr(database, 'DATA_DIR', str(tmp_path))
    database.init_db()
    executable = tmp_path / 'game'
    executable.write_text('')
    game_id = database.add_game('Example', str(executable))
    covers = tmp_path / 'covers'
    covers.mkdir()
    # An AVIF header Qt cannot decode, like F95 covers on stock Qt builds.
    cover = covers / ('b' * 32 + '.avif')
    cover.write_bytes(b'\0\0\0\x1cftypavif' + b'\0' * 64)
    database.set_game_local_cover(game_id, cover.name)
    database.update_setting('steam_games_mode', 'all-installed')
    root = tmp_path / 'steam'
    config = root / 'userdata/123/config'
    config.mkdir(parents=True)
    monkeypatch.setattr(steam_env, 'steam_running', lambda _: False)
    result = sync_games(root, '123', tmp_path / 'script')
    assert result['added'] == 1 and result['convert'] == [str(cover)]
    assert not (config / 'grid').exists()

    notified = []
    worker = steam_sync.SteamSync(tmp_path / 'icon', on_conversions=lambda: notified.append(True))
    monkeypatch.setattr(worker, 'schedule', lambda *_a, **_kw: None)
    monkeypatch.setattr(steam_sync, 'environment', lambda: {'root': str(root), 'account_id': '123'})
    os.utime(config / 'shortcuts.vdf', (time.time() - 10, time.time() - 10))
    worker._run()
    assert notified == [True]
    api = Api()
    api.steam_sync = worker
    patch_api_name(monkeypatch, "is_linux", lambda: True)
    queued = api.get_steam_cover_conversions()['covers']
    assert [(c['token'], c['type']) for c in queued] == [('covers-' + cover.name + '.png', 'image/avif')]
    assert not api.save_steam_cover_png(queued[0]['token'], base64.b64encode(b'not png').decode())['success']
    worker._run()
    queued = api.get_steam_cover_conversions()['covers']
    image = QImage(200, 60, QImage.Format.Format_RGB32)
    image.fill(QColor('blue'))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    png = base64.b64encode(bytes(buffer.data().data())).decode()
    # The failed token is remembered, so it is not offered again this session.
    assert queued == []
    worker._failed_conversions.clear()
    worker._run()
    token = api.get_steam_cover_conversions()['covers'][0]['token']
    assert api.save_steam_cover_png(token, png)['success']
    assert not api.save_steam_cover_png(token, png)['success']
    assert sync_games(root, '123', tmp_path / 'script')['convert'] == []
    link = database.list_steam_shortcuts('123')[0]
    assert (config / 'grid' / f"{link['app_id']}_hero.png").is_file()
