"""Resolution-agnostic event-driven backtest replay and execution."""

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from app.backtesting.events import EventReplayConfig, EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator, SimFill, SimOrder, OrderBook, DepthLevel, ExecutionSide
from app.backtesting.portfolio import Portfolio, PortfolioSnapshot, RiskViolation
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
    risk_blocks: int = 0
    final_snapshot: PortfolioSnapshot | None = None


class EventBacktestEngine:
    """Replay source events with point-in-time, instrument-specific execution."""

    def __init__(self, config: EventReplayConfig | None = None, *, execution: ExecutionSimulator | None = None,
                 portfolio: Portfolio | None = None) -> None:
        self.config = config or EventReplayConfig()
        self.execution = execution
        self.portfolio = portfolio
        self._latest_events: dict[str, MarketEvent] = {}
        self._latest_books: dict[str, tuple[int, OrderBook]] = {}
        self._risk_blocks = 0

    @staticmethod
    def _event_price(event: MarketEvent, side: ExecutionSide | None = None) -> float | None:
        bid = event.payload.get("bid")
        ask = event.payload.get("ask")
        if side == ExecutionSide.BUY and isinstance(ask, (int, float)) and ask > 0:
            return float(ask)
        if side == ExecutionSide.SELL and isinstance(bid, (int, float)) and bid > 0:
            return float(bid)
        for key in ("price", "ltp", "last", "last_price"):
            value = event.payload.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid > 0 and ask > 0:
            return (float(bid) + float(ask)) / 2.0
        return None

    @staticmethod
    def _book_from_event(event: MarketEvent) -> OrderBook | None:
        bids = event.payload.get("bids")
        asks = event.payload.get("asks")
        if not isinstance(bids, (list, tuple)) and not isinstance(asks, (list, tuple)):
            return None

        def levels(raw):
            if not isinstance(raw, (list, tuple)):
                return ()
            out = []
            for item in raw:
                if isinstance(item, DepthLevel):
                    out.append(item)
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    out.append(DepthLevel(float(item[0]), int(item[1])))
                elif isinstance(item, Mapping):
                    out.append(DepthLevel(float(item["price"]), int(item["quantity"])))
            return tuple(out)

        return OrderBook(bids=levels(bids), asks=levels(asks))

    def _update_market_state(self, event: MarketEvent) -> None:
        self._latest_events[event.instrument] = event
        if event.event_type == EventType.DEPTH:
            book = self._book_from_event(event)
            if book is not None:
                self._latest_books[event.instrument] = (event.timestamp_ns, book)

    def _order_reference_price(self, order: SimOrder, observed: MarketEvent) -> float | None:
        book_state = self._latest_books.get(order.instrument)
        if book_state is not None and book_state[0] == observed.timestamp_ns:
            levels = book_state[1].asks if order.side == ExecutionSide.BUY else book_state[1].bids
            if levels:
                return levels[0].price
        return self._event_price(observed, order.side)

    def _execute_decision(self, decision: StrategyDecision, event: MarketEvent) -> tuple[SimFill, ...]:
        if self.execution is None or self.portfolio is None or not decision.orders:
            return ()
        effective_orders: list[SimOrder] = []
        reservations: dict[str, float] = {}
        try:
            for order in decision.orders:
                if not isinstance(order, SimOrder):
                    raise TypeError("strategy orders must be SimOrder instances")
                submitted = max(order.submitted_at_ns, event.timestamp_ns)
                effective_order = SimOrder(order_id=order.order_id, instrument=order.instrument, side=order.side,
                                           quantity=order.quantity, order_type=order.order_type,
                                           limit_price=order.limit_price, stop_price=order.stop_price,
                                           submitted_at_ns=submitted,
                                           queue_ahead_quantity=order.queue_ahead_quantity)
                observed = self._latest_events.get(order.instrument)
                if observed is None or observed.timestamp_ns > event.timestamp_ns:
                    continue
                reference_price = self._order_reference_price(effective_order, observed)
                if reference_price is None:
                    continue
                reservation = effective_order.quantity * reference_price * self.portfolio.risk_config.initial_margin_rate
                self.portfolio.reserve_margin(effective_order.order_id, reservation)
                reservations[effective_order.order_id] = reservation
                effective_orders.append(effective_order)
        except RiskViolation:
            self._risk_blocks += 1
            for order_id in reservations:
                self.portfolio.release_margin(order_id)
            return ()

        fills: list[SimFill] = []
        for order in effective_orders:
            observed = self._latest_events[order.instrument]
            book_state = self._latest_books.get(order.instrument)
            if book_state is not None and book_state[0] == observed.timestamp_ns:
                result = self.execution.execute_depth(order, book_state[1], event.timestamp_ns)
                order_fills = result.fills
            else:
                market_price = self._event_price(observed, order.side)
                order_fills = () if market_price is None else (self.execution.execute(order, market_price, event.timestamp_ns),)
            fills.extend(order_fills)

        try:
            if fills:
                marks = {instrument: self._event_price(event, None) for instrument, event in self._latest_events.items()}
                marks = {k: v for k, v in marks.items() if v is not None and v > 0}
                self.portfolio.apply_fills_atomic(fills, marks)
        except RiskViolation:
            self._risk_blocks += 1
            for order_id in reservations:
                self.portfolio.release_margin(order_id)
            return ()

        filled_by_order: dict[str, int] = {}
        for fill in fills:
            filled_by_order[fill.order_id] = filled_by_order.get(fill.order_id, 0) + fill.quantity
        for order in effective_orders:
            reservation = reservations[order.order_id]
            filled_qty = filled_by_order.get(order.order_id, 0)
            if filled_qty >= order.quantity or filled_qty == 0:
                self.portfolio.release_margin(order.order_id)
            else:
                per_unit = reservation / order.quantity
                self.portfolio.release_margin(order.order_id, per_unit * filled_qty)
        return tuple(fills)

    def run(self, events: Iterable[MarketEvent], strategy: object, *, state: Mapping[str, object] | None = None) -> ReplayStats:
        context_state: dict[str, object] = dict(state or {})
        history: list[MarketEvent] = []
        seen = dispatched = decisions = orders = fill_count = 0
        first: int | None = None
        last: int | None = None
        previous_key: tuple[int, int, str, str, int] | None = None
        started = False
        self._risk_blocks = 0
        self._latest_events.clear()
        self._latest_books.clear()

        for raw_event in events:
            seen += 1
            timestamp_ns = raw_event.timestamp_ns if self.config.timestamp_unit == "ns" else self.config.to_ns(raw_event.timestamp_ns)
            event = raw_event if timestamp_ns == raw_event.timestamp_ns else MarketEvent(
                timestamp_ns, raw_event.instrument, raw_event.event_type, raw_event.payload, raw_event.sequence, raw_event.source)
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
            self._update_market_state(event)
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
        return ReplayStats(events_seen=seen, events_dispatched=dispatched, first_timestamp_ns=first,
                           last_timestamp_ns=last, decisions_emitted=decisions, orders_submitted=orders,
                           fills=fill_count, risk_blocks=self._risk_blocks,
                           final_snapshot=self.portfolio.snapshot() if self.portfolio is not None else None)
