"""Durable replay wrapper that journals events, decisions and resumable checkpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Mapping

from app.backtesting.event_engine import EventBacktestEngine, ReplayStats
from app.backtesting.events import MarketEvent
from app.backtesting.ledger import BacktestLedger, Checkpoint, LedgerRecord, LEDGER_SCHEMA_VERSION
from app.backtesting.order_lifecycle import OrderLifecycle
from app.backtesting.execution import ExecutionSide
from app.backtesting.portfolio import Portfolio, TradeRecord
from app.backtesting.strategy import StrategyDecision, restore_strategy_state, strategy_state


class DurableEventBacktestEngine:
    """Event engine with an append-only audit journal and true stateful resume."""

    def __init__(self, engine: EventBacktestEngine, ledger: BacktestLedger,
                 run_id: str, *, checkpoint_interval: int = 1) -> None:
        if not run_id.strip(): raise ValueError("run_id is required")
        if checkpoint_interval <= 0: raise ValueError("checkpoint_interval must be positive")
        self.engine, self.ledger, self.run_id = engine, ledger, run_id
        self.checkpoint_interval = checkpoint_interval

    @staticmethod
    def _event_key(event: MarketEvent) -> tuple[object, ...]:
        """Stable source identity used for replay idempotency and provenance."""
        return (event.timestamp_ns, event.instrument, event.event_type.value,
                event.sequence, event.source)

    def start_run(self, strategy: object, initial_capital: float, *,
                  schema_version: int = LEDGER_SCHEMA_VERSION,
                  data_source_fingerprint: str | None = None) -> None:
        strategy_id = str(getattr(strategy, "strategy_id", strategy.__class__.__name__))
        strategy_version = str(getattr(strategy, "strategy_version", "unknown"))
        self.ledger.start_run(self.run_id, strategy_id, strategy_version, initial_capital,
                              metadata={"checkpoint_interval": self.checkpoint_interval},
                              schema_version=schema_version,
                              data_source_fingerprint=data_source_fingerprint)

    def _journal_strategy(self, strategy: object):
        ledger, run_id = self.ledger, self.run_id
        existing_event_keys = {(record.timestamp_ns, record.payload.get("instrument"),
                                record.payload.get("event_type"), record.payload.get("sequence"),
                                record.payload.get("source")) for record in ledger.records(run_id, "EVENT")}
        class JournalStrategy:
            strategy_id = getattr(strategy, "strategy_id", strategy.__class__.__name__)
            strategy_version = getattr(strategy, "strategy_version", "unknown")
            def on_start(self, context):
                starter = getattr(strategy, "on_start", None)
                if callable(starter): starter(context)
            def on_event(self, event, context):
                key = DurableEventBacktestEngine._event_key(event)
                if key in existing_event_keys: return None
                handler = getattr(strategy, "on_event", None)
                decision = handler(event, context) if callable(handler) else None
                if decision is not None and not isinstance(decision, StrategyDecision):
                    raise TypeError("event strategy must return StrategyDecision or None")
                ledger.append(LedgerRecord(run_id, "EVENT", event.timestamp_ns,
                    {"instrument": event.instrument, "event_type": event.event_type.value,
                     "sequence": event.sequence, "source": event.source}))
                existing_event_keys.add(key)
                if decision is not None:
                    ledger.append(LedgerRecord(run_id, "DECISION", event.timestamp_ns,
                        {"event_identity": {"timestamp_ns": event.timestamp_ns,
                                             "instrument": event.instrument,
                                             "event_type": event.event_type.value,
                                             "sequence": event.sequence,
                                             "source": event.source},
                         "action": decision.action, "orders": len(decision.orders),
                         "metadata": dict(decision.metadata)}))
                return decision
            def on_end(self, context):
                finisher = getattr(strategy, "on_end", None)
                if callable(finisher): finisher(context)
        return JournalStrategy()

    def _lifecycle_state(self) -> list[Mapping[str, object]]:
        return [lifecycle.export_state() for lifecycle in self.engine._order_lifecycles.values()]

    def _restore_lifecycle_state(self, raw_state: object) -> None:
        if raw_state is None: return
        if not isinstance(raw_state, (list, tuple)): raise ValueError("invalid order_lifecycle_state checkpoint")
        restored: dict[str, OrderLifecycle] = {}
        for raw in raw_state:
            if not isinstance(raw, Mapping): raise ValueError("invalid order lifecycle checkpoint entry")
            lifecycle = OrderLifecycle.restore_state(raw)
            order_id = lifecycle.state.order.order_id
            if order_id in restored: raise ValueError(f"duplicate order lifecycle checkpoint: {order_id}")
            restored[order_id] = lifecycle
        existing = self.engine._order_lifecycles
        if set(existing) != set(restored):
            raise ValueError("checkpoint lifecycle state does not match market state")
        for order_id, lifecycle in restored.items():
            if existing[order_id].to_dict() != lifecycle.to_dict():
                raise ValueError("checkpoint lifecycle state does not match market state")
        self.engine._order_lifecycles = restored

    @staticmethod
    def _trade_state(portfolio: Portfolio) -> list[Mapping[str, object]]:
        return [{"order_id": trade.order_id, "instrument": trade.instrument, "side": trade.side.value,
                 "quantity": trade.quantity, "price": trade.price, "gross_value": trade.gross_value,
                 "fee": trade.fee, "realized_pnl_delta": trade.realized_pnl_delta,
                 "cash_after": trade.cash_after, "equity_after": trade.equity_after,
                 "timestamp_ns": trade.timestamp_ns} for trade in portfolio.trades]

    @staticmethod
    def _restore_trade_state(portfolio: Portfolio, raw_state: object) -> None:
        if raw_state is None: return
        if not isinstance(raw_state, (list, tuple)): raise ValueError("invalid portfolio trade checkpoint")
        trades: list[TradeRecord] = []
        try:
            for raw in raw_state:
                if not isinstance(raw, Mapping): raise ValueError("invalid portfolio trade checkpoint entry")
                quantity = raw["quantity"]
                if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0: raise ValueError("invalid portfolio trade quantity")
                timestamp_ns = raw["timestamp_ns"]
                if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int): raise ValueError("invalid portfolio trade timestamp")
                trades.append(TradeRecord(order_id=str(raw["order_id"]), instrument=str(raw["instrument"]),
                    side=ExecutionSide(str(raw["side"])), quantity=quantity, price=float(raw["price"]),
                    gross_value=float(raw["gross_value"]), fee=float(raw["fee"]),
                    realized_pnl_delta=float(raw["realized_pnl_delta"]), cash_after=float(raw["cash_after"]),
                    equity_after=float(raw["equity_after"]), timestamp_ns=timestamp_ns))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid portfolio trade checkpoint") from exc
        portfolio._trades = trades

    def _save_checkpoint(self, source_cursor: int, dispatched: int, extra: Mapping[str, object], strategy: object) -> None:
        state = dict(extra)
        state["source_cursor"] = source_cursor
        state["events_dispatched"] = dispatched
        state["strategy_state"] = dict(strategy_state(strategy))
        if self.engine.portfolio is not None:
            state["portfolio_state"] = dict(self.engine.portfolio.export_state())
            state["portfolio_trades"] = self._trade_state(self.engine.portfolio)
        else:
            state["portfolio_state"] = None
            state["portfolio_trades"] = None
        state["order_lifecycle_state"] = self._lifecycle_state()
        self.ledger.checkpoint(Checkpoint(self.run_id, source_cursor, int(extra.get("timestamp_ns", 0)), state))

    def run(self, events: Iterable[MarketEvent], strategy: object, *, state: Mapping[str, object] | None = None,
            resume: bool = False, schema_version: int = LEDGER_SCHEMA_VERSION,
            data_source_fingerprint: str | None = None) -> ReplayStats:
        source_events = iter(events)
        checkpoint = self.ledger.load_checkpoint(self.run_id) if resume else None
        context_state = dict(state or {})
        start_cursor = 0; resume_timestamp = 0; expected_identity = None
        if resume:
            self.ledger.validate_resume(self.run_id, schema_version=schema_version,
                                        data_source_fingerprint=data_source_fingerprint)
            if checkpoint is None: raise ValueError("no checkpoint available for resume")
            saved = checkpoint.state
            start_cursor = int(saved.get("source_cursor", checkpoint.event_index))
            if start_cursor < 0: raise ValueError("invalid checkpoint source_cursor")
            expected_identity = saved.get("source_event_identity")
            if start_cursor > 0:
                if not isinstance(expected_identity, Mapping):
                    raise ValueError("checkpoint missing source_event_identity; restart required")
                expected_identity = tuple(expected_identity.get(k) for k in
                                          ("timestamp_ns", "instrument", "event_type", "sequence", "source"))
            resume_timestamp = checkpoint.timestamp_ns
            saved_portfolio = saved.get("portfolio_state")
            if saved_portfolio is not None and self.engine.portfolio is not None:
                self.engine.portfolio.restore_state(saved_portfolio)
                self._restore_trade_state(self.engine.portfolio, saved.get("portfolio_trades"))
            restore_strategy_state(strategy, dict(saved.get("strategy_state", {})))
            saved_context = saved.get("context_state")
            if isinstance(saved_context, Mapping): context_state = dict(saved_context)
            market_state = saved.get("market_state")
            if isinstance(market_state, Mapping): self.engine.restore_market_state(market_state)
            if self.engine.portfolio is not None:
                engine_reserved = dict(self.engine._reserved_margin)
                portfolio_reserved = dict(self.engine.portfolio._reserved_margin)
                if set(engine_reserved) != set(portfolio_reserved) or any(abs(float(engine_reserved[k]) - float(portfolio_reserved[k])) > 1e-9 for k in engine_reserved):
                    raise ValueError("checkpoint margin reservation mismatch between engine and portfolio")
            self._restore_lifecycle_state(saved.get("order_lifecycle_state"))
            self.ledger.append(LedgerRecord(self.run_id, "RUN_RESUME", checkpoint.timestamp_ns,
                {"source_cursor": start_cursor, "event_index": checkpoint.event_index,
                 "open_orders_restored": sum(not x.state.terminal for x in self.engine._order_lifecycles.values())}))

        last_source_event: MarketEvent | None = None
        def tracked_events():
            nonlocal last_source_event
            for raw_index, raw_event in enumerate(source_events):
                if resume and start_cursor > 0 and raw_index == start_cursor - 1:
                    if DurableEventBacktestEngine._event_key(raw_event) != expected_identity:
                        raise ValueError("resume source mismatch at checkpoint cursor")
                last_source_event = raw_event
                yield raw_event

        if not resume:
            try: first_source_event = next(source_events)
            except StopIteration: first_source_event = None
            if first_source_event is not None:
                from itertools import chain
                source_events = chain((first_source_event,), source_events)
                self.ledger.append(LedgerRecord(self.run_id, "RUN_START", first_source_event.timestamp_ns,
                    {"event_count": None, "streaming": True}))

        journaled = self._journal_strategy(strategy)
        def checkpoint_callback(source_cursor: int, dispatched: int, extra: Mapping[str, object]) -> None:
            payload = dict(extra)
            market_state = payload.get("market_state")
            if isinstance(market_state, Mapping):
                timestamps = [int(item.get("timestamp_ns", 0)) for item in market_state.get("latest_events", ()) if isinstance(item, Mapping)]
                payload["timestamp_ns"] = max(timestamps, default=0)
            else: payload["timestamp_ns"] = 0
            if last_source_event is not None:
                e = self._event_key(last_source_event)
                payload["source_event_identity"] = {"timestamp_ns": e[0], "instrument": e[1], "event_type": e[2], "sequence": e[3], "source": e[4]}
            self._save_checkpoint(source_cursor, dispatched, payload, strategy)

        try:
            result = self.engine.run(tracked_events(), journaled, state=context_state, start_event_index=start_cursor,
                                     checkpoint_callback=checkpoint_callback, checkpoint_interval=self.checkpoint_interval)
        except Exception as exc:
            if resume:
                self.ledger.append(LedgerRecord(self.run_id, "RUN_RESUME_FAILED", resume_timestamp,
                    {"source_cursor": start_cursor, "error_type": type(exc).__name__, "error": str(exc)}))
            raise

        final_cursor = result.events_seen
        final_state = {"source_cursor": final_cursor, "events_seen": result.events_seen,
            "events_dispatched": result.events_dispatched, "decisions_emitted": result.decisions_emitted,
            "orders_submitted": result.orders_submitted, "fills": result.fills, "risk_blocks": result.risk_blocks,
            "context_state": context_state, "strategy_state": dict(strategy_state(strategy)),
            "portfolio_state": dict(self.engine.portfolio.export_state()) if self.engine.portfolio is not None else None,
            "portfolio_trades": self._trade_state(self.engine.portfolio) if self.engine.portfolio is not None else None,
            "market_state": self.engine.market_state(), "order_lifecycle_state": self._lifecycle_state(),
            "final_snapshot": asdict(result.final_snapshot) if result.final_snapshot is not None else None}
        if last_source_event is not None:
            e = self._event_key(last_source_event)
            final_state["source_event_identity"] = {"timestamp_ns": e[0], "instrument": e[1], "event_type": e[2], "sequence": e[3], "source": e[4]}
        self.ledger.checkpoint(Checkpoint(self.run_id, final_cursor, result.last_timestamp_ns or 0, final_state))

    def resume_cursor(self) -> int:
        checkpoint = self.ledger.load_checkpoint(self.run_id)
        return 0 if checkpoint is None else int(checkpoint.state.get("source_cursor", checkpoint.event_index))