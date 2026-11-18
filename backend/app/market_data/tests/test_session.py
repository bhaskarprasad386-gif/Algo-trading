from app.market_data.session import MarketSession


def test_startup_waits_for_history_before_live():
    session = MarketSession()
    assert session.startup(False, True).live_running is False
    assert session.startup(True, True).live_running is True


def test_market_close_stops_live_without_losing_history_state():
    session = MarketSession()
    state = session.startup(True, True)
    stopped = session.market_tick(state, False)
    assert stopped.historical_complete is True
    assert stopped.live_running is False
