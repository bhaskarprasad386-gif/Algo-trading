"""Restartable high-resolution replay with strategy-state restoration."""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any, Iterable, Protocol

from app.backtesting.checkpoint import CheckpointStore, ReplayCheckpoint
from app.backtesting.event_dedup import EventDedupStore
from app.backtesting.high_resolution_pnl import ExecutionFill, HighResolutionPositionLedger
from app.backtesting.universal import MarketEvent, ordered_events


class ResumableStrategy(Protocol):
    def on_event(self, event: MarketEvent) -> Any: ...


@dataclass(frozen=True)
class ResumeResult:
    events_processed: int
    signals_processed: int
    net_pnl: float
    resumed_from: tuple[int, int] | None


class RestartableHighResolutionRunner:
    """Checkpoint/resume runner with durable event identity and strategy state."""

    def __init__(self, connection: sqlite3.Connection, run_id: str,
                 position_ledger: HighResolutionPositionLedger | None = None) -> None:
        self.checkpoints = CheckpointStore(connection)
        self.dedup = EventDedupStore(connection)
        self.run_id = run_id
        self.positions = position_ledger or HighResolutionPositionLedger()

    @staticmethod
    def _restore(strategy: Any, state: dict[str, Any]) -> None:
        restore = getattr(strategy, "restore_state", None)
        if callable(restore):
            restore(dict(state))

    @staticmethod
    def _snapshot(strategy: Any) -> dict[str, Any]:
        snapshot = getattr(strategy, "snapshot_state", None)
        if callable(snapshot):
            value = snapshot()
            if isinstance(value, dict):
                return dict(value)
        return {}

    def run(self, events: Iterable[MarketEvent], strategy: ResumableStrategy) -> ResumeResult:
        checkpoint = self.checkpoints.load(self.run_id)
        if checkpoint is not None:
            self._restore(strategy, checkpoint.state)
        resume_key = None if checkpoint is None else (checkpoint.timestamp_ns, checkpoint.sequence)
        processed = 0 if checkpoint is None else checkpoint.processed_events
        signals = 0
        for event in ordered_events(events):
            key = (event.timestamp_ns, event.sequence)
            if resume_key is not None and key <= resume_key:
                continue
            if not self.dedup.mark_if_new(self.run_id, event.timestamp_ns, event.sequence):
                continue
            processed += 1
            signal = strategy.on_event(event)
            if signal is not None:
                side = str(getattr(signal, "side", signal.get("side") if isinstance(signal, dict) else "")).upper()
                quantity = int(getattr(signal, "quantity", signal.get("quantity") if isinstance(signal, dict) else 0))
                if side not in {"BUY", "SELL"} or quantity <= 0:
                    raise ValueError("invalid strategy signal")
                price = float(event.context.get("price", 0))
                if price <= 0:
                    raise ValueError("execution price must be positive")
                self.positions.add(ExecutionFill(side, event.instrument, quantity, price, event.timestamp_ns))
                signals += 1
            self.checkpoints.save(ReplayCheckpoint(
                self.run_id, event.timestamp_ns, event.sequence,
                processed, self.positions.net_pnl, self._snapshot(strategy)))
        return ResumeResult(processed, signals, self.positions.net_pnl, resume_key)
