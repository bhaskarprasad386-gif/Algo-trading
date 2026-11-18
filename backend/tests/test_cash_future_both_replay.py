import pytest

from app.backtesting.cash_future_replay import (
    CashFutureContractLock,
    CashFutureMultiLegBar,
    CashFutureReplayRunner,
)


def test_both_mode_replays_current_and_near_at_same_timestamps():
    lock = CashFutureContractLock(
        mode="BOTH", contracts={"CURRENT": "ABC26SEP", "NEAR": "ABC26OCT"}
    )
    bars = [
        CashFutureMultiLegBar(100, 100.0, {"ABC26SEP": 101.0, "ABC26OCT": 102.0}),
        CashFutureMultiLegBar(200, 103.0, {"ABC26SEP": 104.0, "ABC26OCT": 105.0}),
    ]
    result = CashFutureReplayRunner().run_both(
        bars,
        entry_timestamps=[100],
        exit_timestamps=[200],
        contract_lock=lock,
        lot_size=10,
    )[0]

    assert result.current_instrument == "ABC26SEP"
    assert result.near_instrument == "ABC26OCT"
    assert result.current_result.gross_pnl == 0.0
    assert result.near_result.gross_pnl == 0.0
    assert result.net_pnl == 0.0


def test_both_mode_keeps_leg_pnl_separate_and_combined():
    lock = CashFutureContractLock(
        mode="BOTH", contracts={"CURRENT": "ABC26SEP", "NEAR": "ABC26OCT"}
    )
    bars = [
        CashFutureMultiLegBar(100, 100.0, {"ABC26SEP": 101.0, "ABC26OCT": 102.0}),
        CashFutureMultiLegBar(200, 101.0, {"ABC26SEP": 100.0, "ABC26OCT": 101.0}),
    ]
    result = CashFutureReplayRunner().run_both(
        bars,
        entry_timestamps=[100],
        exit_timestamps=[200],
        contract_lock=lock,
        lot_size=10,
    )[0]

    assert result.current_result.gross_pnl == 20.0
    assert result.near_result.gross_pnl == 20.0
    assert result.gross_pnl == 40.0


def test_both_mode_fails_closed_when_one_synchronized_leg_is_missing():
    lock = CashFutureContractLock(
        mode="BOTH", contracts={"CURRENT": "ABC26SEP", "NEAR": "ABC26OCT"}
    )
    bars = [
        CashFutureMultiLegBar(100, 100.0, {"ABC26SEP": 101.0}),
        CashFutureMultiLegBar(200, 101.0, {"ABC26SEP": 100.0}),
    ]
    with pytest.raises(LookupError, match="NEAR future prices"):
        CashFutureReplayRunner().run_both(
            bars,
            entry_timestamps=[100],
            exit_timestamps=[200],
            contract_lock=lock,
            lot_size=10,
        )


def test_both_mode_rejects_rollover_substitution_at_lock_creation():
    lock = CashFutureContractLock(
        mode="BOTH", contracts={"CURRENT": "ABC26SEP", "NEAR": "ABC26OCT"}
    )
    with pytest.raises(ValueError, match="cannot replace open CURRENT leg"):
        lock.require("CURRENT", "ABC26NOV")
