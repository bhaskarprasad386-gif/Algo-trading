"""Persistent, incremental historical-data store for backtesting.

The store is deliberately provider-agnostic: NSE/other licensed data adapters can
normalize their payloads into HistoricalMarketBar rows. Backtests then read the
validated local store instead of repeatedly downloading the same history.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BacktestDataCoverage, HistoricalMarketBar


TIMEFRAME_1M = "1m"


def instrument_key(
    symbol: str,
    segment: str,
    instrument_type: str,
    contract_month: str | None = None,
) -> str:
    """Build a stable identity for a spot/future contract."""
    return "|".join(
        [
            symbol.strip().upper(),
            segment.strip().upper(),
            instrument_type.strip().upper(),
            (contract_month or "SPOT").strip().upper(),
        ]
    )


def upsert_1m_bars(db: Session, bars: Iterable[dict]) -> int:
    """Persist validated 1-minute bars without duplicating existing minutes.

    SQLite/Postgres uniqueness is enforced by instrument_key + timestamp. Existing
    rows are updated so corrected source data can repair only the affected minute.
    """
    count = 0
    for payload in bars:
        key = payload["instrument_key"]
        timestamp = payload["timestamp"]
        existing = db.scalar(
            select(HistoricalMarketBar).where(
                HistoricalMarketBar.instrument_key == key,
                HistoricalMarketBar.timestamp == timestamp,
            )
        )
        if existing is None:
            existing = HistoricalMarketBar(**payload)
            db.add(existing)
        else:
            for field, value in payload.items():
                if field not in {"instrument_key", "timestamp"}:
                    setattr(existing, field, value)
        count += 1
    db.commit()
    return count


def local_coverage(
    db: Session,
    key: str,
    start: datetime,
    end: datetime,
) -> list[BacktestDataCoverage]:
    """Return validated coverage records intersecting the requested range."""
    return list(
        db.scalars(
            select(BacktestDataCoverage)
            .where(
                BacktestDataCoverage.instrument_key == key,
                BacktestDataCoverage.timeframe == TIMEFRAME_1M,
                BacktestDataCoverage.validated.is_(True),
                BacktestDataCoverage.end_date >= start,
                BacktestDataCoverage.start_date <= end,
            )
            .order_by(BacktestDataCoverage.start_date)
        )
    )


def missing_ranges(
    db: Session,
    key: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Calculate uncovered ranges; callers can fetch only these ranges."""
    coverage = local_coverage(db, key, start, end)
    cursor = start
    missing: list[tuple[datetime, datetime]] = []
    for item in coverage:
        if item.start_date > cursor:
            missing.append((cursor, min(item.start_date, end)))
        if item.end_date > cursor:
            cursor = item.end_date
        if cursor >= end:
            break
    if cursor < end:
        missing.append((cursor, end))
    return [(a, b) for a, b in missing if a < b]


def record_coverage(
    db: Session,
    *,
    key: str,
    symbol: str,
    segment: str,
    contract_month: str | None,
    start: datetime,
    end: datetime,
    row_count: int,
    data_version: str | None,
    source_hash: str | None,
    validated: bool = True,
) -> BacktestDataCoverage:
    """Record a validated imported range in the coverage catalog."""
    item = db.scalar(
        select(BacktestDataCoverage).where(
            BacktestDataCoverage.instrument_key == key,
            BacktestDataCoverage.timeframe == TIMEFRAME_1M,
            BacktestDataCoverage.start_date == start,
            BacktestDataCoverage.end_date == end,
        )
    )
    if item is None:
        item = BacktestDataCoverage(
            instrument_key=key,
            symbol=symbol,
            segment=segment,
            contract_month=contract_month,
            timeframe=TIMEFRAME_1M,
            start_date=start,
            end_date=end,
            row_count=row_count,
            data_version=data_version,
            source_hash=source_hash,
            validated=validated,
        )
        db.add(item)
    else:
        item.row_count = row_count
        item.data_version = data_version
        item.source_hash = source_hash
        item.validated = validated
        item.updated_at = datetime.utcnow()
    db.commit()
    return item


def iter_bars(
    db: Session,
    *,
    key: str,
    start: datetime,
    end: datetime,
    chunk_size: int = 5000,
):
    """Stream bars in bounded chunks so a full F&O year is never loaded at once."""
    stmt = (
        select(HistoricalMarketBar)
        .where(
            HistoricalMarketBar.instrument_key == key,
            HistoricalMarketBar.timestamp >= start,
            HistoricalMarketBar.timestamp <= end,
        )
        .order_by(HistoricalMarketBar.timestamp)
        .execution_options(yield_per=chunk_size)
    )
    yield from db.scalars(stmt)
