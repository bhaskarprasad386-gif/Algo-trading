"""Local fan-out adapter from SharedIndexMarketDataRunner to Box Spread consumers.

This adapter never opens a broker socket. It polls the runner's bounded latest
record state and dispatches only records for keys registered by local consumers.
"""
from __future__ import annotations

from threading import Event, RLock, Thread
from time import sleep
from typing import Any, Callable

from .registry import InstrumentRegistry


class SharedIndexRunnerLocalFeedManager:
    def __init__(self, runner: Any, *, poll_seconds: float = 0.05) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.runner = runner
        self.poll_seconds = float(poll_seconds)
        self.registry = InstrumentRegistry()
        self._lock = RLock()
        self._callbacks: dict[str, Callable[[Any], None]] = {}
        self._last_timestamp: dict[tuple[str, str], int] = {}
        self._stop = Event()
        self._thread: Thread | None = None
        self._closed = False
        self._delivered = 0
        self._callback_errors = 0

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
        with self._lock:
            if self._closed:
                raise RuntimeError("shared runner local feed manager is closed")
            for key in items:
                self.registry.subscribe(name, key, mode)
            self._ensure_worker_locked()
        return self.registry.subscriptions()

    def unsubscribe(self, consumer: str, keys=None) -> None:
        name = str(consumer or "").strip()
        with self._lock:
            targets = tuple(keys) if keys is not None else self.registry.active_keys()
            for key in targets:
                self.registry.unsubscribe(name, key)
            self._prune_worker_locked()

    def clear_consumer(self, consumer: str) -> None:
        name = str(consumer or "").strip()
        with self._lock:
            self.registry.clear_consumer(name)
            self._callbacks.pop(name, None)
            for key in tuple(self._last_timestamp):
                if key[0] == name:
                    self._last_timestamp.pop(key, None)
            self._prune_worker_locked()

    def _ensure_worker_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._poll,
            name="box-shared-index-local-feed",
            daemon=True,
        )
        self._thread.start()

    def _prune_worker_locked(self) -> None:
        if self.registry.subscriptions():
            return
        self._stop.set()

    def _poll(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            with self._lock:
                subscriptions = self.registry.subscriptions()
                callbacks = dict(self._callbacks)
            if not subscriptions:
                continue
            for subscription in subscriptions:
                key = subscription.key
                record = self.runner.latest(key.value)
                if record is None:
                    continue
                for consumer in subscription.consumers:
                    callback = callbacks.get(consumer)
                    if callback is None:
                        continue
                    identity = (consumer, key.value)
                    with self._lock:
                        previous = self._last_timestamp.get(identity, 0)
                        if record.timestamp_ns <= previous:
                            continue
                        self._last_timestamp[identity] = record.timestamp_ns
                    try:
                        callback(record)
                        with self._lock:
                            self._delivered += 1
                    except Exception:
                        with self._lock:
                            self._callback_errors += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = self.registry.subscriptions()
            return {
                "source": "shared-index-market-data-runner",
                "running": bool(self._thread and self._thread.is_alive() and not self._stop.is_set()),
                "consumer_count": len({name for item in active for name in item.consumers}),
                "instrument_count": len(active),
                "delivered_records": self._delivered,
                "callback_errors": self._callback_errors,
                "poll_seconds": self.poll_seconds,
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self.registry.clear_consumer("*")
            for name in tuple(self._callbacks):
                self._callbacks.pop(name, None)
            self._stop.set()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
