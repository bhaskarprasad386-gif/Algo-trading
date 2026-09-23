from __future__ import annotations

import pytest

from app.backtesting.result_ledger import BacktestEvent, BacktestResultLedger, EquityPoint
from app.backtesting.universal_result_service import (
    UniversalResultService,
    UniversalRunNotFoundError,
    UniversalRunNotCompletedError,
)


def _ledger() -> BacktestResultLedger:
    return BacktestResultLedger(":memory:")


def test_summary_requires_completed_run() -> None:
    ledger = _ledger()
    ledger.create_run("run-open", {"initial_capital": 100_000.0})
    service = UniversalResultService(ledger)

    with pytest.raises(UniversalRunNotCompletedError):
        service.summary("run-open")


def test_summary_unknown_run_is_not_found() -> None:
    ledger = _ledger()
    service = UniversalResultService(ledger)

    with pytest.raises(UniversalRunNotFoundError):
        service.summary("missing")


def test_events_use_sequence_pagination_and_bounded_envelope() -> None:
    ledger = _ledger()
    ledger.create_run("run-events", {"initial_capital": 100_000.0})
    ledger.append_events(
        "run-events",
        [BacktestEvent(i, i * 1_000, "EVENT", {"i": i}) for i in range(3)],
    )
    service = UniversalResultService(ledger)

    first = service.events("run-events", limit=2)
    assert first["run_id"] == "run-events"
    assert first["record_type"] == "events"
    assert first["count"] == 2
    assert [row["sequence"] for row in first["data"]] == [0, 1]
    assert first["next_cursor"] == 1

    second = service.events("run-events", limit=2, after_sequence=first["next_cursor"])
    assert [row["sequence"] for row in second["data"]] == [2]
    assert second["count"] == 1
    assert second["next_cursor"] is None


def test_equity_requires_composite_cursor() -> None:
    ledger = _ledger()
    ledger.create_run("run-equity", {"initial_capital": 100_000.0})
    ledger.append_equity(
        "run-equity",
        [
            EquityPoint(1_000, 100_000.0, 0.0, 0.0, 0.0),
            EquityPoint(1_000, 100_010.0, 10.0, 0.0, 0.0),
            EquityPoint(2_000, 100_020.0, 20.0, 0.0, 0.0),
        ],
    )
    service = UniversalResultService(ledger)

    first = service.equity("run-equity", limit=1)
    assert first["count"] == 1
    assert first["next_cursor"]["timestamp_ns"] == 1_000

    cursor = first["next_cursor"]
    second = service.equity(
        "run-equity",
        limit=2,
        after_timestamp_ns=cursor["timestamp_ns"],
        after_equity_id=cursor["equity_id"],
    )
    assert len(second["data"]) == 2
    assert second["data"][0]["timestamp_ns"] == 1_000
    assert second["data"][0]["equity_id"] > cursor["equity_id"]

    with pytest.raises(ValueError, match="supplied together"):
        service.equity("run-equity", after_timestamp_ns=1_000)


def test_invalid_pagination_limits_and_sequence_cursor_are_rejected() -> None:
    ledger = _ledger()
    ledger.create_run("run-validation", {"initial_capital": 100_000.0})
    service = UniversalResultService(ledger)

    with pytest.raises(ValueError, match="positive integer"):
        service.events("run-validation", limit=0)
    with pytest.raises(ValueError, match=">= -1"):
        service.events("run-validation", after_sequence=-2)
    with pytest.raises(ValueError, match="positive integer"):
        service.equity("run-validation", limit=0)
