from app.market_data.data_sync import sync_before_live
from app.market_data.historical_pipeline import prepare_before_live


def test_incomplete_history_without_sync_blocks_live():
    result = prepare_before_live(historical_complete=False)

    assert result.sync.required is True
    assert result.sync.completed is False
    assert result.live_allowed is False


def test_live_gate_opens_only_after_successful_sync():
    calls = []

    def sync():
        calls.append(True)
        return 42

    result = prepare_before_live(historical_complete=False, sync=sync)

    assert calls == [True]
    assert result.sync.required is True
    assert result.sync.completed is True
    assert result.sync.rows_added == 42
    assert result.live_allowed is True


def test_already_complete_history_does_not_run_sync():
    def sync():
        raise AssertionError("sync must not run when history is complete")

    result = prepare_before_live(historical_complete=True, sync=sync)

    assert result.sync.required is False
    assert result.sync.completed is True
    assert result.sync.rows_added == 0
    assert result.live_allowed is True


def test_failed_sync_does_not_open_live_gate():
    def sync():
        raise RuntimeError("historical sync failed")

    try:
        prepare_before_live(historical_complete=False, sync=sync)
    except RuntimeError as exc:
        assert str(exc) == "historical sync failed"
    else:
        raise AssertionError("failed historical sync must not be treated as live-ready")
