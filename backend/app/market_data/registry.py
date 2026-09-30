"""Shared instrument and subscription registry for the common market-data layer."""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Iterable

from .contracts import InstrumentKey


@dataclass(frozen=True)
class InstrumentDescriptor:
    key: InstrumentKey
    symbol: str
    instrument_type: str
    exchange: str
    segment: str
    expiry: str | None = None
    strike: float | None = None
    option_type: str | None = None
    lot_size: int | None = None
    tick_size: float | None = None


@dataclass(frozen=True)
class Subscription:
    key: InstrumentKey
    consumers: frozenset[str]
    mode: int = 1

    @property
    def ref_count(self) -> int:
        return len(self.consumers)


class InstrumentRegistry:
    """Thread-safe registry that deduplicates instruments and broker subscriptions."""

    VALID_MODES = frozenset({1, 2, 3, 4})

    def __init__(self) -> None:
        self._lock = RLock()
        self._instruments: dict[InstrumentKey, InstrumentDescriptor] = {}
        self._consumers: dict[InstrumentKey, set[str]] = {}
        self._consumer_modes: dict[InstrumentKey, dict[str, int]] = {}

    @staticmethod
    def _normalize_consumer(consumer: str) -> str:
        value = str(consumer).strip()
        if not value:
            raise ValueError("consumer is required")
        return value

    @classmethod
    def _validate_mode(cls, mode: int) -> int:
        if isinstance(mode, bool) or not isinstance(mode, int) or mode not in cls.VALID_MODES:
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        return mode

    def register(self, descriptor: InstrumentDescriptor) -> InstrumentKey:
        if not isinstance(descriptor, InstrumentDescriptor):
            raise TypeError("descriptor must be an InstrumentDescriptor")
        if not isinstance(descriptor.key, InstrumentKey):
            raise TypeError("descriptor.key must be an InstrumentKey")
        with self._lock:
            old = self._instruments.get(descriptor.key)
            if old is not None and old != descriptor:
                raise ValueError(f"conflicting descriptor for {descriptor.key.value}")
            self._instruments[descriptor.key] = descriptor
        return descriptor.key

    def register_many(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        """Register a batch atomically: a conflict leaves the registry unchanged."""
        batch = tuple(descriptors)
        with self._lock:
            staged = dict(self._instruments)
            for descriptor in batch:
                if not isinstance(descriptor, InstrumentDescriptor):
                    raise TypeError("descriptor must be an InstrumentDescriptor")
                if not isinstance(descriptor.key, InstrumentKey):
                    raise TypeError("descriptor.key must be an InstrumentKey")
                old = staged.get(descriptor.key)
                if old is not None and old != descriptor:
                    raise ValueError(f"conflicting descriptor for {descriptor.key.value}")
                staged[descriptor.key] = descriptor
            self._instruments = staged
        return len(batch)

    def subscribe(self, consumer: str, key: InstrumentKey, mode: int = 1) -> Subscription:
        consumer = self._normalize_consumer(consumer)
        mode = self._validate_mode(mode)
        if not isinstance(key, InstrumentKey):
            raise TypeError("key must be an InstrumentKey")
        with self._lock:
            if key not in self._instruments:
                raise KeyError(f"instrument is not registered: {key.value}")
            self._consumers.setdefault(key, set()).add(consumer)
            self._consumer_modes.setdefault(key, {})[consumer] = mode
            return self._subscription_locked(key)

    def unsubscribe(self, consumer: str, key: InstrumentKey) -> Subscription | None:
        consumer = str(consumer).strip()
        with self._lock:
            consumers = self._consumers.get(key)
            if consumers is None:
                return None
            consumers.discard(consumer)
            modes = self._consumer_modes.get(key)
            if modes is not None:
                modes.pop(consumer, None)
            if not consumers:
                self._consumers.pop(key, None)
                self._consumer_modes.pop(key, None)
                return None
            return self._subscription_locked(key)

    def _subscription_locked(self, key: InstrumentKey) -> Subscription:
        consumers = frozenset(self._consumers.get(key, set()))
        modes = self._consumer_modes.get(key, {})
        mode = max((modes.get(c, 1) for c in consumers), default=1)
        return Subscription(key, consumers, mode)

    def get(self, key: InstrumentKey) -> InstrumentDescriptor | None:
        with self._lock:
            return self._instruments.get(key)

    def subscriptions(self) -> tuple[Subscription, ...]:
        with self._lock:
            return tuple(
                self._subscription_locked(k)
                for k in sorted(self._consumers, key=lambda x: x.value)
            )

    def active_keys(self) -> tuple[InstrumentKey, ...]:
        with self._lock:
            return tuple(sorted(self._consumers, key=lambda x: x.value))

    def broker_tokens(self, exchange: str | None = None) -> dict[str, tuple[str, ...]]:
        with self._lock:
            groups: dict[str, list[str]] = {}
            for key in self._consumers:
                if exchange and key.exchange.strip().upper() != exchange.strip().upper():
                    continue
                group = f"{key.exchange.strip()}:{key.segment.strip()}"
                groups.setdefault(group, []).append(key.token.strip())
            return {
                group: tuple(sorted(set(tokens), key=lambda x: (len(x), x)))
                for group, tokens in sorted(groups.items())
            }

    def clear_consumer(self, consumer: str) -> int:
        consumer = str(consumer).strip()
        removed = 0
        with self._lock:
            for key in list(self._consumers):
                consumers = self._consumers[key]
                if consumer in consumers:
                    consumers.remove(consumer)
                    removed += 1
                    modes = self._consumer_modes.get(key)
                    if modes is not None:
                        modes.pop(consumer, None)
                    if not consumers:
                        self._consumers.pop(key, None)
                        self._consumer_modes.pop(key, None)
        return removed
