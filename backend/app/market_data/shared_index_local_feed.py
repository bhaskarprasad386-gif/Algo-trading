"""In-process fanout for records already owned by SharedIndexMarketDataRunner.

This adapter never opens or manages a broker WebSocket. It only forwards the
runner's accepted NIFTY/BANKNIFTY records to registered local consumers.
"""
from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Event, RLock, Thread, current_thread
from typing import Any, Callable, Iterable

from .contracts import InstrumentKey, MarketDataRecord
from .registry import InstrumentDescriptor, InstrumentRegistry


class SharedIndexRunnerLocalFeedManager:
    """Bounded local consumer fanout attached to a Shared Runner state object."""

    def __init__(self, runner: Any, *, queue_capacity: int = 2048) -> None:
        if isinstance(queue_capacity, bool) or not isinstance(queue_capacity, int) or queue_capacity < 1:
            raise ValueError("queue_capacity must be a positive integer")
        state = getattr(runner, "state", None)
        if state is None or not callable(getattr(state, "is_instrument_allowed", None)):
            raise TypeError("runner must expose SharedIndexMarketDataState")
        self.runner = runner
        self.state = state
        self.registry = InstrumentRegistry()
        self._queue: Queue[MarketDataRecord] = Queue(maxsize=queue_capacity)
        self._lock = RLock()
        self._callbacks: dict[str, Callable[[MarketDataRecord], None]] = {}
        self._subscriptions: dict[str, set[InstrumentKey]] = {}
        self._original_publish: Callable[[Any], None] | None = None
        self._publish_wrapper: Callable[[Any], None] | None = None
        self._attached = False
        self._closed = False
        self._stop = Event()
        self._worker: Thread | None = None
        self._records_dispatched = 0
        self._queue_dropped = 0
        self._callback_errors = 0

    def attach(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("local feed manager is closed")
            if self._attached:
                return
            original = getattr(self.state, "_publish", None)
            if not callable(original):
                raise TypeError("SharedIndexMarketDataState must expose its publish callback")
            self._original_publish = original

            def publish(record: Any) -> None:
                try:
                    original(record)
                finally:
                    self._on_record(record)

            self._publish_wrapper = publish
            self.state._publish = publish
            self._attached = True

    def validate_consumer(self, consumer: str) -> str:
        normalized = str(consumer or "").strip()
        if not normalized:
            raise ValueError("consumer is required")
        return normalized

    def validate_descriptor(self, descriptor: InstrumentDescriptor) -> None:
        underlying = str(getattr(descriptor, "underlying", "") or "").strip().upper()
        kind = str(getattr(descriptor, "instrument_type", "") or "").strip().lower()
        if underlying not in {"NIFTY", "BANKNIFTY"} or kind not in {"index", "option", "future"}:
            raise PermissionError("local feed accepts only Shared Runner NIFTY/BANKNIFTY instruments")
        if not self.state.is_instrument_allowed(descriptor.key):
            raise PermissionError(
                f"instrument {descriptor.key.value} is not currently owned by SharedIndexMarketDataRunner"
            )

    def register_normalized_callback(self, consumer: str, callback: Callable[[MarketDataRecord], None]) -> None:
        name = self.validate_consumer(consumer)
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            if self._closed:
                raise RuntimeError("local feed manager is closed")
            self._callbacks[name] = callback

    def subscribe(self, consumer: str, keys: Iterable[InstrumentKey], *, mode: int = 3) -> tuple[InstrumentKey, ...]:
        name = self.validate_consumer(consumer)
        normalized = tuple(keys)
        if not normalized:
            raise ValueError("at least one instrument is required")
        if mode not in {1, 2, 3, 4}:
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        for key in normalized:
            if not self.state.is_instrument_allowed(key):
                raise PermissionError(
                    f"instrument {key.value} is not currently owned by SharedIndexMarketDataRunner"
                )
        with self._lock:
            if self._closed:
                raise RuntimeError("local feed manager is closed")
            self._subscriptions[name] = {key.value for key in normalized}
            self._ensure_worker_locked()
        return normalized

    def clear_consumer(self, consumer: str) -> None:
        name = str(consumer or "").strip()
        with self._lock:
            self._callbacks.pop(name, None)
            self._subscriptions.pop(name, None)

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = Thread(
            target=self._dispatch_loop,
            name="shared-index-local-feed",
            daemon=True,
        )
        self._worker.start()

    def _on_record(self, record: Any) -> None:
        key = getattr(getattr(record, "instrument", None), "value", "")
        with self._lock:
            if self._closed or not any(
                any(item.value == key for item in keys)
                for keys in self._subscriptions.values()
            ):
                return
        try:
            self._queue.put_nowait(record)
        except Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except Empty:
                pass
            with self._lock:
                self._queue_dropped += 1
            try:
                self._queue.put_nowait(record)
            except Full:
                with self._lock:
                    self._queue_dropped += 1

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                record = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                key = record.instrument.value
                with self._lock:
                    callbacks = tuple(
                        callback
                        for consumer, callback in self._callbacks.items()
                        if key in self._subscriptions.get(consumer, set())
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

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "attached": self._attached,
                "closed": self._closed,
                "consumer_count": len(self._callbacks),
                "subscription_count": sum(len(keys) for keys in self._subscriptions.values()),
                "records_dispatched": self._records_dispatched,
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "queue_dropped": self._queue_dropped,
                "callback_errors": self._callback_errors,
                "owns_broker_websocket": False,
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._callbacks.clear()
            self._subscriptions.clear()
            if self._attached and self._publish_wrapper is not None and self._original_publish is not None:
                if getattr(self.state, "_publish", None) is self._publish_wrapper:
                    self.state._publish = self._original_publish
            self._attached = False
            self._stop.set()
            worker = self._worker
        if worker is not None and worker is not current_thread():
            worker.join(timeout=1.0)
