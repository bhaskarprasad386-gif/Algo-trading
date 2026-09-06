"""Resolution-agnostic event-driven backtest replay foundation."""

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from app.backtesting.events import EventReplayConfig, EventType, MarketEvent


EventStrategy = Callable[[MarketEvent, Mapping[str, object]], object]


@dataclass(frozen=True)
class ReplayStats:
    events_seen: int
    events_dispatched: int
    first_timestamp_ns: int | None
    last_timestamp_ns: int | None


class EventBacktestEngine:
    """Replay real source events without fabricating higher-frequency data.

    The same engine can consume bars, trades, quotes and depth events. The
    strategy receives the current event plus a mutable-by-replacement state
    mapping, allowing future execution/risk components to share this replay
    contract without coupling the engine to a particular strategy.
    """

    def __init__(self, config: EventReplayConfig | None = None) -> None:
        self.config = config or EventReplayConfig()

    def run(
        self,
        events: Iterable[MarketEvent],
        strategy: EventStrategy,
        *,
        state: Mapping[str, object] | None = None,
    ) -> ReplayStats:
        context: dict[str, object] = dict(state or {})
        seen = 0
        dispatched = 0
        first: int | None = None
        last: int | None = None
        previous_key: tuple[int, int, int] | None = None

        for event in events:
            seen += 1
            timestamp_ns = event.timestamp_ns
            if self.config.timestamp_unit != "ns":
                timestamp_ns = self.config.to_ns(timestamp_ns)
                event = MarketEvent(
                    timestamp_ns=timestamp_ns,
                    instrument=event.instrument,
                    event_type=event.event_type,
                    payload=event.payload,
                    sequence=event.sequence,
                    source=event.source,
                )

            if self.config.include_event_types is not None and event.event_type not in self.config.include_event_types:
                continue

            order_key = (event.timestamp_ns, event.sequence if event.sequence is not None else -1, dispatched)
            if previous_key is not None and order_key[:2] < previous_key[:2]:
                raise ValueError("events must be ordered by timestamp and sequence")
            previous_key = order_key

            if first is None:
                first = event.timestamp_ns
            last = event.timestamp_ns
            strategy(event, context)
            dispatched += 1

        return ReplayStats(
            events_seen=seen,
            events_dispatched=dispatched,
            first_timestamp_ns=first,
            last_timestamp_ns=last,
        )
