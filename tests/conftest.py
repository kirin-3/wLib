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


PAIRED_EXTENSION_TOKEN = "a" * 64


@pytest.fixture(autouse=True)
def _isolated_extension_pairing(tmp_path_factory, monkeypatch):
    """One allowed extension token per test, stored outside the real data folder."""
    import core.extension_pairing as pairing

    # Not tmp_path: several tests assert that their own folder stays empty.
    store = pairing.PairingStore(str(tmp_path_factory.mktemp("pairing")))
    _ = store.check(PAIRED_EXTENSION_TOKEN, "chrome-extension://paired")
    assert store.resolve(store.snapshot()["pending"][0]["id"], True)
    monkeypatch.setattr(pairing, "_store", store)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: takes several seconds (real network loopback)")
    config.addinivalue_line(
        "markers", "schema_fingerprint: keep init_db's skip-when-current check"
    )


@pytest.fixture(autouse=True)
def _init_db_always_migrates(request, monkeypatch):
    """Run every migration on each init_db() call.

    Tests build old databases by dropping tables or writing legacy rows into
    a current one, which a real upgrade only meets with an older fingerprint.
    Tests of the skip itself opt out with @pytest.mark.schema_fingerprint.
    """
    if request.node.get_closest_marker("schema_fingerprint"):
        return
    import itertools

    import core.database

    counter = itertools.count(1)
    monkeypatch.setattr(core.database, "schema_fingerprint", lambda: next(counter))
    # These forced migrations are not real upgrades, so they make no copy.
    monkeypatch.setattr(core.database, "_backup_before_migration", lambda conn: None)
