# SPDX-License-Identifier: GPL-3.0-or-later
"""The PyWebView bridge class.

``Api`` only combines the feature mixins in ``core/api_mixins/``. Put a new method in
the mixin for its feature, or in ``ApiBase`` (``core/api_mixins/base.py``) when two or
more mixins use it."""
from __future__ import annotations

from core.api_mixins.downloads import DownloadsMixin
from core.api_mixins.extension import ExtensionMixin
from core.api_mixins.game_tools import GameToolsMixin
from core.api_mixins.launch import LaunchMixin
from core.api_mixins.library import LibraryMixin
from core.api_mixins.rpgm_runtime import RpgmRuntimeMixin
from core.api_mixins.runtimes import RuntimesMixin
from core.api_mixins.saves import SavesMixin
from core.api_mixins.settings import SettingsMixin
from core.api_mixins.sync import SyncMixin
from core.api_mixins.updates import UpdatesMixin
from core.api_mixins.base import ApiBase
from core.app_version import APP_VERSION as APP_VERSION
from core.versions import normalize_version as normalize_version


class Api(
    LibraryMixin,
    SyncMixin,
    ExtensionMixin,
    LaunchMixin,
    UpdatesMixin,
    GameToolsMixin,
    RuntimesMixin,
    RpgmRuntimeMixin,
    DownloadsMixin,
    SavesMixin,
    SettingsMixin,
    ApiBase,
):
    """The object that PyWebView lists on ``window.pywebview.api``."""
