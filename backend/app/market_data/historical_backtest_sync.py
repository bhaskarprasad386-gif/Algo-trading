"""Incremental Angel One historical-data ingestion for backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.backtest.data_store import instrument_key, missing_ranges, record_coverage, upsert_1m_bars
from app.market_data.candle_normalizer import normalize_candles
from app.market_data.historical import HistoricalDataClient


@dataclass(frozen=True)
class HistoricalBacktestInstrument:
    symbol: str
    token: str
    exchange: str
    segment: str
    instrument_type: str
    contract_month: str | None = None
    expiry_date: date | datetime | None = None
    lot_size: int | None = None

    @property
    def key(self) -> str:
        return instrument_key(self.symbol, self.segment, self.instrument_type, self.contract_month)


@dataclass(frozen=True)
class HistoricalBacktestSyncResult:
    instrument_key: str
    requested_start: datetime
    requested_end: datetime
    ranges_requested: int
    ranges_completed: int
    rows_written: int

    @property
    def completed(self) -> bool:
        return self.ranges_requested == self.ranges_completed


def _naive_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid historical candle timestamp") from exc
    else:
        raise ValueError("invalid historical candle timestamp")
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"invalid historical candle {field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid historical candle {field}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"invalid historical candle {field}")
    return number


def _normalize_for_store(rows: list[Any], instrument: HistoricalBacktestInstrument) -> list[dict[str, Any]]:
    normalized = normalize_candles(rows)
    output: list[dict[str, Any]] = []
    for row in normalized:
        timestamp = _naive_datetime(row.get("timestamp"))
        values = {
            "open": _finite_number(row.get("open"), "open"),
            "high": _finite_number(row.get("high"), "high"),
            "low": _finite_number(row.get("low"), "low"),
            "close": _finite_number(row.get("close"), "close"),
        }
        if values["high"] < max(values["open"], values["close"]) or values["low"] > min(values["open"], values["close"]):
            raise ValueError("invalid historical candle OHLC range")
        output.append({
            "instrument_key": instrument.key, "symbol": instrument.symbol, "segment": instrument.segment,
            "instrument_type": instrument.instrument_type, "contract_month": instrument.contract_month,
            "expiry_date": instrument.expiry_date, "lot_size": instrument.lot_size, "timestamp": timestamp,
            **values,
            "volume": None if row.get("volume") is None else _finite_number(row["volume"], "volume"),
            "open_interest": None if row.get("open_interest") is None else _finite_number(row["open_interest"], "open_interest"),
            "bid": None if row.get("bid") is None else _finite_number(row["bid"], "bid"),
            "ask": None if row.get("ask") is None else _finite_number(row["ask"], "ask"),
            "source": "angel_one", "data_version": row.get("data_version"), "source_hash": row.get("source_hash"),
        })
    return output


def _contiguous_minute_ranges(bars: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    """Return one coverage interval per contiguous 1-minute run.

    Recording one broad interval for a sparse provider response would make an
    internal missing minute look covered forever. Coverage therefore follows
    the actual minute continuity of the validated rows.
    """
    timestamps = sorted({bar["timestamp"] for bar in bars})
    if not timestamps:
        return []
    ranges: list[tuple[datetime, datetime]] = []
    run_start = run_end = timestamps[0]
    for timestamp in timestamps[1:]:
        if timestamp - run_end == timedelta(minutes=1):
            run_end = timestamp
            continue
        ranges.append((run_start, run_end))
        run_start = run_end = timestamp
    ranges.append((run_start, run_end))
    return ranges


def sync_historical_backtest_data(db: Session, *, instrument: HistoricalBacktestInstrument, start: datetime,
                                  end: datetime, client: HistoricalDataClient | None = None,
                                  interval: str = "ONE_MINUTE") -> HistoricalBacktestSyncResult:
    """Fetch only uncovered ranges and durably store validated 1-minute candles."""
    if start >= end:
        raise ValueError("historical sync start must be before end")
    if not instrument.symbol.strip() or not instrument.token.strip():
        raise ValueError("historical instrument symbol and token are required")

    ranges = missing_ranges(db, instrument.key, start, end)
    historical_client = client or HistoricalDataClient()
    completed = 0
    rows_written = 0
    for range_start, range_end in ranges:
        response = historical_client.get_candles(instrument.exchange, instrument.token, interval,
                                                 range_start.isoformat(sep=" "), range_end.isoformat(sep=" "))
        raw_rows = response.get("data") if isinstance(response, dict) else None
        if not isinstance(raw_rows, list) or not raw_rows:
            raise ValueError("historical provider returned no candle data")
        bars = [bar for bar in _normalize_for_store(raw_rows, instrument)
                if range_start <= bar["timestamp"] <= range_end]
        if not bars:
            raise ValueError("historical provider returned no valid candles in requested range")
        written = upsert_1m_bars(db, bars)
        for coverage_start, coverage_end in _contiguous_minute_ranges(bars):
            record_coverage(db, key=instrument.key, symbol=instrument.symbol, segment=instrument.segment,
                            contract_month=instrument.contract_month, start=coverage_start, end=coverage_end,
                            row_count=sum(1 for bar in bars if coverage_start <= bar["timestamp"] <= coverage_end),
                            data_version="angel_one_v1", source_hash=None, validated=True)
        rows_written += written
        completed += 1
    return HistoricalBacktestSyncResult(instrument.key, start, end, len(ranges), completed, rows_written)
