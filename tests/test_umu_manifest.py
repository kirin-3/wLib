# SPDX-License-Identifier: GPL-3.0-or-later
from collections.abc import Callable
from pathlib import Path
from subprocess import STDOUT
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import umu
from core.api import Api
from core.api_mixins import runtimes
from core.database import init_db, update_setting


def test_prepare_can_retry_after_invalid_proton_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = tmp_path / "GE-Proton-test"
    build.mkdir()
    manifest = build / "toolmanifest.vdf"
    _ = manifest.write_bytes(b'"require_tool_appid" "1628350"\n\xff')
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(runtimes, "is_linux", lambda: True)
    monkeypatch.setattr(umu, "locate_umu", lambda: ("/host/umu-run", "system"))
    monkeypatch.setattr(umu, "host_command", lambda command, **_: command)
    popen = Mock(return_value=SimpleNamespace(poll=lambda: 0, returncode=0))
    monkeypatch.setattr(umu, "subprocess", SimpleNamespace(Popen=popen, STDOUT=STDOUT))
    init_db()
    update_setting("proton_path", str(build))
    api = Api()

    def inline_thread(
        *, target: Callable[[str, str, str], None], args: tuple[str, str, str], daemon: bool
    ) -> SimpleNamespace:
        assert daemon
        return SimpleNamespace(start=lambda: target(*args))

    monkeypatch.setattr(umu, "threading", SimpleNamespace(Thread=inline_thread))

    assert api.prepare_umu_runtime()["success"] is True
    failed = api.get_umu_runtime_status()
    assert failed["running"] is False
    assert failed["ready"] is False
    assert failed["error_class"] == "other"
    assert str(manifest) in str(failed["error"])
    assert "UTF-8" in str(failed["error"])
    popen.assert_not_called()

    _ = manifest.write_text('"require_tool_appid" "1628350"', encoding="utf-8")
    assert api.get_umu_runtime_status()["error"] == failed["error"]
    assert api.prepare_umu_runtime()["success"] is True
    retried = api.get_umu_runtime_status()
    assert retried["running"] is False
    assert retried["runtime"] == "steamrt3"
    assert retried["error"] == ""
    popen.assert_called_once()
