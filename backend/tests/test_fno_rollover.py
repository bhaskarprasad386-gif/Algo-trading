from datetime import date

from app.backtesting.contract_master import ContractRecord
from app.backtesting.fno_rollover import build_futures_rollover_chain


def _contract(token: str, expiry: date) -> ContractRecord:
    return ContractRecord(
        exchange="NFO",
        symbol=f"ABC{token}",
        token=token,
        expiry=expiry,
        instrument_type="STOCK_FUTURE",
        underlying="ABC",
        lot_size=250,
        snapshot_date=date(2026, 1, 1),
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
    other = ContractRecord(
        exchange="NFO",
        symbol="XYZJAN",
        token="X",
        expiry=date(2026, 1, 29),
        instrument_type="STOCK_FUTURE",
        underlying="XYZ",
        lot_size=1,
        snapshot_date=date(2026, 1, 1),
        tick_size=0.05,
    )
    index = ContractRecord(
        exchange="NFO",
        symbol="ABCIDXJAN",
        token="I",
        expiry=date(2026, 1, 29),
        instrument_type="INDEX_FUTURE",
        underlying="ABC",
        lot_size=1,
        snapshot_date=date(2026, 1, 1),
        tick_size=0.05,
    )
    chain = build_futures_rollover_chain(
        (_contract("JAN", date(2026, 1, 29)), other, index),
        underlying="ABC",
        instrument_type="STOCK_FUTURE",
        start_date=date(2026, 1, 26),
        end_date=date(2026, 1, 29),
    )

    assert len(chain) == 1
    assert chain[0].contract_token == "JAN"
