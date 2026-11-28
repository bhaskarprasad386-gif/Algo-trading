"""Bounded, coalescing control-plane worker for Shared Index Runner maintenance.

Broker callbacks only publish the latest spot record into a two-key mailbox.
Instrument selection, subscription rotation and instrument-master refresh
admission run on this worker, never on the broker callback thread.
"""
from __future__ import annotations

from threading import Event, RLock, Thread, current_thread
from time import monotonic
from typing import Any, Callable


class SharedIndexControlWorker:
    """Single-worker latest-value mailbox; bounded by the two locked indices."""

    def __init__(self, callback: Callable[[dict[str, Any], int], None]) -> None:
        self._callback = callback
        self._lock = RLock()
        self._wakeup = Event()
        self._stop = Event()
        self._thread: Thread | None = None
        self._generation: int | None = None
        self._pending: dict[str, Any] = {}
        self._coalesced = 0
        self._batches = 0
        self._records = 0
        self._failures = 0
        self._last_duration_ms: float | None = None
        self._max_duration_ms = 0.0
        self._last_error: str | None = None

    def start(self, generation: int) -> None:
        with self._lock:
            thread = self._thread
            if thread is not None and thread.is_alive():
                raise RuntimeError("shared index control worker is still running")
            self._stop.clear()
            self._wakeup.clear()
            self._pending.clear()
            self._generation = int(generation)
            thread = Thread(
                target=self._run,
                name="shared-index-control",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                self._generation = None
                self._stop.set()
                raise

    def submit(self, underlying: str, record: Any, generation: int) -> bool:
        symbol = str(underlying or "").strip().upper()
        if symbol not in {"NIFTY", "BANKNIFTY"}:
            return False
        with self._lock:
            if self._stop.is_set() or self._generation != int(generation):
                return False
            previous = self._pending.get(symbol)
            if previous is not None:
                old_ts = getattr(previous, "timestamp_ns", 0)
                new_ts = getattr(record, "timestamp_ns", 0)
                if isinstance(old_ts, int) and isinstance(new_ts, int) and new_ts <= old_ts:
                    return False
                self._coalesced += 1
            self._pending[symbol] = record
            self._wakeup.set()
            return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wakeup.wait()
            self._wakeup.clear()
            if self._stop.is_set():
                break
            with self._lock:
                batch = self._pending
                self._pending = {}
                generation = self._generation
            if not batch or generation is None:
                continue
            started = monotonic()
            try:
                self._callback(batch, generation)
            except Exception as exc:
                with self._lock:
                    self._failures += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"[:240]
            finally:
                elapsed_ms = max(0.0, (monotonic() - started) * 1000.0)
                with self._lock:
                    self._batches += 1
                    self._records += len(batch)
                    self._last_duration_ms = round(elapsed_ms, 3)
                    self._max_duration_ms = max(self._max_duration_ms, elapsed_ms)

    def stop(self, timeout: float = 2.0) -> bool:
        with self._lock:
            self._stop.set()
            self._pending.clear()
            self._wakeup.set()
            thread = self._thread
        if thread is not None and thread is not current_thread() and thread.ident is not None:
            thread.join(timeout=max(0.0, float(timeout)))
        stopped = thread is None or not thread.is_alive()
        with self._lock:
            if stopped and self._thread is thread:
                self._thread = None
                self._generation = None
        return stopped

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            return {
                "status": "RUNNING" if thread is not None and thread.is_alive() and not self._stop.is_set()
                          else "STOPPED" if thread is None or not thread.is_alive()
                          else "STOPPING",
                "pending_symbols": sorted(self._pending),
                "pending_records": len(self._pending),
                "mailbox_capacity": 2,
                "coalesced_updates": self._coalesced,
                "processed_batches": self._batches,
                "processed_records": self._records,
                "failures": self._failures,
                "last_duration_ms": self._last_duration_ms,
                "max_duration_ms": round(self._max_duration_ms, 3),
                "last_error": self._last_error,
            }
