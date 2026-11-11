# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

pytest.importorskip("core.api_mixins")

from api_patching import patch_api_name  # noqa: E402


def test_unknown_name_raises(monkeypatch):
    with pytest.raises(AttributeError):
        patch_api_name(monkeypatch, "no_such_name_in_any_mixin", None)
