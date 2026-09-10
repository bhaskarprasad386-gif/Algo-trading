"""Durable, bounded Cash-Future historical acquisition orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable, Iterable, Protocol
from zoneinfo import ZoneInfo

from app.backtesting.historical_job_store import HistoricalJobStore
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
    index: int
    start: datetime
    end: datetime


@dataclass(frozen=True)
class CashFutureAcquisitionResult:
    job_id: str
    run_id: str
    total_chunks: int
    completed_chunks: int
    skipped_chunks: int
    failed_chunk: int | None
    processed_points: int
    state: str


def build_cash_future_acquisition_chunks(
    *,
    start: datetime,
    end: datetime,
    chunk_days: int = 1,
) -> tuple[CashFutureAcquisitionChunk, ...]:
    """Split a requested period into bounded calendar-day chunks in IST."""
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if end < start:
        raise ValueError("end must not precede start")

    local_start = start.astimezone(IST)
    local_end = end.astimezone(IST)
    chunks: list[CashFutureAcquisitionChunk] = []
    cursor = local_start.date()
    index = 0
    while cursor <= local_end.date():
        chunk_end_date = min(cursor + timedelta(days=chunk_days - 1), local_end.date())
        day_start = datetime.combine(cursor, time(9, 15), tzinfo=IST)
        day_end = datetime.combine(chunk_end_date, time(15, 30), tzinfo=IST)
        chunk_start = max(local_start, day_start)
        chunk_end = min(local_end, day_end)
        if chunk_end >= chunk_start:
            chunks.append(CashFutureAcquisitionChunk(index, chunk_start, chunk_end))
            index += 1
        cursor = chunk_end_date + timedelta(days=1)
    return tuple(chunks)


def _metadata(
    chunks: tuple[CashFutureAcquisitionChunk, ...],
    *,
    symbol: str,
    contract_month: str,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "symbol": symbol.upper(),
            "contract_month": contract_month,
            "start": chunk.start.isoformat(),
            "end": chunk.end.isoformat(),
        }
        for chunk in chunks
    )


def acquire_cash_future_history_durable(
    *,
    db,
    source: CashFutureHistoricalSource,
    job_store: HistoricalJobStore,
    job_id: str,
    run_id: str,
    symbol: str,
    contract_month: str,
    start: datetime,
    end: datetime,
    chunk_days: int = 1,
    batch_size: int = 1024,
    on_chunk: Callable[[int, CashFutureAcquisitionChunk], None] | None = None,
) -> CashFutureAcquisitionResult:
    """Acquire one Cash-Future contract with durable restart-safe chunks."""
    symbol = str(symbol).strip().upper()
    contract_month = str(contract_month).strip()
    if not symbol or not contract_month:
        raise ValueError("symbol and contract_month are required")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    chunks = build_cash_future_acquisition_chunks(start=start, end=end, chunk_days=chunk_days)
    metadata = _metadata(chunks, symbol=symbol, contract_month=contract_month)
    fingerprint = job_store.fingerprint(metadata)
    try:
        job = job_store.get(job_id)
        if job.run_id != run_id or job.plan_fingerprint != fingerprint or job.total_chunks != len(chunks):
            raise ValueError("existing Cash-Future job does not match run or plan")
    except KeyError:
        job_store.create(
            job_id=job_id,
            run_id=run_id,
            plan_fingerprint=fingerprint,
            total_chunks=len(chunks),
            plan_metadata=metadata,
        )

    job_store.recover_running_chunks(job_id)
    processed_points = 0

    for chunk in chunks:
        state = job_store.chunk_state(job_id, chunk.index)[0]
        if state in {"completed", "skipped"}:
            continue
        job_store.start_chunk(job_id, chunk.index)
        if on_chunk is not None:
            on_chunk(chunk.index, chunk)
        try:
            batch: list[CashFutureHistoryPoint] = []
            for point in source.fetch(
                symbol=symbol,
                contract_month=contract_month,
                start=chunk.start,
                end=chunk.end,
            ):
                if point.symbol.upper() != symbol or point.contract_month != contract_month:
                    raise ValueError("provider returned a point outside the requested Cash-Future identity")
                timestamp = point.timestamp
                if timestamp.tzinfo is None:
                    raise ValueError("provider returned a naive Cash-Future timestamp")
                if not (chunk.start <= timestamp <= chunk.end):
                    raise ValueError("provider returned a point outside the requested chunk")
                batch.append(point)
                if len(batch) >= batch_size:
                    processed_points += save_history_points(db, batch)
                    batch.clear()
            if batch:
                processed_points += save_history_points(db, batch)
            job_store.complete_chunk(job_id, chunk.index)
        except Exception as exc:
            job_store.fail_chunk(job_id, chunk.index, str(exc), recoverable=True)
            break

    job = job_store.finish(job_id)
    return CashFutureAcquisitionResult(
        job_id=job.job_id,
        run_id=job.run_id,
        total_chunks=job.total_chunks,
        completed_chunks=job.completed_chunks,
        skipped_chunks=job.skipped_chunks,
        failed_chunk=job.failed_chunk,
        processed_points=processed_points,
        state=job.state,
    )


__all__ = [
    "CashFutureAcquisitionChunk",
    "CashFutureAcquisitionResult",
    "CashFutureHistoricalSource",
    "acquire_cash_future_history_durable",
    "build_cash_future_acquisition_chunks",
]
