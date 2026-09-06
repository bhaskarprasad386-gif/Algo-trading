"""Adapter that makes the base Strategy backtestable through market events.

The adapter intentionally keeps Strategy's existing rule API intact while
turning matching events into deterministic signals. It does not place live
orders and does not invent missing market observations.
"""

from dataclasses import dataclass
from typing import Mapping

from app.algo.strategy import Strategy
from app.backtesting.events import MarketEvent


@dataclass(frozen=True)
class StrategySignal:
    """Deterministic strategy decision produced from one market event."""

    timestamp_ns: int
    instrument: str
    action: str
    strategy_name: str
    event_type: str


class StrategyEventAdapter:
    """Evaluate the existing base Strategy against replayed event payloads."""

    def __init__(self, strategy: Strategy, *, action: str = "BUY") -> None:
        normalized = action.strip().upper()
        if normalized not in {"BUY", "SELL", "HOLD"}:
            raise ValueError("action must be BUY, SELL, or HOLD")
        self.strategy = strategy
        self.action = normalized

    def on_event(self, event: MarketEvent, context: dict[str, object]) -> StrategySignal | None:
        """Return a signal when all strategy rules match the event payload."""
        numeric_context: Mapping[str, float] = {
            key: float(value)
            for key, value in event.payload.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if not self.strategy.evaluate(numeric_context):
            return None

        signal = StrategySignal(
            timestamp_ns=event.timestamp_ns,
            instrument=event.instrument,
            action=self.action,
            strategy_name=self.strategy.name,
            event_type=event.event_type.value,
        )
        signals = context.setdefault("signals", [])
        if not isinstance(signals, list):
            raise TypeError("context['signals'] must be a list when provided")
        signals.append(signal)
        return signal
