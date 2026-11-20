# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import main


def test_second_launch_exits_when_focus_times_out_during_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = MagicMock()
    bind = MagicMock(side_effect=[server, None])
    focus = MagicMock(return_value=False)

    class StartupStopped(Exception):
        pass

    def slow_log_setup(_path: str) -> None:
        if setup_log.call_count == 1:
            # The first launch has bound the port but cannot serve requests yet.
            main.main()
        raise StartupStopped

    setup_log = MagicMock(side_effect=slow_log_setup)
    monkeypatch.setattr(main, "APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "restore_ctrl_c", lambda: None)
    monkeypatch.setattr(main, "configure_ssl_certificates", lambda: None)
    monkeypatch.setattr(main, "bind_extension_server", bind)
    monkeypatch.setattr(main, "focus_running_instance", focus)
    monkeypatch.setattr(main, "setup_app_log", setup_log)

    with pytest.raises(StartupStopped):
        main.main()

    focus.assert_called_once_with()
    bind.assert_called_once_with()
    setup_log.assert_called_once_with(str(tmp_path))
