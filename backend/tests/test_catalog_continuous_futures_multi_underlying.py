from datetime import date

from app.backtesting.continuous_futures_acquisition import build_catalog_continuous_futures_acquisition_plan
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.trading_calendar import TradingCalendar


def test_catalog_continuous_futures_plan_supports_multiple_underlyings_independently():
    catalog = ContractMasterCatalog(
        [
            ContractRecord(symbol="AAA", underlying="AAA", instrument_type="STOCK_FUTURE", exchange="NFO", token="101", expiry=date(2026, 1, 29)),
            ContractRecord(symbol="AAA", underlying="AAA", instrument_type="STOCK_FUTURE", exchange="NFO", token="102", expiry=date(2026, 2, 26)),
            ContractRecord(symbol="BBB", underlying="BBB", instrument_type="STOCK_FUTURE", exchange="NFO", token="201", expiry=date(2026, 1, 29)),
            ContractRecord(symbol="BBB", underlying="BBB", instrument_type="STOCK_FUTURE", exchange="NFO", token="202", expiry=date(2026, 2, 26)),
        ]
    )
    windows, plan = build_catalog_continuous_futures_acquisition_plan(
        catalog,
        start_date=date(2026, 1, 5),
        end_date=date(2026, 2, 20),
        source="angelone",
        timeframe="1m",
        interval_ns=60_000_000_000,
        calendar=TradingCalendar(),
        max_request_ns=86_400_000_000_000,
    )
    assert {window.underlying for window in windows} == {"AAA", "BBB"}
    instruments = {request.instrument for request in plan.requests}
    assert instruments == {"NFO:101", "NFO:102", "NFO:201", "NFO:202"}
