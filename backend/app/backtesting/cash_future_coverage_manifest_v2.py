"""Deterministic Cash-Future coverage manifest primitives."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class CoverageRange:
    instrument: str
    start_ns: int
    end_ns: int
    expected_points: int
    observed_points: int
    missing_points: int
    complete: bool

@dataclass(frozen=True)
class CoverageManifest:
    source: str
    generated_at_ns: int
    ranges: tuple[CoverageRange, ...]

    @property
    def expected_points(self) -> int:
        return sum(x.expected_points for x in self.ranges)

    @property
    def observed_points(self) -> int:
        return sum(x.observed_points for x in self.ranges)

    @property
    def missing_points(self) -> int:
        return sum(x.missing_points for x in self.ranges)

    @property
    def complete(self) -> bool:
        return self.missing_points == 0 and all(x.complete for x in self.ranges)
