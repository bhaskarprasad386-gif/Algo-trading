"""Cash-Future download queue builder backed by the Angel One instrument master."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

from .cash_future_download_queue import CashFutureDownloadQueue, build_rollover_download_queue
from .historical_cash_token_resolver import HistoricalCashTokenResolver


def build_master_backed_cash_future_queue(
    *,
    master_rows: Iterable[Mapping[str, Any]],
    catalog,
    exchange: str,
    underlying: str,
    start: datetime,
    end: datetime,
    timeframe: str = "1m",
    mode: str = "BOTH",
    source: str = "angelone",
    session_days: Iterable[date] | None = None,
) -> CashFutureDownloadQueue:
    """Resolve NSE cash token from master and reuse contract-aware rollover planning."""
    cash = HistoricalCashTokenResolver(master_rows).resolve(underlying)
    return build_rollover_download_queue(
        catalog=catalog,
        spot_instrument=cash.instrument,
        exchange=exchange,
        underlying=underlying,
        start=start,
        end=end,
        timeframe=timeframe,
        mode=mode,
        source=source,
        session_days=session_days,
    )


__all__ = ["build_master_backed_cash_future_queue"]
