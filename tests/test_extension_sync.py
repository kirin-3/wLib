# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from core.api import Api


def test_sync_retries_unsigned_firefox_update_after_chromium_swap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = Api()
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    bundled_manifest = bundled_dir / "manifest.json"
    installed_dir = tmp_path / "installed"
    chromium_manifest = installed_dir / "chrome" / "manifest.json"
    firefox_xpi = installed_dir / "firefox" / "wLib.xpi"
    monkeypatch.setattr(api, "_get_bundled_extension_dir", lambda: str(bundled_dir))
    monkeypatch.setattr(
        api, "_get_persistent_extension_dir", lambda: str(installed_dir)
    )

    bundled_manifest.write_text('{"version": "1.0.0"}', encoding="utf-8")
    assert api.sync_extension_files()["success"] is True

    bundled_manifest.write_text('{"version": "2.0.0"}', encoding="utf-8")
    with patch.object(
        api, "_write_unsigned_firefox_xpi", side_effect=OSError("interrupted")
    ):
        assert api.sync_extension_files()["success"] is False

    chromium_version = json.loads(chromium_manifest.read_text(encoding="utf-8"))[
        "version"
    ]
    assert chromium_version == "2.0.0"
    with ZipFile(firefox_xpi) as xpi:
        assert json.loads(xpi.read("manifest.json")) == {"version": "1.0.0"}

    result = api.sync_extension_files()

    assert result["success"] is True
    assert result.get("updated") is True
    with ZipFile(firefox_xpi) as xpi:
        assert json.loads(xpi.read("manifest.json")) == {"version": "2.0.0"}
    assert api.sync_extension_files().get("updated") is False
