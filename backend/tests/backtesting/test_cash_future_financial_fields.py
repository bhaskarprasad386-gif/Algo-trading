from datetime import date, datetime

from app.backtesting.cash_future_historical_loader import _merge_pair
from app.backtesting.contract_master import ContractRecord
from app.backtesting.historical_catalog import HistoricalRecord


def _record(timestamp_ns: int, instrument: str, **payload: object) -> HistoricalRecord:
    return HistoricalRecord("angelone", instrument, "1m", timestamp_ns, payload)


def test_cash_future_pair_preserves_margin_charges_and_funding() -> None:
    ts = int(datetime(2026, 1, 5, 9, 30).timestamp() * 1_000_000_000)
    contract = ContractRecord(
        "NFO", "TEST-FUT", "101", date(2026, 1, 29), "STOCK_FUTURE", "TEST", 500
    )
    cash = iter([_record(ts, "TEST", close=100.0, bid=99.9, ask=100.1)])
    future = iter([
        _record(
            ts,
            "NFO:101:TEST-FUT",
            close=102.5,
            margin_required=125000.0,
            charges=37.5,
            funding_cost=12.25,
        )
    ])

    point = next(_merge_pair(cash, future, symbol="TEST", contract=contract))

    assert point.gap == 2.5
    assert point.lot_size == 500
    assert point.margin_required == 125000.0
    assert point.charges == 37.5
    assert point.funding_cost == 12.25
    assert point.expiry_date == date(2026, 1, 29)
