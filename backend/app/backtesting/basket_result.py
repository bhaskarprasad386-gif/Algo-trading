"""Generic result aggregation for atomic multi-leg backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.backtesting.engine import BacktestResult, BacktestTrade
from app.backtesting.multi_leg_pnl import BasketPnl


@dataclass(frozen=True)
class BasketTrade:
    """Backtest trade-shaped summary for one completed multi-leg basket."""

    signal_id: str
    timestamp_ns: int
    gross_pnl: float
    charges: float
    funding: float
    net_pnl: float


class BasketResultAggregator:
    """Convert streamed basket results into the shared BacktestResult contract."""

    def __init__(self, initial_capital: float) -> None:
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        self.initial_capital = float(initial_capital)
        self._capital = self.initial_capital
        self._peak = self.initial_capital
        self._max_drawdown = 0.0
        self._trades = 0
        self._wins = 0
        self._net_pnl = 0.0
        self._returns: list[float] = []

    def add(self, basket: BasketPnl, timestamp_ns: int) -> BasketTrade:
        if timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        trade = BasketTrade(basket.signal_id, timestamp_ns, basket.gross_pnl,
                            basket.charges, basket.funding, basket.net_pnl)
        self._capital += trade.net_pnl
        self._peak = max(self._peak, self._capital)
        self._max_drawdown = max(self._max_drawdown, (self._peak - self._capital) / self._peak)
        self._trades += 1
        self._wins += int(trade.net_pnl > 0)
        self._net_pnl += trade.net_pnl
        self._returns.append(trade.net_pnl / self.initial_capital)
        return trade

    def add_many(self, baskets: Iterable[tuple[BasketPnl, int]]) -> int:
        count = 0
        for basket, timestamp_ns in baskets:
            self.add(basket, timestamp_ns)
            count += 1
        return count

    def result(self) -> BacktestResult:
        returns = tuple(self._returns)
        mean = sum(returns) / len(returns) if returns else 0.0
        variance = sum((value - mean) ** 2 for value in returns) / len(returns) if returns else 0.0
        downside = sum(min(value, 0.0) ** 2 for value in returns) / len(returns) if returns else 0.0
        sharpe = mean / variance ** 0.5 if variance > 0 else 0.0
        sortino = mean / downside ** 0.5 if downside > 0 else 0.0
        return BacktestResult(
            initial_capital=self.initial_capital,
            final_capital=self._capital,
            net_pnl=self._net_pnl,
            total_return=self._net_pnl / self.initial_capital,
            trades=(),
            win_rate=self._wins / self._trades if self._trades else 0.0,
            expectancy=self._net_pnl / self._trades if self._trades else 0.0,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=self._max_drawdown,
            cagr=0.0,
        )
