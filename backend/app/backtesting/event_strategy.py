"""Strategy contract for candle, tick, and event-driven backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class StrategyContext:
    """Point-in-time context supplied to a strategy."""

    timestamp_ns: int
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategySignal:
    """Normalized strategy decision independent of asset class or timeframe."""

    action: str
    quantity: float = 0.0
    reason: str = ""


class EventStrategy(Protocol):
    """Plug-in protocol for stateful event/candle strategies."""

    def on_event(self, context: StrategyContext) -> StrategySignal | None: ...


class StrategyAdapter:
    """Normalize an EventStrategy into deterministic engine callbacks."""

    def __init__(self, strategy: EventStrategy) -> None:
        self.strategy = strategy

    def on_event(self, *, timestamp_ns: int, data: dict[str, Any]) -> StrategySignal | None:
        return self.strategy.on_event(StrategyContext(timestamp_ns=timestamp_ns, data=data))
