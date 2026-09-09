"""Immutable identity and provenance for one independent backtest run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .backtest_resolution import BacktestResolution


@dataclass(frozen=True)
class BacktestRunSpec:
    """Self-contained configuration/provenance for a single backtest."""

    run_id: str
    strategy_id: str
    strategy_version: str
    instrument: str
    start_ns: int
    end_ns: int
    resolution: BacktestResolution
    parameters: Mapping[str, object] = field(default_factory=dict)
    data_watermarks: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("strategy identity is required")
        if not self.instrument.strip():
            raise ValueError("instrument is required")
        if self.start_ns < 0 or self.end_ns < self.start_ns:
            raise ValueError("invalid backtest range")
        if self.resolution.start_ns > self.start_ns or self.resolution.end_ns < self.end_ns:
            raise ValueError("resolution coverage does not contain backtest range")

    @property
    def provenance(self) -> dict[str, object]:
        """Stable, serializable audit metadata for UI/results/replay."""
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "instrument": self.instrument,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "resolution": self.resolution.resolution,
            "source": self.resolution.source,
            "data_watermarks": dict(self.data_watermarks),
        }
