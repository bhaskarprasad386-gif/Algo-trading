"""Position and P&L accounting for universal backtest execution."""

from dataclasses import dataclass
from typing import Iterable

from app.backtesting.execution import ExecutionSide, SimFill


@dataclass(frozen=True)
class Position:
    instrument: str
    quantity: int = 0
    average_price: float = 0.0
    realized_pnl: float = 0.0


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    positions: tuple[Position, ...]


class Portfolio:
    """Deterministic fill accounting for long/short and multi-instrument books."""

    def __init__(self, initial_cash: float = 0.0) -> None:
        if initial_cash < 0:
            raise ValueError("initial_cash cannot be negative")
        self.cash = float(initial_cash)
        self._positions: dict[str, Position] = {}

    def apply_fill(self, fill: SimFill) -> Position:
        old = self._positions.get(fill.instrument, Position(fill.instrument))
        signed = fill.quantity if fill.side == ExecutionSide.BUY else -fill.quantity
        old_qty = old.quantity
        new_qty = old_qty + signed
        realized = old.realized_pnl

        if old_qty == 0 or (old_qty > 0 and signed > 0) or (old_qty < 0 and signed < 0):
            total_abs = abs(old_qty) + abs(signed)
            avg = ((abs(old_qty) * old.average_price) + (abs(signed) * fill.price)) / total_abs
        elif abs(signed) <= abs(old_qty):
            closed = abs(signed)
            direction = 1 if old_qty > 0 else -1
            realized += closed * (fill.price - old.average_price) * direction
            avg = old.average_price if new_qty else 0.0
        else:
            closed = abs(old_qty)
            direction = 1 if old_qty > 0 else -1
            realized += closed * (fill.price - old.average_price) * direction
            avg = fill.price

        self.cash -= signed * fill.price + fill.fee
        position = Position(fill.instrument, new_qty, avg, realized)
        if new_qty == 0 and realized == 0.0:
            self._positions.pop(fill.instrument, None)
        else:
            self._positions[fill.instrument] = position
        return position

    def snapshot(self, marks: dict[str, float] | None = None) -> PortfolioSnapshot:
        marks = marks or {}
        unrealized = 0.0
        positions = tuple(self._positions.values())
        for position in positions:
            mark = marks.get(position.instrument)
            if mark is not None:
                unrealized += position.quantity * (mark - position.average_price)
        realized = sum(p.realized_pnl for p in positions)
        return PortfolioSnapshot(
            cash=self.cash,
            equity=self.cash + unrealized,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            positions=positions,
        )

    def apply_fills(self, fills: Iterable[SimFill]) -> PortfolioSnapshot:
        for fill in fills:
            self.apply_fill(fill)
        return self.snapshot()
