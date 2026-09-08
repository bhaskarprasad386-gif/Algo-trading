"""Position lifecycle accounting for high-resolution event executions."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from app.backtesting.event_execution import EventExecutionResult
from app.backtesting.execution import ExecutionSide


@dataclass(frozen=True)
class HighResolutionTrade:
    instrument: str
    quantity: int
    entry_timestamp_ns: int
    exit_timestamp_ns: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees


class HighResolutionPositionLedger:
    """Match high-resolution fills by instrument without inventing timestamps."""

    def __init__(self) -> None:
        self._open: dict[str, tuple[int, int, float, float]] = {}
        self._net_pnl = 0.0

    def add(self, result: EventExecutionResult) -> HighResolutionTrade | None:
        if result.rejected or not result.fills:
            return None
        fill = result.fills[0]
        if fill.side == ExecutionSide.BUY:
            if fill.instrument in self._open:
                raise ValueError(f"position already open for {fill.instrument}")
            self._open[fill.instrument] = (fill.quantity, fill.filled_at_ns, fill.price, fill.fee)
            return None
        if fill.instrument not in self._open:
            raise ValueError(f"SELL without open position for {fill.instrument}")
        quantity, entry_ts, entry_price, entry_fee = self._open.pop(fill.instrument)
        if quantity != fill.quantity:
            raise ValueError(f"quantity mismatch for {fill.instrument}")
        gross = (fill.price - entry_price) * quantity
        trade = HighResolutionTrade(fill.instrument, quantity, entry_ts, fill.filled_at_ns,
                                     entry_price, fill.price, gross, entry_fee + fill.fee)
        self._net_pnl += trade.net_pnl
        return trade

    def add_many(self, results: Iterable[EventExecutionResult]) -> tuple[HighResolutionTrade, ...]:
        trades: list[HighResolutionTrade] = []
        for result in results:
            trade = self.add(result)
            if trade is not None:
                trades.append(trade)
        return tuple(trades)

    @property
    def net_pnl(self) -> float:
        return self._net_pnl

    def finalize(self) -> float:
        if self._open:
            raise ValueError("cannot finalize with open high-resolution positions")
        return self._net_pnl
