"""Deterministic robustness summary for completed backtest variants."""

from dataclasses import dataclass
from typing import Iterable

from app.backtesting.engine import BacktestResult


@dataclass(frozen=True)
class RobustnessResult:
    """Summarize consistency across multiple completed backtest variants."""

    variants: int
    profitable_variants: int
    profitable_ratio: float
    min_return: float
    max_return: float
    return_range: float
    worst_max_drawdown: float


def summarize_robustness(results: Iterable[BacktestResult]) -> RobustnessResult:
    """Return deterministic cross-variant robustness statistics."""
    items = tuple(results)
    if not items:
        return RobustnessResult(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)

    returns = tuple(result.total_return for result in items)
    profitable = sum(1 for value in returns if value > 0.0)
    min_return = min(returns)
    max_return = max(returns)

    return RobustnessResult(
        variants=len(items),
        profitable_variants=profitable,
        profitable_ratio=profitable / len(items),
        min_return=min_return,
        max_return=max_return,
        return_range=max_return - min_return,
        worst_max_drawdown=max(result.max_drawdown for result in items),
    )
