"""Streaming high-resolution backtest path with durable incremental trade output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from app.backtesting.high_resolution_ledger import HighResolutionLedgerWriter
from app.backtesting.high_resolution_pnl import ExecutionFill, HighResolutionPositionLedger
from app.backtesting.universal import MarketEvent, streaming_events


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
    """Replay an already ordered event stream without materializing history."""

    def __init__(self, position_ledger: HighResolutionPositionLedger | None = None) -> None:
        self.positions = position_ledger or HighResolutionPositionLedger()

    @staticmethod
    def _signal_value(signal: Any, name: str, default: Any = None) -> Any:
        if isinstance(signal, dict):
            return signal.get(name, default)
        return getattr(signal, name, default)

    @classmethod
    def _fill_from_signal(cls, signal: Any, event: MarketEvent) -> ExecutionFill | None:
        if signal is None:
            return None
        side = str(cls._signal_value(signal, "side", "")).upper()
        quantity = cls._signal_value(signal, "quantity", 0)
        if type(quantity) is not int or quantity <= 0 or side not in {"BUY", "SELL"}:
            raise ValueError("invalid strategy signal")
        price_value = cls._signal_value(signal, "price", None)
        if price_value is None:
            price_value = event.context.get("price")
        if isinstance(price_value, bool) or not isinstance(price_value, (int, float)) or price_value <= 0:
            raise ValueError("execution price must be finite and positive")
        return ExecutionFill(side, event.instrument, quantity, float(price_value), event.timestamp_ns)

    def run(
        self,
        events: Iterable[MarketEvent],
        strategy: StreamingStrategy,
        ledger_writer: HighResolutionLedgerWriter | None = None,
    ) -> StreamingBacktestResult:
        processed = 0
        signals = 0
        for event in streaming_events(events):
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
