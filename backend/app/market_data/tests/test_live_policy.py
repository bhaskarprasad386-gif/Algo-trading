from app.market_data.live_policy import LivePolicy


def test_historical_updates_are_allowed_anytime():
    assert LivePolicy().can_update_historical() is True


def test_live_requires_open_market_and_complete_history():
    policy = LivePolicy()
    assert policy.can_start_live(True, True) is True
    assert policy.can_start_live(False, True) is False
    assert policy.can_start_live(True, False) is False


def test_live_stops_when_market_closes():
    assert LivePolicy().can_keep_live(True) is True
    assert LivePolicy().can_keep_live(False) is False
