"""Resolution-agnostic event-driven backtest replay and execution."""

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from app.backtesting.events import EventReplayConfig, EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator, SimFill, SimOrder, OrderBook, DepthLevel, ExecutionSide, OrderType, QueueEvidence
from app.backtesting.order_lifecycle import OrderLifecycle, OrderStatus, TimeInForce, stop_triggered, tif_after_execution
from app.backtesting.portfolio import Portfolio, PortfolioSnapshot, RiskViolation
from app.backtesting.queue_lifecycle import QueueLifecycleState
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
    """Replay source events with point-in-time execution and persistent open orders."""

    def __init__(self, config: EventReplayConfig | None = None, *, execution: ExecutionSimulator | None = None,
                 portfolio: Portfolio | None = None) -> None:
        self.config = config or EventReplayConfig()
        self.execution = execution
        self.portfolio = portfolio
        self._latest_events: dict[str, MarketEvent] = {}
        self._latest_books: dict[str, tuple[int, OrderBook]] = {}
        self._risk_blocks = 0
        self._order_lifecycles: dict[str, OrderLifecycle] = {}
        self._open_orders: dict[str, SimOrder] = {}
        self._reserved_margin: dict[str, float] = {}
        self._dynamic_queue_ahead: dict[str, int] = {}
        self._queue_lifecycles: dict[str, QueueLifecycleState] = {}

    @property
    def order_states(self) -> Mapping[str, object]:
        return {order_id: lifecycle.state for order_id, lifecycle in self._order_lifecycles.items()}

    @property
    def open_orders(self) -> Mapping[str, SimOrder]:
        return dict(self._open_orders)

    @property
    def queue_states(self) -> Mapping[str, QueueLifecycleState]:
        return dict(self._queue_lifecycles)

    @staticmethod
    def _event_price(event: MarketEvent, side: ExecutionSide | None = None) -> float | None:
        bid, ask = event.payload.get("bid"), event.payload.get("ask")
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
        bids, asks = event.payload.get("bids"), event.payload.get("asks")
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

    def _apply_queue_evidence(self, event: MarketEvent) -> None:
        raw = event.payload.get("queue_evidence")
        if not isinstance(raw, (list, tuple)):
            return
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            try:
                price = float(item["price"])
                evidence = QueueEvidence(price=price,
                    executed_quantity=int(item.get("executed_quantity", 0)),
                    cancelled_quantity_ahead=int(item.get("cancelled_quantity_ahead", 0)))
            except (KeyError, TypeError, ValueError):
                continue
            for order_id, order in tuple(self._open_orders.items()):
                if order.instrument != event.instrument or order.order_type != OrderType.LIMIT or order.limit_price != evidence.price:
                    continue
                state = self._queue_lifecycles.get(order_id)
                if state is None:
                    state = QueueLifecycleState(self._dynamic_queue_ahead.get(order_id, order.queue_ahead_quantity))
                if self.execution is not None:
                    state = state.advance(evidence)
                self._queue_lifecycles[order_id] = state
                self._dynamic_queue_ahead[order_id] = state.queue_ahead_quantity

    def _update_market_state(self, event: MarketEvent) -> None:
        self._latest_events[event.instrument] = event
        if event.event_type == EventType.DEPTH:
            book = self._book_from_event(event)
            if book is not None:
                self._latest_books[event.instrument] = (event.timestamp_ns, book)
            self._apply_queue_evidence(event)

    def _order_reference_price(self, order: SimOrder, observed: MarketEvent) -> float | None:
        book_state = self._latest_books.get(order.instrument)
        if book_state is not None and book_state[0] == observed.timestamp_ns:
            levels = book_state[1].asks if order.side == ExecutionSide.BUY else book_state[1].bids
            if levels:
                return levels[0].price
        return self._event_price(observed, order.side)

    def _lifecycle(self, order: SimOrder, timestamp_ns: int) -> OrderLifecycle:
        lifecycle = self._order_lifecycles.get(order.order_id)
        if lifecycle is None:
            lifecycle = OrderLifecycle(order)
            self._order_lifecycles[order.order_id] = lifecycle
            lifecycle.accept(timestamp_ns)
        elif lifecycle.state.status == OrderStatus.SUBMITTED:
            lifecycle.accept(timestamp_ns)
        return lifecycle

    def _submit_effective_order(self, order: SimOrder, event: MarketEvent) -> SimOrder:
        return SimOrder(order_id=order.order_id, instrument=order.instrument, side=order.side,
            quantity=order.quantity, order_type=order.order_type, limit_price=order.limit_price,
            stop_price=order.stop_price, submitted_at_ns=max(order.submitted_at_ns, event.timestamp_ns),
            queue_ahead_quantity=order.queue_ahead_quantity, time_in_force=order.time_in_force)

    def _execution_order(self, order: SimOrder) -> SimOrder:
        queue = self._dynamic_queue_ahead.get(order.order_id, order.queue_ahead_quantity)
        if queue == order.queue_ahead_quantity:
            return order
        return SimOrder(order_id=order.order_id, instrument=order.instrument, side=order.side,
            quantity=order.quantity, order_type=order.order_type, limit_price=order.limit_price,
            stop_price=order.stop_price, submitted_at_ns=order.submitted_at_ns,
            queue_ahead_quantity=queue, time_in_force=order.time_in_force)

    def cancel_order(self, order_id: str, timestamp_ns: int, reason: str = "cancelled") -> None:
        """Cancel an open order and close its current queue generation."""
        lifecycle = self._order_lifecycles.get(order_id)
        order = self._open_orders.get(order_id)
        if lifecycle is None or order is None:
            raise KeyError(f"open order not found: {order_id}")
        lifecycle.cancel(timestamp_ns, reason)
        state = self._queue_lifecycles.get(order_id)
        if state is not None:
            self._queue_lifecycles[order_id] = state.cancel()
        if self.portfolio is not None:
            self.portfolio.release_margin(order_id)
        self._reserved_margin.pop(order_id, None)
        self._open_orders.pop(order_id, None)
        self._dynamic_queue_ahead.pop(order_id, None)

    def replace_order(self, order_id: str, replacement: SimOrder, timestamp_ns: int, queue_ahead_quantity: int | None = None) -> SimOrder:
        """Cancel/replace an order and create a new queue generation for the replacement."""
        lifecycle = self._order_lifecycles.get(order_id)
        old = self._open_orders.get(order_id)
        if lifecycle is None or old is None:
            raise KeyError(f"open order not found: {order_id}")
        if replacement.order_id == order_id:
            raise ValueError("replacement must use a new order_id")
        effective = self._submit_effective_order(replacement, MarketEvent(timestamp_ns, replacement.instrument, EventType.CUSTOM, {}, None, None))
        lifecycle.replace(effective, timestamp_ns)
        prior_queue = self._queue_lifecycles.get(order_id, QueueLifecycleState(self._dynamic_queue_ahead.get(order_id, old.queue_ahead_quantity)))
        self._queue_lifecycles[order_id] = prior_queue.cancel()
        if self.portfolio is not None:
            self.portfolio.release_margin(order_id)
        self._reserved_margin.pop(order_id, None)
        self._open_orders.pop(order_id, None)
        self._dynamic_queue_ahead.pop(order_id, None)
        new_lifecycle = OrderLifecycle(effective)
        new_lifecycle.accept(timestamp_ns)
        self._order_lifecycles[effective.order_id] = new_lifecycle
        queue = effective.queue_ahead_quantity if queue_ahead_quantity is None else queue_ahead_quantity
        if queue < 0:
            raise ValueError("queue_ahead_quantity cannot be negative")
        self._queue_lifecycles[effective.order_id] = prior_queue.reinsert(queue)
        self._dynamic_queue_ahead[effective.order_id] = queue
        self._open_orders[effective.order_id] = effective
        return effective

    def _try_execute_orders(self, orders: Iterable[SimOrder], event: MarketEvent) -> tuple[SimFill, ...]:
        if self.execution is None or self.portfolio is None:
            return ()
        candidates = []
        lifecycles = {}
        for order in orders:
            lifecycle = self._order_lifecycles.get(order.order_id)
            if lifecycle is None or lifecycle.state.terminal:
                continue
            observed = self._latest_events.get(order.instrument)
            if observed is None or observed.timestamp_ns > event.timestamp_ns:
                continue
            observed_price = self._event_price(observed, None)
            if order.order_type == OrderType.STOP and (observed_price is None or not stop_triggered(order, observed_price)):
                continue
            reference_price = self._order_reference_price(order, observed)
            if reference_price is None:
                continue
            candidates.append((self._execution_order(order), observed, reference_price))
            lifecycles[order.order_id] = lifecycle
        fills: list[SimFill] = []
        results = {}
        for order, observed, _ in candidates:
            book_state = self._latest_books.get(order.instrument)
            if book_state is not None and book_state[0] == observed.timestamp_ns:
                result = self.execution.execute_depth(order, book_state[1], event.timestamp_ns)
                if order.time_in_force == TimeInForce.FOK and result.remaining_quantity > 0:
                    lifecycle = lifecycles[order.order_id]
                    if lifecycle.state.status in {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED}:
                        lifecycle.reject(result.reason or "FOK not fully executable", event.timestamp_ns)
                    results[order.order_id] = result
                    continue
                fills.extend(result.fills); results[order.order_id] = result
            else:
                price = self._event_price(observed, order.side)
                if price is not None:
                    executable = True
                    reason = None
                    if order.order_type == OrderType.LIMIT:
                        if order.limit_price is None:
                            executable = False
                            reason = "limit_price is required"
                        elif order.side == ExecutionSide.BUY and price > order.limit_price:
                            executable = False
                            reason = "limit price not executable"
                        elif order.side == ExecutionSide.SELL and price < order.limit_price:
                            executable = False
                            reason = "limit price not executable"
                    if executable:
                        fills.append(self.execution.execute(order, price, event.timestamp_ns))
                        results[order.order_id] = type("ExecutionResult", (), {"remaining_quantity": 0, "rejected": False, "reason": None})()
                    else:
                        results[order.order_id] = type("ExecutionResult", (), {"remaining_quantity": order.quantity, "rejected": False, "reason": reason})()
        try:
            if fills:
                marks = {i: self._event_price(e, None) for i, e in self._latest_events.items()}
                self.portfolio.apply_fills_atomic(fills, {k: v for k, v in marks.items() if v is not None and v > 0})
        except RiskViolation:
            self._risk_blocks += 1
            for order, _, _ in candidates:
                lifecycle = lifecycles[order.order_id]
                if not lifecycle.state.terminal: lifecycle.reject("atomic execution risk violation", event.timestamp_ns)
                self.portfolio.release_margin(order.order_id)
                self._reserved_margin.pop(order.order_id, None); self._open_orders.pop(order.order_id, None)
                self._dynamic_queue_ahead.pop(order.order_id, None); self._queue_lifecycles.pop(order.order_id, None)
            return ()
        filled_by_order: dict[str, int] = {}
        for fill in fills: filled_by_order[fill.order_id] = filled_by_order.get(fill.order_id, 0) + fill.quantity
        for order, _, _ in candidates:
            lifecycle = lifecycles[order.order_id]
            for fill in (f for f in fills if f.order_id == order.order_id): lifecycle.apply_fill(fill)
            result = results.get(order.order_id)
            if not lifecycle.state.terminal and result is not None:
                if order.time_in_force == TimeInForce.IOC: lifecycle.cancel(event.timestamp_ns, result.reason or "IOC residual cancelled")
                elif order.time_in_force == TimeInForce.FOK and lifecycle.state.status in {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED}: lifecycle.reject(result.reason or "FOK residual rejected", event.timestamp_ns)
            remaining = lifecycle.state.remaining_quantity
            terminal_action = tif_after_execution(order.time_in_force, remaining)
            if terminal_action == OrderStatus.CANCELLED and not lifecycle.state.terminal: lifecycle.cancel(event.timestamp_ns, "IOC residual cancelled")
            elif terminal_action == OrderStatus.REJECTED and not lifecycle.state.terminal: lifecycle.reject("FOK residual rejected", event.timestamp_ns)
            filled_qty = filled_by_order.get(order.order_id, 0); reserved = self._reserved_margin.get(order.order_id, 0.0)
            if filled_qty and reserved:
                release = reserved * filled_qty / order.quantity
                self.portfolio.release_margin(order.order_id, release); self._reserved_margin[order.order_id] = max(0.0, reserved - release)
            if lifecycle.state.terminal:
                if self._reserved_margin.get(order.order_id, 0.0): self.portfolio.release_margin(order.order_id)
                self._reserved_margin.pop(order.order_id, None); self._open_orders.pop(order.order_id, None); self._dynamic_queue_ahead.pop(order.order_id, None); self._queue_lifecycles.pop(order.order_id, None)
            else:
                self._open_orders[order.order_id] = order
                state = self._queue_lifecycles.get(order.order_id, QueueLifecycleState(order.queue_ahead_quantity))
                self._queue_lifecycles[order.order_id] = state
                self._dynamic_queue_ahead[order.order_id] = state.queue_ahead_quantity
        return tuple(fills)

    def _execute_decision(self, decision: StrategyDecision, event: MarketEvent) -> tuple[SimFill, ...]:
        if self.execution is None or self.portfolio is None or not decision.orders: return ()
        effective_orders = []
        try:
            for raw_order in decision.orders:
                if not isinstance(raw_order, SimOrder): raise TypeError("strategy orders must be SimOrder instances")
                order = self._submit_effective_order(raw_order, event); lifecycle = self._lifecycle(order, order.submitted_at_ns)
                if lifecycle.state.terminal: continue
                if order.order_id not in self._reserved_margin:
                    observed = self._latest_events.get(order.instrument); reference = self._order_reference_price(order, observed) if observed is not None else None
                    if reference is None: continue
                    reservation = order.quantity * reference * self.portfolio.risk_config.initial_margin_rate
                    self.portfolio.reserve_margin(order.order_id, reservation); self._reserved_margin[order.order_id] = reservation
                self._open_orders[order.order_id] = order
                self._queue_lifecycles.setdefault(order.order_id, QueueLifecycleState(order.queue_ahead_quantity))
                self._dynamic_queue_ahead[order.order_id] = self._queue_lifecycles[order.order_id].queue_ahead_quantity
                effective_orders.append(order)
        except RiskViolation:
            self._risk_blocks += 1
            for order in effective_orders:
                self.portfolio.release_margin(order.order_id); self._reserved_margin.pop(order.order_id, None); self._open_orders.pop(order.order_id, None); self._dynamic_queue_ahead.pop(order.order_id, None); self._queue_lifecycles.pop(order.order_id, None)
                lifecycle = self._order_lifecycles.get(order.order_id)
                if lifecycle is not None and not lifecycle.state.terminal: lifecycle.reject("atomic order reservation risk violation", event.timestamp_ns)
            return ()
        return self._try_execute_orders(effective_orders, event)

    def market_state(self) -> Mapping[str, object]:
        return {
            "latest_events": [{"timestamp_ns": e.timestamp_ns, "instrument": e.instrument, "event_type": e.event_type.value, "payload": dict(e.payload), "sequence": e.sequence, "source": e.source} for e in self._latest_events.values()],
            "latest_books": [{"instrument": instrument, "timestamp_ns": ts, "bids": [(x.price, x.quantity) for x in book.bids], "asks": [(x.price, x.quantity) for x in book.asks]} for instrument, (ts, book) in self._latest_books.items()],
            "open_orders": [{"order_id": o.order_id, "instrument": o.instrument, "side": o.side.value, "quantity": o.quantity, "order_type": o.order_type.value, "limit_price": o.limit_price, "stop_price": o.stop_price, "submitted_at_ns": o.submitted_at_ns, "queue_ahead_quantity": o.queue_ahead_quantity, "dynamic_queue_ahead": self._dynamic_queue_ahead.get(o.order_id, o.queue_ahead_quantity), "queue_generation": self._queue_lifecycles.get(o.order_id, QueueLifecycleState(o.queue_ahead_quantity)).generation, "time_in_force": o.time_in_force.value} for o in self._open_orders.values()],
            "reserved_margin": dict(self._reserved_margin),
        }

    def restore_market_state(self, state: Mapping[str, object]) -> None:
        self._latest_events.clear(); self._latest_books.clear(); self._open_orders.clear(); self._reserved_margin.clear(); self._dynamic_queue_ahead.clear(); self._queue_lifecycles.clear()
        for raw in state.get("latest_events", []):
            e = MarketEvent(int(raw["timestamp_ns"]), str(raw["instrument"]), EventType(raw["event_type"]), dict(raw.get("payload", {})), raw.get("sequence"), raw.get("source")); self._latest_events[e.instrument] = e
        for raw in state.get("latest_books", []):
            book = OrderBook(bids=tuple(DepthLevel(float(p), int(q)) for p, q in raw.get("bids", [])), asks=tuple(DepthLevel(float(p), int(q)) for p, q in raw.get("asks", []))); self._latest_books[str(raw["instrument"])] = (int(raw["timestamp_ns"]), book)
        for raw in state.get("open_orders", []):
            order = SimOrder(order_id=str(raw["order_id"]), instrument=str(raw["instrument"]), side=ExecutionSide(raw["side"]), quantity=int(raw["quantity"]), order_type=OrderType(raw["order_type"]), limit_price=raw.get("limit_price"), stop_price=raw.get("stop_price"), submitted_at_ns=int(raw["submitted_at_ns"]), queue_ahead_quantity=int(raw.get("queue_ahead_quantity", 0)), time_in_force=TimeInForce(raw["time_in_force"]))
            self._open_orders[order.order_id] = order
            queue = int(raw.get("dynamic_queue_ahead", order.queue_ahead_quantity)); generation = int(raw.get("queue_generation", 0))
            self._dynamic_queue_ahead[order.order_id] = queue; self._queue_lifecycles[order.order_id] = QueueLifecycleState(queue, generation, True)
        self._reserved_margin.update({str(k): float(v) for k, v in state.get("reserved_margin", {}).items()})

    def run(self, events: Iterable[MarketEvent], strategy: object, *, state: Mapping[str, object] | None = None, start_event_index: int = 0, checkpoint_callback: Callable[[int, int, Mapping[str, object]], None] | None = None, checkpoint_interval: int | None = None) -> ReplayStats:
        if start_event_index < 0: raise ValueError("start_event_index cannot be negative")
        if checkpoint_interval is not None and checkpoint_interval <= 0: raise ValueError("checkpoint_interval must be positive")
        context_state = dict(state or {}); history = []; seen = dispatched = decisions = orders = fill_count = 0; first = last = None; previous_key = None; started = False; self._risk_blocks = 0
        if start_event_index == 0:
            self._latest_events.clear(); self._latest_books.clear(); self._order_lifecycles.clear(); self._open_orders.clear(); self._reserved_margin.clear(); self._dynamic_queue_ahead.clear(); self._queue_lifecycles.clear()
        for raw_index, raw_event in enumerate(events):
            seen += 1; timestamp_ns = raw_event.timestamp_ns if self.config.timestamp_unit == "ns" else self.config.to_ns(raw_event.timestamp_ns)
            event = raw_event if timestamp_ns == raw_event.timestamp_ns else MarketEvent(timestamp_ns, raw_event.instrument, raw_event.event_type, raw_event.payload, raw_event.sequence, raw_event.source)
            if self.config.include_event_types is not None and event.event_type not in self.config.include_event_types: continue
            if raw_index < start_event_index: continue
            sequence_key = event.sequence if event.sequence is not None else -1; source_key = event.source or ""; key = (event.timestamp_ns, sequence_key, source_key, event.event_type.value)
            if previous_key is not None and key < previous_key: raise ValueError("events must be ordered by timestamp, sequence, source, and type")
            previous_key = key
            if not started:
                starter = getattr(strategy, "on_start", None)
                if callable(starter): starter(StrategyContext(event.timestamp_ns, tuple(), dict(context_state)))
                started = True
            first = event.timestamp_ns if first is None else first; last = event.timestamp_ns; self._update_market_state(event); fill_count += len(self._try_execute_orders(tuple(self._open_orders.values()), event))
            decision = None; handler = getattr(strategy, "on_event", None)
            if callable(handler):
                decision = handler(event, StrategyContext(event.timestamp_ns, tuple(history), dict(context_state)))
                if decision is not None and not isinstance(decision, StrategyDecision): raise TypeError("event strategy must return StrategyDecision or None")
                validate_decision(decision)
                if decision is not None: decisions += 1; orders += len(decision.orders); fill_count += len(self._execute_decision(decision, event))
            elif callable(strategy): strategy(event, context_state)
            else: raise TypeError("strategy must be callable or implement on_event")
            history.append(event); dispatched += 1
            if checkpoint_callback is not None and checkpoint_interval and dispatched % checkpoint_interval == 0:
                checkpoint_callback(raw_index + 1, dispatched, {"context_state": dict(context_state), "market_state": self.market_state()})
        if started:
            finisher = getattr(strategy, "on_end", None)
            if callable(finisher): finisher(StrategyContext(last if last is not None else 0, tuple(history), dict(context_state)))
        return ReplayStats(seen, dispatched, first, last, decisions, orders, fill_count, self._risk_blocks, self.portfolio.snapshot() if self.portfolio is not None else None)
