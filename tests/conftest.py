# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import sys
import tempfile

import pytest


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# core.database reads WLIB_DATA_DIR once, at import, into DATA_DIR and DB_PATH.
# Set it before any test module imports core, so no import-time path points
# at the real ~/.local/share/wLib.
_SESSION_DATA_DIR = tempfile.mkdtemp(prefix="wlib-test-data-")
os.environ["WLIB_DATA_DIR"] = _SESSION_DATA_DIR


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    """Give each test its own data folder and library database.

    A test that only calls setenv("WLIB_DATA_DIR") does not move DB_PATH,
    because core.database caches it at import. Patch both here.
    """
    import core.database

    # Not tmp_path: several tests assert that their own folder stays empty.
    data_dir = str(tmp_path_factory.mktemp("data"))
    monkeypatch.setenv("WLIB_DATA_DIR", data_dir)
    monkeypatch.setattr(core.database, "DATA_DIR", data_dir)
    monkeypatch.setattr(core.database, "DB_PATH", os.path.join(data_dir, "wlib.db"))


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SESSION_DATA_DIR, ignore_errors=True)


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
