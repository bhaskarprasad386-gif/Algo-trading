"""Durable ledger adapter for event-driven execution results."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from app.backtesting.event_execution import EventExecutionResult
from app.backtesting.ledger import BacktestLedger, LedgerRecord


class EventLedgerWriter:
    """Append one event execution at a time; no event history is retained in RAM."""

    def __init__(self, ledger: BacktestLedger) -> None:
        self.ledger = ledger

    def append(self, run_id: str, result: EventExecutionResult, timestamp_ns: int) -> None:
        if result.rejected:
            return
        self.ledger.append(
            LedgerRecord(
                run_id=run_id,
                record_type="event_execution",
                timestamp_ns=timestamp_ns,
                payload={
                    "action": result.signal.action,
                    "quantity": result.signal.quantity,
                    "reason": result.signal.reason,
                    "fills": [asdict(fill) for fill in result.fills],
                },
            )
        )

    def append_many(self, run_id: str, results: Iterable[EventExecutionResult]) -> int:
        records = []
        for result in results:
            if result.rejected:
                continue
            timestamp_ns = result.fills[0].filled_at_ns if result.fills else 0
            records.append(
                LedgerRecord(
                    run_id=run_id,
                    record_type="event_execution",
                    timestamp_ns=timestamp_ns,
                    payload={
                        "action": result.signal.action,
                        "quantity": result.signal.quantity,
                        "reason": result.signal.reason,
                        "fills": [asdict(fill) for fill in result.fills],
                    },
                )
            )
        return self.ledger.append_batch(records)
