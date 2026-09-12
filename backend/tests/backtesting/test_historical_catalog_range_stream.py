from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_iter_records_supports_range_wide_streaming_without_instrument_filter():
    catalog = HistoricalCatalog()
    catalog.ingest(
        [
            HistoricalRecord("angelone", "SBIN", "1m", 100, {"close": 100}),
            HistoricalRecord("angelone", "RELIANCE", "1m", 200, {"close": 200}),
            HistoricalRecord("angelone", "SBIN", "1m", 300, {"close": 101}),
        ]
    )

    rows = tuple(
        catalog.iter_records(
            source="angelone",
            timeframe="1m",
            start_ns=100,
            end_ns=300,
        )
    )

    assert [(row.instrument, row.timestamp_ns) for row in rows] == [
        ("SBIN", 100),
        ("RELIANCE", 200),
        ("SBIN", 300),
    ]

    filtered = tuple(
        catalog.iter_records(
            source="angelone",
            instrument="SBIN",
            timeframe="1m",
            start_ns=100,
            end_ns=300,
        )
    )
    assert [row.timestamp_ns for row in filtered] == [100, 300]
