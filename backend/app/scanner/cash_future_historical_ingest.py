"""Provider-agnostic, bounded Cash-Future historical ingestion bridge.

This module deliberately does not invent provider APIs. A provider adapter supplies
synchronized cash/future observations; this bridge validates them, persists them in
bounded batches, and keeps contract months separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol

from sqlalchemy.orm import Session

from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_history_store import save_history_points


class CashFutureHistoricalSource(Protocol):
    """Real provider adapter returning source-backed synchronized observations."""

    def fetch(
        self,
        *,
        symbol: str,
        contract_month: str,
        start: datetime,
        end: datetime,
    ) -> Iterable[CashFutureHistoryPoint]:
        ...


@dataclass(frozen=True)
class CashFutureHistoricalIngestResult:
    symbol: str
    contract_month: str
    fetched: int
    persisted: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None


def _validate_point(point: CashFutureHistoryPoint, symbol: str, contract_month: str) -> None:
    if point.symbol.strip().upper() != symbol.strip().upper():
        raise ValueError("historical source returned a different symbol")
    if point.contract_month != contract_month:
        raise ValueError("historical source returned a different contract month")
    if point.timestamp.tzinfo is None:
        raise ValueError("historical source timestamps must be timezone-aware")


def ingest_cash_future_history(
    db: Session,
    source: CashFutureHistoricalSource,
    *,
    symbol: str,
    contract_month: str,
    start: datetime,
    end: datetime,
    batch_size: int = 1024,
) -> CashFutureHistoricalIngestResult:
    """Stream one contract's synchronized history into the durable SQL store.

    The provider iterator is never materialized as a full-period list. Each batch
    is committed before the next batch is consumed. Replays are idempotent through
    the history store's symbol/contract/timestamp identity.
    """
    if not symbol.strip() or not contract_month.strip():
        raise ValueError("symbol and contract_month are required")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if end < start:
        raise ValueError("end must not be before start")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    normalized_symbol = symbol.strip().upper()
    normalized_contract = contract_month.strip()
    fetched = 0
    persisted = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    batch: list[CashFutureHistoryPoint] = []

    for point in source.fetch(
        symbol=normalized_symbol,
        contract_month=normalized_contract,
        start=start,
        end=end,
    ):
        _validate_point(point, normalized_symbol, normalized_contract)
        if point.timestamp < start or point.timestamp > end:
            raise ValueError("historical source returned a record outside requested range")
        batch.append(point)
        fetched += 1
        first_timestamp = point.timestamp if first_timestamp is None else min(first_timestamp, point.timestamp)
        last_timestamp = point.timestamp if last_timestamp is None else max(last_timestamp, point.timestamp)
        if len(batch) >= batch_size:
            persisted += save_history_points(db, batch)
            batch.clear()

    if batch:
        persisted += save_history_points(db, batch)

    return CashFutureHistoricalIngestResult(
        symbol=normalized_symbol,
        contract_month=normalized_contract,
        fetched=fetched,
        persisted=persisted,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
    )


__all__ = ["CashFutureHistoricalSource", "CashFutureHistoricalIngestResult", "ingest_cash_future_history"]
