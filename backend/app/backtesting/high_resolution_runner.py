"""High-resolution event replay through the shared execution bridge."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.backtesting.event_execution import EventExecutionBridge, EventExecutionResult
from app.backtesting.event_replay import DeterministicEventReplay, ReplayEvent
from app.backtesting.event_strategy import EventStrategy, StrategyContext


class HighResolutionEventRunner:
    """Replay timestamped events deterministically and execute emitted orders."""

    def __init__(self, execution: EventExecutionBridge | None = None) -> None:
        self.execution = execution or EventExecutionBridge()

    def run(
        self,
        events: Iterable[ReplayEvent],
        *,
        strategy: EventStrategy,
        instrument: str,
        price_key: str = "price",
    ) -> tuple[EventExecutionResult, ...]:
        ordered = DeterministicEventReplay.validate(events)
        results: list[EventExecutionResult] = []
        for event in ordered:
            data = dict(event.data)
            if price_key not in data:
                continue
            signal = strategy.on_event(StrategyContext(event.timestamp_ns, data))
            if signal is None:
                continue
            results.append(self.execution.execute(
                signal,
                instrument=instrument,
                price=float(data[price_key]),
                timestamp_ns=event.timestamp_ns,
            ))
        return tuple(results)
