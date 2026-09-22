"""Immutable identity and provenance for one independent backtest run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .backtest_resolution import BacktestResolution
from .provenance import provenance_hash


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
        for name, value in (("run_id", self.run_id), ("strategy_id", self.strategy_id),
                            ("strategy_version", self.strategy_version), ("instrument", self.instrument)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if isinstance(self.start_ns, bool) or not isinstance(self.start_ns, int):
            raise ValueError("start_ns must be an integer")
        if isinstance(self.end_ns, bool) or not isinstance(self.end_ns, int):
            raise ValueError("end_ns must be an integer")
        if self.start_ns < 0 or self.end_ns < self.start_ns:
            raise ValueError("invalid backtest range")
        if not isinstance(self.resolution, BacktestResolution):
            raise ValueError("resolution metadata is required")
        if self.resolution.start_ns > self.start_ns or self.resolution.end_ns < self.end_ns:
            raise ValueError("resolution coverage does not contain backtest range")
        if not isinstance(self.parameters, Mapping) or not isinstance(self.data_watermarks, Mapping):
            raise ValueError("parameters and data_watermarks must be mappings")
        for key, watermark in self.data_watermarks.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("data watermark keys must be non-empty strings")
            if isinstance(watermark, bool) or not isinstance(watermark, int) or watermark < 0:
                raise ValueError("data watermarks must be non-negative integers")

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
            "parameters": dict(self.parameters),
            "strategy_config_hash": provenance_hash(dict(self.parameters)),
            "data_watermarks": dict(self.data_watermarks),
        }
