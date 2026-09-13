"""Build persisted Cash-Future observations from downloaded raw candles."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_history_store import save_history_points

from .cash_future_download_queue import CashFutureDownloadQueue, CashFutureSegmentDownload
from .historical_catalog import HistoricalCatalog, HistoricalRecord

IST = ZoneInfo("Asia/Kolkata")


def _point_from_rows(
    *,
    symbol: str,
    contract_month: str,
    future: CashFutureSegmentDownload,
    cash_row: HistoricalRecord,
    future_row: HistoricalRecord,
) -> CashFutureHistoryPoint:
    cash_price = float(cash_row.payload["close"])
    future_price = float(future_row.payload["close"])
    gap = future_price - cash_price
    gap_pct = gap / cash_price * 100.0 if cash_price else 0.0
    timestamp = datetime.fromtimestamp(
        cash_row.timestamp_ns / 1_000_000_000,
        tz=timezone.utc,
    ).astimezone(IST)
    return CashFutureHistoryPoint(
        timestamp=timestamp,
        symbol=symbol,
        contract_month=contract_month,
        cash_price=cash_price,
        future_price=future_price,
        gap=gap,
        gap_pct=gap_pct,
        lot_size=future.segment.future.lot_size,
        margin_required=0.0,
        volume=(float(future_row.payload["volume"]) if future_row.payload.get("volume") is not None else None),
        oi=(float(future_row.payload["open_interest"]) if future_row.payload.get("open_interest") is not None else None),
        expiry_date=future.segment.future.expiry,
    )


def _merge_common_timestamps(
    cash_rows: Iterable[HistoricalRecord],
    future_rows: Iterable[HistoricalRecord],
):
    """Yield synchronized rows while retaining at most one row from each stream."""
    cash_iter = iter(cash_rows)
    future_iter = iter(future_rows)
    cash = next(cash_iter, None)
    future = next(future_iter, None)
    while cash is not None and future is not None:
        if cash.timestamp_ns == future.timestamp_ns:
            yield cash, future
            cash = next(cash_iter, None)
            future = next(future_iter, None)
        elif cash.timestamp_ns < future.timestamp_ns:
            cash = next(cash_iter, None)
        else:
            future = next(future_iter, None)


def materialize_cash_future_history(
    *,
    db: Session,
    catalog: HistoricalCatalog,
    queue: CashFutureDownloadQueue,
    symbol: str,
    source: str = "angelone",
    timeframe: str = "1m",
    batch_size: int = 1000,
) -> int:
    """Synchronize downloaded cash/future candles into the Cash-Future history table.

    Each future rollover segment is processed independently and persisted in bounded
    batches. Missing timestamps are never forward-filled or synthesized. Re-running
    the materializer is idempotent because the history store keys observations by
    symbol, contract month and timestamp.
    """
    symbol = symbol.strip().upper()
    source = source.strip()
    timeframe = timeframe.strip()
    if not symbol:
        raise ValueError("symbol is required")
    if not source:
        raise ValueError("source is required")
    if not timeframe:
        raise ValueError("timeframe is required")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    total = 0
    for future in queue.futures:
        contract_month = future.segment.future.expiry.strftime("%Y-%m")
        start_ns = future.request.start_ns
        end_ns = future.request.end_ns
        cash_rows = catalog.iter_records(
            source=source,
            instrument=queue.spot.request.instrument,
            timeframe=timeframe,
            start_ns=start_ns,
            end_ns=end_ns,
        )
        future_rows = catalog.iter_records(
            source=source,
            instrument=future.request.instrument,
            timeframe=timeframe,
            start_ns=start_ns,
            end_ns=end_ns,
        )

        batch: list[CashFutureHistoryPoint] = []
        for cash_row, future_row in _merge_common_timestamps(cash_rows, future_rows):
            batch.append(
                _point_from_rows(
                    symbol=symbol,
                    contract_month=contract_month,
                    future=future,
                    cash_row=cash_row,
                    future_row=future_row,
                )
            )
            if len(batch) >= batch_size:
                total += save_history_points(db, batch)
                batch.clear()
        if batch:
            total += save_history_points(db, batch)

    return total


__all__ = ["materialize_cash_future_history"]
