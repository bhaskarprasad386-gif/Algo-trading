"""Portfolio-level capital allocation primitives for Cash-Future backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Hashable


PositionKey = tuple[str, str]


@dataclass(frozen=True)
class CashFutureCapitalReservation:
    """One position's locked historical margin/capital."""

    position_key: PositionKey
    margin_required: float


@dataclass
class CashFuturePortfolioLedger:
    """Manage capital reservations independently for simultaneous positions.

    Realized capital changes only when realized P&L is applied. Open-position
    margin remains reserved until that exact position is released. This keeps
    available capital from being double-counted across symbols/contracts.
    """

    initial_capital: float
    realized_capital: float | None = None
    reservations: dict[PositionKey, CashFutureCapitalReservation] = field(default_factory=dict)
    blocked_entries: int = 0

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.realized_capital is None:
            self.realized_capital = self.initial_capital

    @property
    def reserved_margin(self) -> float:
        return sum(item.margin_required for item in self.reservations.values())

    @property
    def available_capital(self) -> float:
        return float(self.realized_capital) - self.reserved_margin

    @property
    def open_position_count(self) -> int:
        return len(self.reservations)

    def reserve(self, position_key: PositionKey, margin_required: float) -> bool:
        """Reserve margin for a new position, atomically with validation."""
        key = _validate_key(position_key)
        margin = _normalize_margin(margin_required)
        if key in self.reservations:
            raise ValueError(f"position already has a capital reservation: {key}")
        if margin > self.available_capital:
            self.blocked_entries += 1
            return False
        self.reservations[key] = CashFutureCapitalReservation(key, margin)
        return True

    def release(self, position_key: PositionKey) -> float:
        """Release the exact reservation belonging to an open position."""
        key = _validate_key(position_key)
        reservation = self.reservations.pop(key, None)
        if reservation is None:
            raise ValueError(f"no capital reservation for position: {key}")
        return reservation.margin_required

    def apply_realized_pnl(self, net_profit: float) -> None:
        self.realized_capital = float(self.realized_capital) + float(net_profit)

    def reservation(self, position_key: PositionKey) -> CashFutureCapitalReservation | None:
        return self.reservations.get(_validate_key(position_key))


def _validate_key(position_key: PositionKey) -> PositionKey:
    if not isinstance(position_key, tuple) or len(position_key) != 2:
        raise ValueError("position_key must be (symbol, contract_month)")
    symbol, contract_month = position_key
    if not str(symbol).strip() or not str(contract_month).strip():
        raise ValueError("position_key symbol and contract_month are required")
    return str(symbol), str(contract_month)


def _normalize_margin(margin_required: float) -> float:
    margin = float(margin_required)
    if margin < 0:
        raise ValueError("margin_required cannot be negative")
    return margin


__all__ = [
    "CashFutureCapitalReservation",
    "CashFuturePortfolioLedger",
    "PositionKey",
]
