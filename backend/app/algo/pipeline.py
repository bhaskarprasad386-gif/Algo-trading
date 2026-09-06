"""Generic strategy -> signal -> risk -> execution contracts.

This module contains broker-neutral, deterministic contracts shared by
backtesting and paper trading. It does not route real-money orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol

from app.backtesting.events import MarketEvent


@dataclass(frozen=True)
class Signal:
    strategy: str
    instrument: str
    side: str
    timestamp_ns: int
    quantity: int = 0
    price: float | None = None
    order_type: str = "MARKET"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side.upper() not in {"BUY", "SELL"}:
            raise ValueError("signal side must be BUY or SELL")
        if self.quantity < 0:
            raise ValueError("signal quantity cannot be negative")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str = "approved"
    quantity: int | None = None


class EventStrategy(Protocol):
    name: str

    def on_event(self, event: MarketEvent, state: Mapping[str, object]) -> Signal | None: ...


class RiskPolicy(Protocol):
    def evaluate(self, signal: Signal, state: Mapping[str, object]) -> RiskDecision: ...


class ExecutionSink(Protocol):
    def submit(self, signal: Signal, decision: RiskDecision) -> object: ...


@dataclass
class StrategyExecutionPipeline:
    """Runs one event through strategy, risk and execution stages.

    The execution sink is deliberately injected, so backtests can use a
    deterministic simulator and live paper trading can use a persistent
    paper broker without changing strategy code.
    """

    strategy: EventStrategy
    risk: RiskPolicy
    execution: ExecutionSink
    state: dict[str, object] = field(default_factory=dict)

    def on_event(self, event: MarketEvent) -> object | None:
        signal = self.strategy.on_event(event, self.state)
        if signal is None:
            return None
        decision = self.risk.evaluate(signal, self.state)
        if not decision.approved:
            return {"status": "rejected", "reason": decision.reason, "signal": signal}
        return self.execution.submit(signal, decision)


class AllowAllRisk:
    """Minimal deterministic risk policy for tests and replay composition."""

    def evaluate(self, signal: Signal, state: Mapping[str, object]) -> RiskDecision:
        if signal.quantity <= 0:
            return RiskDecision(False, "quantity must be greater than zero")
        return RiskDecision(True, quantity=signal.quantity)


class CollectExecution:
    """In-memory execution sink used by tests; not a trading adapter."""

    def __init__(self) -> None:
        self.submitted: list[tuple[Signal, RiskDecision]] = []

    def submit(self, signal: Signal, decision: RiskDecision) -> object:
        self.submitted.append((signal, decision))
        return {"status": "accepted", "signal": signal, "decision": decision}


class FunctionStrategy:
    """Adapter for simple event callbacks without imposing a class hierarchy."""

    def __init__(self, name: str, callback: Callable[[MarketEvent, Mapping[str, object]], Signal | None]) -> None:
        self.name = name
        self._callback = callback

    def on_event(self, event: MarketEvent, state: Mapping[str, object]) -> Signal | None:
        return self._callback(event, state)
