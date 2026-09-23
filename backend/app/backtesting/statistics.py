"""Accounting and time-series statistics for universal backtests.

Statistics are calculated from marked equity, not from the number of trades.
This keeps realized P&L, unrealized P&L and performance ratios semantically
separate and works for irregular/millisecond event spacing.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class EquityPoint:
    timestamp_ns: int
    equity: float
    realized_pnl: float
    unrealized_pnl: float

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("equity timestamp_ns must be a non-negative integer")
        for name, value in (("equity", self.equity), ("realized_pnl", self.realized_pnl), ("unrealized_pnl", self.unrealized_pnl)):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class BacktestStatistics:
    net_pnl: float
    total_return: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    max_drawdown: float
    cagr: float | None


class StreamingStatisticsAccumulator:
    """O(1)-memory accumulator preserving batch statistics semantics."""

    def __init__(self, initial_capital: float) -> None:
        if not isfinite(float(initial_capital)) or initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        self.initial_capital = float(initial_capital)
        self._count = 0
        self._return_count = 0
        self._sum_returns = 0.0
        self._sum_return_squares = 0.0
        self._sum_downside_squares = 0.0
        self._sum_intervals_years = 0.0
        self._previous_equity = self.initial_capital
        self._previous_timestamp: int | None = None
        self._first_timestamp: int | None = None
        self._last_timestamp: int | None = None
        self._final_equity: float | None = None
        self._peak = self.initial_capital
        self._max_drawdown = 0.0

    def update(self, point: EquityPoint) -> None:
        if not isinstance(point, EquityPoint):
            raise TypeError("point must be an EquityPoint")
        if self._last_timestamp is not None and point.timestamp_ns < self._last_timestamp:
            raise ValueError("equity timestamps must be non-decreasing")

        if self._first_timestamp is None:
            self._first_timestamp = point.timestamp_ns

        if self._previous_timestamp is not None and point.timestamp_ns > self._previous_timestamp and self._previous_equity > 0:
            elapsed_years = (
                point.timestamp_ns - self._previous_timestamp
            ) / (365.25 * 24 * 60 * 60 * 1_000_000_000)
            value = point.equity / self._previous_equity - 1.0
            if elapsed_years > 0 and isfinite(value):
                return_square = value * value
                downside = min(value, 0.0)
                downside_square = downside * downside
                if isfinite(return_square) and isfinite(downside_square):
                    self._return_count += 1
                    self._sum_returns += value
                    self._sum_return_squares += return_square
                    self._sum_downside_squares += downside_square
                    self._sum_intervals_years += elapsed_years

        self._previous_equity = point.equity
        self._previous_timestamp = point.timestamp_ns
        self._last_timestamp = point.timestamp_ns
        self._final_equity = point.equity
        self._count += 1
        self._peak = max(self._peak, point.equity)
        if self._peak > 0:
            self._max_drawdown = max(
                self._max_drawdown,
                (self._peak - point.equity) / self._peak,
            )

    def export_state(self) -> Mapping[str, Any]:
        """Export the complete O(1) running state for durable checkpointing."""
        return {
            "initial_capital": self.initial_capital,
            "count": self._count,
            "return_count": self._return_count,
            "sum_returns": self._sum_returns,
            "sum_return_squares": self._sum_return_squares,
            "sum_downside_squares": self._sum_downside_squares,
            "sum_intervals_years": self._sum_intervals_years,
            "previous_equity": self._previous_equity,
            "previous_timestamp": self._previous_timestamp,
            "first_timestamp": self._first_timestamp,
            "last_timestamp": self._last_timestamp,
            "final_equity": self._final_equity,
            "peak": self._peak,
            "max_drawdown": self._max_drawdown,
        }

    def restore_state(self, state: Mapping[str, Any]) -> None:
        """Restore a previously exported checkpoint after strict validation."""
        if not isinstance(state, Mapping):
            raise ValueError("invalid statistics checkpoint")
        required = (
            "initial_capital", "count", "return_count", "sum_returns",
            "sum_return_squares", "sum_downside_squares", "sum_intervals_years",
            "previous_equity", "previous_timestamp", "first_timestamp",
            "last_timestamp", "final_equity", "peak", "max_drawdown",
        )
        if any(key not in state for key in required):
            raise ValueError("invalid statistics checkpoint")
        try:
            initial_capital = state["initial_capital"]
            if isinstance(initial_capital, bool) or not isinstance(initial_capital, (int, float)) or not isfinite(float(initial_capital)) or initial_capital <= 0:
                raise ValueError("invalid statistics checkpoint")
            integer_fields = ("count", "return_count")
            for key in integer_fields:
                value = state[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("invalid statistics checkpoint")
            non_negative_floats = (
                "sum_return_squares", "sum_downside_squares", "sum_intervals_years", "max_drawdown"
            )
            for key in non_negative_floats:
                value = state[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)) or float(value) < 0:
                    raise ValueError("invalid statistics checkpoint")
            finite_floats = ("sum_returns", "previous_equity", "peak")
            for key in finite_floats:
                value = state[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                    raise ValueError("invalid statistics checkpoint")
            for key in ("previous_timestamp", "first_timestamp", "last_timestamp"):
                value = state[key]
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                    raise ValueError("invalid statistics checkpoint")
            final_equity = state["final_equity"]
            if final_equity is not None and (isinstance(final_equity, bool) or not isinstance(final_equity, (int, float)) or not isfinite(float(final_equity))):
                raise ValueError("invalid statistics checkpoint")
            count = int(state["count"])
            return_count = int(state["return_count"])
            previous_timestamp = state["previous_timestamp"]
            first_timestamp = state["first_timestamp"]
            last_timestamp = state["last_timestamp"]
            if count == 0:
                if any(value is not None for value in (previous_timestamp, first_timestamp, last_timestamp, final_equity)):
                    raise ValueError("invalid statistics checkpoint")
            else:
                if previous_timestamp is None or first_timestamp is None or last_timestamp is None or final_equity is None:
                    raise ValueError("invalid statistics checkpoint")
                if first_timestamp > last_timestamp or previous_timestamp != last_timestamp:
                    raise ValueError("invalid statistics checkpoint")
            if return_count > max(0, count - 1):
                raise ValueError("invalid statistics checkpoint")
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid statistics checkpoint")
        self.initial_capital = float(initial_capital)
        self._count = count
        self._return_count = return_count
        self._sum_returns = float(state["sum_returns"])
        self._sum_return_squares = float(state["sum_return_squares"])
        self._sum_downside_squares = float(state["sum_downside_squares"])
        self._sum_intervals_years = float(state["sum_intervals_years"])
        self._previous_equity = float(state["previous_equity"])
        self._previous_timestamp = previous_timestamp
        self._first_timestamp = first_timestamp
        self._last_timestamp = last_timestamp
        self._final_equity = None if final_equity is None else float(final_equity)
        self._peak = float(state["peak"])
        self._max_drawdown = float(state["max_drawdown"])

    def finalize(self) -> BacktestStatistics:
        if self._count == 0:
            return BacktestStatistics(0.0, 0.0, None, None, 0.0, None)

        final_equity = self._final_equity
        assert final_equity is not None
        net_pnl = final_equity - self.initial_capital
        total_return = net_pnl / self.initial_capital

        sharpe = self._annualized_ratio(downside_only=False)
        sortino = self._annualized_ratio(downside_only=True)

        assert self._first_timestamp is not None
        assert self._last_timestamp is not None
        elapsed_years = (
            self._last_timestamp - self._first_timestamp
        ) / (365.25 * 24 * 60 * 60 * 1_000_000_000)
        if elapsed_years > 0 and final_equity > 0:
            try:
                cagr = (final_equity / self.initial_capital) ** (1.0 / elapsed_years) - 1.0
            except OverflowError:
                cagr = None
        else:
            cagr = None

        return BacktestStatistics(
            net_pnl,
            total_return,
            sharpe,
            sortino,
            self._max_drawdown,
            cagr,
        )

    def _annualized_ratio(self, *, downside_only: bool) -> float | None:
        if self._return_count < 2 or self._sum_intervals_years <= 0:
            return None
        mean = self._sum_returns / self._return_count
        if downside_only:
            denominator = sqrt(self._sum_downside_squares / self._return_count)
        else:
            variance = (
                self._sum_return_squares / self._return_count
            ) - mean * mean
            denominator = sqrt(max(variance, 0.0))
        if denominator <= 0:
            return None
        average_interval_years = self._sum_intervals_years / self._return_count
        return mean / denominator * sqrt(1.0 / average_interval_years)


def calculate_statistics(
    points: Iterable[EquityPoint],
    initial_capital: float,
) -> BacktestStatistics:
    accumulator = StreamingStatisticsAccumulator(initial_capital)
    for point in points:
        accumulator.update(point)
    return accumulator.finalize()
def _annualized_sharpe(returns: list[float], intervals_years: list[float]) -> float | None:
    if len(returns) < 2 or not intervals_years:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    if variance <= 0:
        return None
    years = sum(intervals_years) / len(intervals_years)
    if years <= 0:
        return None
    periods_per_year = 1.0 / years
    return mean / sqrt(variance) * sqrt(periods_per_year)


def _annualized_sortino(returns: list[float], intervals_years: list[float]) -> float | None:
    if len(returns) < 2 or not intervals_years:
        return None
    mean = sum(returns) / len(returns)
    downside_square = sum(min(value, 0.0) ** 2 for value in returns) / len(returns)
    if downside_square <= 0:
        return None
    years = sum(intervals_years) / len(intervals_years)
    if years <= 0:
        return None
    return mean / sqrt(downside_square) * sqrt(1.0 / years)
