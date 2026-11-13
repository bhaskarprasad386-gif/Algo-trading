"""Bounded, generation-fenced provider ingress primitives.

Transport callbacks should only create an envelope and call publish(). Parsing,
database writes, strategy callbacks and UI fan-out belong to downstream workers.
This module is intentionally not wired into either live broker or scanner yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Lock
from time import monotonic, monotonic_ns, time_ns
from types import MappingProxyType
from typing import Any, Mapping


def _freeze(value: Any) -> Any:
    """Recursively detach and freeze JSON-like provider payloads."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ProviderIngressEnvelope:
    provider: str
    generation: int
    exchange: str
    token: str
    event_type: str
    received_monotonic_ns: int
    received_wall_ns: int
    provider_timestamp_ns: int | None
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        for name in ("provider", "exchange", "token", "event_type"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value.lower() if name == "provider" else value)
        for name in ("generation", "received_monotonic_ns", "received_wall_ns"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.generation < 1:
            raise ValueError("generation must be positive")
        if self.provider_timestamp_ns is not None and (
            isinstance(self.provider_timestamp_ns, bool)
            or not isinstance(self.provider_timestamp_ns, int)
            or self.provider_timestamp_ns < 0
        ):
            raise ValueError("provider_timestamp_ns must be a non-negative integer or None")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        # Detach and recursively freeze nested provider data too.
        object.__setattr__(self, "payload", _freeze(self.payload))

    @classmethod
    def capture(
        cls, *, provider: str, generation: int, exchange: str, token: str,
        event_type: str, payload: Mapping[str, Any],
        provider_timestamp_ns: int | None = None,
    ) -> "ProviderIngressEnvelope":
        return cls(
            provider=provider, generation=generation, exchange=exchange, token=token,
            event_type=event_type, received_monotonic_ns=monotonic_ns(),
            received_wall_ns=time_ns(), provider_timestamp_ns=provider_timestamp_ns,
            payload=payload,
        )


class BoundedProviderIngress:
    """One bounded FIFO lane for one provider/shard and one event class.

    Use distinct instances for market ticks and order lifecycle events. Order
    events must never share a lossy/coalescing tick queue.
    """

    def __init__(self, *, provider: str, capacity: int, event_class: str = "market") -> None:
        if not str(provider).strip():
            raise ValueError("provider is required")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        if event_class not in {"market", "order"}:
            raise ValueError("event_class must be market or order")
        self.provider = str(provider).strip().lower()
        self.event_class = event_class
        self._queue: Queue[ProviderIngressEnvelope] = Queue(maxsize=capacity)
        self._lock = Lock()
        self._generation = 0
        self._closed = False
        self._accepted = self._stale_generation = self._wrong_provider = 0
        self._overflow = 0
        self._stale_dequeued = 0
        self._integrity_fault = False

    def activate_generation(self, generation: int) -> None:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("generation must be a positive integer")
        with self._lock:
            if self._closed:
                raise RuntimeError("ingress is closed")
            if generation <= self._generation:
                raise ValueError("generation must increase monotonically")
            self._generation = generation

    def publish(self, envelope: ProviderIngressEnvelope) -> bool:
        if not isinstance(envelope, ProviderIngressEnvelope):
            raise TypeError("envelope must be ProviderIngressEnvelope")
        with self._lock:
            if self._closed:
                return False
            if envelope.provider != self.provider:
                self._wrong_provider += 1
                return False
            if envelope.generation != self._generation:
                self._stale_generation += 1
                return False
            try:
                self._queue.put_nowait(envelope)
            except Full:
                self._overflow += 1
                self._integrity_fault = True
                return False
            self._accepted += 1
            return True

    def get(self, timeout: float | None = None) -> ProviderIngressEnvelope:
        return self._queue.get(timeout=timeout)

    def get_current(self, timeout: float | None = None) -> ProviderIngressEnvelope:
        """Return only current-generation work, retiring queued frames after reconnect."""
        deadline = None if timeout is None else monotonic() + max(0.0, timeout)
        while True:
            remaining = None if deadline is None else max(0.0, deadline - monotonic())
            try:
                item = self._queue.get(timeout=remaining)
            except Empty:
                raise
            with self._lock:
                current = not self._closed and item.generation == self._generation
                if not current:
                    self._stale_dequeued += 1
            if current:
                return item
            self._queue.task_done()
            if deadline is not None and monotonic() >= deadline:
                raise Empty

    def task_done(self) -> None:
        self._queue.task_done()

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "provider": self.provider,
                "event_class": self.event_class,
                "generation": self._generation,
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "accepted": self._accepted,
                "stale_generation": self._stale_generation,
                "wrong_provider": self._wrong_provider,
                "overflow": self._overflow,
                "stale_dequeued": self._stale_dequeued,
                "integrity_fault": self._integrity_fault,
                "closed": self._closed,
            }
