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
    """Yield common timestamps from sorted streams without loading a year into RAM.

    The persisted historical store is expected to provide each leg ordered by
    timestamp. The merge advances only the stream(s) behind the current maximum
    timestamp, so memory stays bounded to one row per leg.
    """
    spot_it = iter(spot)
    current_it = iter(current)
    near_it = iter(near)

    try:
        spot_row = next(spot_it)
        current_row = next(current_it)
        near_row = next(near_it)
    except StopIteration:
        return

    while True:
        spot_ts, spot_price = spot_row
        current_ts, current_price, current_expiry, current_lot = current_row
        near_ts, near_price, near_expiry, near_lot = near_row

        if spot_ts == current_ts == near_ts:
            if current_lot != near_lot:
                raise ValueError("Current and near futures lot size mismatch")
            yield ReplayBar(
                timestamp=spot_ts,
                spot=spot_price,
                current_future=current_price,
                near_future=near_price,
                current_expiry=current_expiry,
                near_expiry=near_expiry,
                lot_size=current_lot,
            )
            try:
                spot_row = next(spot_it)
                current_row = next(current_it)
                near_row = next(near_it)
            except StopIteration:
                return
            continue

        minimum_ts = min(spot_ts, current_ts, near_ts)
        if spot_ts == minimum_ts:
            try:
                spot_row = next(spot_it)
            except StopIteration:
                return
        if current_ts == minimum_ts:
            try:
                current_row = next(current_it)
            except StopIteration:
                return
        if near_ts == minimum_ts:
            try:
                near_row = next(near_it)
            except StopIteration:
                return
