"""Unified runner bridge for candle, tick and arbitrary event strategies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.backtesting.event_strategy import EventStrategy, StrategyAdapter, StrategyContext, StrategySignal


class UnifiedEventRunner:
    """Replay point-in-time events without imposing a timeframe on strategies."""

    def run(
        self,
        events: Iterable[tuple[int, Mapping[str, Any]]],
        *,
        strategy: EventStrategy,
        on_signal=None,
    ) -> int:
        """Feed events in source order and return emitted signal count.

        The runner deliberately does not synthesize missing ticks or timestamps.
        Event timestamps must be non-negative and non-decreasing.
        """
        adapter = StrategyAdapter(strategy)
        previous = -1
        emitted = 0
        for timestamp_ns, data in events:
            if timestamp_ns < 0:
                raise ValueError("timestamp_ns cannot be negative")
            if timestamp_ns < previous:
                raise ValueError("events must be supplied in timestamp order")
            previous = timestamp_ns
            signal = adapter.on_event(timestamp_ns=timestamp_ns, data=dict(data))
            if signal is not None:
                emitted += 1
                if on_signal is not None:
                    on_signal(signal)
        return emitted
