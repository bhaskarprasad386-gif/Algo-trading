"""Serialize blocking Shared Runner lifecycle operations away from the ASGI loop.

This worker is intentionally narrower than the fetch-only daemon helper: it
owns only the SharedIndexMarketDataRunner start/stop calls. A cancelled asyncio
awaiter does not cancel an in-flight SDK operation; the queued stop therefore
runs after that operation and forms the shutdown barrier.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from collections.abc import Callable
from typing import Any


class SerializedLifecycleWorker:
    """Single-threaded, FIFO executor for one process-owned runtime lifecycle."""

    _STOP = object()

    def __init__(self, *, thread_name: str = "shared-index-lifecycle") -> None:
        self._thread_name = thread_name
        self._queue: queue.Queue[Any] = queue.Queue()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._closing = False

    def _ensure_started(self) -> None:
        with self._lock:
            if self._closing:
                raise RuntimeError("lifecycle worker is closing")
            if self._thread is not None and self._thread.is_alive():
                return
            self._queue = queue.Queue()
            self._thread = threading.Thread(
                target=self._run,
                name=self._thread_name,
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._STOP:
                return
            function, args, kwargs, result = item
            if not result.set_running_or_notify_cancel():
                continue
            try:
                value = function(*args, **kwargs)
            except BaseException as exc:
                result.set_exception(exc)
            else:
                result.set_result(value)

    async def run(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Queue a lifecycle operation; cancellation never abandons its execution."""
        self._ensure_started()
        result: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._queue.put((function, args, kwargs, result))
        return await asyncio.shield(asyncio.wrap_future(result))

    async def close(self, stop_function: Callable[[], Any], *, timeout: float = 5.0) -> Any:
        """Run stop after all admitted operations, then retire the worker thread."""
        with self._lock:
            if self._closing:
                raise RuntimeError("lifecycle worker close already in progress")
            self._closing = True
        try:
            # run() rejects admission while closing, so enqueue the final stop
            # directly behind any already admitted startup operation.
            result: concurrent.futures.Future[Any] = concurrent.futures.Future()
            thread = self._thread
            if thread is None:
                thread = threading.Thread(
                    target=self._run,
                    name=self._thread_name,
                    daemon=True,
                )
                self._thread = thread
                thread.start()
            self._queue.put((stop_function, (), {}, result))
            stopped = await asyncio.shield(asyncio.wrap_future(result))
            self._queue.put(self._STOP)
            await asyncio.to_thread(thread.join, timeout)
            if thread.is_alive():
                raise RuntimeError("shared runner lifecycle worker did not stop before deadline")
            with self._lock:
                self._thread = None
            return stopped
        finally:
            with self._lock:
                self._closing = False
