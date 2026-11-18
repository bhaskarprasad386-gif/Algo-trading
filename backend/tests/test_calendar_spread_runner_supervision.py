import asyncio
from threading import Event

import app.main as main


def test_calendar_spread_supervisor_restarts_unexpected_worker_exit(monkeypatch):
    collectors = []

    class FakeCollector:
        def __init__(self, *args, **kwargs):
            self.stop_event = Event()
            collectors.append(self)

        def stop(self):
            self.stop_event.set()

    calls = 0

    async def fake_run_worker(runner, **kwargs):
        nonlocal calls
        calls += 1
        # First worker exits without a requested stop; the supervisor must retry.
        # Second worker represents a requested stop and should end the loop.
        if calls == 2:
            runner.stop_event.set()

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(main, "LiveCalendarSpreadOneSecondCollector", FakeCollector)
    monkeypatch.setattr(main, "AngelOneAuth", lambda: object())
    monkeypatch.setattr(main, "_run_live_runner_in_daemon_thread", fake_run_worker)
    monkeypatch.setattr(main, "_stop_live_runner_nonblocking", lambda runner, **kwargs: runner.stop())
    monkeypatch.setattr(main.asyncio, "sleep", no_wait)

    asyncio.run(main._live_calendar_spread_loop())

    assert calls == 2
    assert len(collectors) == 2
    assert all(collector.stop_event.is_set() for collector in collectors)


def test_calendar_collector_health_does_not_call_unstarted_worker_running():
    from app.market_data.live_calendar_spread_stream import LiveCalendarSpreadOneSecondCollector

    collector = LiveCalendarSpreadOneSecondCollector(data_db=":memory:")
    health = collector.snapshot()

    assert health["running"] is False
    assert health["worker_alive"] is False
    assert health["stop_requested"] is False


def test_runner_worker_reference_reflects_finished_thread():
    class FakeRunner:
        def run_forever(self):
            return

        def stop(self):
            return

    runner = FakeRunner()
    asyncio.run(main._run_live_runner_in_daemon_thread(runner, name="calendar-test-runner"))

    assert hasattr(runner, "_runner_worker")
    assert runner._runner_worker.is_alive() is False
