from __future__ import annotations

import pytest

from app.backtesting.checkpoint import CheckpointStore, ReplayCheckpoint
from app.backtesting.result_ledger import (
    BacktestEvent,
    BacktestFill,
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


def fill(fill_id: str = "f1", sequence: int = 1, price: float = 101.0) -> BacktestFill:
    return BacktestFill(
        fill_id=fill_id,
        order_id="order-1",
        sequence=sequence,
        timestamp_ns=2_000 + sequence,
        instrument="NIFTY26SEP25000CE",
        side="BUY",
        quantity=2,
        price=price,
        fee=1.5,
        metadata={"leg": "CALL_LONG"},
    )


def test_claim_run_is_atomic_and_only_created_run_can_be_claimed() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("claim-run", {"strategy_id": "universal"})

    assert ledger.claim_run("claim-run") is True
    assert ledger.run("claim-run")["status"] == "RUNNING"
    assert ledger.claim_run("claim-run") is False
    assert ledger.run("claim-run")["status"] == "RUNNING"



def test_claim_run_allows_only_one_of_two_ledger_connections(tmp_path) -> None:
    path = tmp_path / "claim-race.db"
    owner = BacktestResultLedger(str(path))
    contender = BacktestResultLedger(str(path))
    try:
        owner.create_run("claim-race", {"strategy_id": "universal"})
        assert owner.claim_run("claim-race") is True
        assert contender.claim_run("claim-race") is False
        assert owner.run("claim-race")["status"] == "RUNNING"
        assert contender.run("claim-race")["status"] == "RUNNING"
    finally:
        contender.close()
        owner.close()

def test_incremental_fill_append_and_idempotency() -> None:
    ledger = BacktestResultLedger()
    ledger.create_run("run-fills", {"strategy_id": "multi_leg"})

    assert ledger.append_fills("run-fills", [fill()]) == 1
    assert ledger.append_fills("run-fills", [fill()]) == 0
    rows = ledger.fills("run-fills")
    assert len(rows) == 1