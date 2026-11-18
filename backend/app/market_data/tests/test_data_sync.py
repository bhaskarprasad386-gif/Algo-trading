from app.market_data.data_sync import sync_before_live


def test_complete_history_skips_sync():
    called = False

    def sync():
        nonlocal called
        called = True
        return 10

    result = sync_before_live(historical_complete=True, sync=sync)
    assert result.completed is True
    assert result.required is False
    assert result.rows_added == 0
    assert called is False


def test_incomplete_history_runs_sync_before_live():
    result = sync_before_live(
        historical_complete=False,
        sync=lambda: 42,
    )
    assert result.required is True
    assert result.completed is True
    assert result.rows_added == 42


def test_incomplete_history_without_sync_is_not_complete():
    result = sync_before_live(historical_complete=False)
    assert result.required is True
    assert result.completed is False
