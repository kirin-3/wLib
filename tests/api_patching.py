# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch a name that the ``core.api_mixins`` modules import."""
import sys

import pytest

MIXIN_PACKAGE = "core.api_mixins"


def patch_api_name(monkeypatch: pytest.MonkeyPatch, name: str, value: object) -> None:
    """Replace ``name`` in every loaded ``core.api_mixins`` module that has it.

    One patch then reaches the whole ``Api``, as one patch on ``core.api`` did before
    the split. Raise ``AttributeError`` if no module has the name.
    """
    import core.api  # noqa: F401  (loads every mixin module)

    patched = False
    for module_name, module in list(sys.modules.items()):
        if module is None:
            continue
        if module_name != MIXIN_PACKAGE and not module_name.startswith(MIXIN_PACKAGE + "."):
            continue
        if name in vars(module):
            monkeypatch.setattr(module, name, value)
            patched = True
    if not patched:
        raise AttributeError(f"no {MIXIN_PACKAGE} module has the name {name!r}")
