# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys

import pytest


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


@pytest.fixture(autouse=True)
def _no_catalog_startup_check(monkeypatch):
    """Api() would otherwise start a real catalog download on every construction."""
    from core.catalog import CatalogClient

    monkeypatch.setattr(CatalogClient, "start_background", lambda self, force=False: False)
