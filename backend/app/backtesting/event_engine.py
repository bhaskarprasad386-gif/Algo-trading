"""Resolution-agnostic event-driven backtest replay and execution."""

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from app.backtesting.events import EventReplayConfig, EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator, SimFill, SimOrder
from app.backtesting.portfolio import Portfolio, PortfolioSnapshot
from app.backtesting.strategy import StrategyContext, StrategyDecision, validate_decision


LegacyEventStrategy = Callable[[MarketEvent, Mapping[str, object]], object]


@dataclass(frozen=True)
class ReplayStats:
    events_seen: int
    events_dispatched: int
    first_timestamp_ns: int | None
    last_timestamp_ns: int | None
    decisions_emitted: int = 0
    orders_submitted: int = 0
    fills: int = 0
    final_snapshot: PortfolioSnapshot | None = None


class EventBacktestEngine:
    """Replay source events into any compatible event strategy.

    The engine never creates observations between source events. A strategy
    receives only the current event and history strictly before that event,
    preventing look-ahead. Strategy decisions can now flow through the shared
    execution simulator and portfolio, including multiple orders from one
    event. Legacy callback strategies remain supported.
    """

    def __init__(
        self,
        config: EventReplayConfig | None = None,
        *,
        execution: ExecutionSimulator | None = None,
        portfolio: Portfolio | None = None,
    ) -> None:
        self.config = config or EventReplayConfig()
        self.execution = execution
        self.portfolio = portfolio

    @staticmethod
    def _event_price(event: MarketEvent) -> float | None:
        for key in ("price", "ltp", "last", "last_price", "mid"):
            value = event.payload.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        bid = event.payload.get("bid")
        ask = event.payload.get("ask")
        if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid > 0 and ask > 0:
            return (float(bid) + float(ask)) / 2.0
        return None

    def _execute_decision(self, decision: StrategyDecision, event: MarketEvent) -> tuple[SimFill, ...]:
        if self.execution is None or self.portfolio is None or not decision.orders:
            return ()
        market_price = self._event_price(event)
        if market_price is None:
            raise ValueError("cannot execute strategy order: event has no executable price")
        fills: list[SimFill] = []
        for order in decision.orders:
            if not isinstance(order, SimOrder):
                raise TypeError("strategy orders must be SimOrder instances")
            submitted = max(order.submitted_at_ns, event.timestamp_ns)
            effective_order = SimOrder(
                order_id=order.order_id,
                instrument=order.instrument,
                side=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                submitted_at_ns=submitted,
            )
            fill = self.execution.execute(effective_order, market_price, event.timestamp_ns)
            self.portfolio.apply_fill(fill)
            fills.append(fill)
        return tuple(fills)

    def run(
        self,
        events: Iterable[MarketEvent],
        strategy: object,
        *,
        state: Mapping[str, object] | None = None,
    ) -> ReplayStats:
        context_state: dict[str, object] = dict(state or {})
        history: list[MarketEvent] = []
        seen = dispatched = decisions = orders = fill_count = 0
        first: int | None = None
        last: int | None = None
        previous_key: tuple[int, int, str, str, int] | None = None
        started = False

        for raw_event in events:
            seen += 1
            timestamp_ns = raw_event.timestamp_ns
            if self.config.timestamp_unit != "ns":
                timestamp_ns = self.config.to_ns(timestamp_ns)
                event = MarketEvent(timestamp_ns, raw_event.instrument, raw_event.event_type, raw_event.payload, raw_event.sequence, raw_event.source)
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

            first = event.timestamp_ns if first is None else first
            last = event.timestamp_ns
            strategy_context = StrategyContext(event.timestamp_ns, tuple(history), dict(context_state))
            handler = getattr(strategy, "on_event", None)
            if callable(handler):
                decision = handler(event, strategy_context)
                if decision is not None and not isinstance(decision, StrategyDecision):
                    raise TypeError("event strategy must return StrategyDecision or None")
                validate_decision(decision)
                if decision is not None:
                    decisions += 1
                    orders += len(decision.orders)
                    fill_count += len(self._execute_decision(decision, event))
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
            orders_submitted=orders,
            fills=fill_count,
            final_snapshot=self.portfolio.snapshot() if self.portfolio is not None else None,
        )
