from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_event_catalog_preserves_same_timestamp_sequence_order_and_payloads():
    catalog = HistoricalCatalog()
    records = (
        HistoricalRecord("feed", "NFO:101", "orderbook", 1_000, {"side": "ask", "price": 101}, 2),
        HistoricalRecord("feed", "NFO:101", "orderbook", 1_000, {"side": "bid", "price": 99}, 1),
        HistoricalRecord("feed", "NFO:101", "orderbook", 1_001, {"side": "ask", "price": 102}, 3),
    )

    assert catalog.ingest_events(reversed(records)) == 3
    assert catalog.events(
        source="feed", instrument="NFO:101", timeframe="orderbook"
    ) == (records[1], records[0], records[2])
    assert catalog.event_count(
        source="feed", instrument="NFO:101", timeframe="orderbook"
    ) == 3
    assert catalog.events(
        source="feed", instrument="NFO:101", timeframe="orderbook", start_ns=1_000, end_ns=1_000
    ) == (records[1], records[0])


def test_event_catalog_rejects_cadence_gap_detection():
    catalog = HistoricalCatalog()
    record = HistoricalRecord("feed", "NFO:101", "tick", 1_000, {"ltp": 100})
    catalog.ingest_events((record,))

    try:
        catalog.gaps(source="feed", instrument="NFO:101", timeframe="tick", interval_ns=1)
    except ValueError as exc:
        assert str(exc) == "cadence gap detection is not valid for event timeframes"
    else:
        raise AssertionError("event cadence gap detection must be rejected")
