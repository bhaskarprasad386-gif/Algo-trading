"""End-to-end deterministic event replay through strategy and execution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.backtesting.event_execution import EventExecutionBridge, EventExecutionResult
from app.backtesting.event_replay import DeterministicEventReplay, ReplayEvent
from app.backtesting.event_strategy import EventStrategy, StrategyAdapter


class EventReplayRunner:
    """Replay real timestamped events without inventing higher precision."""

    def __init__(self, execution: EventExecutionBridge | None = None) -> None:
        self.execution = execution or EventExecutionBridge()

    def run(
        self,
        events: Iterable[ReplayEvent],
        *,
        strategy: EventStrategy,
        instrument_key: str = "instrument",
        price_key: str = "price",
    ) -> tuple[EventExecutionResult, ...]:
        ordered = DeterministicEventReplay.validate(events)
        adapter = StrategyAdapter(strategy)
        results: list[EventExecutionResult] = []
        for event in ordered:
            data = dict(event.data)
            instrument = str(data.get(instrument_key, ""))
            if price_key not in data:
                continue
            signal = adapter.on_event(timestamp_ns=event.timestamp_ns, data=data)
            if signal is None:
                continue
            results.append(
                self.execution.execute(
                    signal,
                    instrument=instrument,
                    price=float(data[price_key]),
                    timestamp_ns=event.timestamp_ns,
                )
            )
        return tuple(results)
