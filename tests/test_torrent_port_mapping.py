# SPDX-License-Identifier: GPL-3.0-or-later
"""Saving port mapping reaches a running torrent helper."""

import pytest

from core.downloads import torrent_settings, torrents
from core.downloads.torrent_helper import Engine
from core.downloads.torrents import TorrentSupervisor

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _flags(engine: Engine) -> tuple[bool, bool]:
    settings = engine._session.get_settings()
    return bool(settings["enable_upnp"]), bool(settings["enable_natpmp"])


def test_set_limits_turns_port_mapping_on_and_off_in_a_running_session():
    engine = Engine(lambda event: None, {"port_mapping": False})
    assert _flags(engine) == (False, False)
    engine.handle(
        {"cmd": "set_limits", "download_kib": 0, "upload_kib": 0, "port_mapping": True}
    )
    assert _flags(engine) == (True, True)
    engine.handle(
        {"cmd": "set_limits", "download_kib": 0, "upload_kib": 0, "port_mapping": False}
    )
    assert _flags(engine) == (False, False)


def test_set_limits_without_port_mapping_keeps_it():
    engine = Engine(lambda event: None, {"port_mapping": True})
    engine.handle({"cmd": "set_limits", "download_kib": 10, "upload_kib": 5})
    assert _flags(engine) == (True, True)
    assert engine._session.get_settings()["download_rate_limit"] == 10 * 1024


def test_apply_limits_sends_the_saved_port_mapping(monkeypatch):
    sent: list[dict[str, object]] = []
    sup = TorrentSupervisor(command=lambda: (["false"], {}), options=lambda: {})
    monkeypatch.setattr(sup, "_send", lambda command: sent.append(command) or True)
    monkeypatch.setattr(torrent_settings, "limits_kib", lambda: (0, 7))
    monkeypatch.setattr(torrent_settings, "port_mapping", lambda: True)
    assert torrents.torrent_settings is torrent_settings
    sup.apply_limits()
    assert sent == [
        {"cmd": "set_limits", "download_kib": 0, "upload_kib": 7, "port_mapping": True}
    ]
