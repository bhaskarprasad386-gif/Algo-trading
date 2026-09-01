from app.market_data.historical_pipeline import prepare_before_live


def test_live_waits_for_required_historical_sync():
    result = prepare_before_live(historical_complete=False)
    assert result.sync.required is True
    assert result.live_allowed is False


def test_live_starts_after_historical_sync_completes():
    result = prepare_before_live(historical_complete=False, sync=lambda: 12)
    assert result.sync.rows_added == 12
    assert result.live_allowed is True


def test_live_can_start_when_history_is_already_complete():
    result = prepare_before_live(historical_complete=True)
    assert result.live_allowed is True
