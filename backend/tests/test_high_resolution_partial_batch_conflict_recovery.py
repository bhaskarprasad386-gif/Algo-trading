from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def _event(timestamp_ns, payload, sequence=1):
    return HistoricalRecord(
        "feed",
        "NFO:101",
        "tick",
        timestamp_ns,
        payload,
        sequence,
    )


def test_partial_event_batch_restart_deduplicates_exact_replay(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "events.db"))
    first_batch = (
        _event(100, {"ltp": 100}),
        _event(100, {"ltp": 101}, sequence=2),
    )
    assert catalog.ingest_events(first_batch) == 2

    replay_batch = (
        first_batch[1],
        _event(200, {"ltp": 200}),
    )
    assert catalog.ingest_events(replay_batch) == 1
    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 3
    assert catalog.events(
        source="feed", instrument="NFO:101", timeframe="tick"
    ) == (first_batch[0], first_batch[1], replay_batch[1])


def test_conflicting_event_identity_rolls_back_entire_batch(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "events.db"))
    existing = _event(100, {"ltp": 100})
    catalog.ingest_events((existing,))

    conflicting_batch = (
        _event(200, {"ltp": 200}),
        _event(100, {"ltp": 999}),
        _event(300, {"ltp": 300}),
    )
    try:
        catalog.ingest_events(conflicting_batch)
    except ValueError as exc:
        assert str(exc) == (
            "conflicting historical record identity: "
            "('feed', 'NFO:101', 'tick', 100, 1)"
        )
    else:
        raise AssertionError("expected conflicting event identity to fail")

    assert catalog.event_count(source="feed", instrument="NFO:101", timeframe="tick") == 1
    assert catalog.events(
        source="feed", instrument="NFO:101", timeframe="tick"
    ) == (existing,)
