from app.market_data.tick_engine import TickEngine


def test_process_stores_latest_and_history():
    engine = TickEngine(max_ticks_per_symbol=2)
    first = engine.process({"token": "101", "ltp": 100})
    second = engine.process({"token": "101", "ltp": 101})

    assert first["symbol"] == "101"
    assert engine.latest("101")["ltp"] == 101
    assert len(engine.history("101")) == 2


def test_history_is_bounded():
    engine = TickEngine(max_ticks_per_symbol=2)
    for ltp in (100, 101, 102):
        engine.process({"symbol": "NIFTY", "ltp": ltp})

    assert [x["ltp"] for x in engine.history("NIFTY")] == [101, 102]


def test_invalid_tick_is_ignored():
    engine = TickEngine()
    assert engine.process({"ltp": 100}) is None
    assert engine.snapshot() == {}
