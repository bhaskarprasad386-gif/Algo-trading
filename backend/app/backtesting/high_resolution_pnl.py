"""Bounded high-resolution position accounting for streaming backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


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
    """Keep only open positions and aggregates; expose a JSON-safe checkpoint state."""

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

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "open": {
                instrument: {
                    "side": fill.side,
                    "quantity": fill.quantity,
                    "price": fill.price,
                    "timestamp_ns": fill.timestamp_ns,
                }
                for instrument, fill in self._open.items()
            },
            "net_pnl": self._net_pnl,
            "closed_trades": self._closed_trades,
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        open_state = state.get("open", {})
        if not isinstance(open_state, dict):
            raise ValueError("invalid position checkpoint")
        restored: dict[str, ExecutionFill] = {}
        for instrument, raw in open_state.items():
            if not isinstance(raw, dict):
                raise ValueError("invalid position checkpoint")
            side = str(raw.get("side", "")).upper()
            if side != "BUY":
                raise ValueError("only BUY-open positions are supported")
            quantity = int(raw.get("quantity", 0))
            price = float(raw.get("price", 0))
            timestamp_ns = int(raw.get("timestamp_ns", -1))
            fill = ExecutionFill(side, str(instrument), quantity, price, timestamp_ns)
            if quantity <= 0 or price <= 0 or timestamp_ns < 0:
                raise ValueError("invalid position checkpoint")
            restored[str(instrument)] = fill
        net_pnl = float(state.get("net_pnl", 0.0))
        closed_trades = int(state.get("closed_trades", 0))
        if closed_trades < 0:
            raise ValueError("invalid closed trade count")
        self._open = restored
        self._net_pnl = net_pnl
        self._closed_trades = closed_trades

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
