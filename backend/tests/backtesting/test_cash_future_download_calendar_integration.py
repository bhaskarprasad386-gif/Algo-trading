from datetime import date, datetime

from app.backtesting.cash_future_historical_loader import (
    CashFutureHistoricalLoader,
    CashFutureHistorySelection,
)
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_downloaded_paired_records_are_read_by_calendar_loader() -> None:
    catalog = HistoricalCatalog(":memory:")
    contracts = ContractMasterCatalog(":memory:")
    try:
        trading_day = date(2026, 1, 6)
        contract = ContractRecord(
            exchange="NFO",
            symbol="AAA-FUT",
            token="101",
            expiry=date(2026, 1, 29),
            instrument_type="STOCK_FUTURE",
            underlying="AAA",
            lot_size=100,
            snapshot_date=trading_day,
        )
        contracts.upsert_snapshot(trading_day, [contract])

        timestamp = datetime(2026, 1, 6, 11, 0).astimezone()
        timestamp_ns = int(timestamp.timestamp() * 1_000_000_000)
        catalog.ingest(
            [
                HistoricalRecord(
                    source="angelone",
                    instrument="AAA",
                    timeframe="1m",
                    timestamp_ns=timestamp_ns,
                    payload={"close": 100.0},
                ),
                HistoricalRecord(
                    source="angelone",
                    instrument="NFO:101:AAA-FUT",
                    timeframe="1m",
                    timestamp_ns=timestamp_ns,
                    payload={"close": 112.0, "margin_required": 125000.0},
                ),
            ]
        )

        selection = CashFutureHistorySelection(
            spot_instrument="AAA",
            exchange="NFO",
            underlying="AAA",
            start_date=trading_day,
            end_date=trading_day,
            timeframe="1m",
            mode="CURRENT",
            source="angelone",
        )
        points = list(CashFutureHistoricalLoader(catalog, contracts).iter_points(selection))

        assert len(points) == 1
        assert points[0].symbol == "AAA"
        assert points[0].timestamp.hour == 11
        assert points[0].cash_price == 100.0
        assert points[0].future_price == 112.0
        assert points[0].gap == 12.0
        assert points[0].lot_size == 100
        assert points[0].margin_required == 125000.0
    finally:
        catalog.close()
        contracts.close()
