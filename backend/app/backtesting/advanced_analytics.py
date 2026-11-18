"""Phase 7-8 deterministic analytics for opportunities, audit replay and robustness."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from random import Random
from statistics import mean, pstdev
from typing import Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class OpportunityPoint:
    timestamp_ns: int
    spread: float
    executable_spread: float
    entry: float | None = None
    exit: float | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if self.spread < 0 or self.executable_spread < 0:
            raise ValueError("spread values cannot be negative")


@dataclass(frozen=True)
class OpportunityWindow:
    start_ns: int
    end_ns: int
    peak_spread: float
    peak_executable_spread: float
    duration_ns: int

    def __post_init__(self) -> None:
        if self.end_ns < self.start_ns:
            raise ValueError("opportunity window must be ordered")


@dataclass(frozen=True)
class OpportunityAnalytics:
    maximum: OpportunityPoint | None
    executable_maximum: OpportunityPoint | None
    windows: tuple[OpportunityWindow, ...]

    @classmethod
    def analyze(
        cls,
        points: Iterable[OpportunityPoint],
        *,
        minimum_executable_spread: float = 0.0,
    ) -> "OpportunityAnalytics":
        ordered = tuple(sorted(points, key=lambda p: p.timestamp_ns))
        if minimum_executable_spread < 0:
            raise ValueError("minimum_executable_spread cannot be negative")
        maximum = max(ordered, key=lambda p: (p.spread, -p.timestamp_ns), default=None)
        executable = max(
            (p for p in ordered if p.executable_spread >= minimum_executable_spread),
            key=lambda p: (p.executable_spread, -p.timestamp_ns),
            default=None,
        )
        windows: list[OpportunityWindow] = []
        active: list[OpportunityPoint] = []
        for point in ordered:
            if point.executable_spread >= minimum_executable_spread:
                active.append(point)
                continue
            if active:
                windows.append(cls._window(active))
                active = []
        if active:
            windows.append(cls._window(active))
        return cls(maximum, executable, tuple(windows))

    @staticmethod
    def _window(points: Sequence[OpportunityPoint]) -> OpportunityWindow:
        return OpportunityWindow(
            start_ns=points[0].timestamp_ns,
            end_ns=points[-1].timestamp_ns,
            peak_spread=max(p.spread for p in points),
            peak_executable_spread=max(p.executable_spread for p in points),
            duration_ns=points[-1].timestamp_ns - points[0].timestamp_ns,
        )


@dataclass(frozen=True)
class MfeMae:
    entry_price: float
    maximum_favorable_excursion: float
    maximum_adverse_excursion: float

    @classmethod
    def from_path(cls, entry_price: float, prices: Iterable[float], *, direction: int = 1) -> "MfeMae":
        if entry_price <= 0 or direction not in {-1, 1}:
            raise ValueError("invalid MFE/MAE inputs")
        path = tuple(float(p) for p in prices)
        if not path:
            return cls(entry_price, 0.0, 0.0)
        returns = tuple(direction * (p - entry_price) / entry_price for p in path)
        return cls(entry_price, max(returns), min(returns))


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    timestamp_ns: int
    event_type: str
    instrument: str
    payload: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.timestamp_ns < 0:
            raise ValueError("audit sequence/timestamp must be non-negative")


class AuditReplay:
    """Deterministic audit trail: only supplied source events are replayed."""

    @staticmethod
    def ordered(events: Iterable[AuditEvent]) -> tuple[AuditEvent, ...]:
        result = tuple(sorted(events, key=lambda e: (e.timestamp_ns, e.sequence)))
        sequences = [e.sequence for e in result]
        if len(sequences) != len(set(sequences)):
            raise ValueError("audit event sequence must be unique")
        return result

    @classmethod
    def explain(cls, events: Iterable[AuditEvent], *, timestamp_ns: int) -> tuple[AuditEvent, ...]:
        return tuple(e for e in cls.ordered(events) if e.timestamp_ns <= timestamp_ns)


@dataclass(frozen=True)
class WalkForwardSplit:
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class WalkForwardConfig:
    train_size: int
    validation_size: int
    test_size: int
    step: int

    def __post_init__(self) -> None:
        if min(self.train_size, self.validation_size, self.test_size, self.step) <= 0:
            raise ValueError("walk-forward sizes and step must be positive")


def walk_forward_splits(length: int, config: WalkForwardConfig) -> tuple[WalkForwardSplit, ...]:
    if length < 0:
        raise ValueError("length cannot be negative")
    result = []
    start = 0
    required = config.train_size + config.validation_size + config.test_size
    while start + required <= length:
        train_end = start + config.train_size
        validation_end = train_end + config.validation_size
        test_end = validation_end + config.test_size
        result.append(WalkForwardSplit(start, train_end, train_end, validation_end, validation_end, test_end))
        start += config.step
    return tuple(result)


@dataclass(frozen=True)
class RobustnessPoint:
    parameter: tuple[str, float]
    score: float


@dataclass(frozen=True)
class MonteCarloSummary:
    runs: int
    mean: float
    stdev: float
    p05: float
    p50: float
    p95: float


def robustness_sweep(
    parameter: str,
    values: Iterable[float],
    evaluator: Callable[[float], float],
) -> tuple[RobustnessPoint, ...]:
    if not parameter.strip():
        raise ValueError("parameter is required")
    return tuple(RobustnessPoint((parameter, float(v)), float(evaluator(float(v)))) for v in values)


def monte_carlo(
    outcomes: Sequence[float],
    *,
    runs: int = 1000,
    seed: int = 0,
) -> MonteCarloSummary:
    if not outcomes or runs <= 0:
        raise ValueError("outcomes and positive runs are required")
    rng = Random(seed)
    samples = [sum(rng.choice(outcomes) for _ in outcomes) for _ in range(runs)]
    ordered = sorted(samples)

    def percentile(p: float) -> float:
        index = min(len(ordered) - 1, int((len(ordered) - 1) * p))
        return float(ordered[index])

    return MonteCarloSummary(runs, mean(samples), pstdev(samples), percentile(0.05), percentile(0.50), percentile(0.95))


@dataclass(frozen=True)
class StressScenario:
    name: str
    slippage_multiplier: float = 1.0
    latency_multiplier: float = 1.0
    liquidity_multiplier: float = 1.0
    missing_data_fraction: float = 0.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("scenario name is required")
        if min(self.slippage_multiplier, self.latency_multiplier, self.liquidity_multiplier) < 0:
            raise ValueError("stress multipliers cannot be negative")
        if not 0 <= self.missing_data_fraction <= 1:
            raise ValueError("missing_data_fraction must be between 0 and 1")


def apply_stress(base_value: float, scenario: StressScenario) -> float:
    if base_value < 0:
        raise ValueError("base_value cannot be negative")
    penalty = scenario.slippage_multiplier * scenario.latency_multiplier * scenario.liquidity_multiplier
    return base_value * max(0.0, 1.0 - scenario.missing_data_fraction) / max(1.0, penalty)
