from app.backtesting.engine import BacktestConfig, BacktestEngine, EventContext, EventSignal
from app.backtesting.historical_catalog import HistoricalRecord


def _event(timestamp_ns, sequence, payload):
    return HistoricalRecord(
        source="test",
        instrument="NFO:123",
        timeframe="tick",
        timestamp_ns=timestamp_ns,
        payload=payload,
        sequence=sequence,
    )


def test_event_runner_preserves_timestamp_sequence_and_payload_order():
    seen = []

    def strategy(event: EventContext):
        seen.append((event.timestamp_ns, event.sequence, event.payload["bid"], event.instrument))
        return None

    events = [_event(1_000_001, 2, {"bid": 100.2}), _event(1_000_001, 3, {"bid": 100.3})]
    result = BacktestEngine().run_events(events, strategy)

    assert result.trades == ()
    assert seen == [(1_000_001, 2, 100.2, "NFO:123"), (1_000_001, 3, 100.3, "NFO:123")]


def test_event_runner_supports_arbitrary_payload_and_buy_sell_pnl():
    decisions = {1: EventSignal("BUY", 100.0), 2: EventSignal("SELL", 103.0)}

    def strategy(event: EventContext):
        return decisions[event.sequence]

    events = [_event(1_000_000_001, 1, {"bid": 99.9, "ask": 100.0, "depth": {"bid_qty": 500}}),
              _event(1_000_000_002, 2, {"bid": 102.9, "ask": 103.0, "depth": {"ask_qty": 400}})]
    result = BacktestEngine(BacktestConfig(initial_capital=10_000.0)).run_events(events, strategy)

    assert len(result.trades) == 1
    assert result.trades[0].entry_timestamp == 1_000_000_001
    assert result.trades[0].exit_timestamp == 1_000_000_002
    assert result.trades[0].gross_pnl == 3.0
    assert result.net_pnl == 3.0


def test_event_runner_rejects_unordered_high_resolution_events():
    events = [_event(2_000, 1, {"price": 101.0}), _event(1_000, 2, {"price": 100.0})]

    try:
        BacktestEngine().run_events(events, lambda event: None)
    except ValueError as exc:
        assert "ordered" in str(exc)
        return
    raise AssertionError("expected unordered event validation")


def test_event_runner_uses_payload_price_when_signal_has_no_price():
    events = [_event(1_000, 1, {"price": 50.0}), _event(2_000, 2, {"price": 52.0})]
    actions = iter(("BUY", "SELL"))

    result = BacktestEngine().run_events(events, lambda event: next(actions))

    assert result.net_pnl == 2.0
