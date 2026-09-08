"""Unified strategy runner for candle, event and atomic multi-leg backtests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.algo.strategy import Strategy
from app.backtesting.basket_result import BasketResultAggregator
from app.backtesting.engine import BacktestConfig, BacktestEngine, BacktestResult
from app.backtesting.event_strategy import EventStrategy, StrategyContext, StrategySignal
from app.backtesting.multi_leg import MultiLegSignal
from app.backtesting.multi_leg_execution import AtomicMultiLegExecutor
from app.backtesting.multi_leg_pnl import BasketPnl


class UnifiedStrategyRunner:
    """Expose one strategy API while keeping execution models asset-agnostic."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()
        self.engine = BacktestEngine(self.config)
        self.multi_leg_executor = AtomicMultiLegExecutor()

    def run_single(
        self,
        candles: Iterable[Mapping[str, object]],
        *,
        entry: Strategy,
        exit: Strategy,
    ) -> BacktestResult:
        return self.engine.run(candles, entry_strategy=entry, exit_strategy=exit)

    def run_events(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        strategy: EventStrategy,
    ) -> tuple[StrategySignal, ...]:
        """Process arbitrary timestamped events without assuming candles."""
        signals: list[StrategySignal] = []
        for event in events:
            if "timestamp_ns" not in event:
                raise ValueError("event timestamp_ns is required")
            timestamp_ns = int(event["timestamp_ns"])
            if timestamp_ns < 0:
                raise ValueError("event timestamp_ns cannot be negative")
            data = dict(event)
            data.pop("timestamp_ns", None)
            signal = strategy.on_event(StrategyContext(timestamp_ns=timestamp_ns, data=data))
            if signal is not None:
                signals.append(signal)
        return tuple(signals)

    def run_multi_leg(
        self,
        signals: Iterable[tuple[MultiLegSignal, Mapping[str, float], Mapping[str, float]]],
        *,
        pnl_builder,
    ) -> BacktestResult:
        """Run atomic baskets and aggregate them into the shared result contract."""
        aggregator = BasketResultAggregator(self.config.initial_capital)
        for signal, entry_prices, exit_prices in signals:
            entry_result = self.multi_leg_executor.execute(signal, entry_prices)
            if entry_result.rejected:
                continue
            exit_result = self.multi_leg_executor.execute(signal, exit_prices)
            if exit_result.rejected:
                continue
            entries = {fill.order_id: fill for fill in entry_result.fills}
            exits = {fill.order_id: fill for fill in exit_result.fills}
            basket: BasketPnl = pnl_builder(signal.signal_id, entries, exits)
            aggregator.add(basket, signal.timestamp_ns)
        return aggregator.result()
