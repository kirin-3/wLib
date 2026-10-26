# SPDX-License-Identifier: GPL-3.0-or-later
"""Source adapters. Each one writes pages to the catalog as it goes and keeps
its cursor in state.sqlite, so an interrupted run resumes where it stopped."""

from .base import Source, SourceContext
from .dlsite import DlsiteSource
from .f95 import F95Source
from .ryuugames import RyuugamesSource
from .vndb import VndbSource

__all__ = ["Source", "SourceContext", "get_sources"]


def get_sources() -> dict[str, Source]:
    sources: list[Source] = [F95Source(), DlsiteSource(), RyuugamesSource(), VndbSource()]
    return {source.name: source for source in sources}
