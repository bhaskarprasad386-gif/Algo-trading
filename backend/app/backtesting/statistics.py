"""Accounting and time-series statistics for universal backtests.

Statistics are calculated from marked equity, not from the number of trades.
This keeps realized P&L, unrealized P&L and performance ratios semantically
separate and works for irregular/millisecond event spacing.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Iterable


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


def calculate_statistics(
    points: Iterable[EquityPoint],
    initial_capital: float,
) -> BacktestStatistics:
    if not isfinite(float(initial_capital)) or initial_capital <= 0:
        raise ValueError("initial_capital must be finite and positive")
    curve = tuple(points)
    if not curve:
        return BacktestStatistics(0.0, 0.0, None, None, 0.0, None)
    for previous, current in zip(curve, curve[1:]):
        if current.timestamp_ns <= previous.timestamp_ns:
            raise ValueError("equity timestamps must be strictly increasing")

    final_equity = curve[-1].equity
    net_pnl = final_equity - initial_capital
    total_return = net_pnl / initial_capital

    peak = initial_capital
    max_drawdown = 0.0
    for point in curve:
        peak = max(peak, point.equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - point.equity) / peak)

    returns: list[float] = []
    intervals_years: list[float] = []
    previous_equity = initial_capital
    previous_timestamp = curve[0].timestamp_ns
    for point in curve:
        elapsed_years = (point.timestamp_ns - previous_timestamp) / (365.25 * 24 * 60 * 60 * 1_000_000_000)
        if previous_equity > 0 and elapsed_years > 0:
            value = point.equity / previous_equity - 1.0
            if isfinite(value):
                returns.append(value)
                intervals_years.append(elapsed_years)
        previous_equity = point.equity
        previous_timestamp = point.timestamp_ns

    sharpe = _annualized_sharpe(returns, intervals_years)
    sortino = _annualized_sortino(returns, intervals_years)

    elapsed_years = (curve[-1].timestamp_ns - curve[0].timestamp_ns) / (365.25 * 24 * 60 * 60 * 1_000_000_000)
    if elapsed_years > 0 and final_equity > 0:
        cagr = (final_equity / initial_capital) ** (1.0 / elapsed_years) - 1.0
    else:
        cagr = None

    return BacktestStatistics(net_pnl, total_return, sharpe, sortino, max_drawdown, cagr)


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
