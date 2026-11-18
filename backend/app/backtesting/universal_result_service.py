"""Read-only service boundary for Universal backtest results."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .canonical_summary import CanonicalRunSummary
from .result_ledger import BacktestResultLedger


class UniversalRunNotFoundError(ValueError):
    """Raised when a requested Universal run does not exist."""


class UniversalRunNotCompletedError(ValueError):
    """Raised when a canonical summary is requested before completion."""


class UniversalResultService:
    """Bounded read service over the durable Universal result ledger."""

    def __init__(self, ledger: BacktestResultLedger) -> None:
        self.ledger = ledger

    def summary(self, run_id: str) -> dict[str, Any]:
        self._require_run(run_id)
        try:
            summary = CanonicalRunSummary.from_completed_run(self.ledger, run_id)
        except ValueError as exc:
            if "COMPLETED" in str(exc):
                raise UniversalRunNotCompletedError(str(exc)) from exc
            raise
        return asdict(summary)

    def events(self, run_id: str, *, limit: int = 100, after_sequence: int = -1) -> dict[str, Any]:
        self._validate_sequence_cursor(after_sequence)
        rows = self._page(self.ledger.events, run_id, limit, after_sequence)
        return self._sequence_page(run_id, "events", rows, limit)

    def fills(self, run_id: str, *, limit: int = 100, after_sequence: int = -1) -> dict[str, Any]:
        self._validate_sequence_cursor(after_sequence)
        rows = self._page(self.ledger.fills, run_id, limit, after_sequence)
        return self._sequence_page(run_id, "fills", rows, limit)

    def trades(self, run_id: str, *, limit: int = 100, after_sequence: int = -1) -> dict[str, Any]:
        self._validate_sequence_cursor(after_sequence)
        rows = self._page(self.ledger.trades, run_id, limit, after_sequence)
        return self._sequence_page(run_id, "trades", rows, limit)

    def equity(
        self,
        run_id: str,
        *,
        limit: int = 100,
        after_timestamp_ns: int = -1,
        after_equity_id: int = -1,
    ) -> dict[str, Any]:
        self._validate_limit(limit)
        if (after_timestamp_ns == -1) != (after_equity_id == -1):
            raise ValueError(
                "after_timestamp_ns and after_equity_id must be supplied together"
            )
        self._require_run(run_id)
        rows = self.ledger.equity(
            run_id,
            limit=limit + 1,
            after_timestamp_ns=after_timestamp_ns,
            after_equity_id=after_equity_id,
        )
        has_more = len(rows) > limit
        data_rows = rows[:limit]
        data = [dict(row) for row in data_rows]
        next_cursor = None
        if has_more and data_rows:
            last = data_rows[-1]
            next_cursor = {
                "timestamp_ns": int(last["timestamp_ns"]),
                "equity_id": int(last["equity_id"]),
            }
        return {
            "run_id": run_id,
            "record_type": "equity",
            "data": data,
            "count": len(data),
            "next_cursor": next_cursor,
        }

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")

    @staticmethod
    def _validate_sequence_cursor(after_sequence: int) -> None:
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int):
            raise ValueError("after_sequence must be an integer")
        if after_sequence < -1:
            raise ValueError("after_sequence must be >= -1")

    def _require_run(self, run_id: str) -> None:
        try:
            row = self.ledger.run(run_id)
        except ValueError as exc:
            raise UniversalRunNotFoundError(f"run not found: {run_id}") from exc
        if row is None:
            raise UniversalRunNotFoundError(f"run not found: {run_id}")

    def _page(self, method, run_id: str, limit: int, after_sequence: int):
        self._validate_limit(limit)
        self._require_run(run_id)
        return method(run_id, limit=limit + 1, after_sequence=after_sequence)

    @staticmethod
    def _sequence_page(run_id: str, record_type: str, rows: list[Any], limit: int) -> dict[str, Any]:
        has_more = len(rows) > limit
        data_rows = rows[:limit]
        data = [dict(row) for row in data_rows]
        next_cursor = int(data_rows[-1]["sequence"]) if has_more and data_rows else None
        return {
            "run_id": run_id,
            "record_type": record_type,
            "data": data,
            "count": len(data),
            "next_cursor": next_cursor,
        }
