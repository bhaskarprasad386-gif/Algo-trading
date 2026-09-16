"""Restartable high-resolution streaming replay with atomic durable state."""
from __future__ import annotations

from dataclasses import dataclass
import math
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
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        self.store = AtomicReplayStore(connection)
        self.run_id = run_id
        self.positions = position_ledger or HighResolutionPositionLedger()
        self.batch_size = batch_size

    @staticmethod
    def _fill(signal: Any, event: MarketEvent) -> ExecutionFill | None:
        if signal is None:
            return None
        if isinstance(signal, dict):
            side = str(signal.get("side", "")).strip().upper()
            quantity = signal.get("quantity", 0)
            price_value = signal.get("price")
        else:
            side = str(getattr(signal, "side", "")).strip().upper()
            quantity = getattr(signal, "quantity", 0)
            price_value = getattr(signal, "price", None)
        if side not in {"BUY", "SELL"} or type(quantity) is not int or quantity <= 0:
            raise ValueError("invalid strategy signal")
        if price_value is None:
            price_value = event.context.get("price")
        if isinstance(price_value, bool) or not isinstance(price_value, (int, float)) or not math.isfinite(float(price_value)) or price_value <= 0:
            raise ValueError("execution price must be finite and positive")
        return ExecutionFill(side, event.instrument.strip(), quantity, float(price_value), event.timestamp_ns)

    @staticmethod
    def _strategy_state(strategy: Any) -> dict[str, Any]:
        snapshot = getattr(strategy, "snapshot_state", None)
        value = snapshot() if callable(snapshot) else {}
        if not isinstance(value, dict):
            raise ValueError("strategy snapshot must be a dictionary")
        return dict(value)

    def _restore(self, strategy: Any, state: dict[str, Any]) -> int:
        if not isinstance(state, dict):
            raise ValueError("invalid checkpoint state")
        if isinstance(state.get("positions"), dict):
            self.positions.restore_state(state["positions"])
        restore = getattr(strategy, "restore_state", None)
        if state.get("strategy") is not None and callable(restore):
            restore(dict(state["strategy"]))
        signals = state.get("signals_processed", 0)
        if type(signals) is not int or signals < 0:
            raise ValueError("invalid checkpoint signal count")
        return signals

    def run(self, events: Iterable[MarketEvent], strategy: ResumableStrategy, ledger_writer: HighResolutionLedgerWriter | None = None) -> ResumableBacktestResult:
        checkpoint = self.store.load_checkpoint(self.run_id)
        signals = 0
        if checkpoint is not None:
            signals = self._restore(strategy, checkpoint.state)
        resume_key = None if checkpoint is None else (checkpoint.timestamp_ns, checkpoint.sequence, checkpoint.instrument, checkpoint.event_type)
        processed = 0 if checkpoint is None else checkpoint.processed_events
        batch_events = 0
        pending_trades: list[Any] = []
        batch_positions = self.positions.snapshot_state()
        batch_strategy = self._strategy_state(strategy)
        batch_processed, batch_signals = processed, signals
        self.store.begin()
        latest_checkpoint = checkpoint
        try:
            for event in streaming_events(events):
                key = (event.timestamp_ns, event.sequence, event.instrument, event.event_type)
                if resume_key is not None:
                    if checkpoint is not None and (checkpoint.instrument or checkpoint.event_type):
                        if key <= resume_key:
                            continue
                    elif key[:2] <= resume_key[:2]:
                        continue
                if not self.store.mark_if_new(self.run_id, event.timestamp_ns, event.sequence, event.instrument, event.event_type):
                    continue
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
                    {"strategy": self._strategy_state(strategy), "positions": self.positions.snapshot_state(), "signals_processed": signals},
                    event.instrument, event.event_type,
                )
                if batch_events >= self.batch_size:
                    self.store.save_checkpoint(latest_checkpoint)
                    self.store.commit()
                    if ledger_writer is not None:
                        for trade in pending_trades:
                            ledger_writer.append(trade)
                    pending_trades.clear()
                    resume_key = key
                    checkpoint = latest_checkpoint
                    batch_events = 0
                    batch_positions = self.positions.snapshot_state()
                    batch_strategy = self._strategy_state(strategy)
                    batch_processed, batch_signals = processed, signals
                    self.store.begin()
            if batch_events:
                self.store.save_checkpoint(latest_checkpoint)
                self.store.commit()
                if ledger_writer is not None:
                    for trade in pending_trades:
                        ledger_writer.append(trade)
                pending_trades.clear()
            else:
                self.store.rollback()
        except Exception:
            self.store.rollback()
            self.positions.restore_state(batch_positions)
            restore = getattr(strategy, "restore_state", None)
            if callable(restore):
                restore(batch_strategy)
            processed, signals = batch_processed, batch_signals
            raise
        return ResumableBacktestResult(
            processed, signals, self.positions.closed_trades, self.positions.net_pnl,
            self.positions.open_positions,
            None if checkpoint is None else (checkpoint.timestamp_ns, checkpoint.sequence),
        )
