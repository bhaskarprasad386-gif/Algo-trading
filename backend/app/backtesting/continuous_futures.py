"""Derived continuous futures records built from real contract history.

The continuous series is a view over raw contract records. It never creates,
interpolates, forward-fills, or modifies market-data records. Rollover boundaries
come only from the expiry-driven windows produced by ``fno_rollover``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from .fno_rollover import FNORolloverWindow
from .historical_catalog import HistoricalRecord

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class ContinuousFuturesRecord:
    """One raw record exposed through a continuous-contract view."""

    underlying: str
    instrument_type: str
    contract_token: str
    record: HistoricalRecord

    @property
    def timestamp_ns(self) -> int:
        return self.record.timestamp_ns

    @property
    def timeframe(self) -> str:
        return self.record.timeframe

    @property
    def payload(self) -> Mapping[str, object]:
        return self.record.payload


def _session_date(timestamp_ns: int) -> date:
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc).astimezone(IST).date()


def _day_start_ns(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=IST).timestamp() * 1_000_000_000)


def _day_end_ns(value: date) -> int:
    return int(datetime.combine(value, time.max, tzinfo=IST).timestamp() * 1_000_000_000)


def build_continuous_futures_series(
    windows: Iterable[FNORolloverWindow],
    records_by_token: Mapping[str, Iterable[HistoricalRecord]],
) -> tuple[ContinuousFuturesRecord, ...]:
    """Project raw contract records through expiry-driven rollover windows.

    Only records belonging to a window's real contract and date range are
    emitted. Missing records remain missing, so no artificial rollover candle
    or synthetic bridge is introduced. Records are returned in timestamp order.
    """
    windows = tuple(windows)
    if not windows:
        return ()

    output: list[ContinuousFuturesRecord] = []
    for window in windows:
        raw_records = records_by_token.get(window.contract_token, ())
        start_ns = _day_start_ns(window.start_date)
        end_ns = _day_end_ns(window.end_date)
        for record in raw_records:
            if not window.start_date <= _session_date(record.timestamp_ns) <= window.end_date:
                continue
            if not start_ns <= record.timestamp_ns <= end_ns:
                continue
            output.append(
                ContinuousFuturesRecord(
                    underlying=window.underlying,
                    instrument_type=window.instrument_type,
                    contract_token=window.contract_token,
                    record=record,
                )
            )

    output.sort(key=lambda item: item.timestamp_ns)
    return tuple(output)


__all__ = ["ContinuousFuturesRecord", "build_continuous_futures_series"]
