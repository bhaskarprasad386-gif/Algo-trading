"""Durable, resumable backtest execution primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

from .backtest_trade_ledger import BacktestTradeLedger


class BacktestChunkRunner(Protocol):
    def __call__(self, events: Sequence[object]) -> Iterable[object]: ...


@dataclass(frozen=True)
class BacktestCheckpoint:
    job_id: str
    chunk_index: int
    processed_events: int


class ResumableBacktestRunner:
    """Run a backtest in bounded chunks and persist completed trades."""

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
        if not job_id:
            raise ValueError("job_id must not be empty")
        if start_chunk < 0:
            raise ValueError("start_chunk must be non-negative")

        chunk: list[object] = []
        chunk_index = start_chunk
        processed = start_chunk * self.chunk_size

        for event in events:
            chunk.append(event)
            if len(chunk) < self.chunk_size:
                continue
            self._persist_chunk(job_id, chunk_index, execute_chunk(chunk))
            processed += len(chunk)
            chunk.clear()
            chunk_index += 1

        if chunk:
            self._persist_chunk(job_id, chunk_index, execute_chunk(chunk))
            processed += len(chunk)
            chunk_index += 1

        return BacktestCheckpoint(job_id, chunk_index, processed)

    def _persist_chunk(
        self, job_id: str, chunk_index: int, trades: Iterable[object]
    ) -> None:
        rows = tuple(trades)
        self.ledger.append_trades(job_id, rows)
