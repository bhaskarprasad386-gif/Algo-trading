"""Persistent, incremental historical-data store for backtesting.

The store is deliberately provider-agnostic: NSE/other licensed data adapters can
normalize their payloads into HistoricalMarketBar rows. Backtests then read the
validated local store instead of repeatedly downloading the same history.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BacktestDataCoverage, HistoricalMarketBar


TIMEFRAME_1M = "1m"


def instrument_key(symbol: str, segment: str, instrument_type: str, contract_month: str | None = None) -> str:
    """Build a stable identity for a spot/future contract."""
    symbol = symbol.strip().upper()
    segment = segment.strip().upper()
    instrument_type = instrument_type.strip().upper()
    contract = (contract_month or "SPOT").strip().upper()
    if not symbol or not segment or not instrument_type or not contract:
        raise ValueError("instrument identity fields are required")
    return "|".join([symbol, segment, instrument_type, contract])


def _validate_bar(payload: dict) -> None:
    required = {"instrument_key", "timestamp", "symbol", "segment", "instrument_type", "open", "high", "low", "close"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"historical bar missing fields: {sorted(missing)}")
    if not isinstance(payload["timestamp"], datetime):
        raise ValueError("historical bar timestamp must be datetime")
    if not all(isinstance(payload[field], (int, float)) and not isinstance(payload[field], bool) and math.isfinite(float(payload[field])) for field in ("open", "high", "low", "close")):
        raise ValueError("historical bar OHLC values must be finite")
    open_price, high, low, close = (float(payload[field]) for field in ("open", "high", "low", "close"))
    if min(open_price, high, low, close) <= 0 or high < max(open_price, close) or low > min(open_price, close) or high < low:
        raise ValueError("historical bar has invalid OHLC relationship")
    for field in ("volume", "open_interest", "bid", "ask"):
        value = payload.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))):
            raise ValueError(f"historical bar {field} must be finite when provided")
    for field in ("volume", "open_interest"):
        value = payload.get(field)
        if value is not None and float(value) < 0:
            raise ValueError(f"historical bar {field} cannot be negative")
    bid, ask = payload.get("bid"), payload.get("ask")
    if bid is not None and ask is not None and (float(bid) <= 0 or float(ask) <= 0 or float(bid) > float(ask)):
        raise ValueError("historical bar bid/ask is invalid")
    lot_size = payload.get("lot_size")
    if lot_size is not None and (isinstance(lot_size, bool) or not isinstance(lot_size, int) or lot_size <= 0):
        raise ValueError("historical bar lot_size must be a positive integer")


def upsert_1m_bars(db: Session, bars: Iterable[dict]) -> int:
    """Persist validated 1-minute bars without duplicating existing minutes."""
    count = 0
    for payload in bars:
        if not isinstance(payload, dict):
            raise ValueError("historical bar payload must be a mapping")
        _validate_bar(payload)
        key = payload["instrument_key"]
        timestamp = payload["timestamp"]
        existing = db.scalar(select(HistoricalMarketBar).where(HistoricalMarketBar.instrument_key == key, HistoricalMarketBar.timestamp == timestamp))
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


def local_coverage(db: Session, key: str, start: datetime, end: datetime) -> list[BacktestDataCoverage]:
    """Return validated coverage records intersecting the requested range."""
    if not isinstance(start, datetime) or not isinstance(end, datetime) or start > end:
        raise ValueError("coverage range is invalid")
    return list(db.scalars(select(BacktestDataCoverage).where(BacktestDataCoverage.instrument_key == key, BacktestDataCoverage.timeframe == TIMEFRAME_1M, BacktestDataCoverage.validated.is_(True), BacktestDataCoverage.end_date >= start, BacktestDataCoverage.start_date <= end).order_by(BacktestDataCoverage.start_date)))


def missing_ranges(db: Session, key: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Calculate uncovered ranges; callers can fetch only these ranges."""
    if not isinstance(start, datetime) or not isinstance(end, datetime) or start > end:
        raise ValueError("coverage range is invalid")
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


def record_coverage(db: Session, *, key: str, symbol: str, segment: str, contract_month: str | None, start: datetime, end: datetime, row_count: int, data_version: str | None, source_hash: str | None, validated: bool = True) -> BacktestDataCoverage:
    """Record a validated imported range in the coverage catalog."""
    if not isinstance(start, datetime) or not isinstance(end, datetime) or start >= end:
        raise ValueError("coverage start/end must be datetimes with start before end")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise ValueError("row_count must be a non-negative integer")
    if not isinstance(validated, bool):
        raise ValueError("validated must be a boolean")
    if not str(key).strip() or not str(symbol).strip() or not str(segment).strip():
        raise ValueError("coverage identity fields are required")
    item = db.scalar(select(BacktestDataCoverage).where(BacktestDataCoverage.instrument_key == key, BacktestDataCoverage.timeframe == TIMEFRAME_1M, BacktestDataCoverage.start_date == start, BacktestDataCoverage.end_date == end))
    if item is None:
        item = BacktestDataCoverage(instrument_key=key, symbol=symbol, segment=segment, contract_month=contract_month, timeframe=TIMEFRAME_1M, start_date=start, end_date=end, row_count=row_count, data_version=data_version, source_hash=source_hash, validated=validated)
        db.add(item)
    else:
        item.row_count = row_count
        item.data_version = data_version
        item.source_hash = source_hash
        item.validated = validated
        item.updated_at = datetime.utcnow()
    db.commit()
    return item


def iter_bars(db: Session, *, key: str, start: datetime, end: datetime, chunk_size: int = 5000):
    """Stream bars in bounded chunks so a full F&O year is never loaded at once."""
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if not isinstance(start, datetime) or not isinstance(end, datetime) or start > end:
        raise ValueError("bar range is invalid")
    stmt = select(HistoricalMarketBar).where(HistoricalMarketBar.instrument_key == key, HistoricalMarketBar.timestamp >= start, HistoricalMarketBar.timestamp <= end).order_by(HistoricalMarketBar.timestamp).execution_options(yield_per=chunk_size)
    yield from db.scalars(stmt)
