# SPDX-License-Identifier: GPL-3.0-or-later
"""Source adapters. Each one writes pages to the catalog as it goes and keeps
its cursor in state.sqlite, so an interrupted run resumes where it stopped."""

from .base import Source, SourceContext
from .dlsite import DlsiteSource
from .f95 import F95Source
from .f95_threads import F95ThreadsSource
from .ryuugames import RyuugamesSource
from .vndb import VndbSource

__all__ = ["STEPS", "Source", "SourceContext", "get_sources"]

# Everything a run can collect, in run order: the app's SOURCES plus
# builder-only steps. f95_threads follows f95 so the browser keeps its proxy.
STEPS: tuple[str, ...] = ("f95", "f95_threads", "dlsite", "ryuugames", "vndb")


def get_sources() -> dict[str, Source]:
    sources: list[Source] = [
        F95Source(),
        F95ThreadsSource(),
        DlsiteSource(),
        RyuugamesSource(),
        VndbSource(),
    ]
    return {source.name: source for source in sources}
