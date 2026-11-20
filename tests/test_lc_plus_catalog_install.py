"""LC Plus detection after a catalog install."""
from core import lewdcorner_plus
from core.api import Api


def test_catalog_install_starts_lc_plus_detection(monkeypatch):
    api = Api()
    for name in ("_fill_lc_threads_from_catalog", "_fill_ratings_from_catalog", "_fill_thread_details_from_catalog"):
        monkeypatch.setattr(api, name, lambda: 0)
    triggers = []
    monkeypatch.setattr(lewdcorner_plus, "start", triggers.append)

    api._on_catalog_installed()

    # "connect" keeps the 24-hour rule, so a catalog install never re-reads a recent tier.
    assert triggers == ["connect"]

