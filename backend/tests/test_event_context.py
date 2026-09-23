from app.backtesting.engine import EventContext
from app.backtesting.historical_catalog import HistoricalRecord


def test_event_context_keeps_legacy_positional_shape_and_exposes_read_only_views():
    record = HistoricalRecord("test", "NIFTY", "tick", 100, {"price": 100.0}, 1)
    context = EventContext(100, 1, "test", "NIFTY", record.payload, record)

    assert context.timestamp_ns == 100
    assert context.instrument == "NIFTY"
    assert context.portfolio_snapshot is None
    assert context.open_orders == ()
    assert context.available_margin is None


def test_event_context_accepts_engine_owned_portfolio_views():
    record = HistoricalRecord("test", "NIFTY", "tick", 100, {"price": 100.0}, 1)
    snapshot = object()
    order = object()
    context = EventContext(
        100, 1, "test", "NIFTY", record.payload, record,
        snapshot, (order,), 12345.0,
    )

    assert context.portfolio_snapshot is snapshot
    assert context.open_orders == (order,)
    assert context.available_margin == 12345.0
