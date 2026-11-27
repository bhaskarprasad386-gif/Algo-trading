from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_catalog_replay import CatalogReplayLeg, HistoricalCatalogEventReplay
import json



def test_catalog_replay_joins_only_exact_complete_timestamps():
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("test", "NSE:ABC", "1m", 1, {"bid": 99, "ask": 100}),
        HistoricalRecord("test", "NSE:ABC", "1m", 2, {"bid": 100, "ask": 101}),
        HistoricalRecord("test", "NFO:FUT", "1m", 1, {"bid": 109, "ask": 110}),
        HistoricalRecord("test", "NFO:FUT", "1m", 3, {"bid": 111, "ask": 112}),
    ])
    events = list(HistoricalCatalogEventReplay(catalog).events((CatalogReplayLeg("cash_future", "test", "NSE:ABC", "1m"), CatalogReplayLeg("future", "test", "NFO:FUT", "1m")), start_ns=1, end_ns=3))
    assert [event["timestamp_ns"] for event in events] == [1]
    assert events[0]["cash_future"]["ask"] == 100
    assert events[0]["future"]["bid"] == 109


def test_catalog_replay_preserves_exact_leg_identity():
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("test", "NSE:ABC", "1m", 1, {"bid": 99, "ask": 100}, sequence=17),
        HistoricalRecord("test", "NFO:FUT-20260924", "1m", 1, {"bid": 109, "ask": 110}, sequence=29),
    ])
    legs = (
        CatalogReplayLeg("cash_future", "test", "NSE:ABC", "1m"),
        CatalogReplayLeg("future", "test", "NFO:FUT-20260924", "1m"),
    )
    event = next(HistoricalCatalogEventReplay(catalog).events(legs, start_ns=1, end_ns=1))
    assert event["__replay_legs__"]["cash_future"] == {
        "source": "test",
        "instrument": "NSE:ABC",
        "timeframe": "1m",
        "sequence": 17,
    }
    assert event["__replay_legs__"]["future"] == {
        "source": "test",
        "instrument": "NFO:FUT-20260924",
        "timeframe": "1m",
        "sequence": 29,
    }


def test_catalog_replay_identity_metadata_does_not_mutate_payload():
    payload = {"bid": 99, "ask": 100, "__replay_legs__": "payload-owned"}
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("test", "NSE:ABC", "1m", 1, payload),
        HistoricalRecord("test", "NFO:FUT", "1m", 1, {"bid": 109, "ask": 110}),
    ])
    legs = (
        CatalogReplayLeg("cash", "test", "NSE:ABC", "1m"),
        CatalogReplayLeg("future", "test", "NFO:FUT", "1m"),
    )
    event = next(HistoricalCatalogEventReplay(catalog).events(legs, start_ns=1, end_ns=1))
    assert event["cash"]["__replay_legs__"] == "payload-owned"
    assert event["__replay_legs__"]["cash"]["instrument"] == "NSE:ABC"


def test_catalog_replay_never_fabricates_missing_leg():
    catalog = HistoricalCatalog()
    catalog.ingest([HistoricalRecord("test", "A", "ms", 10, {"price": 10}), HistoricalRecord("test", "A", "ms", 20, {"price": 20}), HistoricalRecord("test", "B", "ms", 10, {"price": 30})])
    events = list(HistoricalCatalogEventReplay(catalog).events((CatalogReplayLeg("a", "test", "A", "ms"), CatalogReplayLeg("b", "test", "B", "ms")), start_ns=10, end_ns=20))
    assert [event["timestamp_ns"] for event in events] == [10]
    assert all(event["timestamp_ns"] != 20 for event in events)


def test_catalog_replay_rejects_duplicate_timestamp_per_leg():
    catalog = HistoricalCatalog()
    catalog.ingest([HistoricalRecord("test", "A", "ms", 10, {"price": 10}, sequence=1), HistoricalRecord("test", "A", "ms", 10, {"price": 11}, sequence=2)])
    try:
        list(HistoricalCatalogEventReplay(catalog).events((CatalogReplayLeg("a", "test", "A", "ms"),), start_ns=10, end_ns=10))
    except ValueError as exc:
        assert "multiple catalog records" in str(exc)
    else:
        raise AssertionError("duplicate timestamp must be rejected")


def test_catalog_replay_reports_mixed_timeframes_explicitly():
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("test", "A", "1m", 10, {"price": 10}),
        HistoricalRecord("test", "B", "1s", 10, {"price": 20}),
    ])
    events = list(HistoricalCatalogEventReplay(catalog).events((
        CatalogReplayLeg("minute", "test", "A", "1m"),
        CatalogReplayLeg("second", "test", "B", "1s"),
    ), start_ns=10, end_ns=10))
    assert events[0]["data_resolution"] == "mixed"


