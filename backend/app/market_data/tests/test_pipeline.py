from app.market_data.pipeline import MarketDataPipeline


def test_pipeline_history_first_then_live():
    pipeline = MarketDataPipeline()
    state = pipeline.startup(True)
    assert state.historical_requested is True
    assert state.data.historical_complete is True
    assert state.data.live_running is True


def test_pipeline_does_not_run_live_when_market_is_closed():
    pipeline = MarketDataPipeline()
    state = pipeline.startup(False)
    assert state.data.historical_complete is True
    assert state.data.live_running is False


def test_pipeline_stops_live_at_market_close():
    pipeline = MarketDataPipeline()
    state = pipeline.startup(True)
    stopped = pipeline.tick(state, False)
    assert stopped.data.historical_complete is True
    assert stopped.data.live_running is False
