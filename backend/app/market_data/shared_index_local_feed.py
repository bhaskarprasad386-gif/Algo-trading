"""In-process fan-out from SharedIndexMarketDataRunner to strategy consumers.

This module deliberately has no broker socket factory, authentication object,
or WebSocket manager. The Shared Runner remains the only production broker-feed
owner; local strategy feeds can subscribe only to its currently active keys.
"""
from __future__ import annotations

from queue import Empty, Full, Queue
from threading import RLock, Thread
from time import time_ns
from typing import Callable, Iterable

from .contracts import InstrumentKey, MarketDataRecord
from .registry import InstrumentRegistry


class SharedIndexRunnerLocalFeedManager:
    """Bounded local consumer fan-out over the Shared Runner's accepted records."""

    _STOP = object()

    def __init__(self, runner, *, queue_capacity: int = 8192) -> None:
        if isinstance(queue_capacity, bool) or not isinstance(queue_capacity, int) or queue_capacity < 1:
            raise ValueError("queue_capacity must be a positive integer")
        self.runner = runner
        self.registry = InstrumentRegistry()
        self._queue: Queue = Queue(maxsize=queue_capacity)
        self._lock = RLock()
        self._callbacks: dict[str, Callable[[MarketDataRecord], None]] = {}
        self._owners: dict[str, set[str]] = {}
        self._keys: dict[str, InstrumentKey] = {}
        self._worker: Thread | None = None
        self._attached = False
        self._closed = False
        self._previous_publish = None
        self._attached_publish = None
        self._queue_dropped = 0
        self._stale_dropped = 0
        self._records_received = 0
        self._records_dispatched = 0
        self._callback_errors = 0

    @staticmethod
    def _consumer_name(consumer: str) -> str:
        value = str(consumer or "").strip()
        if not value:
            raise ValueError("consumer is required")
        return value

    def attach(self) -> None:
        """Attach one non-blocking callback to the runner's normalized state."""
        with self._lock:
            if self._closed:
                raise RuntimeError("shared runner local feed manager is closed")
            if self._attached:
                return
            state = getattr(self.runner, "state", None)
            if state is None or not hasattr(state, "_publish"):
                raise TypeError("SharedIndexMarketDataRunner must expose a publishable state")
            self._previous_publish = state._publish

            def publish(record):
                previous = self._previous_publish
                try:
                    if callable(previous):
                        previous(record)
                finally:
                    self.on_record(record)

            self._attached_publish = publish
            state._publish = publish
            self._attached = True

    def register_normalized_callback(self, consumer: str, callback) -> None:
        name = self._consumer_name(consumer)
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            if self._closed:
                raise RuntimeError("shared runner local feed manager is closed")
            self._callbacks[name] = callback

    def unregister_normalized_callback(self, consumer: str) -> None:
        name = self._consumer_name(consumer)
        with self._lock:
            self._callbacks.pop(name, None)

    def register_normalized_observer(self, consumer: str, callback) -> None:
        # Observers share the same bounded local fan-out and cannot create sockets.
        self.register_normalized_callback(consumer, callback)

    def unregister_normalized_observer(self, consumer: str) -> None:
        self.unregister_normalized_callback(consumer)

    def subscribe(self, consumer: str, keys: Iterable[InstrumentKey], mode: int = 3):
        name = self._consumer_name(consumer)
        items = tuple(keys)
        if not items:
            raise ValueError("at least one instrument key is required")
        if len(set(items)) != len(items):
            raise ValueError("duplicate instrument keys in local subscription")
        if isinstance(mode, bool) or not isinstance(mode, int) or mode not in InstrumentRegistry.VALID_MODES:
            raise ValueError("mode must be one of 1, 2, 3 or 4")

        state = getattr(self.runner, "state", None)
        allows = getattr(state, "is_instrument_allowed", None)
        if not callable(allows):
            raise TypeError("Shared Runner state does not expose active instrument ownership")
        rejected = [key.value for key in items if not allows(key)]
        if rejected:
            with self._lock:
                already_owns_keys = any(name in owners for owners in self._owners.values())
                if not already_owns_keys:
                    self._callbacks.pop(name, None)
                    unused = tuple(key for key in items if key.value not in self._owners)
                    self.registry.unregister_many(unused)
            raise PermissionError(
                "local strategy consumers may subscribe only to instruments currently owned "
                f"by SharedIndexMarketDataRunner; rejected={rejected[:8]}"
            )

        with self._lock:
            if self._closed:
                raise RuntimeError("shared runner local feed manager is closed")
            old = self.registry.snapshot_consumer(name)
            added: list[InstrumentKey] = []
            try:
                for key in items:
                    self.registry.subscribe(name, key, mode)
                    added.append(key)
            except Exception:
                self.registry.restore_consumer(name, old)
                raise
            for key in items:
                value = key.value
                self._keys[value] = key
                self._owners.setdefault(value, set()).add(name)
            self._ensure_worker_locked()
        return items

    def unsubscribe(self, consumer: str, keys: Iterable[InstrumentKey] | None = None) -> int:
        name = self._consumer_name(consumer)
        with self._lock:
            selected = tuple(keys) if keys is not None else tuple(
                key for value, key in self._keys.items()
                if name in self._owners.get(value, set())
            )
            removed = 0
            for key in selected:
                self.registry.unsubscribe(name, key)
                owners = self._owners.get(key.value)
                if owners is not None:
                    owners.discard(name)
                    if not owners:
                        self._owners.pop(key.value, None)
                        self._keys.pop(key.value, None)
                        self.registry.unregister_many((key,))
                removed += 1
            return removed

    def clear_consumer(self, consumer: str) -> int:
        name = self._consumer_name(consumer)
        with self._lock:
            removed = self.registry.clear_consumer(name)
            self._callbacks.pop(name, None)
            for value, owners in tuple(self._owners.items()):
                owners.discard(name)
                if not owners:
                    key = self._keys.pop(value, None)
                    self._owners.pop(value, None)
                    if key is not None:
                        self.registry.unregister_many((key,))
            return removed

    def on_record(self, record: MarketDataRecord) -> bool:
        if not isinstance(record, MarketDataRecord):
            return False
        with self._lock:
            if self._closed:
                return False
            self._records_received += 1
        try:
            self._queue.put_nowait(record)
            return True
        except Full:
            # Prefer the latest quote over stale backlog without blocking the
            # Shared Runner's broker callback thread.
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except Empty:
                pass
            with self._lock:
                self._queue_dropped += 1
            try:
                self._queue.put_nowait(record)
                return True
            except Full:
                with self._lock:
                    self._queue_dropped += 1
                return False

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = Thread(
            target=self._dispatch_loop,
            name="shared-runner-local-market-data-fanout",
            daemon=True,
        )
        self._worker.start()

    def _dispatch_loop(self) -> None:
        while True:
            try:
                record = self._queue.get(timeout=0.5)
            except Empty:
                with self._lock:
                    if self._closed:
                        return
                continue
            try:
                if record is self._STOP:
                    return
                state = getattr(self.runner, "state", None)
                max_age_ns = getattr(state, "max_age_ns", 5_000_000_000)
                timestamp_ns = getattr(record, "timestamp_ns", 0)
                age_ns = time_ns() - timestamp_ns if isinstance(timestamp_ns, int) and not isinstance(timestamp_ns, bool) else max_age_ns + 1
                if age_ns > max_age_ns or age_ns < -1_000_000_000:
                    with self._lock:
                        self._stale_dropped += 1
                    continue
                key = getattr(getattr(record, "instrument", None), "value", "")
                with self._lock:
                    consumers = tuple(self._owners.get(key, ()))
                    callbacks = tuple(
                        callback for consumer in consumers
                        if (callback := self._callbacks.get(consumer)) is not None
                    )
                for callback in callbacks:
                    try:
                        callback(record)
                        with self._lock:
                            self._records_dispatched += 1
                    except Exception:
                        with self._lock:
                            self._callback_errors += 1
            finally:
                self._queue.task_done()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "attached": self._attached,
                "closed": self._closed,
                "consumer_count": len(self._callbacks),
                "instrument_count": len(self._owners),
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "queue_dropped": self._queue_dropped,
                "stale_dropped": self._stale_dropped,
                "records_received": self._records_received,
                "records_dispatched": self._records_dispatched,
                "callback_errors": self._callback_errors,
                "worker_alive": bool(self._worker and self._worker.is_alive()),
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for consumer in tuple(self._callbacks):
                self.clear_consumer(consumer)
            worker = self._worker
            state = getattr(self.runner, "state", None)
            if state is not None and state._publish is self._attached_publish:
                state._publish = self._previous_publish
            self._attached = False
        if worker is not None and worker.is_alive():
            try:
                self._queue.put_nowait(self._STOP)
            except Full:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except Empty:
                    pass
                self._queue.put_nowait(self._STOP)
            worker.join(timeout=2.0)
