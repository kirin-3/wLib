import pytest
from unittest.mock import MagicMock, patch
from core.api import Api

@pytest.fixture
def api():
    with patch('core.api.init_db'):
        with patch('core.api.Scraper'):
            with patch('core.api.Launcher'):
                return Api()

def test_check_for_updates_found(api):
    url = "http://f95.com/game"
    remote_version = "1.0"
    current_version = "0.9"

    # Setup mock scraper return
    api.scraper.get_thread_version.return_value = {
        "success": True,
        "version": remote_version
    }

    # We need to mock the database connection used inside check_for_updates
    # Api.check_for_updates calls: from core.database import get_connection
    # So we patch core.api.get_connection? No, it imports inside the method.
    # We must patch core.database.get_connection globally or where it is defined.
    with patch('core.database.get_connection') as mock_conn:
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor

        # cursor.fetchone() called twice:
        # 1. To get current version (SELECT version ...)
        # Wait, the code updates first, then selects?
        # "UPDATE games SET latest_version = ? ..."
        # "SELECT version FROM games ..."

        # We need to simulate the SELECT returning the current version
        mock_cursor.fetchone.return_value = [current_version]

        result = api.check_for_updates(url)

        assert result["success"] is True
        assert result["version"] == remote_version
        assert result["has_update"] is True

        # Verify DB calls
        mock_cursor.execute.assert_any_call("UPDATE games SET latest_version = ? WHERE f95_url = ?", (remote_version, url))

def test_install_rpgmaker_dependencies(api):
    # Api.install_rpgmaker_dependencies calls get_setting from core.database
    with patch('core.database.get_setting') as mock_setting:
        mock_setting.return_value = "/prefix"
        with patch('subprocess.Popen') as mock_popen:
            result = api.install_rpgmaker_dependencies()

            assert result["success"] is True
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert "winetricks" in args

def test_open_extension_folder_linux(api):
    # Patch shutil.which and subprocess.Popen in core.api (imported there? No, imported inside method)
    # The method does `import subprocess, shutil`.
    # So we must patch `shutil.which` and `subprocess.Popen` where they are used?
    # Since they are imported inside the function, we have to patch the system modules 'shutil' and 'subprocess'

    with patch('shutil.which') as mock_which:
        with patch('subprocess.Popen') as mock_popen:
            mock_which.side_effect = lambda cmd: "/usr/bin/" + cmd if cmd == "xdg-open" else None

            result = api.open_extension_folder()

            assert result["success"] is True
            mock_popen.assert_called_once()
            cmd_list = mock_popen.call_args[0][0]
            assert cmd_list[0] == "/usr/bin/xdg-open"
