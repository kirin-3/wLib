# SPDX-License-Identifier: GPL-3.0-or-later
import sys
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


posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="fcntl locks are POSIX only"
)


@posix_only
@pytest.mark.parametrize("code", ["ENOLCK", "EOPNOTSUPP", "ENOSYS"])
def test_data_folder_without_lock_support_still_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    import errno
    import fcntl

    def no_locks(_fd: int, _op: int) -> None:
        raise OSError(getattr(errno, code), "locks not supported")

    monkeypatch.setattr(fcntl, "flock", no_locks)
    lock = main.acquire_instance_lock(str(tmp_path))
    assert lock is not None
    lock.close()


@posix_only
def test_data_folder_held_by_another_copy_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import fcntl

    def held(_fd: int, _op: int) -> None:
        raise BlockingIOError(errno.EWOULDBLOCK, "held")

    monkeypatch.setattr(fcntl, "flock", held)
    assert main.acquire_instance_lock(str(tmp_path)) is None
