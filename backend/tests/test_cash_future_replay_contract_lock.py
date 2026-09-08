import pytest

from app.backtesting.cash_future_replay import CashFutureContractLock


def test_contract_lock_keeps_historical_identity_across_rollover():
    lock = CashFutureContractLock(
        mode="BOTH",
        contracts={"CURRENT": "NFO:ABC26SEP", "NEAR": "NFO:ABC26OCT"},
    )
    lock.require("CURRENT", "NFO:ABC26SEP")
    lock.require("NEAR", "NFO:ABC26OCT")
    assert lock.contracts == {
        "CURRENT": "NFO:ABC26SEP",
        "NEAR": "NFO:ABC26OCT",
    }

    with pytest.raises(ValueError, match="cannot replace open CURRENT leg"):
        lock.require("CURRENT", "NFO:ABC26OCT")


def test_contract_lock_rejects_missing_selected_leg():
    with pytest.raises(LookupError, match="missing historical contract identity"):
        CashFutureContractLock(mode="BOTH", contracts={"CURRENT": "NFO:ABC26SEP"})
