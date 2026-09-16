"""Bounded high-resolution position accounting for streaming backtests."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class ExecutionFill:
    side: Side
    instrument: str
    quantity: int
    price: float
    timestamp_ns: int

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument is required")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if isinstance(self.price, bool) or not isinstance(self.price, (int, float)) or not isfinite(float(self.price)) or self.price <= 0:
            raise ValueError("price must be finite and positive")
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be a non-negative integer")


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
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument is required")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if isinstance(self.entry_timestamp_ns, bool) or not isinstance(self.entry_timestamp_ns, int) or self.entry_timestamp_ns < 0:
            raise ValueError("entry timestamp must be non-negative")
        if isinstance(self.exit_timestamp_ns, bool) or not isinstance(self.exit_timestamp_ns, int) or self.exit_timestamp_ns < 0:
            raise ValueError("exit timestamp must be non-negative")
        if self.exit_timestamp_ns < self.entry_timestamp_ns:
            raise ValueError("exit timestamp cannot precede entry timestamp")
        for name, value in (("entry_price", self.entry_price), ("exit_price", self.exit_price), ("gross_pnl", self.gross_pnl), ("fees", self.fees)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.entry_price <= 0 or self.exit_price <= 0 or self.fees < 0:
            raise ValueError("prices must be positive and fees non-negative")

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees


class HighResolutionPositionLedger:
    """Keep only open positions and aggregates; expose JSON-safe checkpoint state."""

    def __init__(self) -> None:
        self._open: dict[str, ExecutionFill] = {}
        self._net_pnl = 0.0
        self._closed_trades = 0

    def add(self, fill: ExecutionFill) -> HighResolutionTrade | None:
        if not isinstance(fill, ExecutionFill):
            raise TypeError("fill must be an ExecutionFill")
        instrument = fill.instrument.strip()
        if fill.side == "BUY":
            if instrument in self._open:
                raise ValueError("position already open")
            self._open[instrument] = ExecutionFill("BUY", instrument, fill.quantity, fill.price, fill.timestamp_ns)
            return None

        entry = self._open.get(instrument)
        if entry is None:
            raise ValueError("no open position")
        if entry.quantity != fill.quantity:
            raise ValueError("quantity mismatch")
        if fill.timestamp_ns < entry.timestamp_ns:
            raise ValueError("exit timestamp cannot precede entry timestamp")
        trade = HighResolutionTrade(
            instrument,
            fill.quantity,
            entry.timestamp_ns,
            fill.timestamp_ns,
            entry.price,
            fill.price,
            (fill.price - entry.price) * fill.quantity,
        )
        self._open.pop(instrument)
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
        if not isinstance(state, dict):
            raise ValueError("invalid position checkpoint")
        open_state = state.get("open", {})
        if not isinstance(open_state, dict):
            raise ValueError("invalid position checkpoint")
        net_pnl = state.get("net_pnl", 0.0)
        closed_trades = state.get("closed_trades", 0)
        if isinstance(net_pnl, bool) or not isinstance(net_pnl, (int, float)) or not isfinite(float(net_pnl)):
            raise ValueError("invalid net pnl checkpoint")
        if type(closed_trades) is not int or closed_trades < 0:
            raise ValueError("invalid closed trade count")
        restored: dict[str, ExecutionFill] = {}
        for instrument, raw in open_state.items():
            if not isinstance(instrument, str) or not instrument.strip() or not isinstance(raw, dict):
                raise ValueError("invalid position checkpoint")
            normalized = instrument.strip()
            if normalized in restored:
                raise ValueError("duplicate normalized instrument in position checkpoint")
            if raw.get("side") != "BUY":
                raise ValueError("only BUY-open positions are supported")
            quantity = raw.get("quantity")
            timestamp_ns = raw.get("timestamp_ns")
            if type(quantity) is not int or type(timestamp_ns) is not int:
                raise ValueError("position checkpoint types are invalid")
            fill = ExecutionFill("BUY", normalized, quantity, raw.get("price"), timestamp_ns)
            restored[normalized] = fill
        self._open = restored
        self._net_pnl = float(net_pnl)
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
