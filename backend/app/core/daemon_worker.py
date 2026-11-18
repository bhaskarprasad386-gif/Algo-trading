"""Run blocking network work without tying shutdown to asyncio's default executor.

Requests cannot be force-cancelled safely once a socket read is in progress.
A dedicated daemon thread lets the ASGI event loop cancel its await immediately;
the worker is data-fetch-only and is terminated by process exit if its socket
never returns. Callers must not create feeds, runners, or mutate runtime state
inside the worker function.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def run_blocking_daemon(
    function: Callable[..., T],
    *args: Any,
    thread_name: str = "blocking-network-fetch",
) -> T:
    """Run a blocking data operation in a daemon thread and await its result.

    Cancelling the coroutine does not pretend to cancel the underlying HTTP
    request; it only detaches the awaiter. The daemon worker cannot hold up
    asyncio's default-executor shutdown or the process shutdown.
    """
    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[T] = loop.create_future()

    def publish_result(value: T) -> None:
        if not result_future.done():
            result_future.set_result(value)

    def publish_error(error: BaseException) -> None:
        if not result_future.done():
            result_future.set_exception(error)

    def worker() -> None:
        try:
            value = function(*args)
        except BaseException as error:
            try:
                loop.call_soon_threadsafe(publish_error, error)
            except RuntimeError:
                # The event loop has already closed during process shutdown.
                pass
        else:
            try:
                loop.call_soon_threadsafe(publish_result, value)
            except RuntimeError:
                pass

    thread = threading.Thread(target=worker, name=thread_name, daemon=True)
    thread.start()
    return await result_future
