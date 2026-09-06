"""Resolution-agnostic event-driven backtest replay foundation."""

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from app.backtesting.events import EventReplayConfig, EventType, MarketEvent
from app.backtesting.strategy import StrategyContext, StrategyDecision, validate_decision


LegacyEventStrategy = Callable[[MarketEvent, Mapping[str, object]], object]


@dataclass(frozen=True)
class ReplayStats:
    events_seen: int
    events_dispatched: int
    first_timestamp_ns: int | None
    last_timestamp_ns: int | None
    decisions_emitted: int = 0


class EventBacktestEngine:
    """Replay source events into any compatible event strategy.

    The engine never creates observations between source events. A strategy
    receives only the current event and history strictly before that event,
    preventing look-ahead. Legacy callback strategies remain supported.
    """

    def __init__(self, config: EventReplayConfig | None = None) -> None:
        self.config = config or EventReplayConfig()

    def run(
        self,
        events: Iterable[MarketEvent],
        strategy: object,
        *,
        state: Mapping[str, object] | None = None,
    ) -> ReplayStats:
        context_state: dict[str, object] = dict(state or {})
        history: list[MarketEvent] = []
        seen = 0
        dispatched = 0
        decisions = 0
        first: int | None = None
        last: int | None = None
        previous_key: tuple[int, int, str, str, int] | None = None
        started = False

        for raw_event in events:
            seen += 1
            timestamp_ns = raw_event.timestamp_ns
            if self.config.timestamp_unit != "ns":
                timestamp_ns = self.config.to_ns(timestamp_ns)
                event = MarketEvent(
                    timestamp_ns=timestamp_ns,
                    instrument=raw_event.instrument,
                    event_type=raw_event.event_type,
                    payload=raw_event.payload,
                    sequence=raw_event.sequence,
                    source=raw_event.source,
                )
            else:
                event = raw_event

            if self.config.include_event_types is not None and event.event_type not in self.config.include_event_types:
                continue

            sequence_key = event.sequence if event.sequence is not None else -1
            source_key = event.source or ""
            event_key = (event.timestamp_ns, sequence_key, source_key, event.event_type.value, dispatched)
            if previous_key is not None and event_key[:4] < previous_key[:4]:
                raise ValueError("events must be ordered by timestamp, sequence, source, and type")
            previous_key = event_key

            if not started:
                starter = getattr(strategy, "on_start", None)
                if callable(starter):
                    starter(StrategyContext(event.timestamp_ns, tuple(), dict(context_state)))
                started = True

            if first is None:
                first = event.timestamp_ns
            last = event.timestamp_ns

            strategy_context = StrategyContext(
                timestamp_ns=event.timestamp_ns,
                history=tuple(history),
                state=dict(context_state),
            )
            handler = getattr(strategy, "on_event", None)
            if callable(handler):
                decision = handler(event, strategy_context)
                if decision is not None and not isinstance(decision, StrategyDecision):
                    raise TypeError("event strategy must return StrategyDecision or None")
                validate_decision(decision)
                if decision is not None:
                    decisions += 1
            elif callable(strategy):
                strategy(event, context_state)
            else:
                raise TypeError("strategy must be callable or implement on_event")

            history.append(event)
            dispatched += 1

        if started:
            finisher = getattr(strategy, "on_end", None)
            if callable(finisher):
                finisher(StrategyContext(last if last is not None else 0, tuple(history), dict(context_state)))

        return ReplayStats(
            events_seen=seen,
            events_dispatched=dispatched,
            first_timestamp_ns=first,
            last_timestamp_ns=last,
            decisions_emitted=decisions,
        )
