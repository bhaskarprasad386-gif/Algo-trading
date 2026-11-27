"""Socket-free local fan-out for consumers of SharedIndexMarketDataRunner."""
from __future__ import annotations

from queue import Empty, Full, Queue
from threading import RLock, Thread
from time import time_ns
from typing import Callable, Iterable

from .contracts import InstrumentKey, MarketDataRecord
from .registry import InstrumentRegistry


class SharedIndexRunnerLocalFeedManager:
    """Fan out accepted Shared Runner records without creating broker sessions."""

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
    def _name(consumer: str) -> str:
        value = str(consumer or "").strip()
        if not value:
            raise ValueError("consumer is required")
        return value

    def attach(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("shared runner local feed manager is closed")
            if self._attached:
                return
            state = getattr(self.runner, "state", None)
            if state is None or not hasattr(state, "_publish"):
                raise TypeError("Shared Runner must expose a publishable state")
            self._previous_publish = state._publish

            def publish(record):
                try:
                    if callable(self._previous_publish):
                        self._previous_publish(record)
                finally:
                    self.on_record(record)

            self._attached_publish = publish
            state._publish = publish
            self._attached = True

    def register_normalized_callback(self, consumer: str, callback) -> None:
        name = self._name(consumer)
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            if self._closed:
                raise RuntimeError("shared runner local feed manager is closed")
            self._callbacks[name] = callback

    def unregister_normalized_callback(self, consumer: str) -> None:
        with self._lock:
            self._callbacks.pop(self._name(consumer), None)

    def register_normalized_observer(self, consumer: str, callback) -> None:
        self.register_normalized_callback(consumer, callback)

    def unregister_normalized_observer(self, consumer: str) -> None:
        self.unregister_normalized_callback(consumer)

    def _validate_runner_descriptor(self, descriptor) -> None:
        lifecycle = getattr(self.runner, "lifecycle", None)
        spot_feed = getattr(lifecycle, "_spot_feed", None)
        broker_manager = getattr(spot_feed, "manager", None)
        broker_registry = getattr(broker_manager, "registry", None)
        canonical = broker_registry.get(descriptor.key) if broker_registry is not None else None
        if canonical is None:
            raise PermissionError(
                f"instrument is not registered by SharedIndexMarketDataRunner: {descriptor.key.value}"
            )
        for field in ("instrument_type", "exchange", "segment", "underlying"):
            supplied = str(getattr(descriptor, field, "") or "").strip().upper()
            expected = str(getattr(canonical, field, "") or "").strip().upper()
            if supplied != expected:
                raise PermissionError(f"local descriptor mismatch for {descriptor.key.value}: {field}")
        if str(getattr(descriptor, "instrument_type", "")).strip().lower() == "option":
            for field in ("expiry", "option_type"):
                supplied = str(getattr(descriptor, field, "") or "").strip().upper()
                expected = str(getattr(canonical, field, "") or "").strip().upper()
                if supplied != expected:
                    raise PermissionError(f"local option mismatch for {descriptor.key.value}: {field}")
            supplied_strike = getattr(descriptor, "strike", None)
            expected_strike = getattr(canonical, "strike", None)
            if supplied_strike is None or expected_strike is None or float(supplied_strike) != float(expected_strike):
                raise PermissionError(f"local option mismatch for {descriptor.key.value}: strike")

    def subscribe(self, consumer: str, keys: Iterable[InstrumentKey], mode: int = 3):
        name = self._name(consumer)
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
                if not any(name in owners for owners in self._owners.values()):
                    self._callbacks.pop(name, None)
                    self.registry.unregister_many(tuple(key for key in items if key.value not in self._owners))
            raise PermissionError(
                "local strategy consumers may subscribe only to instruments currently owned "
                f"by SharedIndexMarketDataRunner; rejected={rejected[:8]}"
            )

        try:
            for key in items:
                descriptor = self.registry.get(key)
                if descriptor is None:
                    raise KeyError(f"local instrument is not registered: {key.value}")
                self._validate_runner_descriptor(descriptor)
        except Exception:
            with self._lock:
                if not any(name in owners for owners in self._owners.values()):
                    self._callbacks.pop(name, None)
                    self.registry.unregister_many(tuple(key for key in items if key.value not in self._owners))
            raise

        with self._lock:
            if self._closed:
                raise RuntimeError("shared runner local feed manager is closed")
            previous = self.registry.snapshot_consumer(name)
            try:
                for key in items:
                    self.registry.subscribe(name, key, mode)
            except Exception:
                self.registry.restore_consumer(name, previous)
                raise
            for key in items:
                self._keys[key.value] = key
                self._owners.setdefault(key.value, set()).add(name)
            self._ensure_worker_locked()
        return items

    def unsubscribe(self, consumer: str, keys: Iterable[InstrumentKey] | None = None) -> int:
        name = self._name(consumer)
        with self._lock:
            selected = tuple(keys) if keys is not None else tuple(
                key for value, key in self._keys.items() if name in self._owners.get(value, set())
            )
            for key in selected:
                self.registry.unsubscribe(name, key)
                owners = self._owners.get(key.value)
                if owners is not None:
                    owners.discard(name)
                    if not owners:
                        self._owners.pop(key.value, None)
                        self._keys.pop(key.value, None)
                        self.registry.unregister_many((key,))
            return len(selected)

    def clear_consumer(self, consumer: str) -> int:
        name = self._name(consumer)
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
        if self._worker is None or not self._worker.is_alive():
            self._worker = Thread(target=self._dispatch_loop, name="shared-runner-local-feed", daemon=True)
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
                stamp = getattr(record, "timestamp_ns", 0)
                age_ns = time_ns() - stamp if isinstance(stamp, int) and not isinstance(stamp, bool) else max_age_ns + 1
                if age_ns > max_age_ns or age_ns < -1_000_000_000:
                    with self._lock:
                        self._stale_dropped += 1
                    continue
                key = getattr(getattr(record, "instrument", None), "value", "")
                with self._lock:
                    callbacks = tuple(
                        self._callbacks[consumer]
                        for consumer in self._owners.get(key, ())
                        if consumer in self._callbacks
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
