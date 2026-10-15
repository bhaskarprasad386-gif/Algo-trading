from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner


def test_runner_snapshot_does_not_confuse_clear_stop_event_with_running_worker():
    class Manager:
        def snapshot(self):
            return {"connected_groups": [], "active_instruments": 0}

    runner = LiveCashFutureCommonRunner("ignored", manager=Manager())
    snapshot = runner.snapshot()

    assert snapshot["running"] is False
    assert snapshot["worker_alive"] is False
    assert snapshot["stop_requested"] is False
    assert snapshot["phase"] == "CREATED"
    assert snapshot["subscription_active"] is False
    assert snapshot["common_feed"]["active_instruments"] == 0


def test_runner_snapshot_exposes_instrument_master_failure():
    class Master:
        def download(self):
            raise TimeoutError("instrument master timed out")

    runner = LiveCashFutureCommonRunner(
        "ignored",
        instrument_master=Master(),
        manager=CommonWebSocketManager(socket_factory=lambda: object()),
    )

    try:
        runner.run_forever()
    except TimeoutError:
        pass
    else:
        raise AssertionError("instrument master error should propagate to supervisor")

    snapshot = runner.snapshot()
    assert snapshot["phase"] == "INSTRUMENT_MASTER_ERROR"
    assert "instrument master timed out" in snapshot["last_runner_error"]
    assert snapshot["registered_instruments"] == 0
