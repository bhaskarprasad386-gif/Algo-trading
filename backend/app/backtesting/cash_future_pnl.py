"""Deterministic cash-future P&L calculation for replay bars."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CashFutureTrade:
    spot_entry: float
    spot_exit: float
    future_entry: float
    future_exit: float
    quantity: int
    lot_size: int
    brokerage: float = 0.0
    funding: float = 0.0
    slippage: float = 0.0

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.lot_size <= 0:
            raise ValueError("quantity and lot_size must be positive")
        for value in (self.spot_entry, self.spot_exit, self.future_entry, self.future_exit):
            if value < 0:
                raise ValueError("prices cannot be negative")

    @property
    def units(self) -> int:
        return self.quantity * self.lot_size

    @property
    def spot_pnl(self) -> float:
        return (self.spot_exit - self.spot_entry) * self.units

    @property
    def future_pnl(self) -> float:
        return (self.future_entry - self.future_exit) * self.units

    @property
    def gross_pnl(self) -> float:
        return self.spot_pnl + self.future_pnl

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.brokerage - self.funding - self.slippage


def cash_future_gap(spot: float, future: float) -> float:
    if spot < 0 or future < 0:
        raise ValueError("prices cannot be negative")
    return future - spot
