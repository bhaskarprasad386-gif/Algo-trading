from __future__ import annotations

import pytest

from app.backtesting.ledger import BacktestLedger, LedgerRecord
from app.backtesting.result_store import LedgerSequence


def _seed_ledger() -> tuple[BacktestLedger, LedgerSequence]:
    ledger = BacktestLedger()
    ledger.start_run("result-seq", "strategy", "1", 100_000.0)
    for index, equity in enumerate((100_010.0, 99_990.0, 100_025.0)):
        ledger.append(
            LedgerRecord(
                "result-seq",
                "equity",
                index + 1,
                {"equity": equity},
            )
        )
    return ledger, LedgerSequence(ledger, "result-seq", "equity")


def test_ledger_sequence_supports_length_index_negative_index_iteration_and_slice():
    ledger, sequence = _seed_ledger()
    try:
        assert len(sequence) == 3
        assert sequence[0]["equity"] == 100_010.0
        assert sequence[-1]["equity"] == 100_025.0
        assert [item["equity"] for item in sequence] == [100_010.0, 99_990.0, 100_025.0]
        assert [item["equity"] for item in sequence[1:]] == [99_990.0, 100_025.0]
    finally:
        ledger.close()


def test_ledger_sequence_empty_negative_index_raises_index_error():
    ledger = BacktestLedger()
    ledger.start_run("empty-seq", "strategy", "1", 100_000.0)
    sequence = LedgerSequence(ledger, "empty-seq", "equity")
    try:
        assert len(sequence) == 0
        with pytest.raises(IndexError):
            _ = sequence[-1]
    finally:
        ledger.close()


def test_ledger_sequence_reads_through_ledger_without_materializing_records():
    ledger, sequence = _seed_ledger()
    try:
        assert sequence[0]["equity"] == 100_010.0
        assert ledger.record_count("result-seq", "equity") == 3
    finally:
        ledger.close()
