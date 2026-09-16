"""Restartable high-resolution replay with strategy and position restoration."""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any, Iterable, Protocol

from app.backtesting.checkpoint import CheckpointStore, ReplayCheckpoint
from app.backtesting.event_dedup import EventDedupStore
from app.backtesting.high_resolution_pnl import ExecutionFill, HighResolutionPositionLedger
from app.backtesting.universal import MarketEvent, streaming_events


class ResumableStrategy(Protocol):
    def on_event(self, event: MarketEvent) -> Any: ...


@dataclass(frozen=True)
class ResumeResult:
    events_processed: int
    signals_processed: int
    net_pnl: float
    resumed_from: tuple[int, int] | None


class RestartableHighResolutionRunner:
    """Checkpoint/resume runner with durable event identity and complete state."""

    def __init__(self, connection: sqlite3.Connection, run_id: str, position_ledger: HighResolutionPositionLedger | None = None) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        self.checkpoints = CheckpointStore(connection)
        self.dedup = EventDedupStore(connection)
        self.run_id = run_id
        self.positions = position_ledger or HighResolutionPositionLedger()

    @staticmethod
    def _restore(strategy: Any, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("invalid checkpoint state")
        strategy_state = state.get("strategy", state)
        restore = getattr(strategy, "restore_state", None)
        if callable(restore):
            if not isinstance(strategy_state, dict):
                raise ValueError("invalid strategy checkpoint state")
            restore(dict(strategy_state))

    @staticmethod
    def _snapshot(strategy: Any, positions: HighResolutionPositionLedger) -> dict[str, Any]:
        snapshot = getattr(strategy, "snapshot_state", None)
        strategy_state = snapshot() if callable(snapshot) else {}
        if not isinstance(strategy_state, dict):
            raise ValueError("strategy snapshot must be a dictionary")
        return {"strategy": dict(strategy_state), "positions": positions.snapshot_state()}

    def run(self, events: Iterable[MarketEvent], strategy: ResumableStrategy) -> ResumeResult:
        checkpoint = self.checkpoints.load(self.run_id)
        if checkpoint is not None:
            self._restore(strategy, checkpoint.state)
            positions_state = checkpoint.state.get("positions") if isinstance(checkpoint.state, dict) else None
            if positions_state is not None:
                self.positions.restore_state(positions_state)

        resume_key = None if checkpoint is None else (
            checkpoint.timestamp_ns,
            checkpoint.sequence,
            checkpoint.instrument,
            checkpoint.event_type,
        )
        processed = 0 if checkpoint is None else checkpoint.processed_events
        signals = 0

        for event in streaming_events(events):
            key = (event.timestamp_ns, event.sequence, event.instrument, event.event_type)
            if resume_key is not None:
                if checkpoint.instrument or checkpoint.event_type:
                    if key <= resume_key:
                        continue
                elif key[:2] <= resume_key[:2]:
                    continue
            if not self.dedup.mark_if_new(self.run_id, event.timestamp_ns, event.sequence, event.instrument, event.event_type):
                continue
            processed += 1
            signal = strategy.on_event(event)
            if signal is not None:
                side = str(getattr(signal, "side", signal.get("side") if isinstance(signal, dict) else "")).upper()
                quantity = getattr(signal, "quantity", signal.get("quantity") if isinstance(signal, dict) else 0)
                if type(quantity) is not int or quantity <= 0 or side not in {"BUY", "SELL"}:
                    raise ValueError("invalid strategy signal")
                raw_price = getattr(signal, "price", None) if not isinstance(signal, dict) else signal.get("price")
                price = event.context.get("price") if raw_price is None else raw_price
                if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
                    raise ValueError("execution price must be finite and positive")
                self.positions.add(ExecutionFill(side, event.instrument, quantity, float(price), event.timestamp_ns))
                signals += 1
            self.checkpoints.save(ReplayCheckpoint(
                self.run_id,
                event.timestamp_ns,
                event.sequence,
                processed,
                self.positions.net_pnl,
                self._snapshot(strategy, self.positions),
                event.instrument,
                event.event_type,
            ))
        return ResumeResult(
            processed,
            signals,
            self.positions.net_pnl,
            None if checkpoint is None else (checkpoint.timestamp_ns, checkpoint.sequence),
        )
