"""Bounded local fan-out from SharedIndexMarketDataRunner to strategy adapters.

This adapter consumes runner-published records only. It never opens or
subscribes to a broker WebSocket and never polls the shared cache.
"""
from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Lock, RLock, Thread
from typing import Any, Callable

from .registry import InstrumentRegistry


class SharedIndexRunnerLocalFeedManager:
    def __init__(self, runner: Any, *, queue_capacity: int = 2048) -> None:
        if isinstance(queue_capacity, bool) or int(queue_capacity) < 1:
            raise ValueError("queue_capacity must be a positive integer")
        self.runner = runner
        self.registry = InstrumentRegistry()
        self._queue_capacity = int(queue_capacity)
        self._queue: Queue[Any] = Queue(maxsize=self._queue_capacity)
        self._lock = RLock()
        self._dispatch_lock = Lock()
        self._callbacks: dict[str, Callable[[Any], None]] = {}
        self._owners: dict[str, set[str]] = {}
        self._keys: dict[str, Any] = {}
        self._last_timestamp: dict[tuple[str, str], int] = {}
        self._closed = False
        self._attached = False
        self._thread: Thread | None = None
        self._delivered = 0
        self._callback_errors = 0
        self._queue_dropped = 0

    def attach(self) -> None:
        """Route the runner's already-accepted records into the bounded queue."""
        with self._lock:
            if self._closed:
                self._queue = Queue(maxsize=self._queue_capacity)
                self._thread = None
                self._closed = False
                self._attached = False
            if self._attached:
                return
            state = getattr(self.runner, "state", None)
            if state is None or not hasattr(state, "_publish"):
                raise TypeError("runner state does not expose the record publication hook")
            state._publish = self.on_record
            self._attached = True

    def on_record(self, record: Any) -> bool:
        """Non-blocking producer hook; invoked only by Shared Runner state."""
        key = getattr(getattr(record, "instrument", None), "value", "")
        with self._lock:
            if self._closed or not self._attached or key not in self._owners:
                return False
        try:
            self._queue.put_nowait(record)
            return True
        except Full:
            with self._lock:
                self._queue_dropped += 1
            return False

    def register_normalized_callback(self, consumer: str, callback: Callable[[Any], None]) -> None:
        name = str(consumer or "").strip()
        if not name or not callable(callback):
            raise ValueError("consumer and callable callback are required")
        with self._lock:
            if self._closed:
                raise RuntimeError("shared runner local feed manager is closed")
            self._callbacks[name] = callback

    def unregister_normalized_callback(self, consumer: str) -> None:
        name = str(consumer or "").strip()
        with self._lock:
            self._callbacks.pop(name, None)
            for key in tuple(self._last_timestamp):
                if key[0] == name:
                    self._last_timestamp.pop(key, None)

    def subscribe(self, consumer: str, keys, mode: int = 1):
        name = str(consumer or "").strip()
        items = tuple(keys or ())
        if not name or not items:
            raise ValueError("consumer and instrument keys are required")
        state = getattr(self.runner, "state", None)
        allows = getattr(state, "is_instrument_allowed", None)
        if not callable(allows):
            raise TypeError("Shared Runner state does not expose active instrument ownership")
        rejected = [key.value for key in items if not allows(key)]
        if rejected:
            raise PermissionError(
                "local strategy consumers may subscribe only to instruments currently owned "
                f"by SharedIndexMarketDataRunner; rejected={rejected[:8]}"
            )
        with self._lock:
            if self._closed:
                raise RuntimeError("shared runner local feed manager is closed")
            for key in items:
                self.registry.subscribe(name, key, mode)
                self._keys[key.value] = key
                self._owners.setdefault(key.value, set()).add(name)
            self._ensure_worker_locked()
        return self.registry.subscriptions()

    def unsubscribe(self, consumer: str, keys=None) -> None:
        name = str(consumer or "").strip()
        with self._lock:
            targets = tuple(keys) if keys is not None else self.registry.active_keys()
            for key in targets:
                self.registry.unsubscribe(name, key)
                owners = self._owners.get(key.value)
                if owners is not None:
                    owners.discard(name)
                    if not owners:
                        self._owners.pop(key.value, None)
                        self._keys.pop(key.value, None)
            self.registry.unregister_many(
                key for key in targets if key.value not in self._owners
            )

    def clear_consumer(self, consumer: str) -> None:
        name = str(consumer or "").strip()
        with self._lock:
            owned = tuple(
                key for key, consumers in self._owners.items() if name in consumers
            )
            self.registry.clear_consumer(name)
            self._callbacks.pop(name, None)
            for key in owned:
                consumers = self._owners.get(key)
                if consumers is not None:
                    consumers.discard(name)
                    if not consumers:
                        self._owners.pop(key, None)
                        self._last_timestamp.pop((name, key), None)
                        local_key = self._keys.pop(key, None)
                        if local_key is not None:
                            self.registry.unregister_many((local_key,))
            for identity in tuple(self._last_timestamp):
                if identity[0] == name:
                    self._last_timestamp.pop(identity, None)

    def _ensure_worker_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = Thread(
            target=self._dispatch,
            name="shared-index-local-fanout",
            daemon=True,
        )
        self._thread.start()

    def _dispatch(self) -> None:
        while True:
            try:
                record = self._queue.get(timeout=0.25)
            except Empty:
                with self._lock:
                    if self._closed:
                        return
                continue
            if record is None:
                self._queue.task_done()
                return
            key = getattr(getattr(record, "instrument", None), "value", "")
            timestamp_ns = getattr(record, "timestamp_ns", 0)
            with self._lock:
                consumers = tuple(self._owners.get(key, ()))
                callbacks = tuple(
                    (name, self._callbacks.get(name)) for name in consumers
                )
            for name, callback in callbacks:
                if callback is None:
                    continue
                identity = (name, key)
                with self._dispatch_lock:
                    with self._lock:
                        previous = self._last_timestamp.get(identity, 0)
                        if timestamp_ns <= previous:
                            continue
                        self._last_timestamp[identity] = timestamp_ns
                    try:
                        callback(record)
                        with self._lock:
                            self._delivered += 1
                    except Exception:
                        with self._lock:
                            self._callback_errors += 1
            self._queue.task_done()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = self.registry.subscriptions()
            return {
                "source": "shared-index-market-data-runner",
                "attached": self._attached,
                "running": bool(self._thread and self._thread.is_alive() and not self._closed),
                "consumer_count": len({name for item in active for name in item.consumers}),
                "instrument_count": len(active),
                "delivered_records": self._delivered,
                "callback_errors": self._callback_errors,
                "queue_dropped": self._queue_dropped,
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._attached = False
            local_keys = tuple(self._keys.values())
            self._owners.clear()
            self._keys.clear()
            self._callbacks.clear()
            self._last_timestamp.clear()
            for subscription in self.registry.subscriptions():
                for name in subscription.consumers:
                    self.registry.clear_consumer(name)
            self.registry.unregister_many(local_keys)
            thread = self._thread
        # The queue is bounded; shutdown must not block behind a saturated queue.
        try:
            self._queue.put_nowait(None)
        except Full:
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except Empty:
                    break
            try:
                self._queue.put_nowait(None)
            except Full:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
