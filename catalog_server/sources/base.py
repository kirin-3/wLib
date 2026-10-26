# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared adapter interface."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..net import Pacer
from ..store import CatalogStore, StateStore


@dataclass
class SourceContext:
    catalog: CatalogStore
    state: StateStore
    pacer: Pacer
    now: Callable[[], str]
    rows: int = 0

    def upsert(self, table: str, key: str, rows: Sequence[Mapping[str, object]]) -> None:
        self.rows += self.catalog.upsert(table, key, rows, self.now())


class Source(Protocol):
    name: str

    def incremental(self, ctx: SourceContext) -> None:
        """Fetch what changed since the last run; no-op before the first load."""
        ...

    def sweep_due(self, ctx: SourceContext) -> bool:
        """Whether this run should advance (or start) the full-listing sweep."""
        ...

    def sweep_step(self, ctx: SourceContext) -> bool:
        """Fetch the next sweep page and move the cursor; True once the sweep is done."""
        ...

    def reset_sweep(self, ctx: SourceContext) -> None:
        """Clear the sweep cursor after a completed sweep."""
        ...

