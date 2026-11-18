"""Lazy result views backed by the durable backtest ledger."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from app.backtesting.ledger import BacktestLedger


class LedgerSequence(Sequence[dict[str, Any]]):
    """Read ledger payloads lazily while preserving sequence-style access."""

    def __init__(self, ledger: BacktestLedger, run_id: str, record_type: str) -> None:
        if not run_id.strip() or not record_type.strip():
            raise ValueError("run_id and record_type are required")
        self._ledger = ledger
        self._run_id = run_id
        self._record_type = record_type

    def __len__(self) -> int:
        return self._ledger.record_count(self._run_id, self._record_type)

    def __getitem__(self, index: int | slice) -> dict[str, Any] | tuple[dict[str, Any], ...]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return tuple(self[position] for position in range(start, stop, step))
        if not isinstance(index, int):
            raise TypeError("ledger sequence index must be an integer or slice")
        return dict(self._ledger.record_at(self._run_id, self._record_type, index).payload)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for record in self._ledger.iter_records(self._run_id, self._record_type):
            yield dict(record.payload)


__all__ = ["LedgerSequence"]
