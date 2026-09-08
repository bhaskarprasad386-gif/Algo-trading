"""Bounded high-resolution position accounting for streaming backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class ExecutionFill:
    side: Side
    instrument: str
    quantity: int
    price: float
    timestamp_ns: int


@dataclass(frozen=True)
class HighResolutionTrade:
    instrument: str
    quantity: int
    entry_timestamp_ns: int
    exit_timestamp_ns: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float = 0.0

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.entry_timestamp_ns < 0 or self.exit_timestamp_ns < 0:
            raise ValueError("timestamps must be non-negative")
        if self.exit_timestamp_ns < self.entry_timestamp_ns:
            raise ValueError("exit timestamp cannot precede entry timestamp")

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees


class HighResolutionPositionLedger:
    """Keep only currently open positions and running aggregates; trades are emitted."""

    def __init__(self) -> None:
        self._open: dict[str, ExecutionFill] = {}
        self._net_pnl = 0.0
        self._closed_trades = 0

    def add(self, fill: ExecutionFill) -> HighResolutionTrade | None:
        if fill.quantity <= 0 or fill.timestamp_ns < 0 or fill.price <= 0:
            raise ValueError("invalid execution fill")
        if fill.side == "BUY":
            if fill.instrument in self._open:
                raise ValueError("position already open")
            self._open[fill.instrument] = fill
            return None
        if fill.instrument not in self._open:
            raise ValueError("no open position")
        entry = self._open.pop(fill.instrument)
        if entry.quantity != fill.quantity:
            raise ValueError("quantity mismatch")
        trade = HighResolutionTrade(
            instrument=fill.instrument,
            quantity=fill.quantity,
            entry_timestamp_ns=entry.timestamp_ns,
            exit_timestamp_ns=fill.timestamp_ns,
            entry_price=entry.price,
            exit_price=fill.price,
            gross_pnl=(fill.price - entry.price) * fill.quantity,
        )
        self._net_pnl += trade.net_pnl
        self._closed_trades += 1
        return trade

    @property
    def net_pnl(self) -> float:
        return self._net_pnl

    @property
    def closed_trades(self) -> int:
        return self._closed_trades

    @property
    def open_positions(self) -> int:
        return len(self._open)

    def finalize(self) -> float:
        return self._net_pnl
