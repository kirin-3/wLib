from unittest.mock import MagicMock
import pytest

from core.api import Api
from api_patching import patch_api_name


@pytest.fixture(autouse=True)
def isolated_library(tmp_path, monkeypatch):
    from core import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path))
    database.init_db()




def test_unsupported_bridge_methods(monkeypatch):
    api = Api()
    patch_api_name(monkeypatch, "is_linux", lambda: False)
    for call in (api.get_steam_status, api.add_wlib_to_steam, api.sync_steam_shortcuts,
                 api.set_steam_games_mode, api.set_steam_account, api.set_game_steam_shortcut, api.get_game_steam_state):
        assert call()['code'] == 'unsupported'


def test_game_update_schedules_sync(monkeypatch):
    from core.database import add_game
    api = Api()
    api.steam_sync = MagicMock()
    game = add_game('Example', '/game')
    assert api.update_game(game, {'title': 'Renamed'})['success']
    api.steam_sync.schedule.assert_called_once()


def test_linux_bridge_sets_mode_mark_and_retries(monkeypatch):
    from core import database, steam_sync
    api = Api()
    api.steam_sync = MagicMock()
    patch_api_name(monkeypatch, "is_linux", lambda: True)
    monkeypatch.setattr(steam_sync, 'environment', lambda: {'account_id': '123', 'accounts': ['123'], 'mode': 'marked'})
    game = database.add_game('Game', '/example')
    database.put_steam_shortcut('123', game, 42, 'user_removed')
    assert api.set_steam_games_mode('marked')['success']
    assert database.get_setting('steam_games_mode') == 'marked'
    assert api.set_steam_account('123')['success']
    assert not api.set_steam_account('missing')['success']
    assert api.set_game_steam_shortcut(game, True)['success']
    assert database.get_game(game)['steam_shortcut'] == 1
    assert database.list_steam_shortcuts('123') == []
    assert api.sync_steam_shortcuts()['success']
    api.steam_sync.schedule.assert_called_with(0, retry_removed=True)


def test_game_steam_state_reports_why_a_game_is_ineligible(monkeypatch, tmp_path):
    from core import database, steam_sync
    api = Api()
    patch_api_name(monkeypatch, "is_linux", lambda: True)
    monkeypatch.setattr(steam_sync, 'environment', lambda: {'account_id': '123', 'accounts': ['123'], 'mode': 'marked'})
    present = tmp_path / 'game.sh'
    present.write_text('')
    page = tmp_path / 'index.html'
    page.write_text('')
    cases = {str(present): (True, ''), str(tmp_path / 'gone.exe'): (False, 'missing_file'), str(page): (False, 'browser_game')}
    for path, (eligible, reason) in cases.items():
        game_id = database.add_game(path, path)
        if path == str(page):
            database.update_game(game_id, {'html_player': 'browser'})
        state = api.get_game_steam_state(game_id)
        assert (state['eligible'], state['reason']) == (eligible, reason)
    # The built-in player runs HTML as a process, so Steam can launch it.
    builtin = api.get_game_steam_state(database.add_game('Built-in', str(page)))
    assert (builtin['eligible'], builtin['reason']) == (True, '')
