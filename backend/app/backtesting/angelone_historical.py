"""Angel One SmartAPI adapter for durable historical candle ingestion.

This adapter is deliberately thin: authentication is delegated to the existing
AngelOneAuth service and persistence remains in HistoricalCatalog. No synthetic
or fallback market data is generated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from app.algo.auth import AngelOneAuth

from .historical_catalog import HistoricalRecord
from .historical_ingest import HistoricalFetchRequest
from .historical_rate_limiter import HistoricalRateLimiter


INTERVAL_MAP = {
    "1m": "ONE_MINUTE",
    "3m": "THREE_MINUTE",
    "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}

IST = ZoneInfo("Asia/Kolkata")


def _ns_to_angel_datetime(timestamp_ns: int) -> str:
    """Format a UTC nanosecond timestamp in the IST format Angel One expects."""
    dt = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")


def _timestamp_ns(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value * 1_000_000_000) if float(value) < 10_000_000_000 else int(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


class AngelOneHistoricalSource:
    """Real Angel One candle source implementing HistoricalSource."""

    source_name = "angelone"

    def __init__(self, auth: AngelOneAuth | None = None, *, limiter: HistoricalRateLimiter | None = None) -> None:
        self.auth = auth or AngelOneAuth()
        self.limiter = limiter or HistoricalRateLimiter()

    def fetch(self, request: HistoricalFetchRequest) -> Iterable[HistoricalRecord]:
        interval = INTERVAL_MAP.get(request.timeframe)
        if interval is None:
            raise ValueError(f"unsupported Angel One timeframe: {request.timeframe}")
        if request.start_ns > request.end_ns:
            raise ValueError("invalid historical range")

        parts = request.instrument.split(":")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError("instrument must be EXCHANGE:TOKEN or EXCHANGE:TOKEN:SYMBOL")
        exchange, token = parts[0], parts[1]
        symbol = parts[2] if len(parts) > 2 else request.instrument

        client = self.auth.get_client()
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": _ns_to_angel_datetime(request.start_ns),
            "todate": _ns_to_angel_datetime(request.end_ns),
        }
        self.limiter.acquire()
        response = client.getCandleData(params)
        if not isinstance(response, dict) or not response.get("status"):
            message = response.get("message", "Angel One historical API failed") if isinstance(response, dict) else "invalid Angel One response"
            raise RuntimeError(message)

        rows = response.get("data") or []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise ValueError("invalid Angel One candle row")
            ts = _timestamp_ns(row[0])
            if not request.start_ns <= ts <= request.end_ns:
                continue
            payload = {
                "exchange": exchange,
                "symboltoken": token,
                "symbol": symbol,
                "timestamp": row[0],
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            if len(row) > 6 and row[6] is not None:
                payload["open_interest"] = float(row[6])
            yield HistoricalRecord(
                source=self.source_name,
                instrument=request.instrument,
                timeframe=request.timeframe,
                timestamp_ns=ts,
                payload=payload,
            )
