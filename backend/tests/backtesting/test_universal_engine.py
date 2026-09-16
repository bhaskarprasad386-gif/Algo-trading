from app.backtesting.engine import EventSignal
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.universal_engine import UniversalEventBacktestEngine


def _event(ts: int, instrument: str, price: float, seq: int) -> HistoricalRecord:
    return HistoricalRecord(
        source="test",
        instrument=instrument,
        timeframe="tick",
        timestamp_ns=ts,
        payload={"price": price},
        sequence=seq,
    )


def test_universal_engine_isolates_multiple_instruments_and_supports_shorts() -> None:
    events = [
        _event(1, "AAA", 100.0, 1),
        _event(2, "BBB", 200.0, 1),
        _event(3, "AAA", 110.0, 1),
        _event(4, "BBB", 190.0, 1),
    ]

    def strategy(ctx):
        if ctx.instrument == "AAA":
            return EventSignal("BUY" if ctx.timestamp_ns == 1 else "SELL")
        return EventSignal("SELL" if ctx.timestamp_ns == 2 else "BUY")

    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run(events, strategy)

    assert result.fill_count == 4
    assert result.realized_pnl == 20.0
    assert result.unrealized_pnl == 0.0
    assert result.final_equity == 100_020.0
    assert result.snapshots[-1].positions == ()


def test_universal_engine_keeps_positions_independent_by_instrument() -> None:
    events = [
        _event(1, "AAA", 100.0, 1),
        _event(2, "BBB", 200.0, 1),
    ]

    def strategy(ctx):
        return EventSignal("BUY")

    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run(events, strategy)

    positions = {position.instrument: position.quantity for position in result.snapshots[-1].positions}
    assert positions == {"AAA": 1, "BBB": 1}
