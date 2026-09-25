"""Derived continuous futures records built from real contract history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from .fno_rollover import FNORolloverWindow
from .historical_catalog import HistoricalCatalog, HistoricalRecord

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


def _validate_windows(windows: tuple[FNORolloverWindow, ...]) -> None:
    ordered = sorted(windows, key=lambda window: (window.underlying, window.instrument_type, window.start_date, window.end_date, window.contract_token))
    previous_by_key: dict[tuple[str, str], FNORolloverWindow] = {}
    for window in ordered:
        key = (window.underlying, window.instrument_type)
        previous = previous_by_key.get(key)
        if previous is not None and window.start_date <= previous.end_date:
            raise ValueError(
                f"overlapping continuous futures windows for {window.underlying}/{window.instrument_type}: "
                f"{previous.start_date.isoformat()}..{previous.end_date.isoformat()} overlaps "
                f"{window.start_date.isoformat()}..{window.end_date.isoformat()}"
            )
        previous_by_key[key] = window


def build_continuous_futures_series(
    windows: Iterable[FNORolloverWindow],
    records_by_token: Mapping[str, Iterable[HistoricalRecord]],
    *,
    instrument_prefix: str = "NFO:",
) -> tuple[ContinuousFuturesRecord, ...]:
    """Project raw contract records through expiry-driven rollover windows."""
    if type(instrument_prefix) is not str or not instrument_prefix:
        raise ValueError("instrument_prefix must be a non-empty string")
    windows = tuple(windows)
    if not windows:
        return ()
    _validate_windows(windows)

    output: list[ContinuousFuturesRecord] = []
    for window in windows:
        raw_records = records_by_token.get(window.contract_token, ())
        start_ns = _day_start_ns(window.start_date)
        end_ns = _day_end_ns(window.end_date)
        expected_instrument = f"{instrument_prefix}{window.contract_token}"
        for record in raw_records:
            if record.instrument != expected_instrument:
                raise ValueError(f"record instrument {record.instrument!r} does not match contract token {window.contract_token!r}")
            if not window.start_date <= _session_date(record.timestamp_ns) <= window.end_date:
                continue
            if not start_ns <= record.timestamp_ns <= end_ns:
                continue
            output.append(ContinuousFuturesRecord(window.underlying, window.instrument_type, window.contract_token, record))

    output.sort(key=lambda item: (item.timestamp_ns, item.underlying, item.instrument_type, item.contract_token, item.record.instrument, item.record.sequence if item.record.sequence is not None else -1))
    return tuple(output)


def build_continuous_futures_series_from_catalog(
    catalog: HistoricalCatalog,
    windows: Iterable[FNORolloverWindow],
    *,
    source: str,
    timeframe: str,
    instrument_prefix: str = "NFO:",
) -> tuple[ContinuousFuturesRecord, ...]:
    """Build a continuous series directly from durable historical catalog data."""
    windows = tuple(windows)
    if not windows:
        return ()

    # Stream only the timestamp range required by each rollover window. The
    # returned series is still materialized because this public API returns a
    # tuple, but the intermediate per-contract catalog tuples are no longer
    # duplicated in memory.
    output: list[ContinuousFuturesRecord] = []
    for window in windows:
        start_ns = _day_start_ns(window.start_date)
        end_ns = _day_end_ns(window.end_date)
        instrument = f"{instrument_prefix}{window.contract_token}"
        for record in catalog.iter_records(
            source=source,
            instrument=instrument,
            timeframe=timeframe,
            start_ns=start_ns,
            end_ns=end_ns,
        ):
            output.append(
                ContinuousFuturesRecord(
                    window.underlying,
                    window.instrument_type,
                    window.contract_token,
                    record,
                )
            )

    output.sort(
        key=lambda item: (
            item.timestamp_ns,
            item.underlying,
            item.instrument_type,
            item.contract_token,
            item.record.instrument,
            item.record.sequence if item.record.sequence is not None else -1,
        )
    )
    return tuple(output)


__all__ = ["ContinuousFuturesRecord", "build_continuous_futures_series", "build_continuous_futures_series_from_catalog"]
