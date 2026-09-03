"""Synchronized 1-minute Spot/Current/Near future replay helpers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class ReplayBar:
    timestamp: datetime
    spot: float
    current_future: float
    near_future: float
    current_expiry: date
    near_expiry: date
    lot_size: int

    @property
    def current_gap(self) -> float:
        return self.current_future - self.spot

    @property
    def near_gap(self) -> float:
        return self.near_future - self.spot


def synchronize_minute_bars(
    spot: Iterable[tuple[datetime, float]],
    current: Iterable[tuple[datetime, float, date, int]],
    near: Iterable[tuple[datetime, float, date, int]],
) -> Iterator[ReplayBar]:
    """Yield only timestamps present in all three legs, in timestamp order."""
    spots = {ts: price for ts, price in spot}
    currents = {ts: (price, expiry, lot) for ts, price, expiry, lot in current}
    nears = {ts: (price, expiry, lot) for ts, price, expiry, lot in near}

    for timestamp in sorted(spots.keys() & currents.keys() & nears.keys()):
        current_price, current_expiry, current_lot = currents[timestamp]
        near_price, near_expiry, near_lot = nears[timestamp]
        if current_lot != near_lot:
            raise ValueError("Current and near futures lot size mismatch")
        yield ReplayBar(
            timestamp=timestamp,
            spot=spots[timestamp],
            current_future=current_price,
            near_future=near_price,
            current_expiry=current_expiry,
            near_expiry=near_expiry,
            lot_size=current_lot,
        )
