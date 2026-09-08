"""Historical cash/futures acquisition across an expiry-driven futures chain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from app.market_data.futures_rollover import FuturesContract, build_futures_chain, map_rollover
from app.market_data.historical_backtest_sync import (
    HistoricalBacktestInstrument,
    HistoricalBacktestSyncResult,
    sync_historical_backtest_data,
)


@dataclass(frozen=True)
class CashFutureRolloverSyncResult:
    cash: HistoricalBacktestSyncResult
    futures: tuple[HistoricalBacktestSyncResult, ...]
    contracts: tuple[FuturesContract, ...]

    @property
    def completed(self) -> bool:
        return self.cash.completed and all(result.completed for result in self.futures)

    @property
    def rows_written(self) -> int:
        return self.cash.rows_written + sum(result.rows_written for result in self.futures)


def sync_cash_future_rollover_history(
    db: Session,
    *,
    cash: HistoricalBacktestInstrument,
    instrument_master_rows: Iterable[dict],
    underlying: str,
    start: datetime,
    end: datetime,
    client=None,
    interval: str = "ONE_MINUTE",
    sync_fn: Callable = sync_historical_backtest_data,
) -> CashFutureRolloverSyncResult:
    """Sync cash continuously and each expiry-specific futures contract separately."""
    if start >= end:
        raise ValueError("cash/future rollover sync start must be before end")
    if cash.instrument_type.upper() != "CASH":
        raise ValueError("cash instrument_type must be CASH")
    if cash.segment.upper() != "NSE":
        raise ValueError("cash segment must be NSE")

    chain = build_futures_chain(instrument_master_rows, underlying=underlying)
    windows = map_rollover(chain, start, end)
    if not windows:
        raise ValueError("no eligible NFO futures contracts cover the requested period")

    cash_result = sync_fn(db, instrument=cash, start=start, end=end, client=client, interval=interval)
    future_results: list[HistoricalBacktestSyncResult] = []
    used_contracts: list[FuturesContract] = []
    for window_start, window_end, contract in windows:
        future_results.append(sync_fn(
            db,
            instrument=contract.instrument,
            start=window_start,
            end=window_end,
            client=client,
            interval=interval,
        ))
        used_contracts.append(contract)

    return CashFutureRolloverSyncResult(
        cash=cash_result,
        futures=tuple(future_results),
        contracts=tuple(used_contracts),
    )
