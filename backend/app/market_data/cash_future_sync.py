"""Incremental cash/future historical acquisition for backtesting.

Cash and each selected futures contract are stored independently.  The pair is
only considered ready when both legs have complete validated coverage for the
requested window.  Uncovered ranges are delegated to the existing incremental
historical sync so retries never redownload validated minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from app.backtest.data_store import missing_ranges
from app.market_data.historical_backtest_sync import (
    HistoricalBacktestInstrument,
    HistoricalBacktestSyncResult,
    sync_historical_backtest_data,
)


@dataclass(frozen=True)
class CashFutureSyncResult:
    cash: HistoricalBacktestSyncResult
    future: HistoricalBacktestSyncResult

    @property
    def completed(self) -> bool:
        return self.cash.completed and self.future.completed

    @property
    def rows_written(self) -> int:
        return self.cash.rows_written + self.future.rows_written


def sync_cash_future_history(
    db: Session,
    *,
    cash: HistoricalBacktestInstrument,
    future: HistoricalBacktestInstrument,
    start: datetime,
    end: datetime,
    client=None,
    interval: str = "ONE_MINUTE",
    sync_fn: Callable = sync_historical_backtest_data,
) -> CashFutureSyncResult:
    """Acquire only missing cash/future ranges and return independent coverage state."""
    if start >= end:
        raise ValueError("cash/future sync start must be before end")
    if cash.instrument_type.upper() != "CASH":
        raise ValueError("cash instrument_type must be CASH")
    if future.instrument_type.upper() != "FUTURE":
        raise ValueError("future instrument_type must be FUTURE")
    if cash.segment.upper() != "NSE":
        raise ValueError("cash segment must be NSE")
    if future.segment.upper() != "NFO":
        raise ValueError("future segment must be NFO")

    # Resolve the two legs independently.  This is deliberate: one leg may be
    # complete while the other has a provider gap, and the next retry should
    # fetch only the incomplete leg/ranges.
    cash_result = sync_fn(
        db,
        instrument=cash,
        start=start,
        end=end,
        client=client,
        interval=interval,
    )
    future_result = sync_fn(
        db,
        instrument=future,
        start=start,
        end=end,
        client=client,
        interval=interval,
    )
    return CashFutureSyncResult(cash=cash_result, future=future_result)


def cash_future_missing_ranges(
    db: Session,
    *,
    cash: HistoricalBacktestInstrument,
    future: HistoricalBacktestInstrument,
    start: datetime,
    end: datetime,
) -> dict[str, list[tuple[datetime, datetime]]]:
    """Expose the exact uncovered windows for UI/progress reporting."""
    return {
        "cash": missing_ranges(db, cash.key, start, end),
        "future": missing_ranges(db, future.key, start, end),
    }
