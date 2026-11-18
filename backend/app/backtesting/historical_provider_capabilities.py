"""Explicit provider capability declarations for historical-data resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class HistoricalProviderCapabilities:
    """Declare exactly which native timeframes a provider can supply."""

    source: str
    timeframes: frozenset[str]

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source is required")
        if not self.timeframes or any(not timeframe.strip() for timeframe in self.timeframes):
            raise ValueError("at least one non-empty timeframe is required")

    def supports(self, timeframe: str) -> bool:
        return timeframe in self.timeframes

    def require(self, timeframe: str) -> None:
        if not self.supports(timeframe):
            supported = ", ".join(sorted(self.timeframes))
            raise ValueError(
                f"provider {self.source!r} does not natively support timeframe "
                f"{timeframe!r}; supported: {supported}"
            )


def capabilities(source: str, timeframes: Iterable[str]) -> HistoricalProviderCapabilities:
    """Build an immutable capability declaration from provider metadata."""
    return HistoricalProviderCapabilities(source, frozenset(timeframes))
