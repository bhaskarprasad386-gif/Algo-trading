"""State container for chunked high-resolution event backtests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EventExecutionState:
    """Mutable execution state carried across event chunks."""

    capital: float
    peak_capital: float
    max_drawdown: float = 0.0
    open_trade: tuple[object, float] | None = None
    previous_key: tuple[int, int] | None = None
