"""Bounded shadow-only quote parity ingestion worker.

Transport callbacks may submit immutable observation pairs without performing
analysis or SQLite I/O. This worker never publishes executable quotes.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict, deque
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread, current_thread
from time import monotonic_ns
from typing import Any

from .provider_quote_parity import analyze_quote_parity
from .provider_quote_quality import ProviderQuoteObservation
from .provider_quote_parity_store import SQLiteQuoteParityStore


@dataclass(frozen=True)
class QuoteParityEnvelope:
    left: ProviderQuoteObservation
    right: ProviderQuoteObservation
    received_monotonic_ns: int
    observed_at_utc_ns: int
    now_exchange_timestamp_ns: int
    max_age_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.left, ProviderQuoteObservation) or not isinstance(self.right, ProviderQuoteObservation):
            raise TypeError("left and right must be ProviderQuoteObservation")
        for name in ("received_monotonic_ns", "observed_at_utc_ns", "now_exchange_timestamp_ns", "max_age_ns"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.left.contract != self.right.contract:
            raise ValueError("pair must carry the same canonical contract identity")


class BoundedQuoteParityWorker:
    """One bounded FIFO worker for diagnostic analysis and SQLite persistence."""

    def __init__(
        self,
        store: SQLiteQuoteParityStore,
        *,
        capacity: int = 2048,
        name: str = "provider-quote-parity",
    ) -> None:
        if not isinstance(store, SQLiteQuoteParityStore):
            raise TypeError("store must be SQLiteQuoteParityStore")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._store = store
        self._queue: Queue[QuoteParityEnvelope] = Queue(maxsize=capacity)
        self._name = name
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._closed = False
        self._integrity_fault = False
        self._fault_reason: str | None = None
        self._active_generation: dict[str, int] = {}
        self._last_pair_time: OrderedDict[tuple, int] = OrderedDict()
        self._dedupe_capacity = max(1024, capacity * 4)
        self._seen_pair_keys: set[tuple] = set()
        self._seen_pair_order: deque[tuple] = deque()
        self._drain_on_stop = True
        self._counters = {
            "submitted": 0, "processed": 0, "persisted": 0,
            "duplicates": 0, "stale_generation": 0,
            "out_of_order": 0, "queue_overflow": 0,
            "handler_errors": 0, "discarded_on_shutdown": 0,
        }
        self._last_error: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("worker is closed")
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name=self._name, daemon=True)
            self._thread.start()

    def activate_generation(self, provider: str, generation: int) -> None:
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("provider is required")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
            raise ValueError("generation must be a positive integer")
        with self._lock:
            previous = self._active_generation.get(provider)
            if previous is not None and generation <= previous:
                raise ValueError("generation must strictly increase")
            self._active_generation[provider] = generation

    def submit(self, envelope: QuoteParityEnvelope) -> bool:
        """Non-blocking submission. False means rejected; no silent queue eviction."""
        if not isinstance(envelope, QuoteParityEnvelope):
            raise TypeError("envelope must be QuoteParityEnvelope")
        left, right = envelope.left, envelope.right
        with self._lock:
            if self._closed or self._integrity_fault:
                return False
            for observation in (left, right):
                current = self._active_generation.get(observation.provider)
                if current is None or current != observation.generation:
                    self._counters["stale_generation"] += 1
                    return False
            pair = tuple(sorted((left.provider, right.provider)))
            pair_key = (left.contract, pair[0], pair[1])
            event_identity = tuple(sorted((
                (left.provider, left.generation, left.exchange_timestamp_ns or 0),
                (right.provider, right.generation, right.exchange_timestamp_ns or 0),
            )))
            event_key = (pair_key[0], event_identity)
            if event_key in self._seen_pair_keys:
                self._counters["duplicates"] += 1
                return False
            previous_time = self._last_pair_time.get(pair_key)
            if previous_time is not None and envelope.received_monotonic_ns < previous_time:
                self._counters["out_of_order"] += 1
                return False
            try:
                self._queue.put_nowait(envelope)
            except Full:
                self._integrity_fault = True
                self._fault_reason = "queue_overflow"
                self._counters["queue_overflow"] += 1
                return False
            self._seen_pair_keys.add(event_key)
            self._seen_pair_order.append(event_key)
            while len(self._seen_pair_order) > self._dedupe_capacity:
                expired = self._seen_pair_order.popleft()
                self._seen_pair_keys.discard(expired)
            self._last_pair_time[pair_key] = envelope.received_monotonic_ns
            self._last_pair_time.move_to_end(pair_key)
            while len(self._last_pair_time) > self._dedupe_capacity:
                self._last_pair_time.popitem(last=False)
            self._counters["submitted"] += 1
            return True

    def _run(self) -> None:
        while True:
            if self._stop.is_set() and self._queue.empty():
                return
            try:
                envelope = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                with self._lock:
                    if self._stop.is_set() and not self._drain_on_stop:
                        self._counters["discarded_on_shutdown"] += 1
                        continue
                    if self._integrity_fault:
                        self._counters["discarded_on_shutdown"] += 1
                        continue
                    stale = any(
                        self._active_generation.get(obs.provider, obs.generation) != obs.generation
                        for obs in (envelope.left, envelope.right)
                    )
                    if stale:
                        self._counters["stale_generation"] += 1
                        continue
                metrics = analyze_quote_parity(
                    envelope.left, envelope.right,
                    now_monotonic_ns=monotonic_ns(),
                    now_exchange_timestamp_ns=envelope.now_exchange_timestamp_ns,
                    max_age_ns=envelope.max_age_ns,
                )
                self._store.record(metrics, observed_at_utc_ns=envelope.observed_at_utc_ns)
                with self._lock:
                    self._counters["processed"] += 1
                    self._counters["persisted"] += 1
            except Exception as exc:
                with self._lock:
                    self._counters["handler_errors"] += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"[:240]
            finally:
                self._queue.task_done()

    def stop(self, *, drain: bool = True, timeout_seconds: float = 2.0) -> bool:
        if not isinstance(drain, bool):
            raise TypeError("drain must be bool")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        with self._lock:
            self._closed = True
            self._drain_on_stop = drain
            thread = self._thread
            if thread is None and drain and not self._integrity_fault and not self._queue.empty():
                thread = Thread(target=self._run, name=self._name, daemon=True)
                self._thread = thread
                thread.start()
            if not drain or self._integrity_fault:
                while True:
                    try:
                        self._queue.get_nowait()
                        self._queue.task_done()
                        self._counters["discarded_on_shutdown"] += 1
                    except Empty:
                        break
            self._stop.set()
        if thread is not None and thread is not current_thread():
            thread.join(timeout_seconds)
        return thread is None or not thread.is_alive()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._counters,
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "dedupe_entries": len(self._seen_pair_keys),
                "dedupe_capacity": self._dedupe_capacity,
                "pair_order_entries": len(self._last_pair_time),
                "running": self._thread is not None and self._thread.is_alive(),
                "closed": self._closed,
                "integrity_fault": self._integrity_fault,
                "fault_reason": self._fault_reason,
                "last_error": self._last_error,
                "active_generations": dict(self._active_generation),
            }
