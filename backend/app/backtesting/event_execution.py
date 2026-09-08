"""Execution bridge for event-driven strategy signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.backtesting.event_strategy import StrategySignal
from app.backtesting.execution import ExecutionResult, ExecutionSide, ExecutionSimulator, SimOrder


@dataclass(frozen=True)
class EventExecutionResult:
    signal: StrategySignal
    fills: tuple
    rejected: bool = False
    reason: str | None = None


class EventExecutionBridge:
    """Translate normalized event signals into the universal execution simulator."""

    def __init__(self, simulator: ExecutionSimulator | None = None) -> None:
        self.simulator = simulator or ExecutionSimulator()

    def execute(self, signal: StrategySignal, *, instrument: str, price: float, timestamp_ns: int) -> EventExecutionResult:
        if not instrument.strip():
            raise ValueError("instrument is required")
        if timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if signal.action not in {"BUY", "SELL"}:
            return EventExecutionResult(signal, (), True, f"unsupported action: {signal.action}")
        if signal.quantity <= 0:
            return EventExecutionResult(signal, (), True, "signal quantity must be positive")
        order = SimOrder(
            order_id=f"event:{timestamp_ns}:{instrument}:{signal.action}",
            instrument=instrument,
            side=ExecutionSide(signal.action),
            quantity=int(signal.quantity),
            submitted_at_ns=timestamp_ns,
        )
        fill = self.simulator.execute(order, price, timestamp_ns)
        return EventExecutionResult(signal, (fill,))
