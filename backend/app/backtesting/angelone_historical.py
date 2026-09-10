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
from .historical_provider_capabilities import capabilities
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
ANGEL_ONE_HISTORICAL_CAPABILITIES = capabilities("angelone", INTERVAL_MAP)

IST = ZoneInfo("Asia/Kolkata")


def _ns_to_angel_datetime(timestamp_ns: int) -> str:
    """Format a UTC nanosecond timestamp in the IST format Angel One expects."""
    dt = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")


def _timestamp_ns(value: Any) -> int:
    """Normalize epoch seconds/milliseconds/microseconds/nanoseconds or ISO time to ns."""
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric < 10_000_000_000:
            return int(numeric * 1_000_000_000)
        if numeric < 10_000_000_000_000:
            return int(numeric * 1_000_000)
        if numeric < 10_000_000_000_000_000:
            return int(numeric * 1_000)
        return int(numeric)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _chunk_ranges(start_ns: int, end_ns: int, chunk_days: int) -> Iterable[tuple[int, int]]:
    """Yield bounded, non-overlapping UTC ranges for one historical request."""
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    chunk_ns = chunk_days * 86_400 * 1_000_000_000
    start = start_ns
    while start <= end_ns:
        end = min(end_ns, start + chunk_ns - 1)
        yield start, end
        start = end + 1


class AngelOneHistoricalSource:
    """Real Angel One candle source implementing HistoricalSource."""

    source_name = "angelone"
    capabilities = ANGEL_ONE_HISTORICAL_CAPABILITIES

    def __init__(
        self,
        auth: AngelOneAuth | None = None,
        *,
        limiter: HistoricalRateLimiter | None = None,
        chunk_days: int = 30,
    ) -> None:
        if chunk_days <= 0:
            raise ValueError("chunk_days must be positive")
        self.auth = auth or AngelOneAuth()
        self.limiter = limiter or HistoricalRateLimiter()
        self.chunk_days = chunk_days

    def fetch(self, request: HistoricalFetchRequest) -> Iterable[HistoricalRecord]:
        self.capabilities.require(request.timeframe)
        interval = INTERVAL_MAP[request.timeframe]
        if request.start_ns > request.end_ns:
            raise ValueError("invalid historical range")

        parts = request.instrument.split(":")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError("instrument must be EXCHANGE:TOKEN or EXCHANGE:TOKEN:SYMBOL")
        exchange, token = parts[0], parts[1]
        symbol = parts[2] if len(parts) > 2 else request.instrument

        client = self.auth.get_client()
        for chunk_start_ns, chunk_end_ns in _chunk_ranges(
            request.start_ns, request.end_ns, self.chunk_days
        ):
            params = {
                "exchange": exchange,
                "symboltoken": token,
                "interval": interval,
                "fromdate": _ns_to_angel_datetime(chunk_start_ns),
                "todate": _ns_to_angel_datetime(chunk_end_ns),
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


__all__ = ["AngelOneHistoricalSource", "ANGEL_ONE_HISTORICAL_CAPABILITIES"]
