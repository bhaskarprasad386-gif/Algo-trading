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
