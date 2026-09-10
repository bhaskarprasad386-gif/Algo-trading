from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_catalog_preserves_multiple_events_at_same_timestamp_by_sequence():
    catalog = HistoricalCatalog()
    timestamp_ns = 1_700_000_000_000_000_000

    inserted = catalog.ingest(
        [
            HistoricalRecord("provider", "NSE:123", "tick", timestamp_ns, {"side": "bid", "price": 100}, 2),
            HistoricalRecord("provider", "NSE:123", "tick", timestamp_ns, {"side": "ask", "price": 101}, 1),
        ]
    )

    assert inserted == 2
    records = catalog.records(source="provider", instrument="NSE:123", timeframe="tick")
    assert [(record.timestamp_ns, record.sequence, record.payload["side"]) for record in records] == [
        (timestamp_ns, 1, "ask"),
        (timestamp_ns, 2, "bid"),
    ]

    catalog.close()
