"""Durable replay wrapper that journals events, decisions and checkpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Mapping

from app.backtesting.event_engine import EventBacktestEngine, ReplayStats
from app.backtesting.events import MarketEvent
from app.backtesting.ledger import BacktestLedger, Checkpoint, LedgerRecord
from app.backtesting.strategy import StrategyDecision


class DurableEventBacktestEngine:
    """Event engine with an append-only audit journal and resumable cursor."""

    def __init__(self, engine: EventBacktestEngine, ledger: BacktestLedger,
                 run_id: str, *, checkpoint_interval: int = 1) -> None:
        if not run_id.strip():
            raise ValueError("run_id is required")
        if checkpoint_interval <= 0:
            raise ValueError("checkpoint_interval must be positive")
        self.engine = engine
        self.ledger = ledger
        self.run_id = run_id
        self.checkpoint_interval = checkpoint_interval

    def start_run(self, strategy: object, initial_capital: float) -> None:
        strategy_id = str(getattr(strategy, "strategy_id", strategy.__class__.__name__))
        strategy_version = str(getattr(strategy, "strategy_version", "unknown"))
        self.ledger.start_run(
            self.run_id, strategy_id, strategy_version, initial_capital,
            metadata={"checkpoint_interval": self.checkpoint_interval},
        )

    def run(self, events: Iterable[MarketEvent], strategy: object,
            *, state: Mapping[str, object] | None = None) -> ReplayStats:
        source_events = tuple(events)
        ledger = self.ledger
        run_id = self.run_id
        ledger.append(LedgerRecord(
            run_id, "RUN_START", source_events[0].timestamp_ns if source_events else 0,
            {"event_count": len(source_events)},
        ))

        class JournalStrategy:
            strategy_id = getattr(strategy, "strategy_id", strategy.__class__.__name__)
            strategy_version = getattr(strategy, "strategy_version", "unknown")

            def on_start(self, context):
                starter = getattr(strategy, "on_start", None)
                if callable(starter):
                    starter(context)

            def on_event(self, event, context):
                ledger.append(LedgerRecord(
                    run_id, "EVENT", event.timestamp_ns,
                    {"instrument": event.instrument, "event_type": event.event_type.value,
                     "sequence": event.sequence, "source": event.source},
                ))
                handler = getattr(strategy, "on_event", None)
                if not callable(handler):
                    return None
                decision = handler(event, context)
                if decision is not None:
                    if not isinstance(decision, StrategyDecision):
                        raise TypeError("event strategy must return StrategyDecision or None")
                    ledger.append(LedgerRecord(
                        run_id, "DECISION", event.timestamp_ns,
                        {"action": decision.action, "orders": len(decision.orders),
                         "metadata": dict(decision.metadata)},
                    ))
                return decision

            def on_end(self, context):
                finisher = getattr(strategy, "on_end", None)
                if callable(finisher):
                    finisher(context)

        journaled = JournalStrategy()
        result = self.engine.run(source_events, journaled, state=state)
        ledger.checkpoint(Checkpoint(
            run_id, result.events_dispatched, result.last_timestamp_ns or 0,
            {
                "events_seen": result.events_seen,
                "events_dispatched": result.events_dispatched,
                "decisions_emitted": result.decisions_emitted,
                "orders_submitted": result.orders_submitted,
                "fills": result.fills,
                "risk_blocks": result.risk_blocks,
                "state": dict(state or {}),
                "final_snapshot": asdict(result.final_snapshot) if result.final_snapshot is not None else None,
            },
        ))
        ledger.append(LedgerRecord(
            run_id, "RUN_END", result.last_timestamp_ns or 0,
            {"events_dispatched": result.events_dispatched, "fills": result.fills,
             "risk_blocks": result.risk_blocks},
        ))
        return result

    def resume_cursor(self) -> int:
        checkpoint = self.ledger.load_checkpoint(self.run_id)
        return 0 if checkpoint is None else checkpoint.event_index
