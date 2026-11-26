import asyncio

from app.core.event_loop_telemetry import EventLoopLagMonitor


def test_event_loop_lag_monitor_uses_bounded_histogram():
    monitor = EventLoopLagMonitor(interval_seconds=0.25)
    for lag in (0.001, 0.02, 0.08, 0.7):
        monitor.observe(lag)
    snapshot = monitor.snapshot()
    assert snapshot["sample_count"] == 4
    assert snapshot["lag_buckets"]["le_0.01s"] == 1
    assert snapshot["lag_buckets"]["le_0.025s"] == 1
    assert snapshot["lag_buckets"]["le_0.1s"] == 1
    assert snapshot["lag_buckets"]["gt_0.5s"] == 1
    assert snapshot["p95_lag_bucket"] == "gt_0.5s"


def test_event_loop_lag_monitor_task_cancels_cleanly():
    async def run():
        monitor = EventLoopLagMonitor(interval_seconds=0.005)
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert monitor.snapshot()["sample_count"] >= 1

    asyncio.run(run())
