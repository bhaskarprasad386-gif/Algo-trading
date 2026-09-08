"""Unified strategy runner for candle, event and atomic multi-leg backtests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.algo.strategy import Strategy
from app.backtesting.basket_result import BasketResultAggregator
from app.backtesting.engine import BacktestConfig, BacktestEngine, BacktestResult
from app.backtesting.event_execution import EventExecutionBridge, EventExecutionResult
from app.backtesting.event_strategy import EventStrategy, StrategyAdapter
from app.backtesting.execution import ExecutionSide, ExecutionSimulator
from app.backtesting.multi_leg import MultiLegSignal
from app.backtesting.multi_leg_execution import AtomicMultiLegExecutor
from app.backtesting.multi_leg_pnl import BasketPnl


class UnifiedStrategyRunner:
    """Expose one strategy API while keeping execution models asset-agnostic."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()
        self.engine = BacktestEngine(self.config)
        self.multi_leg_executor = AtomicMultiLegExecutor()
        self.event_executor = EventExecutionBridge(ExecutionSimulator())

    def run_single(self, candles: Iterable[Mapping[str, object]], *, entry: Strategy, exit: Strategy) -> BacktestResult:
        return self.engine.run(candles, entry_strategy=entry, exit_strategy=exit)

    def run_events(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        strategy: EventStrategy,
        instrument_key: str = "instrument",
        price_key: str = "price",
    ) -> tuple[EventExecutionResult, ...]:
        """Execute timestamped event signals through the universal execution model."""
        adapter = StrategyAdapter(strategy)
        results: list[EventExecutionResult] = []
        for event in events:
            if "timestamp_ns" not in event:
                raise ValueError("event timestamp_ns is required")
            timestamp_ns = int(event["timestamp_ns"])
            data = dict(event)
            data.pop("timestamp_ns", None)
            signal = adapter.on_event(timestamp_ns=timestamp_ns, data=data)
            if signal is None:
                continue
            if instrument_key not in event or price_key not in event:
                raise ValueError(f"event requires {instrument_key} and {price_key} for execution")
            results.append(self.event_executor.execute(
                signal,
                instrument=str(event[instrument_key]),
                price=float(event[price_key]),
                timestamp_ns=timestamp_ns,
            ))
        return tuple(results)

    def run_event_trades(
        self,
        pairs: Iterable[tuple[Mapping[str, Any], Mapping[str, Any]]],
    ) -> BacktestResult:
        """Value already-executed event entry/exit pairs using shared result metrics."""
        aggregator = BasketResultAggregator(self.config.initial_capital)
        for entry_event, exit_event in pairs:
            entry = EventExecutionResult(
                signal=entry_event["signal"], fills=(entry_event["fill"],)
            )
            exit_ = EventExecutionResult(
                signal=exit_event["signal"], fills=(exit_event["fill"],)
            )
            a, b = entry.fills[0], exit_.fills[0]
            if a.instrument != b.instrument or a.side == b.side or a.quantity != b.quantity:
                raise ValueError("event entry/exit fills are incompatible")
            direction = 1.0 if a.side == ExecutionSide.BUY else -1.0
            gross = (b.price - a.price) * a.quantity * direction
            fees = a.fee + b.fee
            aggregator.add(_EventBasket(entry.signal.reason or entry.signal.action, gross, fees), b.filled_at_ns)
        return aggregator.result()

    def run_multi_leg(self, signals: Iterable[tuple[MultiLegSignal, Mapping[str, float], Mapping[str, float]]], *, pnl_builder) -> BacktestResult:
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


class _EventBasket:
    def __init__(self, signal_id: str, gross: float, fees: float) -> None:
        self.signal_id = signal_id
        self.gross_pnl = gross
        self.charges = fees
        self.funding = 0.0
        self.net_pnl = gross - fees
        self.legs = ()
