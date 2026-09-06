"""Build durable, contract-aware historical download requests for Cash-Future replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

from .cash_future_rollover_plan import CashFutureSegment, build_mode_segments
from .historical_ingest import HistoricalFetchRequest


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
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


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
    """Create exact-token future requests for every historical contract segment."""
    if end < start:
        raise ValueError("end must not precede start")
    segments_by_leg = build_mode_segments(
        catalog, exchange=exchange, underlying=underlying,
        start=start.date(), end=end.date(), mode=mode,
    )
    start_ns, end_ns = _ns(start), _ns(end)
    spot = HistoricalFetchRequest("angelone", spot_instrument, timeframe, start_ns, end_ns)
    items: list[CashFutureSegmentDownload] = []
    for segments in segments_by_leg:
        for segment in segments:
            seg_start = max(start, datetime.combine(segment.start, time.min, tzinfo=timezone.utc))
            seg_end = min(end, datetime.combine(segment.end, time.max, tzinfo=timezone.utc))
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
