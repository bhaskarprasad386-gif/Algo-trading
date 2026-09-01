from app.market_data.update_policy import DataUpdatePolicy


def test_historical_update_is_allowed_off_market():
    policy = DataUpdatePolicy()
    assert policy.allow_historical_update() is True


def test_live_data_requires_open_market_session():
    policy = DataUpdatePolicy()
    assert policy.allow_live_data(True) is True
    assert policy.allow_live_data(False) is False
