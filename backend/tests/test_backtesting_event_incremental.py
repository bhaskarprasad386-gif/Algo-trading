from app.backtesting.engine import BacktestConfig, EventSignal
from app.backtesting.event_incremental import run_catalog_events_incremental, run_events_incremental
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def _event(timestamp_ns, sequence, price):
    return HistoricalRecord(
        source="test",
        instrument="NFO:123",
        timeframe="tick",
        timestamp_ns=timestamp_ns,
        payload={"price": price},
        sequence=sequence,
    )


def test_event_incremental_persists_bounded_chunks_and_returns_aggregate_metrics():
    events = [
        _event(1_000, 1, 100.0), _event(2_000, 2, 101.0),
        _event(3_000, 3, 110.0), _event(4_000, 4, 108.0),
        _event(5_000, 5, 120.0), _event(6_000, 6, 123.0),
    ]
    actions = iter(("BUY", "SELL", "BUY", "SELL", "BUY", "SELL"))
    chunks = []

    result = run_events_incremental(
        BacktestConfig(initial_capital=10_000.0),
        events,
        lambda event: EventSignal(next(actions)),
        persist_chunk=lambda chunk, index: chunks.append((index, chunk)),
        chunk_size=2,
    )

    assert [len(chunk) for _, chunk in chunks] == [2, 1]
    assert [trade.net_pnl for _, chunk in chunks for trade in chunk] == [1.0, -2.0, 3.0]
    assert result.trades == ()
    assert result.net_pnl == 2.0
    assert result.win_rate == 2 / 3


def test_catalog_incremental_runner_streams_range_without_event_materialization():
    catalog = HistoricalCatalog()
    catalog.ingest_events([
        _event(1_000, 1, 90.0),
        _event(2_000, 2, 100.0),
        _event(3_000, 3, 110.0),
    ])
    chunks = []
    actions = iter(("BUY", "SELL"))

    result = run_catalog_events_incremental(
        catalog,
        BacktestConfig(initial_capital=10_000.0),
        source="test",
        instrument="NFO:123",
        start_ns=1_000,
        end_ns=2_000,
        strategy=lambda event: next(actions),
        persist_chunk=lambda chunk, index: chunks.append(chunk),
        chunk_size=10,
    )

    assert len(chunks) == 1
    assert len(chunks[0]) == 1
    assert chunks[0][0].entry_timestamp == 1_000
    assert chunks[0][0].exit_timestamp == 2_000
    assert result.net_pnl == 10.0
    catalog.close()


def test_event_incremental_rejects_unordered_events_before_persisting():
    events = [_event(2_000, 2, 102.0), _event(1_000, 1, 101.0)]
    persisted = []

    try:
        run_events_incremental(
            BacktestConfig(),
            events,
            lambda event: "NONE",
            persist_chunk=lambda chunk, index: persisted.append(chunk),
        )
    except ValueError as exc:
        assert "ordered" in str(exc)
    else:
        raise AssertionError("expected unordered event validation")

    assert persisted == []
