# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin the names that PyWebView lists on ``window.pywebview.api``.

Update the fixture on purpose with ``WLIB_UPDATE_SNAPSHOT=1 pytest tests/test_js_api_surface.py``.
"""
import inspect
import json
import os
from pathlib import Path

from core.api import Api


FIXTURE = Path(__file__).parent / "fixtures" / "js_api_surface.json"


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
