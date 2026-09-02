"""Small deterministic backtesting engine foundation.

The engine is deliberately broker-independent. It consumes ordered candle-like
records and separate entry/exit strategies, applies configurable slippage and
transaction cost rates, and returns immutable trade/result records.
"""

from dataclasses import dataclass
from typing import Iterable, Mapping

from app.algo.strategy import Strategy


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 100_000.0
    quantity: float = 1.0
    slippage_rate: float = 0.0
    transaction_cost_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.slippage_rate < 0 or self.transaction_cost_rate < 0:
            raise ValueError("cost rates cannot be negative")


@dataclass(frozen=True)
class BacktestTrade:
    entry_timestamp: object
    exit_timestamp: object
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    costs: float
    net_pnl: float


@dataclass(frozen=True)
class BacktestResult:
    initial_capital: float
    final_capital: float
    net_pnl: float
    total_return: float
    trades: tuple[BacktestTrade, ...]
    win_rate: float
    expectancy: float
    max_drawdown: float


class BacktestEngine:
    """Run a deterministic close-to-close long-only strategy backtest."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(
        self,
        candles: Iterable[Mapping[str, object]],
        entry_strategy: Strategy,
        exit_strategy: Strategy,
    ) -> BacktestResult:
        capital = self.config.initial_capital
        peak_capital = capital
        max_drawdown = 0.0
        open_trade: tuple[object, float] | None = None
        trades: list[BacktestTrade] = []

        for candle in candles:
            timestamp = candle.get("timestamp")
            close = float(candle["close"])
            if close <= 0:
                raise ValueError("candle close must be positive")

            context = {key: float(value) for key, value in candle.items() if _is_number(value)}

            if open_trade is None and entry_strategy.evaluate(context):
                entry_price = close * (1.0 + self.config.slippage_rate)
                open_trade = (timestamp, entry_price)
            elif open_trade is not None and exit_strategy.evaluate(context):
                entry_timestamp, entry_price = open_trade
                exit_price = close * (1.0 - self.config.slippage_rate)
                gross_pnl = (exit_price - entry_price) * self.config.quantity
                traded_value = (entry_price + exit_price) * self.config.quantity
                costs = traded_value * self.config.transaction_cost_rate
                net_pnl = gross_pnl - costs
                capital += net_pnl
                peak_capital = max(peak_capital, capital)
                drawdown = (peak_capital - capital) / peak_capital
                max_drawdown = max(max_drawdown, drawdown)
                trades.append(
                    BacktestTrade(
                        entry_timestamp=entry_timestamp,
                        exit_timestamp=timestamp,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=self.config.quantity,
                        gross_pnl=gross_pnl,
                        costs=costs,
                        net_pnl=net_pnl,
                    )
                )
                open_trade = None

        net_pnl = capital - self.config.initial_capital
        wins = sum(1 for trade in trades if trade.net_pnl > 0)
        win_rate = wins / len(trades) if trades else 0.0
        expectancy = net_pnl / len(trades) if trades else 0.0
        total_return = net_pnl / self.config.initial_capital
        return BacktestResult(
            initial_capital=self.config.initial_capital,
            final_capital=capital,
            net_pnl=net_pnl,
            total_return=total_return,
            trades=tuple(trades),
            win_rate=win_rate,
            expectancy=expectancy,
            max_drawdown=max_drawdown,
        )


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
