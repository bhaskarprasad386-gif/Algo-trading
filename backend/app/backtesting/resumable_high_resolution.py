"""Restartable high-resolution streaming replay with durable checkpoints."""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any, Iterable, Protocol

from app.backtesting.checkpoint import CheckpointStore, ReplayCheckpoint
from app.backtesting.event_dedup import EventDedupStore
from app.backtesting.high_resolution_ledger import HighResolutionLedgerWriter
from app.backtesting.high_resolution_pnl import ExecutionFill, HighResolutionPositionLedger
from app.backtesting.universal import MarketEvent, ordered_events


class ResumableStrategy(Protocol):
    def on_event(self, event: MarketEvent) -> Any: ...


@dataclass(frozen=True)
class ResumableBacktestResult:
    events_processed: int
    signals_processed: int
    trades_closed: int
    net_pnl: float
    open_positions: int
    resumed_from: tuple[int, int] | None


class ResumableHighResolutionRunner:
    """Runs high-resolution execution with durable strategy and position recovery."""

    def __init__(self, connection: sqlite3.Connection, run_id: str,
                 position_ledger: HighResolutionPositionLedger | None = None) -> None:
        self.checkpoints = CheckpointStore(connection)
        self.dedup = EventDedupStore(connection)
        self.run_id = run_id
        self.positions = position_ledger or HighResolutionPositionLedger()

    @staticmethod
    def _fill(signal: Any, event: MarketEvent) -> ExecutionFill | None:
        if signal is None:
            return None
        side = str(getattr(signal, "side", signal.get("side") if isinstance(signal, dict) else "")).upper()
        quantity = int(getattr(signal, "quantity", signal.get("quantity") if isinstance(signal, dict) else 0))
        if side not in {"BUY", "SELL"} or quantity <= 0:
            raise ValueError("invalid strategy signal")
        price = float(event.context.get("price", 0))
        if price <= 0:
            raise ValueError("execution price must be positive")
        return ExecutionFill(side, event.instrument, quantity, price, event.timestamp_ns)

    @staticmethod
    def _strategy_state(strategy: Any) -> dict[str, Any]:
        snapshot = getattr(strategy, "snapshot_state", None)
        if callable(snapshot):
            value = snapshot()
            if isinstance(value, dict):
                return value
        return {}

    def _restore(self, strategy: Any, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("invalid checkpoint state")
        position_state = state.get("positions")
        if isinstance(position_state, dict):
            self.positions.restore_state(position_state)
        strategy_state = state.get("strategy")
        restore = getattr(strategy, "restore_state", None)
        if strategy_state is not None and callable(restore):
            if not isinstance(strategy_state, dict):
                raise ValueError("invalid strategy checkpoint")
            restore(strategy_state)

    def run(self, events: Iterable[MarketEvent], strategy: ResumableStrategy,
            ledger_writer: HighResolutionLedgerWriter | None = None) -> ResumableBacktestResult:
        checkpoint = self.checkpoints.load(self.run_id)
        resume_key = None if checkpoint is None else (checkpoint.timestamp_ns, checkpoint.sequence)
        processed = 0 if checkpoint is None else checkpoint.processed_events
        if checkpoint is not None:
            self._restore(strategy, checkpoint.state)
        signals = 0
        for event in ordered_events(events):
            key = (event.timestamp_ns, event.sequence)
            if resume_key is not None and key <= resume_key:
                continue
            if not self.dedup.mark_if_new(self.run_id, event.timestamp_ns, event.sequence):
                continue
            processed += 1
            signal = strategy.on_event(event)
            fill = self._fill(signal, event)
            if fill is not None:
                signals += 1
                trade = self.positions.add(fill)
                if trade is not None and ledger_writer is not None:
                    ledger_writer.append(trade)
            self.checkpoints.save(ReplayCheckpoint(
                self.run_id, event.timestamp_ns, event.sequence,
                processed, self.positions.net_pnl,
                {"strategy": self._strategy_state(strategy), "positions": self.positions.snapshot_state()},
            ))
        return ResumableBacktestResult(
            processed, signals, self.positions.closed_trades,
            self.positions.net_pnl, self.positions.open_positions, resume_key)
