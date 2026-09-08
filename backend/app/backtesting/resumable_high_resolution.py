"""Restartable high-resolution streaming replay with atomic durable state."""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3
from typing import Any, Iterable, Protocol
from app.backtesting.atomic_replay import AtomicReplayStore
from app.backtesting.checkpoint import ReplayCheckpoint
from app.backtesting.high_resolution_ledger import HighResolutionLedgerWriter
from app.backtesting.high_resolution_pnl import ExecutionFill, HighResolutionPositionLedger
from app.backtesting.universal import MarketEvent, streaming_events

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
    """Runs an ordered source with O(1) event memory and bounded atomic batches."""
    def __init__(self, connection: sqlite3.Connection, run_id: str, position_ledger: HighResolutionPositionLedger | None = None, batch_size: int = 1000) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.store = AtomicReplayStore(connection)
        self.run_id = run_id
        self.positions = position_ledger or HighResolutionPositionLedger()
        self.batch_size = batch_size

    @staticmethod
    def _fill(signal: Any, event: MarketEvent) -> ExecutionFill | None:
        if signal is None: return None
        side = str(getattr(signal, "side", signal.get("side") if isinstance(signal, dict) else "")).upper()
        quantity = int(getattr(signal, "quantity", signal.get("quantity") if isinstance(signal, dict) else 0))
        if side not in {"BUY", "SELL"} or quantity <= 0: raise ValueError("invalid strategy signal")
        price = float(event.context.get("price", 0))
        if price <= 0: raise ValueError("execution price must be positive")
        return ExecutionFill(side, event.instrument, quantity, price, event.timestamp_ns)

    @staticmethod
    def _strategy_state(strategy: Any) -> dict[str, Any]:
        snapshot = getattr(strategy, "snapshot_state", None)
        value = snapshot() if callable(snapshot) else {}
        return value if isinstance(value, dict) else {}

    def _restore(self, strategy: Any, state: dict[str, Any]) -> None:
        if not isinstance(state, dict): raise ValueError("invalid checkpoint state")
        if isinstance(state.get("positions"), dict): self.positions.restore_state(state["positions"])
        restore = getattr(strategy, "restore_state", None)
        if state.get("strategy") is not None and callable(restore): restore(state["strategy"])

    def run(self, events: Iterable[MarketEvent], strategy: ResumableStrategy, ledger_writer: HighResolutionLedgerWriter | None = None) -> ResumableBacktestResult:
        checkpoint = self.store.load_checkpoint(self.run_id)
        resume_key = None if checkpoint is None else (checkpoint.timestamp_ns, checkpoint.sequence)
        processed = 0 if checkpoint is None else checkpoint.processed_events
        if checkpoint is not None: self._restore(strategy, checkpoint.state)
        signals = 0
        batch_events = 0
        pending_trades: list[Any] = []
        batch_positions = self.positions.snapshot_state()
        batch_strategy = self._strategy_state(strategy)
        batch_processed, batch_signals = processed, signals
        self.store.begin()
        try:
            for event in streaming_events(events):
                key = (event.timestamp_ns, event.sequence)
                if resume_key is not None and key <= resume_key: continue
                if not self.store.mark_if_new(self.run_id, event.timestamp_ns, event.sequence): continue
                processed += 1
                batch_events += 1
                signal = strategy.on_event(event)
                fill = self._fill(signal, event)
                if fill is not None:
                    signals += 1
                    trade = self.positions.add(fill)
                    if trade is not None:
                        self.store.append_trade(self.run_id, trade)
                        pending_trades.append(trade)
                latest_checkpoint = ReplayCheckpoint(
                    self.run_id, event.timestamp_ns, event.sequence, processed,
                    self.positions.net_pnl,
                    {"strategy": self._strategy_state(strategy), "positions": self.positions.snapshot_state()},
                )
                if batch_events >= self.batch_size:
                    self.store.save_checkpoint(latest_checkpoint)
                    self.store.commit()
                    if ledger_writer is not None:
                        for trade in pending_trades: ledger_writer.append(trade)
                    pending_trades.clear()
                    resume_key = (event.timestamp_ns, event.sequence)
                    batch_events = 0
                    batch_positions = self.positions.snapshot_state()
                    batch_strategy = self._strategy_state(strategy)
                    batch_processed, batch_signals = processed, signals
                    self.store.begin()
            if batch_events:
                self.store.save_checkpoint(latest_checkpoint)
                self.store.commit()
                if ledger_writer is not None:
                    for trade in pending_trades: ledger_writer.append(trade)
                pending_trades.clear()
            else:
                self.store.rollback()
        except Exception:
            self.store.rollback()
            self.positions.restore_state(batch_positions)
            restore = getattr(strategy, "restore_state", None)
            if callable(restore): restore(batch_strategy)
            processed, signals = batch_processed, batch_signals
            raise
        return ResumableBacktestResult(processed, signals, self.positions.closed_trades, self.positions.net_pnl, self.positions.open_positions, resume_key)
