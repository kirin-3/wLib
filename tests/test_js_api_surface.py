# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin the names that PyWebView lists on ``window.pywebview.api``.

Update the fixture on purpose with ``WLIB_UPDATE_SNAPSHOT=1 pytest tests/test_js_api_surface.py``.
"""
import inspect
import json
import os
import re
from pathlib import Path

import pytest

from core.api import Api
from core.database import init_db


FIXTURE = Path(__file__).parent / "fixtures" / "js_api_surface.json"


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    """Api() reads the database and prunes attachments, so keep it out of the real data folder."""
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    init_db()


def walk(obj: object) -> dict[str, list[str]]:
    """Copy of ``get_functions`` in PyWebView 6.2.1 ``webview/util.py`` (inside ``inject_pywebview``).

    Check this copy when the ``pywebview`` version in ``requirements*.txt`` changes.
    """
    seen: list[int] = []
    functions: dict[str, list[str]] = {}

    def get_functions(obj: object, base_name: str = "") -> None:
        if id(obj) in seen:
            return
        seen.append(id(obj))
        for name in dir(obj):
            full_name = f"{base_name}.{name}" if base_name else name
            if name.startswith("_"):
                continue
            try:
                attr = getattr(obj, name)
                if not getattr(attr, "_serializable", True):
                    continue
                if inspect.ismethod(attr) or inspect.isfunction(attr):
                    functions[full_name] = list(inspect.getfullargspec(attr).args)[1:]
                elif inspect.isclass(attr) or (
                    isinstance(attr, object) and not callable(attr) and hasattr(attr, "__module__")
                ):
                    get_functions(attr, full_name)
            except Exception:
                continue

    get_functions(obj)
    return functions


def assert_matches_snapshot(actual: dict[str, list[str]]) -> None:
    actual = dict(sorted(actual.items()))
    if os.environ.get("WLIB_UPDATE_SNAPSHOT") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
        return
    expected: dict[str, list[str]] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if actual == expected:
        return
    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    changed = sorted(
        f"{name}: {expected[name]} -> {actual[name]}"
        for name in set(actual) & set(expected)
        if actual[name] != expected[name]
    )
    lines = ["The bridge listing does not match tests/fixtures/js_api_surface.json."]
    if added:
        lines.append("Added: " + ", ".join(added))
    if removed:
        lines.append("Removed: " + ", ".join(removed))
    if changed:
        lines.append("Changed: " + "; ".join(changed))
    lines.append("If this is on purpose, run with WLIB_UPDATE_SNAPSHOT=1.")
    raise AssertionError("\n".join(lines))


def test_bridge_listing_matches_snapshot():
    assert_matches_snapshot(walk(Api()))


HELPER_ATTRS = (
    "window",
    "steam_sync",
    "scraper",
    "launcher",
    "library_sync",
    "cover_cache",
    "cover_cards",
    "catalog",
)

PYTHON_ONLY_METHODS = (
    "set_window",
    "reserve_runner_launch",
    "release_runner_launch",
    "sync_extension_files",
    "set_startup_extension_sync_status",
    "complete_dropbox_callback",
    "complete_gdrive_callback",
)

API_TS = Path(__file__).resolve().parent.parent / "ui" / "src" / "services" / "api.ts"
INVOKE_RE = re.compile(r'\binvoke(?:LibraryChange)?\s*(?:<[^()"]*?>)?\s*\(\s*"([a-z0-9_]+)"')


def test_bridge_lists_no_nested_names():
    nested = sorted(name for name in walk(Api()) if "." in name)
    assert nested == []


class _StubWindow:
    def evaluate_js(self, script: str) -> None:
        pass

    def destroy(self) -> None:
        pass


class _StubSteamSync:
    def __init__(self, icon: object, on_conversions: object = None) -> None:
        self.icon = icon

    def schedule(self) -> None:
        pass

    def conversions(self) -> list[str]:
        return []


def test_runtime_helpers_are_not_listed(monkeypatch):
    import core.api
    import core.steam_sync

    monkeypatch.setattr(core.api, "is_linux", lambda: True)
    monkeypatch.setattr(core.steam_sync, "SteamSync", _StubSteamSync)
    api = Api()
    api.set_window(_StubWindow())  # pyright: ignore[reportArgumentType]
    api._start_steam_sync("icon.png")  # pyright: ignore[reportPrivateUsage]
    assert isinstance(api.window, _StubWindow)
    assert isinstance(api.steam_sync, _StubSteamSync)

    listed = walk(api)
    leaked = sorted(
        name for name in listed if name.split(".", 1)[0] in HELPER_ATTRS
    )
    assert leaked == []


def test_bridge_listing_equals_ui_calls():
    called = set(INVOKE_RE.findall(API_TS.read_text(encoding="utf-8")))
    listed = set(walk(Api()))
    assert sorted(called - listed) == [], "api.ts calls names that the bridge does not list"
    assert sorted(listed - called) == [], "the bridge lists names that api.ts does not call"


def test_python_only_methods_stay_callable():
    api = Api()
    listed = walk(api)
    for name in PYTHON_ONLY_METHODS:
        assert callable(getattr(api, name)), name
        assert name not in listed, name
