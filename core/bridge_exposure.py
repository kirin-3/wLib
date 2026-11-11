# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep names off the PyWebView bridge listing (``window.pywebview.api``).

PyWebView 6.2.1 skips any attribute whose ``_serializable`` attribute is
``False`` when it builds the listing. It uses the same flag for its own
``Event``, ``State`` and ``DOM`` objects. ``tests/test_js_api_surface.py``
pins the listing, so a PyWebView upgrade that changes this fails a test.

This hides names from the listing only. PyWebView resolves a called name
with ``getattr`` and has no allow list, so page JavaScript can still call a
hidden name through the bridge. Only the app's own UI runs JavaScript in the
window.
"""

from collections.abc import Callable


def hide_from_js[T](obj: T) -> T:
    """Flag a helper object so the bridge does not list its methods. Returns ``obj``."""
    setattr(obj, "_serializable", False)
    return obj


def python_only[F: Callable[..., object]](func: F) -> F:
    """Keep a method that only Python calls off the bridge listing.

    Put it outermost if the method has other decorators.
    """
    setattr(func, "_serializable", False)
    return func
