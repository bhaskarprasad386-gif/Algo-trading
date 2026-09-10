"""Durable, resumable backtest execution primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

from .backtest_job_store import BacktestJobStore
from .backtest_trade_ledger import BacktestTradeLedger
from .engine import BacktestTrade


class BacktestChunkRunner(Protocol):
    def __call__(
        self, events: Sequence[object]
    ) -> Iterable[tuple[str, BacktestTrade]]: ...


@dataclass(frozen=True)
class BacktestCheckpoint:
    job_id: str
    chunk_index: int
    processed_events: int


class ResumableBacktestRunner:
    """Run a backtest in bounded chunks and persist completed trades.

    The legacy ``run`` API keeps its explicit ``start_chunk`` behavior. The
    ``run_durable`` API stores chunk checkpoints in ``BacktestJobStore`` so a
    restart can recover without manually supplying a checkpoint.

    Chunk execution is deliberately isolated from strategy state. Stateful
    strategies must either deterministically reconstruct state before the
    resumed chunk or provide their own strategy-state checkpointing layer.
    """

    def __init__(self, ledger: BacktestTradeLedger, chunk_size: int = 500) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.ledger = ledger
        self.chunk_size = chunk_size

    def run(
        self,
        job_id: str,
        events: Iterable[object],
        execute_chunk: BacktestChunkRunner,
        *,
        start_chunk: int = 0,
    ) -> BacktestCheckpoint:
        if not job_id.strip():
            raise ValueError("job_id must not be empty")
        if start_chunk < 0:
            raise ValueError("start_chunk must be non-negative")

        skip_events = start_chunk * self.chunk_size
        iterator = iter(events)
        skipped = 0
        while skipped < skip_events:
            try:
                next(iterator)
            except StopIteration:
                return BacktestCheckpoint(job_id, start_chunk, skipped)
            skipped += 1

        chunk: list[object] = []
        chunk_index = start_chunk
        processed = skipped

        for event in iterator:
            chunk.append(event)
            if len(chunk) < self.chunk_size:
                continue
            self._persist_chunk(job_id, execute_chunk(chunk))
            processed += len(chunk)
            chunk.clear()
            chunk_index += 1

        if chunk:
            self._persist_chunk(job_id, execute_chunk(chunk))
            processed += len(chunk)
            chunk_index += 1

        return BacktestCheckpoint(job_id, chunk_index, processed)

    def run_durable(
        self,
        job_id: str,
        run_id: str,
        events: Iterable[object],
        execute_chunk: BacktestChunkRunner,
        *,
        job_store: BacktestJobStore,
        total_chunks: int,
        plan_parts: Iterable[object],
    ) -> BacktestCheckpoint:
        """Resume a chunked backtest from durable job/chunk checkpoints.

        ``plan_parts`` must identify the immutable data/strategy plan without
        requiring the full event stream to be materialized. The ledger is
        committed before the chunk checkpoint, making a crash between those
        operations safely replayable because the trade ledger is idempotent.
        """
        if not job_id.strip() or not run_id.strip():
            raise ValueError("job_id and run_id must not be empty")
        if total_chunks < 0:
            raise ValueError("total_chunks must be non-negative")

        plan_fingerprint = job_store.fingerprint(
            (self.chunk_size, *tuple(plan_parts))
        )
        existing = job_store.get(job_id)
        if existing is None:
            job_store.create(job_id, run_id, plan_fingerprint, total_chunks)
        else:
            if existing[1] != run_id:
                raise ValueError("run_id does not match existing backtest job")
            if existing[2] != plan_fingerprint:
                raise ValueError("plan fingerprint does not match existing backtest job")
            if int(existing[4]) != total_chunks:
                raise ValueError("total_chunks does not match existing backtest job")

        job_store.recover_running_chunks(job_id)
        pending = set(job_store.pending_indices(job_id))
        if not pending:
            job_store.finish(job_id)
            return BacktestCheckpoint(job_id, total_chunks, int(job_store.get(job_id)[7]))

        iterator = iter(events)
        processed_events = 0
        chunk_index = 0

        while chunk_index < total_chunks:
            chunk: list[object] = []
            while len(chunk) < self.chunk_size:
                try:
                    chunk.append(next(iterator))
                except StopIteration:
                    break
            if not chunk:
                break

            current_index = chunk_index
            if current_index in pending:
                job_store.start_chunk(job_id, current_index)
                try:
                    trades = execute_chunk(tuple(chunk))
                    self._persist_chunk(job_id, trades)
                    job_store.complete_chunk(job_id, current_index, len(chunk))
                except Exception as exc:
                    job_store.fail_chunk(job_id, current_index, str(exc))
                    raise

            processed_events += len(chunk)
            chunk_index += 1

        job_store.finish(job_id)
        row = job_store.get(job_id)
        durable_processed = int(row[7]) if row is not None else processed_events
        return BacktestCheckpoint(job_id, chunk_index, durable_processed)

    def _persist_chunk(
        self, job_id: str, trades: Iterable[tuple[str, BacktestTrade]]
    ) -> None:
        rows = tuple(trades)
        trade_ids = tuple(trade_id for trade_id, _ in rows)
        trade_rows = tuple(trade for _, trade in rows)
        self.ledger.append_chunk(job_id, trade_ids, trade_rows)
