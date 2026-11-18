from app.market_data.historical_sync import HistoricalSync


def test_disabled_sync_does_not_request_network_work():
    result = HistoricalSync().sync(False)
    assert result.requested is False
    assert result.completed is True


def test_enabled_sync_completes_before_live_layer_uses_it():
    result = HistoricalSync().sync(True)
    assert result.requested is True
    assert result.completed is True
    assert result.rows_added >= 0
    assert result.rows_updated >= 0
