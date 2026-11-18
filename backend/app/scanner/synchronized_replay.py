"""Synchronized 1-minute Spot/Current/Near future replay helpers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cash_future_history import CashFutureHistory


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
    """Yield common timestamps from sorted streams without loading a year into RAM."""
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


def iter_persisted_symbol_replay(
    db: Session,
    symbol: str,
    start: datetime,
    end: datetime,
) -> Iterator[ReplayBar]:
    """Stream synchronized Spot/Current/Near rows from persisted history.

    Each database timestamp is grouped in memory only for that minute. The
    futures are selected by historical expiry date, so contract labels from
    today's market cannot leak into an old replay.
    """
    stmt = (
        select(CashFutureHistory)
        .where(
            CashFutureHistory.symbol == symbol.upper(),
            CashFutureHistory.timestamp >= start,
            CashFutureHistory.timestamp <= end,
            CashFutureHistory.expiry_date.is_not(None),
        )
        .order_by(
            CashFutureHistory.timestamp,
            CashFutureHistory.expiry_date,
            CashFutureHistory.contract_month,
        )
        .execution_options(yield_per=1000)
    )

    result = db.execute(stmt).scalars()
    pending_timestamp: datetime | None = None
    bucket: list[CashFutureHistory] = []

    def flush(rows: list[CashFutureHistory]) -> ReplayBar | None:
        if not rows:
            return None
        ts = rows[0].timestamp
        eligible = [r for r in rows if r.expiry_date is not None and r.expiry_date >= ts.date()]
        contracts: dict[str, CashFutureHistory] = {}
        for row in eligible:
            contracts.setdefault(row.contract_month, row)
        ordered = sorted(contracts.values(), key=lambda r: (r.expiry_date, r.contract_month))
        if len(ordered) < 2:
            return None
        current, near = ordered[0], ordered[1]
        if current.lot_size != near.lot_size:
            raise ValueError("Current and near futures lot size mismatch")
        return ReplayBar(
            timestamp=ts,
            spot=current.cash_price,
            current_future=current.future_price,
            near_future=near.future_price,
            current_expiry=current.expiry_date,
            near_expiry=near.expiry_date,
            lot_size=current.lot_size,
        )

    for row in result:
        if pending_timestamp is None:
            pending_timestamp = row.timestamp
        if row.timestamp != pending_timestamp:
            replay = flush(bucket)
            if replay is not None:
                yield replay
            bucket = []
            pending_timestamp = row.timestamp
        bucket.append(row)

    replay = flush(bucket)
    if replay is not None:
        yield replay
