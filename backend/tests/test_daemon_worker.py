import asyncio
import threading
import time

from app.core.daemon_worker import run_blocking_daemon


def test_blocking_daemon_returns_result_and_propagates_error():
    async def scenario():
        assert await run_blocking_daemon(lambda: 42) == 42

        def fail():
            raise ValueError("fetch failed")

        try:
            await run_blocking_daemon(fail)
        except ValueError as exc:
            assert str(exc) == "fetch failed"
        else:
            raise AssertionError("worker exception was not propagated")

    asyncio.run(scenario())


def test_cancelling_daemon_worker_does_not_wait_for_blocked_io():
    started = threading.Event()
    release = threading.Event()

    def blocked_fetch():
        started.set()
        release.wait(timeout=5)
        return "finished"

    async def scenario():
        task = asyncio.create_task(run_blocking_daemon(blocked_fetch))
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        before = time.monotonic()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled worker await did not propagate cancellation")
        assert time.monotonic() - before < 0.5

    try:
        asyncio.run(scenario())
    finally:
        release.set()
