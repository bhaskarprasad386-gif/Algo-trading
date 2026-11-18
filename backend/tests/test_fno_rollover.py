from datetime import date

import pytest

from app.backtesting.contract_master import ContractRecord
from app.backtesting.fno_rollover import (
    FNORolloverWindow,
    build_futures_rollover_chain,
    validate_futures_rollover_chain,
)


def _contract(token: str, expiry: date, snapshot_date: date | None = date(2026, 1, 1)) -> ContractRecord:
    return ContractRecord(
        exchange="NFO",
        symbol=f"ABC{token}",
        token=token,
        expiry=expiry,
        instrument_type="STOCK_FUTURE",
        underlying="ABC",
        lot_size=250,
        snapshot_date=snapshot_date,
        tick_size=0.05,
    )


def test_rollover_moves_to_next_expiry_after_current_contract_expires():
    chain = build_futures_rollover_chain(
        (_contract("JAN", date(2026, 1, 29)), _contract("FEB", date(2026, 2, 26))),
        underlying="ABC",
        instrument_type="STOCK_FUTURE",
        start_date=date(2026, 1, 26),
        end_date=date(2026, 3, 2),
    )
    assert [(w.start_date, w.end_date, w.contract_token) for w in chain] == [
        (date(2026, 1, 26), date(2026, 1, 29), "JAN"),
        (date(2026, 1, 30), date(2026, 2, 26), "FEB"),
    ]


def test_future_snapshot_contract_is_rejected_to_prevent_lookahead():
    chain = build_futures_rollover_chain(
        (_contract("FUTURE", date(2026, 1, 29), date(2026, 1, 10)),),
        underlying="ABC",
        instrument_type="STOCK_FUTURE",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 8),
    )
    assert chain == ()


def test_snapshotless_contract_remains_supported():
    chain = build_futures_rollover_chain(
        (_contract("JAN", date(2026, 1, 29), None),),
        underlying="ABC",
        instrument_type="STOCK_FUTURE",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 8),
    )
    assert chain[0].contract_token == "JAN"


def test_rollover_never_invents_contract_after_last_expiry():
    chain = build_futures_rollover_chain(
        (_contract("JAN", date(2026, 1, 29)),),
        underlying="ABC",
        instrument_type="STOCK_FUTURE",
        start_date=date(2026, 1, 30),
        end_date=date(2026, 2, 2),
    )
    assert chain == ()


def test_rollover_ignores_other_underlyings_and_instrument_types():
    other = ContractRecord("NFO", "XYZJAN", "X", date(2026, 1, 29), "STOCK_FUTURE", "XYZ", 1, date(2026, 1, 1), 0.05)
    index = ContractRecord("NFO", "ABCIDXJAN", "I", date(2026, 1, 29), "INDEX_FUTURE", "ABC", 1, date(2026, 1, 1), 0.05)
    chain = build_futures_rollover_chain(
        (_contract("JAN", date(2026, 1, 29)), other, index),
        underlying="ABC", instrument_type="STOCK_FUTURE", start_date=date(2026, 1, 26), end_date=date(2026, 1, 29)
    )
    assert len(chain) == 1
    assert chain[0].contract_token == "JAN"


def test_rollover_validation_accepts_contiguous_chain():
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 26), date(2026, 1, 29)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 30), date(2026, 2, 26)),
    )
    validate_futures_rollover_chain(windows, underlying="ABC", instrument_type="STOCK_FUTURE")


def test_rollover_validation_rejects_gap():
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 26), date(2026, 1, 29)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 2, 2), date(2026, 2, 26)),
    )
    with pytest.raises(ValueError, match="gap or overlap"):
        validate_futures_rollover_chain(windows, underlying="ABC", instrument_type="STOCK_FUTURE")


def test_rollover_validation_rejects_overlap():
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 26), date(2026, 1, 29)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 29), date(2026, 2, 26)),
    )
    with pytest.raises(ValueError, match="gap or overlap"):
        validate_futures_rollover_chain(windows, underlying="ABC", instrument_type="STOCK_FUTURE")
