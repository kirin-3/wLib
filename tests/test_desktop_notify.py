# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
from unittest.mock import MagicMock

import pytest

from core import desktop_notify


@pytest.mark.parametrize(
    "updates,completed,expected",
    [
        ([], [], None),
        (["Alpha"], [], ("1 game update", "Alpha")),
        (["Alpha", "Beta", "Gamma"], [], ("3 game updates", "Alpha, Beta and 1 more")),
        (
            ["Alpha", "Beta"],
            ["Delta"],
            ("2 game updates, 1 wishlist game completed", "Alpha, Beta and 1 more"),
        ),
        ([], ["Delta", "Echo"], ("2 wishlist games completed", "Delta, Echo")),
    ],
)
def test_summary(updates, completed, expected):
    assert desktop_notify.build_update_summary(updates, completed) == expected


@pytest.mark.parametrize(
    "failure", [None, OSError("missing"), subprocess.TimeoutExpired("notify-send", 5)]
)
def test_sender_fallback_and_silent_failure(monkeypatch, failure):
    monkeypatch.setattr(desktop_notify, "host_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(desktop_notify.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(desktop_notify, "host_command", lambda cmd: cmd)
    run = MagicMock(
        side_effect=[
            failure or subprocess.CompletedProcess([], 1),
            OSError("bus missing"),
        ]
    )
    monkeypatch.setattr(desktop_notify.subprocess, "run", run)
    desktop_notify._send('Title "quoted"', "Alpha <Beta>")
    assert run.call_count == 2
    assert run.call_args_list[0].args[0][0].endswith("notify-send")
    assert run.call_args_list[1].args[0][0].endswith("gdbus")
    gdbus_args = run.call_args_list[1].args[0]
    assert gdbus_args.index("--") < gdbus_args.index("wLib")
    assert gdbus_args[-1] == "-1"
    assert all(call.kwargs["timeout"] == 5 for call in run.call_args_list)


def test_notify_send_success_and_non_linux(monkeypatch):
    monkeypatch.setattr(desktop_notify, "host_which", lambda _: "/bin/notify-send")
    monkeypatch.setattr(desktop_notify, "host_command", lambda cmd: cmd)
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(desktop_notify.subprocess, "run", run)
    desktop_notify._send("Title", "Alpha")
    run.assert_called_once()
    monkeypatch.setattr(desktop_notify.sys, "platform", "win32")
    thread = MagicMock()
    monkeypatch.setattr(desktop_notify.threading, "Thread", thread)
    desktop_notify.send_notification("Title", "Alpha")
    thread.assert_not_called()
