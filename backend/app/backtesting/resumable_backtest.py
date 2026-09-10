"""Durable, resumable backtest execution primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

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

    ``start_chunk`` means that all earlier full chunks are already durable. The
    runner consumes and skips those events before executing the remaining chunks,
    so a restart does not execute completed chunks again.
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

    def _persist_chunk(
        self, job_id: str, trades: Iterable[tuple[str, BacktestTrade]]
    ) -> None:
        rows = tuple(trades)
        trade_ids = tuple(trade_id for trade_id, _ in rows)
        trade_rows = tuple(trade for _, trade in rows)
        self.ledger.append_chunk(job_id, trade_ids, trade_rows)
