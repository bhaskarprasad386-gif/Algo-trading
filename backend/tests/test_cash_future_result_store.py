from __future__ import annotations

import sqlite3

import pytest

from app.backtesting.cash_future_pnl import CashFutureTrade
from app.backtesting.cash_future_replay import CashFutureBothReplayTrade, CashFutureReplayTrade
from app.backtesting.cash_future_result_store import CashFutureResultStore


def _trade(future: str = "FUT"):
    result = CashFutureTrade(
        spot_entry=100.0,
        spot_exit=103.0,
        future_entry=105.0,
        future_exit=101.0,
        quantity=1,
        lot_size=50,
    )
    return CashFutureReplayTrade(1, 2, future, 50, 1, result)


def test_single_leg_mode_is_preserved_and_duplicate_is_ignored():
    conn = sqlite3.connect(":memory:")
    store = CashFutureResultStore(conn)
    trade = _trade()

    assert store.append("run", trade, mode="NEAR") is True
    assert store.append("run", trade, mode="NEAR") is False
    assert store.summary("run").trade_count == 1
    row = conn.execute("SELECT mode, future_instrument FROM cash_future_results").fetchone()
    assert row == ("NEAR", "FUT")


def test_both_trade_is_stored_as_combined_result():
    conn = sqlite3.connect(":memory:")
    store = CashFutureResultStore(conn)
    current = _trade("CURRENT")
    near = _trade("NEAR")
    trade = CashFutureBothReplayTrade(
        1, 2, current.future_instrument, near.future_instrument, 50, 1,
        current.result, near.result,
    )

    assert store.append("run", trade, mode="BOTH") is True
    summary = store.summary("run")
    assert summary.trade_count == 1
    assert summary.gross_pnl == pytest.approx(700.0)


def test_append_many_is_chunked_and_idempotent():
    conn = sqlite3.connect(":memory:")
    store = CashFutureResultStore(conn)
    trades = (_trade(f"F{i}") for i in range(5))

    assert store.append_many("run", trades, mode="CURRENT", chunk_size=2) == 5
    assert store.append_many("run", (_trade(f"F{i}") for i in range(5)), mode="CURRENT", chunk_size=2) == 0
    assert store.count("run") == 5


def test_invalid_mode_combination_fails_closed():
    conn = sqlite3.connect(":memory:")
    store = CashFutureResultStore(conn)
    with pytest.raises(ValueError, match="single-leg trade cannot be stored as BOTH"):
        store.append("run", _trade(), mode="BOTH")
    with pytest.raises(ValueError, match="BOTH trade must be stored with mode BOTH"):
        store.append("run", CashFutureBothReplayTrade(
            1, 2, "CURRENT", "NEAR", 50, 1, _trade("C").result, _trade("N").result
        ), mode="CURRENT")
