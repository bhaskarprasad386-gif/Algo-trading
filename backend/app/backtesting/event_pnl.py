"""P&L accounting for event-driven entry and exit executions."""

from __future__ import annotations

from dataclasses import dataclass

from app.backtesting.execution import ExecutionSide, SimFill


@dataclass(frozen=True)
class EventTradePnl:
    """Completed event-driven trade with execution economics applied."""

    instrument: str
    quantity: int
    side: ExecutionSide
    entry_price: float
    exit_price: float
    entry_fee: float
    exit_fee: float

    @property
    def gross_pnl(self) -> float:
        direction = 1.0 if self.side == ExecutionSide.BUY else -1.0
        return (self.exit_price - self.entry_price) * self.quantity * direction

    @property
    def charges(self) -> float:
        return self.entry_fee + self.exit_fee

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.charges


def build_event_trade_pnl(entry: SimFill, exit: SimFill) -> EventTradePnl:
    if entry.instrument != exit.instrument or entry.side != exit.side:
        raise ValueError("event entry/exit instrument or side mismatch")
    if entry.quantity != exit.quantity:
        raise ValueError("event entry/exit quantity mismatch")
    return EventTradePnl(
        instrument=entry.instrument,
        quantity=entry.quantity,
        side=entry.side,
        entry_price=entry.price,
        exit_price=exit.price,
        entry_fee=entry.fee,
        exit_fee=exit.fee,
    )
