"""Materialize synchronized historical catalog bars into Cash-Future history."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_history_store import save_history_points


def _close(record: HistoricalRecord) -> float:
    value = record.payload.get("close")
    if value is None:
        raise ValueError(f"historical record has no close price: {record.instrument} {record.timestamp_ns}")
    return float(value)


def _optional_float(record: HistoricalRecord, *keys: str) -> float | None:
    for key in keys:
        value = record.payload.get(key)
        if value is not None:
            return float(value)
    return None


def _timestamp(record: HistoricalRecord) -> datetime:
    return datetime.fromtimestamp(record.timestamp_ns / 1_000_000_000, tz=None)


def _point_from_records(
    spot_record: HistoricalRecord,
    future_record: HistoricalRecord,
    *,
    symbol: str,
    contract_month: str,
    lot_size: int,
    margin_required: float,
    expiry_date: date | None,
) -> CashFutureHistoryPoint:
    cash_price = _close(spot_record)
    future_price = _close(future_record)
    if cash_price <= 0:
        raise ValueError(f"cash price must be positive: {spot_record.timestamp_ns}")
    gap = future_price - cash_price
    return CashFutureHistoryPoint(
        timestamp=_timestamp(spot_record),
        symbol=symbol,
        contract_month=contract_month,
        cash_price=cash_price,
        future_price=future_price,
        gap=gap,
        gap_pct=(gap / cash_price) * 100.0,
        lot_size=lot_size,
        margin_required=margin_required,
        volume=_optional_float(spot_record, "volume"),
        oi=_optional_float(future_record, "oi", "open_interest"),
        cash_bid=_optional_float(spot_record, "bid", "best_bid", "cash_bid"),
        cash_ask=_optional_float(spot_record, "ask", "best_ask", "cash_ask"),
        future_bid=_optional_float(future_record, "bid", "best_bid", "future_bid"),
        future_ask=_optional_float(future_record, "ask", "best_ask", "future_ask"),
        cash_bid_qty=_optional_float(spot_record, "bid_qty", "best_bid_qty", "cash_bid_qty"),
        cash_ask_qty=_optional_float(spot_record, "ask_qty", "best_ask_qty", "cash_ask_qty"),
        future_bid_qty=_optional_float(future_record, "bid_qty", "best_bid_qty", "future_bid_qty"),
        future_ask_qty=_optional_float(future_record, "ask_qty", "best_ask_qty", "future_ask_qty"),
        expiry_date=expiry_date,
    )


def materialize_cash_future_history(
    db: Session,
    catalog: HistoricalCatalog,
    *,
    source: str,
    spot_instrument: str,
    future_instrument: str,
    symbol: str,
    contract_month: str,
    lot_size: int,
    margin_required: float = 0.0,
    expiry_date: date | None = None,
    timeframe: str = "1m",
    start_ns: int | None = None,
    end_ns: int | None = None,
    batch_size: int = 1000,
) -> int:
    """Synchronize two catalog streams and persist bounded Cash-Future observations.

    Missing timestamps on either leg are skipped. Quote/depth fields are copied
    only when the source actually contains them; no volume/OI-derived liquidity
    is manufactured.
    """
    if not symbol.strip() or not contract_month.strip():
        raise ValueError("symbol and contract_month are required")
    if lot_size <= 0:
        raise ValueError("lot_size must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    spot = catalog.iter_records(source=source, instrument=spot_instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns)
    future = catalog.iter_records(source=source, instrument=future_instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns)
    spot_record = next(spot, None)
    future_record = next(future, None)
    batch: list[CashFutureHistoryPoint] = []
    inserted = 0

    while spot_record is not None and future_record is not None:
        if spot_record.timestamp_ns < future_record.timestamp_ns:
            spot_record = next(spot, None)
            continue
        if future_record.timestamp_ns < spot_record.timestamp_ns:
            future_record = next(future, None)
            continue

        batch.append(_point_from_records(
            spot_record, future_record,
            symbol=symbol,
            contract_month=contract_month,
            lot_size=lot_size,
            margin_required=margin_required,
            expiry_date=expiry_date,
        ))
        if len(batch) >= batch_size:
            inserted += save_history_points(db, batch)
            batch.clear()
        spot_record = next(spot, None)
        future_record = next(future, None)

    if batch:
        inserted += save_history_points(db, batch)
    return inserted


__all__ = ["materialize_cash_future_history"]
