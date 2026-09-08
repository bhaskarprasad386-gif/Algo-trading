"""Shared result aggregation for event-driven backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.backtesting.engine import BacktestResult
from app.backtesting.event_pnl import EventTradePnl


@dataclass
class EventResultAggregator:
    """Aggregate event trades without retaining individual trades."""

    initial_capital: float
    _capital: float = 0.0
    _net_pnl: float = 0.0
    _trades: int = 0
    _wins: int = 0
    _peak: float = 0.0
    _max_drawdown: float = 0.0

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        self.initial_capital = float(self.initial_capital)
        self._capital = self.initial_capital
        self._peak = self.initial_capital

    def add(self, trade: EventTradePnl) -> None:
        self._capital += trade.net_pnl
        self._net_pnl += trade.net_pnl
        self._trades += 1
        self._wins += int(trade.net_pnl > 0)
        self._peak = max(self._peak, self._capital)
        self._max_drawdown = max(
            self._max_drawdown,
            (self._peak - self._capital) / self._peak,
        )

    def add_many(self, trades: Iterable[EventTradePnl]) -> None:
        for trade in trades:
            self.add(trade)

    def result(self) -> BacktestResult:
        return BacktestResult(
            initial_capital=self.initial_capital,
            final_capital=self._capital,
            net_pnl=self._net_pnl,
            total_return=self._net_pnl / self.initial_capital,
            trades=(),
            win_rate=self._wins / self._trades if self._trades else 0.0,
            expectancy=self._net_pnl / self._trades if self._trades else 0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown=self._max_drawdown,
            cagr=0.0,
        )
