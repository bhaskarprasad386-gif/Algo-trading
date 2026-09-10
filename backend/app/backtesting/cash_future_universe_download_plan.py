"""Deterministic all-stock-F&O Cash-Future historical acquisition planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from .historical_cash_token_resolver import HistoricalCashTokenResolver
from .historical_ingest import HistoricalFetchRequest
from .historical_sync import HistoricalSyncPlan

MARKET_TZ = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


@dataclass(frozen=True)
class CashFutureUniverseDownloadJob:
    """One stock underlying with its cash request and exact contract-month requests."""

    underlying: str
    spot: HistoricalFetchRequest
    futures: tuple[HistoricalFetchRequest, ...]

    @property
    def all_requests(self) -> tuple[HistoricalFetchRequest, ...]:
        return (self.spot, *self.futures)


@dataclass(frozen=True)
class CashFutureUniverseDownloadPlan:
    """Bounded deterministic acquisition plan for the stock F&O universe."""

    jobs: tuple[CashFutureUniverseDownloadJob, ...]
    plan: HistoricalSyncPlan

    @property
    def requests(self) -> tuple[HistoricalFetchRequest, ...]:
        return self.plan.requests


def _ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=MARKET_TZ)
    return int(value.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _session_bounds(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, MARKET_OPEN, tzinfo=MARKET_TZ),
        datetime.combine(day, MARKET_CLOSE, tzinfo=MARKET_TZ),
    )


def _days(start: datetime, end: datetime, session_days: Iterable[date] | None) -> tuple[date, ...]:
    local_start = start.astimezone(MARKET_TZ) if start.tzinfo else start.replace(tzinfo=MARKET_TZ)
    local_end = end.astimezone(MARKET_TZ) if end.tzinfo else end.replace(tzinfo=MARKET_TZ)
    if session_days is None:
        return tuple(
            date.fromordinal(day)
            for day in range(local_start.date().toordinal(), local_end.date().toordinal() + 1)
        )
    return tuple(sorted({day for day in session_days if local_start.date() <= day <= local_end.date()}))


def _contract_request(
    *,
    item: CashFutureUniverseItem,
    source: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    session_days: tuple[date, ...],
) -> HistoricalFetchRequest | None:
    local_start = start.astimezone(MARKET_TZ) if start.tzinfo else start.replace(tzinfo=MARKET_TZ)
    local_end = end.astimezone(MARKET_TZ) if end.tzinfo else end.replace(tzinfo=MARKET_TZ)
    if item.expiry < local_start.date():
        return None
    bounded_end = min(local_end, datetime.combine(item.expiry, MARKET_CLOSE, tzinfo=MARKET_TZ))
    bounded_start = local_start
    if bounded_end < bounded_start:
        return None
    days = tuple(day for day in session_days if bounded_start.date() <= day <= bounded_end.date())
    if not days:
        return None
    first_start = max(bounded_start, _session_bounds(days[0])[0])
    last_end = min(bounded_end, _session_bounds(days[-1])[1])
    if last_end < first_start:
        return None
    return HistoricalFetchRequest(
        source,
        f"NFO:{item.future_token}:{item.future_symbol}",
        timeframe,
        _ns(first_start),
        _ns(last_end),
    )


def build_cash_future_universe_download_plan(
    *,
    universe: CashFutureFnoUniverse,
    master_rows: Iterable[Mapping[str, Any]],
    start: datetime,
    end: datetime,
    timeframe: str = "1m",
    source: str = "angelone",
    session_days: Iterable[date] | None = None,
) -> CashFutureUniverseDownloadPlan:
    """Build all stock cash/future requests without materializing historical rows.

    Every represented stock contract month is kept as its exact NFO token. Index futures
    remain outside this plan because there is no NSE ``*-EQ`` cash token to pair with them.
    """
    if not source.strip():
        raise ValueError("historical download source cannot be empty")
    if end < start:
        raise ValueError("end must not precede start")

    cash_tokens = HistoricalCashTokenResolver(master_rows)
    days = _days(start, end, session_days)
    jobs: list[CashFutureUniverseDownloadJob] = []
    requests: list[HistoricalFetchRequest] = []

    for underlying in universe.stock_underlyings:
        cash = cash_tokens.resolve(underlying)
        local_start = start.astimezone(MARKET_TZ) if start.tzinfo else start.replace(tzinfo=MARKET_TZ)
        local_end = end.astimezone(MARKET_TZ) if end.tzinfo else end.replace(tzinfo=MARKET_TZ)
        spot_start = max(local_start, _session_bounds(days[0])[0]) if days else local_start
        spot_end = min(local_end, _session_bounds(days[-1])[1]) if days else local_end
        if spot_end < spot_start:
            continue
        spot = HistoricalFetchRequest(source, cash.instrument, timeframe, _ns(spot_start), _ns(spot_end))
        futures: list[HistoricalFetchRequest] = []
        for item in universe.stocks:
            if item.underlying != underlying:
                continue
            request = _contract_request(
                item=item,
                source=source,
                timeframe=timeframe,
                start=start,
                end=end,
                session_days=days,
            )
            if request is not None:
                futures.append(request)
        futures_tuple = tuple(futures)
        jobs.append(CashFutureUniverseDownloadJob(underlying, spot, futures_tuple))
        requests.extend((spot, *futures_tuple))

    jobs_tuple = tuple(jobs)
    return CashFutureUniverseDownloadPlan(jobs_tuple, HistoricalSyncPlan(tuple(requests)))


__all__ = [
    "CashFutureUniverseDownloadJob",
    "CashFutureUniverseDownloadPlan",
    "build_cash_future_universe_download_plan",
]
