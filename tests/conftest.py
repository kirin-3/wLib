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


@pytest.fixture(autouse=True)
def _no_f95_rate_limit_cooldown(monkeypatch):
    """A 429 in one test must not hold back F95 requests in the next."""
    import core.scraper

    monkeypatch.setattr(core.scraper, "_f95_cooldown_until", 0.0)
