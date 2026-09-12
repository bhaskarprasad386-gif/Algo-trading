from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.backtesting.cash_future_historical_loader import CashFutureHistoricalLoader, CashFutureHistorySelection
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord

MARKET_TZ = ZoneInfo("Asia/Kolkata")


def test_loader_resolves_normalized_downloaded_cash_instrument_and_nse_alias() -> None:
    catalog = HistoricalCatalog(":memory:")
    contracts = ContractMasterCatalog(":memory:")
    try:
        trading_day = date(2026, 1, 6)
        contracts.upsert_snapshot(
            trading_day,
            [
                ContractRecord(
                    exchange="NFO",
                    symbol="AAA-FUT",
                    token="101",
                    expiry=date(2026, 1, 29),
                    instrument_type="STOCK_FUTURE",
                    underlying="AAA",
                    lot_size=100,
                    snapshot_date=trading_day,
                )
            ],
        )
        timestamp = datetime(2026, 1, 6, 11, 0, tzinfo=MARKET_TZ)
        timestamp_ns = int(timestamp.timestamp() * 1_000_000_000)
        catalog.ingest(
            [
                HistoricalRecord("angelone", "NSE:999:AAA", "1m", timestamp_ns, {"close": 100.0}),
                HistoricalRecord("angelone", "NFO:101:AAA-FUT", "1m", timestamp_ns, {"close": 112.0, "margin_required": 125000.0}),
            ]
        )

        selection = CashFutureHistorySelection(
            spot_instrument="AAA",
            exchange="NSE",
            underlying="AAA",
            start_date=trading_day,
            end_date=trading_day,
            timeframe="1m",
            mode="CURRENT",
            source="angelone",
        )
        points = list(CashFutureHistoricalLoader(catalog, contracts).iter_points(selection))

        assert len(points) == 1
        assert points[0].cash_price == 100.0
        assert points[0].future_price == 112.0
        assert points[0].gap == 12.0
        assert points[0].lot_size == 100
        assert points[0].margin_required == 125000.0
    finally:
        catalog.close()
        contracts.close()
