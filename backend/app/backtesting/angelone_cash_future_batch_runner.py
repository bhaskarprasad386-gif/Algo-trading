"""Durable sequential execution for bounded Angel One Cash-Future stock batches."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Iterable, Mapping

from .angelone_cash_future_runner import (
    AngelOneCashFutureRunConfig,
    iter_cash_future_stock_batches,
    run_angelone_cash_future_history,
)
from .cash_future_universe import CashFutureFnoUniverse
from .historical_job_store import HistoricalJobStore


@dataclass(frozen=True)
class AngelOneCashFutureBatchResult:
    batch_index: int
    stock_underlyings: tuple[str, ...]
    skipped: bool
    result: object | None


def _batch_job_id(run_id: str, batch_index: int) -> str:
    return f"{run_id}:cash-future-stock-batch:{batch_index}"


def _batch_fingerprint(
    *,
    stock_underlyings: tuple[str, ...],
    indices: tuple[str, ...],
    start: datetime,
    end: datetime,
    batch_size: int,
) -> str:
    return HistoricalJobStore.fingerprint((
        {
            "stock_underlyings": stock_underlyings,
            "indices": indices,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "batch_size": batch_size,
        },
    ))


def run_angelone_cash_future_history_in_batches(
    *,
    ingestion,
    contract_master,
    universe: CashFutureFnoUniverse,
    master_rows: Iterable[Mapping[str, object]],
    start: datetime,
    end: datetime,
    spot_sessions_by_underlying,
    db,
    catalog,
    config: AngelOneCashFutureRunConfig,
    batch_size: int = 10,
    job_store: HistoricalJobStore,
    run_id: str,
    future_sessions_by_instrument=None,
    auth=None,
    limiter=None,
    retry_policy=None,
    on_progress: Callable[[str, object], None] | None = None,
    coverage_store=None,
    margin_required: float = 0.0,
    materialize_batch_size: int = 1000,
) -> tuple[AngelOneCashFutureBatchResult, ...]:
    """Run stock batches in order and resume completed batches after restart."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if not run_id.strip():
        raise ValueError("run_id is required")

    # Materialize once because master_rows may be a one-shot generator. Every
    # batch must receive the same contract-master universe for deterministic
    # offsets and resume-safe execution.
    master_rows = tuple(master_rows)
    batches = tuple(iter_cash_future_stock_batches(universe, batch_size=batch_size))
    results: list[AngelOneCashFutureBatchResult] = []

    for batch_index, batch in enumerate(batches):
        underlyings = tuple(batch.stock_underlyings)
        job_id = _batch_job_id(run_id, batch_index)
        fingerprint = _batch_fingerprint(
            stock_underlyings=underlyings,
            indices=tuple(item.underlying for item in batch.indices),
            start=start,
            end=end,
            batch_size=batch_size,
        )

        try:
            job = job_store.get(job_id)
        except KeyError:
            job_store.create(
                job_id=job_id,
                run_id=run_id,
                plan_fingerprint=fingerprint,
                total_chunks=1,
                plan_metadata=(
                    {
                        "batch_index": batch_index,
                        "stock_underlyings": underlyings,
                        "indices": tuple(item.underlying for item in batch.indices),
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "batch_size": batch_size,
                    },
                ),
            )
            job = job_store.get(job_id)

        if job.plan_fingerprint != fingerprint:
            raise ValueError(f"batch plan changed for durable job {job_id}")

        if job.state == "completed":
            results.append(
                AngelOneCashFutureBatchResult(
                    batch_index=batch_index,
                    stock_underlyings=underlyings,
                    skipped=True,
                    result=None,
                )
            )
            continue

        job_store.recover_running_chunks(job_id)
        job_store.start_chunk(job_id, 0)
        try:
            batch_config = replace(
                config,
                max_stock_underlyings=batch_size,
                stock_batch_offset=batch_index * batch_size,
            )
            result = run_angelone_cash_future_history(
                ingestion=ingestion,
                contract_master=contract_master,
                universe=universe,
                master_rows=master_rows,
                start=start,
                end=end,
                spot_sessions_by_underlying=spot_sessions_by_underlying,
                db=db,
                catalog=catalog,
                future_sessions_by_instrument=future_sessions_by_instrument,
                job_store=job_store,
                run_id=f"{run_id}:batch:{batch_index}",
                config=batch_config,
                auth=auth,
                limiter=limiter,
                retry_policy=retry_policy,
                on_progress=on_progress,
                coverage_store=coverage_store,
                margin_required=margin_required,
                batch_size=materialize_batch_size,
            )
            result.require_backtest_ready()
            job_store.complete_chunk(job_id, 0)
            job_store.finish(job_id)
            results.append(
                AngelOneCashFutureBatchResult(
                    batch_index=batch_index,
                    stock_underlyings=underlyings,
                    skipped=False,
                    result=result,
                )
            )
        except Exception as exc:
            job_store.fail_chunk(job_id, 0, str(exc), recoverable=True)
            job_store.finish(job_id)
            raise

    return tuple(results)


__all__ = ["AngelOneCashFutureBatchResult", "run_angelone_cash_future_history_in_batches"]
