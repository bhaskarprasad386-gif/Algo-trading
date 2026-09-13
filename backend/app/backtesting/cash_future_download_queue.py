"""Build durable, contract-aware historical download requests for Cash-Future replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from .cash_future_rollover_plan import CashFutureSegment, build_mode_segments
from .historical_ingest import HistoricalFetchRequest


MARKET_TZ = ZoneInfo("Asia/Kolkata")
DEFAULT_SOURCE = "angelone"


@dataclass(frozen=True)
class CashFutureSegmentDownload:
    segment: CashFutureSegment | None  # None for spot
    request: HistoricalFetchRequest


@dataclass(frozen=True)
class CashFutureDownloadQueue:
    spot: CashFutureSegmentDownload
    futures: tuple[CashFutureSegmentDownload, ...]

    @property
    def all_requests(self) -> tuple[HistoricalFetchRequest, ...]:
        return (self.spot.request, *(item.request for item in self.futures))


def _ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=MARKET_TZ)
    return int(value.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _market_day_bounds(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, time.min, tzinfo=MARKET_TZ),
        datetime.combine(day, time.max, tzinfo=MARKET_TZ),
    )


def build_rollover_download_queue(
    *,
    catalog,
    spot_instrument: str,
    exchange: str,
    underlying: str,
    start: datetime,
    end: datetime,
    timeframe: str = "1m",
    mode: str = "BOTH",
    source: str = DEFAULT_SOURCE,
    session_days: Iterable[date] | None = None,
) -> CashFutureDownloadQueue:
    """Create exact-token future requests using Indian market-local session days."""
    source = str(source).strip()
    if not source:
        raise ValueError("historical download source cannot be empty")
    if end < start:
        raise ValueError("end must not precede start")

    local_start = start.astimezone(MARKET_TZ) if start.tzinfo else start.replace(tzinfo=MARKET_TZ)
    local_end = end.astimezone(MARKET_TZ) if end.tzinfo else end.replace(tzinfo=MARKET_TZ)
    resolved_days = None if session_days is None else tuple(sorted(set(session_days)))
    segments_by_leg = build_mode_segments(
        catalog,
        exchange=exchange,
        underlying=underlying,
        start=local_start.date(),
        end=local_end.date(),
        mode=mode,
        session_days=resolved_days,
    )

    start_ns, end_ns = _ns(start), _ns(end)
    spot_request = HistoricalFetchRequest(source, spot_instrument, timeframe, start_ns, end_ns)
    spot = CashFutureSegmentDownload(None, spot_request)
    
    items: list[CashFutureSegmentDownload] = []
    for segments in segments_by_leg:
        for segment in segments:
            day_start = _market_day_bounds(segment.start)[0]
            day_end = _market_day_bounds(segment.end)[1]
            seg_start = max(local_start, day_start)
            seg_end = min(local_end, day_end)
            if seg_end < seg_start:
                continue
            items.append(CashFutureSegmentDownload(
                segment,
                HistoricalFetchRequest(
                    source,
                    f"{segment.future.exchange}:{segment.future.token}:{segment.future.symbol}",
                    timeframe,
                    _ns(seg_start), _ns(seg_end),
                ),
            ))
    return CashFutureDownloadQueue(spot=spot, futures=tuple(items))
