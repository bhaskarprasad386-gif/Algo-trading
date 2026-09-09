from __future__ import annotations

import pytest

from app.backtesting.result_ledger import (
    BacktestEvent,
    BacktestResultLedger,
    BacktestTrade,
    EquityPoint,
)


def trade(trade_id: str = "t1", sequence: int = 1, net: float = 95.0) -> BacktestTrade:
    return BacktestTrade(
        trade_id=trade_id,
        sequence=sequence,
        timestamp_ns=1_000 + sequence,
        instrument="NIFTY",
        side="BUY",
        quantity=1,
        entry_price=100.0,
        exit_price=200.0,
        gross_pnl=100.0,
        fees=3.0,
        slippage=2.0,
        net_pnl=net,
        contract="NIFTY26SEP25000CE",
        expiry="2026-09-24",
        strike=25000,
        leg="CALL_LONG",
        data_resolution="s",
        metadata={"strategy": "box"},
    )


def test_incremental_append_and_idempotency() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-a", {"strategy_id": "box", "resolution": "s"})

    assert ledger.append_events("run-a", [BacktestEvent(1, 1000, "SIGNAL", {"side": "BUY"})]) == 1
    assert ledger.append_trades("run-a", [trade()]) == 1
    assert ledger.append_equity("run-a", [EquityPoint(1000, 100095, 95, 0, 0)]) == 1

    assert ledger.append_events("run-a", [BacktestEvent(1, 1000, "SIGNAL", {"side": "BUY"})]) == 0
    assert ledger.append_trades("run-a", [trade()]) == 0
    assert ledger.append_equity("run-a", [EquityPoint(1000, 100095, 95, 0, 0)]) == 0

    assert len(ledger.events("run-a")) == 1
    assert len(ledger.trades("run-a")) == 1
    assert len(ledger.equity("run-a")) == 1


def test_conflicting_duplicates_are_rejected() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-a", {"strategy_id": "synthetic_cash_carry"})
    ledger.append_trades("run-a", [trade()])
    with pytest.raises(ValueError, match="conflicting duplicate"):
        ledger.append_trades("run-a", [trade(net=94.0)])

    ledger.append_events("run-a", [BacktestEvent(2, 1002, "ENTRY", {"price": 10})])
    with pytest.raises(ValueError, match="conflicting duplicate"):
        ledger.append_events("run-a", [BacktestEvent(2, 1002, "ENTRY", {"price": 11})])


def test_runs_are_isolated_and_cursor_is_incremental() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-a", {"strategy_id": "strategy_a"})
    ledger.create_run("run-b", {"strategy_id": "strategy_b"})
    ledger.append_trades("run-a", [trade("a1", 1), trade("a2", 2)])
    ledger.append_trades("run-b", [trade("b1", 1)])

    page = ledger.trades("run-a", limit=1)
    assert [row["trade_id"] for row in page] == ["a1"]
    next_page = ledger.trades("run-a", after_sequence=page[-1]["sequence"])
    assert [row["trade_id"] for row in next_page] == ["a2"]
    assert [row["trade_id"] for row in ledger.trades("run-b")] == ["b1"]


def test_unknown_run_is_rejected() -> None:
    ledger = BacktestResultLedger()
    with pytest.raises(ValueError, match="unknown run"):
        ledger.trades("missing")
