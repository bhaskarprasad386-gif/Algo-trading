"""Bounded per-shard callback dispatch adapter for Angel One.

CommonWebSocketManager creates one adapter per active socket generation only
when async_callback_dispatch=True. The generic manager default remains
synchronous for compatibility; the process-wide shared strategy manager opts
in explicitly. Each adapter owns one shard FIFO and one callback worker.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread, current_thread
from time import monotonic, monotonic_ns
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
    enqueued_at_ns: int


class AngelShardCallbackAdapter:
    """Bounded FIFO callback adapter; instantiate one per Angel socket shard."""

    def __init__(self, *, capacity: int = 2048, poll_seconds: float = 0.05, slow_callback_seconds: float = 0.25) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if slow_callback_seconds <= 0:
            raise ValueError("slow_callback_seconds must be positive")
        self._queue: Queue[ShardCallbackBatch] = Queue(maxsize=capacity)
        self._poll_seconds = float(poll_seconds)
        self._slow_callback_ns = int(float(slow_callback_seconds) * 1_000_000_000)
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._generation = 0
        self._closed = False
        self._accepted = self._overflow = self._stale = 0
        self._discarded = self._copy_errors = 0
        self._processed = self._callback_errors = 0
        self._callback_attempts = self._callback_successes = 0
        self._max_queue_depth = 0
        self._queue_wait_total_ns = self._queue_wait_max_ns = 0
        self._callback_duration_total_ns = self._callback_duration_max_ns = 0
        self._slow_callbacks = 0
        self._callback_kind_metrics: dict[str, dict[str, int]] = {}
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
            # A non-draining retirement advances the generation as well as
            # closing the adapter. Check the generation first so late publishes
            # are accounted as stale rather than being hidden by the closed flag.
            if generation != self._generation:
                self._stale += 1
                return False
            if self._closed:
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
        batch = ShardCallbackBatch(generation, detached, monotonic_ns())
        if not batch.invocations:
            return True
        with self._lock:
            if generation != self._generation:
                self._stale += 1
                return False
            if self._closed:
                return False
            try:
                self._queue.put_nowait(batch)
            except Full:
                self._overflow += 1
                self._fail_closed_locked("queue_overflow: shard adapter retired")
                return False
            self._accepted += 1
            self._max_queue_depth = max(self._max_queue_depth, self._queue.qsize())
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
                    queue_wait_ns = max(0, monotonic_ns() - batch.enqueued_at_ns)
                    with self._lock:
                        self._queue_wait_total_ns += queue_wait_ns
                        self._queue_wait_max_ns = max(self._queue_wait_max_ns, queue_wait_ns)
                    for invocation in batch.invocations:
                        # Revalidate generation immediately before each consumer invocation.
                        with self._lock:
                            if batch.generation != self._generation:
                                self._stale += 1
                                break
                            self._callback_attempts += 1
                        callback_started_ns = monotonic_ns()
                        callback_error = None
                        try:
                            invocation.callback(invocation.payload)
                        except Exception as exc:
                            callback_error = exc
                        callback_duration_ns = max(0, monotonic_ns() - callback_started_ns)
                        with self._lock:
                            self._callback_duration_total_ns += callback_duration_ns
                            self._callback_duration_max_ns = max(self._callback_duration_max_ns, callback_duration_ns)
                            kind_metrics = self._callback_kind_metrics.setdefault(
                                str(invocation.callback_kind),
                                {"attempts": 0, "errors": 0, "slow": 0,
                                 "queue_wait_total_ns": 0, "queue_wait_max_ns": 0,
                                 "duration_total_ns": 0, "duration_max_ns": 0},
                            )
                            # Batch wait is shared by its FIFO invocations. Attribute
                            # it to each consumer kind so queue pressure can be
                            # correlated with the same kind-level duration/error data.
                            kind_metrics["queue_wait_total_ns"] += queue_wait_ns
                            kind_metrics["queue_wait_max_ns"] = max(
                                kind_metrics["queue_wait_max_ns"], queue_wait_ns
                            )
                            kind_metrics["attempts"] += 1
                            kind_metrics["duration_total_ns"] += callback_duration_ns
                            kind_metrics["duration_max_ns"] = max(kind_metrics["duration_max_ns"], callback_duration_ns)
                            if callback_duration_ns >= self._slow_callback_ns:
                                self._slow_callbacks += 1
                                kind_metrics["slow"] += 1
                            if callback_error is not None:
                                self._callback_errors += 1
                                kind_metrics["errors"] += 1
                                self._last_error = (
                                    f"{invocation.callback_kind}: "
                                    f"{type(callback_error).__name__}: {callback_error}"
                                )[:240]
                            else:
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
            thread = self._thread
            called_from_worker = thread is current_thread()
            if not drain and not called_from_worker:
                self._generation += 1
            if called_from_worker:
                # A consumer may synchronously trigger manager shutdown from
                # inside its own callback. Joining the current worker would
                # raise RuntimeError (and can abort socket shutdown). Self-stop
                # cannot wait for a drain, so fail closed and let _run observe
                # the generation fence immediately after this invocation.
                # Repeated self-stop calls are idempotent once the worker has
                # already been told to exit; do not advance the fence twice.
                if not self._stop.is_set():
                    self._generation += 1
                    self._discard_pending_locked()
                    self._stop.set()
                return
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
            # A graceful drain that exhausts its shutdown budget must stop
            # dispatching the remainder of a dequeued batch as well as queued
            # batches. The callback already executing cannot be cancelled, but
            # advancing the generation fences every later invocation.
            with self._lock:
                self._generation += 1
                self._discard_pending_locked()
                self._stop.set()
            raise TimeoutError("Angel shard callback worker exceeded shutdown budget")
        with self._lock:
            self._thread = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "generation": self._generation,
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "queue_high_watermark": self._max_queue_depth,
                "queue_wait_total_ns": self._queue_wait_total_ns,
                "queue_wait_max_ns": self._queue_wait_max_ns,
                "accepted_batches": self._accepted,
                "processed_batches": self._processed,
                "stale_batches": self._stale,
                "overflow_batches": self._overflow,
                "discarded_batches": self._discarded,
                "payload_copy_errors": self._copy_errors,
                "callback_attempts": self._callback_attempts,
                "callback_successes": self._callback_successes,
                "callback_errors": self._callback_errors,
                "callback_duration_total_ns": self._callback_duration_total_ns,
                "callback_duration_max_ns": self._callback_duration_max_ns,
                "slow_callback_threshold_ns": self._slow_callback_ns,
                "slow_callback_count": self._slow_callbacks,
                "callback_kind_metrics": {kind: dict(metrics) for kind, metrics in self._callback_kind_metrics.items()},
                "integrity_fault": self._integrity_fault,
                "delivery_status": "DEGRADED" if self._integrity_fault or self._callback_errors else "HEALTHY",
                "last_error": self._last_error,
                "running": bool(self._thread and self._thread.is_alive()),
                "closed": self._closed,
            }
