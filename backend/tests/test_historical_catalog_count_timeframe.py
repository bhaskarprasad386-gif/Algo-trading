from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_count_can_be_scoped_to_timeframe():
    catalog = HistoricalCatalog()
    catalog.ingest(
        [
            HistoricalRecord("provider", "NSE:1:SBIN", "1m", 1, {"close": 100}),
            HistoricalRecord("provider", "NSE:1:SBIN", "1d", 2, {"close": 101}),
        ]
    )

    assert catalog.count(source="provider", instrument="NSE:1:SBIN") == 2
    assert catalog.count(source="provider", instrument="NSE:1:SBIN", timeframe="1m") == 1
    assert catalog.count(source="provider", instrument="NSE:1:SBIN", timeframe="1d") == 1
    catalog.close()
