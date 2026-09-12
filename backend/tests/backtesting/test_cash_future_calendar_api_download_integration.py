from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.monthly_results_routes import monthly_gap_top10


MARKET_TZ = ZoneInfo("Asia/Kolkata")


class _EmptyDb:
    def execute(self, *_args, **_kwargs):
        class _Result:
            def mappings(self):
                return self

            def all(self):
                return []

        return _Result()


def test_monthly_calendar_top10_reads_downloaded_catalog_without_market_bars(monkeypatch, tmp_path) -> None:
    data_db = tmp_path / "historical.db"
    contract_db = tmp_path / "contracts.db"
    monkeypatch.setattr(
        "app.backtesting.monthly_results_routes.settings.BACKTEST_DATA_DB",
        str(data_db),
    )
    monkeypatch.setattr(
        "app.backtesting.monthly_results_routes.settings.BACKTEST_CONTRACT_DB",
        str(contract_db),
    )

    trading_day = date(2026, 1, 6)
    timestamp = datetime(2026, 1, 6, 11, 0, tzinfo=MARKET_TZ)
    timestamp_ns = int(timestamp.timestamp() * 1_000_000_000)

    contracts = ContractMasterCatalog(str(contract_db))
    catalog = HistoricalCatalog(str(data_db))
    try:
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
        catalog.ingest(
            [
                HistoricalRecord(
                    source="angelone",
                    instrument="NSE:999:AAA",
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
    finally:
        catalog.close()
        contracts.close()

    response = monthly_gap_top10(
        year=2026,
        month=1,
        instrument_type="STOCK",
        contract_month=None,
        db=_EmptyDb(),
    )

    assert response["status"] == "success"
    assert response["count"] == 1
    item = response["data"][0]
    assert item["symbol"] == "AAA"
    assert item["month_gap_high"] == 12.0
    assert item["gap_value"] == 1200.0
    assert item["gap_high_date"] == trading_day
    assert item["gap_high_timestamp"] == timestamp.isoformat()
    assert item["cash_price_at_gap_high"] == 100.0
    assert item["future_price_at_gap_high"] == 112.0
    assert item["lot_size"] == 100
    assert item["expiry_date"] == date(2026, 1, 29)
    assert item["is_expiry_day"] is False
