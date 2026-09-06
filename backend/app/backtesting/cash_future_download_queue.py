"""Build durable, contract-aware historical download requests for Cash-Future replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .cash_future_rollover_plan import CashFutureSegment, build_mode_segments
from .historical_ingest import HistoricalFetchRequest


MARKET_TZ = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class CashFutureSegmentDownload:
    segment: CashFutureSegment
    request: HistoricalFetchRequest


@dataclass(frozen=True)
class CashFutureDownloadQueue:
    spot: HistoricalFetchRequest
    futures: tuple[CashFutureSegmentDownload, ...]

    @property
    def all_requests(self) -> tuple[HistoricalFetchRequest, ...]:
        return (self.spot, *(item.request for item in self.futures))


def _ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=MARKET_TZ)
    return int(value.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _market_day_bounds(day) -> tuple[datetime, datetime]:
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
) -> CashFutureDownloadQueue:
    """Create exact-token future requests using Indian market-local day boundaries."""
    if end < start:
        raise ValueError("end must not precede start")
    segments_by_leg = build_mode_segments(
        catalog, exchange=exchange, underlying=underlying,
        start=start.astimezone(MARKET_TZ).date() if start.tzinfo else start.date(),
        end=end.astimezone(MARKET_TZ).date() if end.tzinfo else end.date(),
        mode=mode,
    )
    start_ns, end_ns = _ns(start), _ns(end)
    spot = HistoricalFetchRequest("angelone", spot_instrument, timeframe, start_ns, end_ns)
    items: list[CashFutureSegmentDownload] = []
    for segments in segments_by_leg:
        for segment in segments:
            day_start, day_end = _market_day_bounds(segment.start)[0], _market_day_bounds(segment.end)[1]
            seg_start = max(start, day_start) if start.tzinfo else max(start.replace(tzinfo=MARKET_TZ), day_start)
            seg_end = min(end, day_end) if end.tzinfo else min(end.replace(tzinfo=MARKET_TZ), day_end)
            items.append(CashFutureSegmentDownload(
                segment,
                HistoricalFetchRequest(
                    "angelone",
                    f"{segment.future.exchange}:{segment.future.token}:{segment.future.symbol}",
                    timeframe,
                    _ns(seg_start), _ns(seg_end),
                ),
            ))
    return CashFutureDownloadQueue(spot=spot, futures=tuple(items))
