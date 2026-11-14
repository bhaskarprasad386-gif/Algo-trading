"""Opt-in, per-shard callback dispatch adapter for Angel One.

This adapter is intentionally standalone. CommonWebSocketManager does not
instantiate it unless a separately reviewed integration opts in. One adapter
instance owns one shard's FIFO and invokes its callbacks on one worker thread.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class CallbackInvocation:
    callback: Callable[[Any], None]
    payload: Any
    callback_kind: str


@dataclass(frozen=True)
class ShardCallbackBatch:
    generation: int
    invocations: tuple[CallbackInvocation, ...]


class AngelShardCallbackAdapter:
    """Bounded FIFO callback adapter; instantiate one per Angel socket shard."""

    def __init__(self, *, capacity: int = 2048, poll_seconds: float = 0.05) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._queue: Queue[ShardCallbackBatch] = Queue(maxsize=capacity)
        self._poll_seconds = float(poll_seconds)
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._generation = 0
        self._closed = False
        self._accepted = self._overflow = self._stale = 0
        self._discarded = self._copy_errors = 0
        self._processed = self._callback_errors = 0
        self._callback_attempts = self._callback_successes = 0
        self._integrity_fault = False
        self._last_error: str | None = None

    def activate_generation(self, generation: int) -> None:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("generation must be a positive integer")
        with self._lock:
            if self._closed:
                raise RuntimeError("adapter is closed")
            if generation <= self._generation:
                raise ValueError("generation must increase monotonically")
            self._generation = generation

    def _discard_pending_locked(self) -> int:
        discarded = 0
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break
            else:
                self._queue.task_done()
                discarded += 1
        self._discarded += discarded
        return discarded

    def _fail_closed_locked(self, reason: str) -> None:
        # A queue-integrity failure retires this adapter generation. Fence
        # already-queued work and reject every subsequent publish. A callback
        # already executing cannot be cancelled safely and may finish.
        self._integrity_fault = True
        self._closed = True
        self._generation += 1
        self._last_error = reason[:240]
        self._discard_pending_locked()
        self._stop.set()

    def publish(
        self,
        *,
        generation: int,
        invocations: Iterable[CallbackInvocation],
    ) -> bool:
        with self._lock:
            if self._closed:
                return False
            if generation != self._generation:
                self._stale += 1
                return False
        # Detach SDK payloads before they cross the thread boundary while
        # preserving the payload shape expected by existing consumers.
        try:
            detached = tuple(
                CallbackInvocation(item.callback, deepcopy(item.payload), item.callback_kind)
                for item in invocations
            )
        except Exception as exc:
            with self._lock:
                if not self._closed and generation == self._generation:
                    self._copy_errors += 1
                    self._fail_closed_locked(
                        f"payload_copy: {type(exc).__name__}: {exc}"
                    )
            return False
        batch = ShardCallbackBatch(generation, detached)
        if not batch.invocations:
            return True
        with self._lock:
            if self._closed:
                return False
            if generation != self._generation:
                self._stale += 1
                return False
            try:
                self._queue.put_nowait(batch)
            except Full:
                self._overflow += 1
                self._fail_closed_locked("queue_overflow: shard adapter retired")
                return False
            self._accepted += 1
            return True

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("adapter is closed")
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(
                target=self._run,
                name=f"angel-shard-callback-{id(self):x}",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                batch = self._queue.get(timeout=self._poll_seconds)
            except Empty:
                continue
            try:
                with self._lock:
                    current = batch.generation == self._generation
                    if not current:
                        self._stale += 1
                if current:
                    for invocation in batch.invocations:
                        # Retirement may race after the batch was dequeued or
                        # while an earlier callback in this batch was running.
                        # Revalidate immediately before each consumer invocation.
                        with self._lock:
                            # _closed blocks new publishes. Accepted work may
                            # still drain during graceful stop; non-draining stop
                            # and integrity retirement advance the generation.
                            if batch.generation != self._generation:
                                self._stale += 1
                                break
                            self._callback_attempts += 1
                        try:
                            invocation.callback(invocation.payload)
                        except Exception as exc:
                            with self._lock:
                                self._callback_errors += 1
                                self._last_error = (
                                    f"{invocation.callback_kind}: "
                                    f"{type(exc).__name__}: {exc}"
                                )[:240]
                        else:
                            with self._lock:
                                self._callback_successes += 1
                    with self._lock:
                        self._processed += 1
            finally:
                self._queue.task_done()

    def stop(self, *, timeout: float = 3.0, drain: bool = True) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = monotonic() + timeout
        with self._lock:
            self._closed = True
            # A draining stop rejects new publishes via _closed but preserves
            # the active generation so already-accepted FIFO batches can run.
            # A non-draining stop retires the generation and fences queued work.
            if not drain:
                self._generation += 1
            thread = self._thread
        if not drain:
            discarded = 0
            while True:
                try:
                    self._queue.get_nowait()
                except Empty:
                    break
                else:
                    self._queue.task_done()
                    discarded += 1
            with self._lock:
                self._discarded += discarded
            self._stop.set()
            if thread is None:
                return
        elif thread is None:
            # No worker was started, so there is nobody available to drain
            # accepted work. Account for it rather than silently retaining it.
            discarded = 0
            while True:
                try:
                    self._queue.get_nowait()
                except Empty:
                    break
                else:
                    self._queue.task_done()
                    discarded += 1
            with self._lock:
                self._discarded += discarded
            return
        else:
            while not self._queue.empty() and monotonic() < deadline:
                if not thread.is_alive():
                    break
                thread.join(timeout=min(self._poll_seconds, max(0.0, deadline - monotonic())))
            self._stop.set()
        thread.join(timeout=max(0.0, deadline - monotonic()))
        if thread.is_alive():
            raise TimeoutError("Angel shard callback worker exceeded shutdown budget")
        with self._lock:
            self._thread = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "generation": self._generation,
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "accepted_batches": self._accepted,
                "processed_batches": self._processed,
                "stale_batches": self._stale,
                "overflow_batches": self._overflow,
                "discarded_batches": self._discarded,
                "payload_copy_errors": self._copy_errors,
                "callback_attempts": self._callback_attempts,
                "callback_successes": self._callback_successes,
                "callback_errors": self._callback_errors,
                "integrity_fault": self._integrity_fault,
                "delivery_status": "DEGRADED" if self._integrity_fault or self._callback_errors else "HEALTHY",
                "last_error": self._last_error,
                "running": bool(self._thread and self._thread.is_alive()),
                "closed": self._closed,
            }
