from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner


def test_box_runner_snapshot_exposes_startup_websocket_states():
    runner = LiveBoxSpreadRunner(
        object(),
        [],
        allowed_stock_symbols=frozenset(),
        auth=object(),
    )
    snapshot = runner.snapshot()
    assert snapshot["running"] is True
    assert snapshot["underlying_feed"]["state"] == "not_started"
    assert snapshot["option_recorder"]["state"] == "not_started"
    assert snapshot["readiness"]["state"] == "starting"


def test_box_runner_safe_snapshot_handles_test_doubles_without_snapshot():
    class Feed:
        pass

    runner = LiveBoxSpreadRunner(
        object(),
        [],
        allowed_stock_symbols=frozenset(),
        auth=object(),
    )
    runner.feed = Feed()
    snapshot = runner.snapshot()
    assert snapshot["underlying_feed"]["state"] == "snapshot_error"
    assert "snapshot" in snapshot["underlying_feed"]["reason"]
