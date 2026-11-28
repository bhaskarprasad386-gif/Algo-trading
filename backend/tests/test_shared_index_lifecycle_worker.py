import asyncio
import threading

import pytest

from app.market_data.shared_index_lifecycle_worker import SerializedLifecycleWorker


def test_lifecycle_worker_keeps_event_loop_responsive_and_serializes_stop():
    async def scenario():
        worker = SerializedLifecycleWorker()
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def blocking_start():
            calls.append("start-enter")
            entered.set()
            assert release.wait(2.0)
            calls.append("start-exit")
            return {"enabled": True}

        def stop():
            calls.append("stop")
            return {"enabled": False}

        start_task = asyncio.create_task(worker.run(blocking_start))
        assert await asyncio.to_thread(entered.wait, 1.0)

        # A blocking SDK start must not prevent unrelated ASGI-loop work.
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
        start_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start_task

        close_task = asyncio.create_task(worker.close(stop, timeout=1.0))
        await asyncio.sleep(0)
        assert "stop" not in calls
        release.set()
        result = await close_task

        assert result == {"enabled": False}
        assert calls == ["start-enter", "start-exit", "stop"]

    asyncio.run(scenario())


def test_lifecycle_worker_propagates_operation_errors_and_can_restart():
    async def scenario():
        worker = SerializedLifecycleWorker()

        def fail():
            raise ValueError("startup failed")

        with pytest.raises(ValueError, match="startup failed"):
            await worker.run(fail)

        assert await worker.close(lambda: "stopped") == "stopped"
        assert await worker.run(lambda: "restarted") == "restarted"
        assert await worker.close(lambda: "stopped-again") == "stopped-again"

    asyncio.run(scenario())

def test_lifecycle_worker_fails_closed_when_stop_raises():
    async def scenario():
        worker = SerializedLifecycleWorker()

        def fail_stop():
            raise RuntimeError("stop failed")

        with pytest.raises(RuntimeError, match="stop failed"):
            await worker.close(fail_stop)

        with pytest.raises(RuntimeError, match="closing"):
            await worker.run(lambda: "must-not-run")

    asyncio.run(scenario())

def test_lifecycle_worker_shutdown_deadline_fails_closed_for_hung_start():
    async def scenario():
        worker = SerializedLifecycleWorker()
        entered = threading.Event()
        release = threading.Event()
        stop_called = threading.Event()

        def blocking_start():
            entered.set()
            release.wait(2.0)

        def stop():
            stop_called.set()

        start_task = asyncio.create_task(worker.run(blocking_start))
        assert await asyncio.to_thread(entered.wait, 1.0)
        start_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start_task
        with pytest.raises(RuntimeError, match="did not stop before deadline"):
            await worker.close(stop, timeout=0.05)

        assert not stop_called.is_set()
        with pytest.raises(RuntimeError, match="closing"):
            await worker.run(lambda: None)

        release.set()
        await asyncio.sleep(0.05)
        assert stop_called.is_set()

    asyncio.run(scenario())
