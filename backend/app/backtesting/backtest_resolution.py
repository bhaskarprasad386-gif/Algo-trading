"""Select the finest genuine historical resolution available to one backtest.

The selector never synthesizes a finer timeframe from coarser data. A backtest
asks its data provider for complete coverage in preference order and receives
one explicit resolution for that run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

Resolution = Literal["ms", "s", "m", "h"]
_RESOLUTION_RANK: dict[str, int] = {"ms": 0, "s": 1, "m": 2, "h": 3}


@dataclass(frozen=True)
class ResolutionCoverage:
    """Coverage result for one candidate resolution."""
    resolution: Resolution
    complete: bool
    start_ns: int
    end_ns: int
    source: str

    def __post_init__(self) -> None:
        if self.resolution not in _RESOLUTION_RANK:
            raise ValueError(f"unsupported resolution: {self.resolution}")
        if not isinstance(self.complete, bool):
            raise ValueError("complete must be boolean")
        if self.start_ns < 0 or self.end_ns < 0:
            raise ValueError("coverage timestamps must be non-negative")
        if self.end_ns < self.start_ns:
            raise ValueError("coverage end must be >= start")
        if not str(self.source).strip():
            raise ValueError("coverage source is required")


@dataclass(frozen=True)
class BacktestResolution:
    """Immutable resolution decision attached to one independent run."""
    resolution: Resolution
    source: str
    start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        if self.resolution not in _RESOLUTION_RANK:
            raise ValueError(f"unsupported resolution: {self.resolution}")
        if not str(self.source).strip():
            raise ValueError("resolution source is required")
        if self.start_ns < 0 or self.end_ns < self.start_ns:
            raise ValueError("invalid resolution coverage window")


def choose_finest_genuine_resolution(coverage: Iterable[ResolutionCoverage]) -> BacktestResolution:
    """Choose ms, then s, then m, then h, but only when coverage is complete."""
    candidates = [item for item in coverage if item.complete]
    if not candidates:
        raise ValueError("no complete genuine resolution is available")
    chosen = min(candidates, key=lambda item: _RESOLUTION_RANK[item.resolution])
    return BacktestResolution(chosen.resolution, chosen.source, chosen.start_ns, chosen.end_ns)
