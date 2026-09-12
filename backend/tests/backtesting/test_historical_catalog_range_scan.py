from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_iter_records_supports_range_wide_streaming_without_instrument():
    catalog = HistoricalCatalog()
    catalog.ingest(
        [
            HistoricalRecord("angelone", "SBIN", "1m", 100, {"close": 10}),
            HistoricalRecord("angelone", "RELIANCE", "1m", 200, {"close": 20}),
            HistoricalRecord("angelone", "SBIN", "1m", 300, {"close": 30}),
            HistoricalRecord("other", "SBIN", "1m", 400, {"close": 40}),
        ]
    )

    rows = tuple(
        catalog.iter_records(
            source="angelone",
            timeframe="1m",
            start_ns=150,
            end_ns=350,
        )
    )

    assert [(row.instrument, row.timestamp_ns) for row in rows] == [
        ("RELIANCE", 200),
        ("SBIN", 300),
    ]
