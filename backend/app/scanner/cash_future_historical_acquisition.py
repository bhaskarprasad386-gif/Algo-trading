"""Restart-safe durable acquisition of synchronized Cash-Future history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Mapping, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.backtesting.historical_job_store import HistoricalJob, HistoricalJobStore
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_history_store import save_history_points

IST = ZoneInfo("Asia/Kolkata")


class CashFutureHistoricalSource(Protocol):
    def fetch(
        self,
        *,
        symbol: str,
        contract_month: str,
        start: datetime,
        end: datetime,
    ) -> Iterable[CashFutureHistoryPoint]: ...


@dataclass(frozen=True)
class CashFutureAcquisitionChunk:
    symbol: str
    contract_month: str
    start: datetime
    end: datetime

    def metadata(self) -> Mapping[str, str]:
        return {
            "symbol": self.symbol,
            "contract_month": self.contract_month,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


@dataclass(frozen=True)
class CashFutureAcquisitionResult:
    job: HistoricalJob
    chunks_processed: int
    observations_saved: int


def _normalize(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    return value.astimezone(IST)


def build_cash_future_acquisition_plan(
    contract_requests: Iterable[tuple[str, str]],
    *,
    start: datetime,
    end: datetime,
) -> tuple[CashFutureAcquisitionChunk, ...]:
    """Build weekday, session-bounded chunks without materializing market data."""
    start = _normalize(start)
    end = _normalize(end)
    if end < start:
        raise ValueError("end must not be before start")

    pairs = tuple((symbol.strip().upper(), month.strip()) for symbol, month in contract_requests)
    if not pairs or any(not symbol or not month for symbol, month in pairs):
        raise ValueError("contract_requests must contain non-empty symbol/month pairs")

    chunks: list[CashFutureAcquisitionChunk] = []
    current = start.date()
    final = end.date()
    while current <= final:
        if current.weekday() < 5:
            session_start = datetime.combine(current, time(9, 15), tzinfo=IST)
            session_end = datetime.combine(current, time(15, 30), tzinfo=IST)
            chunk_start = max(start, session_start)
            chunk_end = min(end, session_end)
            if chunk_start <= chunk_end:
                for symbol, month in pairs:
                    chunks.append(CashFutureAcquisitionChunk(symbol, month, chunk_start, chunk_end))
        current += timedelta(days=1)
    return tuple(chunks)


def acquire_cash_future_history_durable(
    db: Session,
    source: CashFutureHistoricalSource,
    job_store: HistoricalJobStore,
    *,
    chunks: Iterable[CashFutureAcquisitionChunk],
    job_id: str,
    run_id: str | None = None,
    batch_size: int = 500,
) -> CashFutureAcquisitionResult:
    """Acquire planned chunks with durable restart state and bounded DB batches."""
    if not job_id.strip():
        raise ValueError("job_id is required")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    plan = tuple(chunks)
    metadata = tuple(chunk.metadata() for chunk in plan)
    fingerprint = job_store.fingerprint(metadata)
    try:
        existing = job_store.get(job_id)
        if existing.plan_fingerprint != fingerprint:
            raise ValueError(f"job {job_id} plan fingerprint does not match")
    except KeyError:
        existing = job_store.create(
            job_id=job_id,
            run_id=run_id or uuid4().hex,
            plan_fingerprint=fingerprint,
            total_chunks=len(plan),
            plan_metadata=metadata,
        )

    job_store.recover_running_chunks(job_id)
    pending = set(job_store.pending_indices(job_id))
    saved = 0
    processed = 0

    for index in sorted(pending):
        chunk = plan[index]
        job_store.start_chunk(job_id, index)
        try:
            batch: list[CashFutureHistoryPoint] = []
            for point in source.fetch(
                symbol=chunk.symbol,
                contract_month=chunk.contract_month,
                start=chunk.start,
                end=chunk.end,
            ):
                batch.append(point)
                if len(batch) >= batch_size:
                    saved += save_history_points(db, batch)
                    batch.clear()
            if batch:
                saved += save_history_points(db, batch)
            job_store.complete_chunk(job_id, index)
            processed += 1
        except Exception as exc:
            job_store.fail_chunk(job_id, index, str(exc), recoverable=True)
            break

    return CashFutureAcquisitionResult(
        job=job_store.finish(job_id),
        chunks_processed=processed,
        observations_saved=saved,
    )


__all__ = [
    "CashFutureAcquisitionChunk",
    "CashFutureAcquisitionResult",
    "CashFutureHistoricalSource",
    "acquire_cash_future_history_durable",
    "build_cash_future_acquisition_plan",
]
