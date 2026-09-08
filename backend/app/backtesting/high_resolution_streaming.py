"""Streaming high-resolution backtest path with durable incremental trade output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from app.backtesting.high_resolution_ledger import HighResolutionLedgerWriter
from app.backtesting.high_resolution_pnl import ExecutionFill, HighResolutionPositionLedger
from app.backtesting.universal import MarketEvent, ordered_events


class StreamingStrategy(Protocol):
    def on_event(self, event: MarketEvent) -> Any:
        """Return a signal-like object or None; strategy owns only bounded state."""
        ...


@dataclass(frozen=True)
class StreamingBacktestResult:
    events_processed: int
    signals_processed: int
    trades_closed: int
    net_pnl: float
    open_positions: int


class HighResolutionStreamingRunner:
    """Replay supplied events without materializing event or trade history."""

    def __init__(self, position_ledger: HighResolutionPositionLedger | None = None) -> None:
        self.positions = position_ledger or HighResolutionPositionLedger()

    @staticmethod
    def _fill_from_signal(signal: Any, event: MarketEvent) -> ExecutionFill | None:
        if signal is None:
            return None
        side = str(getattr(signal, "side", signal.get("side") if isinstance(signal, dict) else "")).upper()
        quantity = int(getattr(signal, "quantity", signal.get("quantity") if isinstance(signal, dict) else 0))
        if side not in {"BUY", "SELL"} or quantity <= 0:
            raise ValueError("invalid strategy signal")
        price_value = event.context.get("price")
        if price_value is None:
            raise ValueError("execution price is required")
        price = float(price_value)
        if price <= 0:
            raise ValueError("execution price must be positive")
        return ExecutionFill(side, event.instrument, quantity, price, event.timestamp_ns)

    def run(
        self,
        events: Iterable[MarketEvent],
        strategy: StreamingStrategy,
        ledger_writer: HighResolutionLedgerWriter | None = None,
    ) -> StreamingBacktestResult:
        processed = 0
        signals = 0
        for event in ordered_events(events):
            processed += 1
            signal = strategy.on_event(event)
            fill = self._fill_from_signal(signal, event)
            if fill is None:
                continue
            signals += 1
            trade = self.positions.add(fill)
            if trade is not None and ledger_writer is not None:
                ledger_writer.append(trade)
        return StreamingBacktestResult(
            events_processed=processed,
            signals_processed=signals,
            trades_closed=self.positions.closed_trades,
            net_pnl=self.positions.net_pnl,
            open_positions=self.positions.open_positions,
        )
