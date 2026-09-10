"""Deterministic Cash-Future historical coverage manifest helpers.

The manifest is intentionally provider-agnostic: it describes what coverage is
expected for a resolved contract/session and what the canonical catalog contains.
No synthetic timestamps are created outside declared sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, Mapping


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
    ranges: tuple[CoverageRange, ...] = field(default_factory=tuple)

    @property
    def expected_points(self) -> int:
        return sum(item.expected_points for item in self.ranges)

    @property
    def observed_points(self) -> int:
        return sum(item.observed_points for item in self.ranges)

    @property
    def missing_points(self) -> int:
        return sum(item.missing_points for item in self.ranges)

    @property
    def complete(self) -> bool:
        return self.missing_points == 0 and all(item.complete for item in self.ranges)


def _as_ns(value: int | datetime) -> int:
    if isinstance(value, int):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


def expected_timestamp_count(start_ns: int, end_ns: int, interval_ns: int) -> int:
    """Count inclusive canonical timestamps in a bounded range."""
    if interval_ns <= 0:
        raise ValueError("interval_ns must be positive")
    if end_ns < start_ns:
        raise ValueError("end_ns must be >= start_ns")
    return ((end_ns - start_ns) // interval_ns) + 1


def build_coverage_manifest(
    *,
    source: str,
    ranges: Iterable[CoverageRange],
    generated_at: int | datetime | None = None,
) -> CoverageManifest:
    """Build a stable manifest sorted by instrument and time bounds."""
    if not source:
        raise ValueError("source must not be empty")
    ordered = tuple(sorted(ranges, key=lambda item: (item.instrument, item.start_ns, item.end_ns)))
    for item in ordered:
        if item.start_ns > item.end_ns:
            raise ValueError("coverage range start_ns must be <= end_ns")
        if min(item.expected_points, item.observed_points, item.missing_points) < 0:
            raise ValueError("coverage counts must not be negative")
        if item.observed_points + item.missing_points != item.expected_points:
            raise ValueError("observed_points + missing_points must equal expected_points")
        if item.complete != (item.missing_points == 0):
            raise ValueError("complete must match missing_points == 0")
    stamp = _as_ns(generated_at) if generated_at is not None else int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    return CoverageManifest(source=source, generated_at_ns=stamp, ranges=ordered)


def manifest_from_catalog(
    *,
    source: str,
    instrument: str,
    start_ns: int,
    end_ns: int,
    interval_ns: int,
    observed_timestamps: Iterable[int],
    generated_at: int | datetime | None = None,
) -> CoverageManifest:
    """Create a manifest entry from canonical observed timestamps.

    Timestamps are restricted to the requested bounded range. Missing points are
    therefore explicit and can be handed directly to the gap planner.
    """
    expected = expected_timestamp_count(start_ns, end_ns, interval_ns)
    observed = {int(ts) for ts in observed_timestamps if start_ns <= int(ts) <= end_ns}
    observed_count = len(observed)
    missing = expected - observed_count
    entry = CoverageRange(
        instrument=instrument,
        start_ns=start_ns,
        end_ns=end_ns,
        expected_points=expected,
        observed_points=observed_count,
        missing_points=missing,
        complete=missing == 0,
    )
    return build_coverage_manifest(source=source, ranges=(entry,), generated_at=generated_at)


def manifest_summary(manifest: CoverageManifest) -> Mapping[str, object]:
    """Return a serialization-friendly deterministic summary."""
    return {
        "source": manifest.source,
        "generated_at_ns": manifest.generated_at_ns,
        "ranges": len(manifest.ranges),
        "expected_points": manifest.expected_points,
        "observed_points": manifest.observed_points,
        "missing_points": manifest.missing_points,
        "complete": manifest.complete,
    }


__all__ = [
    "CoverageRange",
    "CoverageManifest",
    "expected_timestamp_count",
    "build_coverage_manifest",
    "manifest_from_catalog",
    "manifest_summary",
]
