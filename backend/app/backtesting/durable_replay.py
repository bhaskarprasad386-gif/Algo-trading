"""Durable replay wrapper that journals events, decisions and resumable checkpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Mapping

from app.backtesting.event_engine import EventBacktestEngine, ReplayStats
from app.backtesting.events import MarketEvent
from app.backtesting.ledger import BacktestLedger, Checkpoint, LedgerRecord
from app.backtesting.order_lifecycle import OrderLifecycle
from app.backtesting.strategy import StrategyDecision, restore_strategy_state, strategy_state


class DurableEventBacktestEngine:
    """Event engine with an append-only audit journal and true stateful resume."""

    def __init__(self, engine: EventBacktestEngine, ledger: BacktestLedger,
                 run_id: str, *, checkpoint_interval: int = 1) -> None:
        if not run_id.strip(): raise ValueError("run_id is required")
        if checkpoint_interval <= 0: raise ValueError("checkpoint_interval must be positive")
        self.engine, self.ledger, self.run_id = engine, ledger, run_id
        self.checkpoint_interval = checkpoint_interval

    def start_run(self, strategy: object, initial_capital: float) -> None:
        strategy_id = str(getattr(strategy, "strategy_id", strategy.__class__.__name__))
        strategy_version = str(getattr(strategy, "strategy_version", "unknown"))
        self.ledger.start_run(self.run_id, strategy_id, strategy_version, initial_capital,
                              metadata={"checkpoint_interval": self.checkpoint_interval})

    @staticmethod
    def _event_key(event: MarketEvent) -> tuple[object, ...]:
        """Stable audit identity for one source event; used only for journal idempotency."""
        return (event.timestamp_ns, event.instrument, event.event_type.value,
                event.sequence, event.source)

    def _journal_strategy(self, strategy: object):
        ledger, run_id = self.ledger, self.run_id
        existing_event_keys = {
            (record.timestamp_ns,
             record.payload.get("instrument"),
             record.payload.get("event_type"),
             record.payload.get("sequence"),
             record.payload.get("source"))
            for record in ledger.records(run_id, "EVENT")
        }
        class JournalStrategy:
            strategy_id = getattr(strategy, "strategy_id", strategy.__class__.__name__)
            strategy_version = getattr(strategy, "strategy_version", "unknown")
            def on_start(self, context):
                starter = getattr(strategy, "on_start", None)
                if callable(starter): starter(context)
            def on_event(self, event, context):
                handler = getattr(strategy, "on_event", None)
                if not callable(handler):
                    decision = None
                else:
                    decision = handler(event, context)
                    if decision is not None:
                        if not isinstance(decision, StrategyDecision): raise TypeError("event strategy must return StrategyDecision or None")
                key = DurableEventBacktestEngine._event_key(event)
                if key not in existing_event_keys:
                    ledger.append(LedgerRecord(run_id, "EVENT", event.timestamp_ns,
                        {"instrument": event.instrument, "event_type": event.event_type.value,
                         "sequence": event.sequence, "source": event.source}))
                    existing_event_keys.add(key)
                if decision is not None:
                    ledger.append(LedgerRecord(run_id, "DECISION", event.timestamp_ns,
                        {"action": decision.action, "orders": len(decision.orders), "metadata": dict(decision.metadata)}))
                return decision
            def on_end(self, context):
                finisher = getattr(strategy, "on_end", None)
                if callable(finisher): finisher(context)
        return JournalStrategy()

    def _lifecycle_state(self) -> list[Mapping[str, object]]:
        """Serialize every known order lifecycle, including open partial/STOP orders."""
        return [lifecycle.export_state() for lifecycle in self.engine._order_lifecycles.values()]

    def _restore_lifecycle_state(self, raw_state: object) -> None:
        """Restore lifecycle state before replaying the next source event."""
        self.engine._order_lifecycles.clear()
        if raw_state is None:
            return
        if not isinstance(raw_state, (list, tuple)):
            raise ValueError("invalid order_lifecycle_state checkpoint")
        for raw in raw_state:
            if not isinstance(raw, Mapping):
                raise ValueError("invalid order lifecycle checkpoint entry")
            lifecycle = OrderLifecycle.restore_state(raw)
            self.engine._order_lifecycles[lifecycle.state.order.order_id] = lifecycle

    def _save_checkpoint(self, source_cursor: int, dispatched: int, extra: Mapping[str, object], strategy: object) -> None:
        state = dict(extra)
        state["source_cursor"] = source_cursor
        state["events_dispatched"] = dispatched
        state["strategy_state"] = dict(strategy_state(strategy))
        state["portfolio_state"] = dict(self.engine.portfolio.export_state()) if self.engine.portfolio is not None else None
        state["order_lifecycle_state"] = self._lifecycle_state()
        self.ledger.checkpoint(Checkpoint(self.run_id, source_cursor, int(extra.get("timestamp_ns", 0)), state))

    def run(self, events: Iterable[MarketEvent], strategy: object, *, state: Mapping[str, object] | None = None,
            resume: bool = False) -> ReplayStats:
        source_events = tuple(events)
        checkpoint = self.ledger.load_checkpoint(self.run_id) if resume else None
        context_state = dict(state or {})
        start_cursor = 0
        if resume:
            if checkpoint is None: raise ValueError("no checkpoint available for resume")
            saved = checkpoint.state
            start_cursor = int(saved.get("source_cursor", checkpoint.event_index))
            saved_portfolio = saved.get("portfolio_state")
            if saved_portfolio is not None and self.engine.portfolio is not None:
                self.engine.portfolio.restore_state(saved_portfolio)
            restore_strategy_state(strategy, dict(saved.get("strategy_state", {})))
            saved_context = saved.get("context_state")
            if isinstance(saved_context, Mapping): context_state = dict(saved_context)
            market_state = saved.get("market_state")
            if isinstance(market_state, Mapping): self.engine.restore_market_state(market_state)
            self._restore_lifecycle_state(saved.get("order_lifecycle_state"))
            self.ledger.append(LedgerRecord(self.run_id, "RUN_RESUME", checkpoint.timestamp_ns,
                                            {"source_cursor": start_cursor, "event_index": checkpoint.event_index,
                                             "open_orders_restored": sum(not x.state.terminal for x in self.engine._order_lifecycles.values())}))
        elif source_events:
            self.ledger.append(LedgerRecord(self.run_id, "RUN_START", source_events[0].timestamp_ns,
                                            {"event_count": len(source_events)}))

        journaled = self._journal_strategy(strategy)
        def checkpoint_callback(source_cursor: int, dispatched: int, extra: Mapping[str, object]) -> None:
            payload = dict(extra); payload["timestamp_ns"] = source_events[source_cursor - 1].timestamp_ns if source_cursor else 0
            self._save_checkpoint(source_cursor, dispatched, payload, strategy)

        result = self.engine.run(source_events, journaled, state=context_state, start_event_index=start_cursor,
                                 checkpoint_callback=checkpoint_callback, checkpoint_interval=self.checkpoint_interval)
        final_state = {
            "source_cursor": len(source_events), "events_seen": result.events_seen,
            "events_dispatched": result.events_dispatched, "decisions_emitted": result.decisions_emitted,
            "orders_submitted": result.orders_submitted, "fills": result.fills, "risk_blocks": result.risk_blocks,
            "context_state": context_state, "strategy_state": dict(strategy_state(strategy)),
            "portfolio_state": dict(self.engine.portfolio.export_state()) if self.engine.portfolio is not None else None,
            "market_state": self.engine.market_state(),
            "order_lifecycle_state": self._lifecycle_state(),
            "final_snapshot": asdict(result.final_snapshot) if result.final_snapshot is not None else None,
        }
        self.ledger.checkpoint(Checkpoint(self.run_id, len(source_events), result.last_timestamp_ns or 0, final_state))
        self.ledger.append(LedgerRecord(self.run_id, "RUN_END", result.last_timestamp_ns or 0,
                                        {"events_dispatched": result.events_dispatched, "fills": result.fills,
                                         "risk_blocks": result.risk_blocks}))
        return result

    def resume_cursor(self) -> int:
        checkpoint = self.ledger.load_checkpoint(self.run_id)
        return 0 if checkpoint is None else int(checkpoint.state.get("source_cursor", checkpoint.event_index))

    def checkpoint_state(self) -> Mapping[str, object] | None:
        checkpoint = self.ledger.load_checkpoint(self.run_id)
        return None if checkpoint is None else dict(checkpoint.state)
