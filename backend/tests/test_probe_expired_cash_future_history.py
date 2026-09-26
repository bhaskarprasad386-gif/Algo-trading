from datetime import date

from app.backtesting.contract_master import ContractRecord
from scripts.probe_expired_cash_future_history import select_expired_stock_futures


def _record(token: str, expiry: date, *, instrument_type: str = "STOCK_FUTURE") -> ContractRecord:
    return ContractRecord(
        exchange="NFO",
        symbol=f"ABC{token}FUT",
        token=token,
        expiry=expiry,
        instrument_type=instrument_type,
        underlying="ABC",
        lot_size=100,
    )


def test_select_expired_stock_futures_uses_real_expired_records_only() -> None:
    records = (
        _record("101", date(2026, 8, 27)),
        _record("102", date(2026, 9, 24)),
        _record("103", date(2026, 10, 29)),
        _record("104", date(2026, 9, 24), instrument_type="FUTIDX"),
        _record("102", date(2026, 9, 24)),  # duplicate
    )

    selected = select_expired_stock_futures(records, today=date(2026, 9, 26))

    assert [r.token for r in selected] == ["102", "101"]
    assert all(r.expiry < date(2026, 9, 26) for r in selected)
    assert all(r.instrument_type == "STOCK_FUTURE" for r in selected)


def test_select_expired_stock_futures_honors_limit() -> None:
    records = tuple(
        _record(str(100 + i), date(2026, 9, 20 - i))
        for i in range(5)
    )

    selected = select_expired_stock_futures(
        records, today=date(2026, 9, 26), max_contracts=2
    )

    assert len(selected) == 2
    assert selected[0].expiry > selected[1].expiry
