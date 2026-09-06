"""Stable strategy contract for arbitrary event-driven backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from app.backtesting.events import MarketEvent


@dataclass(frozen=True)
class StrategyContext:
    """Point-in-time state visible to a strategy; no future events are exposed."""

    timestamp_ns: int
    history: tuple[MarketEvent, ...] = ()
    state: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyDecision:
    """Strategy output. Orders are intentionally generic for single/multi-leg use."""

    action: str
    orders: tuple[Any, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


class EventStrategy(Protocol):
    """Any event strategy that can be replayed by the universal backtest engine."""

    strategy_id: str
    strategy_version: str

    def on_event(self, event: MarketEvent, context: StrategyContext) -> StrategyDecision | None:
        ...

    def on_start(self, context: StrategyContext) -> None:
        ...

    def on_end(self, context: StrategyContext) -> None:
        ...


def strategy_state(strategy: object) -> Mapping[str, Any]:
    """Return checkpointable strategy state when the strategy supports it."""
    getter = getattr(strategy, "get_state", None)
    if not callable(getter):
        return {}
    state = getter()
    if not isinstance(state, Mapping):
        raise TypeError("strategy get_state() must return a mapping")
    return dict(state)


def restore_strategy_state(strategy: object, state: Mapping[str, Any]) -> None:
    """Restore checkpoint state; never silently discard strategy state."""
    if not state:
        return
    setter = getattr(strategy, "set_state", None)
    if not callable(setter):
        raise ValueError("checkpoint contains strategy state but strategy cannot restore it")
    setter(dict(state))


def validate_decision(decision: StrategyDecision | None) -> None:
    """Reject malformed strategy output before execution/ledger integration."""
    if decision is None:
        return
    if not decision.action.strip():
        raise ValueError("strategy decision action is required")
    if not isinstance(decision.orders, tuple):
        raise ValueError("strategy decision orders must be a tuple")
