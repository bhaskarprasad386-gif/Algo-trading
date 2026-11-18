from __future__ import annotations

import pytest

from app.backtesting.canonical_summary import CanonicalRunSummary
from app.backtesting.result_ledger import BacktestEvent, BacktestResultLedger, EquityPoint


def test_canonical_summary_reconstructs_marked_equity_statistics_in_pages() -> None:
    ledger = BacktestResultLedger(":memory:")
    ledger.create_run("run-canonical", {"strategy_id": "universal"})
    ledger.append_events(
        "run-canonical",
        [BacktestEvent(i, i * 1_000, "EVENT", {}) for i in range(4)],
    )
    ledger.append_equity(
        "run-canonical",
        [
            EquityPoint(0, 100_000.0, 0.0, 0.0, 0.0),
            EquityPoint(1_000, 100_100.0, 100.0, 0.0, 0.0),
            EquityPoint(2_000, 99_900.0, 50.0, -150.0, 0.0),
            EquityPoint(3_000, 100_300.0, 450.0, -150.0, 0.0),
        ],
    )
    ledger.set_status("run-canonical", "COMPLETED")

    summary = CanonicalRunSummary.from_completed_run(
        ledger, "run-canonical", initial_capital=100_000.0, equity_page_size=2
    )

    assert summary.status == "COMPLETED"
    assert summary.provenance["strategy_id"] == "universal"
    assert summary.initial_capital == pytest.approx(100_000.0)
    assert summary.final_equity == pytest.approx(100_300.0)
    assert summary.realized_pnl == pytest.approx(450.0)
    assert summary.unrealized_pnl == pytest.approx(-150.0)
    assert summary.net_pnl == pytest.approx(300.0)
    assert summary.total_return == pytest.approx(0.003)
    assert summary.event_count == 4
    assert summary.equity_count == 4


def test_canonical_summary_does_not_use_trade_net_pnl(monkeypatch) -> None:
    ledger = BacktestResultLedger(":memory:")
    ledger.create_run("run-accounting-boundary", {"strategy_id": "universal"})
    ledger.append_equity(
        "run-accounting-boundary",
        [
            EquityPoint(0, 100_000.0, 0.0, 0.0, 0.0),
            EquityPoint(1_000_000_000, 100_050.0, 50.0, 0.0, 0.0),
        ],
    )
    ledger.set_status("run-accounting-boundary", "COMPLETED")

    def fail_trade_sum(*args, **kwargs):
        raise AssertionError("canonical net P&L must not use trade_net_pnl")

    monkeypatch.setattr(ledger, "trade_net_pnl", fail_trade_sum)
    summary = CanonicalRunSummary.from_completed_run(
        ledger, "run-accounting-boundary", initial_capital=100_000.0, equity_page_size=1
    )

    assert summary.net_pnl == pytest.approx(50.0)


def test_canonical_summary_requires_completed_run() -> None:
    ledger = BacktestResultLedger(":memory:")
    ledger.create_run("run-open", {"strategy_id": "universal"})

    with pytest.raises(ValueError, match="COMPLETED"):
        CanonicalRunSummary.from_completed_run(
            ledger, "run-open", initial_capital=100_000.0
        )


def test_canonical_summary_empty_completed_run_uses_initial_capital() -> None:
    ledger = BacktestResultLedger(":memory:")
    ledger.create_run("run-empty", {"strategy_id": "universal"})
    ledger.set_status("run-empty", "COMPLETED")

    summary = CanonicalRunSummary.from_completed_run(
        ledger, "run-empty", initial_capital=100_000.0
    )

    assert summary.final_equity == pytest.approx(100_000.0)
    assert summary.net_pnl == pytest.approx(0.0)
    assert summary.equity_count == 0


def test_canonical_summary_reads_initial_capital_from_persisted_provenance() -> None:
    ledger = BacktestResultLedger(":memory:")
    ledger.create_run(
        "run-persisted-capital",
        {"strategy_id": "universal", "initial_capital": 125_000.0},
    )
    ledger.append_equity(
        "run-persisted-capital",
        [
            EquityPoint(0, 125_000.0, 0.0, 0.0, 0.0),
            EquityPoint(1_000, 125_250.0, 250.0, 0.0, 0.0),
        ],
    )
    ledger.set_status("run-persisted-capital", "COMPLETED")

    summary = CanonicalRunSummary.from_completed_run(ledger, "run-persisted-capital")

    assert summary.initial_capital == pytest.approx(125_000.0)
    assert summary.final_equity == pytest.approx(125_250.0)


def test_canonical_summary_rejects_capital_mismatch_with_persisted_provenance() -> None:
    ledger = BacktestResultLedger(":memory:")
    ledger.create_run(
        "run-capital-mismatch",
        {"strategy_id": "universal", "initial_capital": 125_000.0},
    )
    ledger.set_status("run-capital-mismatch", "COMPLETED")

    with pytest.raises(ValueError, match="does not match persisted"):
        CanonicalRunSummary.from_completed_run(
            ledger,
            "run-capital-mismatch",
            initial_capital=100_000.0,
        )
