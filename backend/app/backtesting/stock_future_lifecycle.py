"""Expiry-aware lifecycle primitives for stock-future backtests.

A contract is identified by its exact exchange/segment/symbol/token/expiry.
Lifecycle filtering is explicit so adjacent expiries cannot be silently mixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


def _to_ns(value: int | datetime) -> int:
    if isinstance(value, int):
        if value < 0:
            raise ValueError("timestamp must be non-negative")
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


@dataclass(frozen=True)
class StockFutureLifecycle:
    """Immutable identity and active interval for one stock-future contract."""

    exchange: str
    segment: str
    symbol: str
    token: str
    expiry: date
    start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        if not all((self.exchange, self.segment, self.symbol, self.token)):
            raise ValueError("contract identity fields must be non-empty")
        if self.start_ns < 0 or self.end_ns < 0:
            raise ValueError("lifecycle timestamps must be non-negative")
        if self.end_ns < self.start_ns:
            raise ValueError("end_ns must be >= start_ns")

    @property
    def instrument_key(self) -> str:
        return (
            f"{self.exchange}:{self.segment}:{self.symbol}:"
            f"{self.token}:{self.expiry.isoformat()}"
        )

    def contains(self, timestamp: int | datetime) -> bool:
        """Return whether a timestamp belongs to this contract lifecycle."""
        timestamp_ns = _to_ns(timestamp)
        return self.start_ns <= timestamp_ns <= self.end_ns

    def require_timestamp(self, timestamp: int | datetime) -> int:
        """Validate lifecycle membership and return the normalized nanoseconds."""
        timestamp_ns = _to_ns(timestamp)
        if not self.contains(timestamp_ns):
            raise ValueError(
                f"timestamp {timestamp_ns} is outside lifecycle {self.instrument_key}"
            )
        return timestamp_ns


def lifecycle_for_expiry(
    *,
    exchange: str,
    segment: str,
    symbol: str,
    token: str,
    expiry: date,
    start: int | datetime,
    end: int | datetime,
) -> StockFutureLifecycle:
    """Construct a lifecycle with explicit boundaries; no timestamps are fabricated."""
    return StockFutureLifecycle(
        exchange=exchange,
        segment=segment,
        symbol=symbol,
        token=token,
        expiry=expiry,
        start_ns=_to_ns(start),
        end_ns=_to_ns(end),
    )
