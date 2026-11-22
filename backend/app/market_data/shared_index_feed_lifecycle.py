"""Lifecycle primitives for a locked-universe shared index data runner.

This module only registers data subscriptions with the process-wide Angel feed.
It never creates a socket or submits orders.
"""
from __future__ import annotations

from threading import RLock
from typing import Callable, Iterable

from .common_strategy_feed import CommonStrategyMarketFeed
from .contracts import InstrumentKey
from .registry import InstrumentDescriptor



class SharedIndexFeedLifecycle:
    """Maintain independent spot/options subscription owners on the shared feed."""

    SPOT_CONSUMER = "shared-index-market-data:spot"
    OPTIONS_CONSUMER = "shared-index-market-data:options"



    def __init__(self, *, manager=None, record_callback: Callable | None = None):
        self._lock = RLock()
        self._spot_feed = CommonStrategyMarketFeed(self.SPOT_CONSUMER, manager=manager, warm_start=True)
        self._options_feed = CommonStrategyMarketFeed(self.OPTIONS_CONSUMER, manager=self._spot_feed.manager, warm_start=True)
        self._callback = record_callback or (lambda _record: None)
        self._spot_keys: tuple[InstrumentKey, ...] = ()
        self._option_keys: tuple[InstrumentKey, ...] = ()
        self._spot_started = False
        self._options_started = False



    @staticmethod
    def _validated_descriptors(descriptors: Iterable[InstrumentDescriptor]) -> tuple[InstrumentDescriptor, ...]:
        items = tuple(descriptors)
        if not items:
            raise ValueError("subscription descriptor set must not be empty")
        if len({d.key for d in items}) != len(items):
            raise ValueError("duplicate instruments in descriptor set")
        return items



    def start_spot(self, descriptors: Iterable[InstrumentDescriptor]) -> tuple[InstrumentKey, ...]:
        items = self._validated_descriptors(descriptors)
        if len(items) != 2 or {d.underlying for d in items} != {"NIFTY", "BANKNIFTY"}:
            raise ValueError("spot subscription must be exactly NIFTY and BANKNIFTY")
        with self._lock:
            if self._spot_started:
                raise RuntimeError("spot subscription owner is already active")
            keys = self._spot_feed.start(items, self._callback)
            self._spot_keys = keys
            self._spot_started = True
            return keys



    def start_options(self, descriptors: Iterable[InstrumentDescriptor]) -> tuple[InstrumentKey, ...]:
        items = self._validated_descriptors(descriptors)
        counts = {"NIFTY": 26, "BANKNIFTY": 22}
        per_underlying = {name: sum(1 for d in items if d.underlying == name) for name in counts}
        if per_underlying != counts or any(d.instrument_type != "option" for d in items):
            raise ValueError("option set must contain exactly 26 NIFTY weekly and 22 BANKNIFTY monthly contracts")
        with self._lock:
            if self._options_started:
                raise RuntimeError("options subscription owner is already active")
            keys = self._options_feed.start(items, self._callback)
            self._option_keys = keys
            self._options_started = True
            return keys



    def rotate_options(self, descriptors: Iterable[InstrumentDescriptor]) -> tuple[InstrumentKey, ...]:
        items = self._validated_descriptors(descriptors)
        counts = {"NIFTY": 26, "BANKNIFTY": 22}
        per_underlying = {name: sum(1 for d in items if d.underlying == name) for name in counts}
        if per_underlying != counts or any(d.instrument_type != "option" for d in items):
            raise ValueError("rotation rejected: option set violates locked counts")
        new_keys = tuple(d.key for d in items)
        with self._lock:
            if not self._options_started:
                raise RuntimeError("options owner must be started before rotation")
            old_keys = self._option_keys
            manager = self._options_feed.manager
            fresh = [d for d in items if manager.registry.get(d.key) is None]
            if fresh:
                manager.registry.register_many(fresh)
            # Subscribe first to avoid a deliberate gap; remove obsolete keys only
            # after the new set is active under this independent options owner.
            manager.subscribe(self.OPTIONS_CONSUMER, list(new_keys), mode=self._options_feed.mode)
            obsolete = [key for key in old_keys if key not in set(new_keys)]
            if obsolete:
                manager.unsubscribe(self.OPTIONS_CONSUMER, obsolete)
            self._option_keys = new_keys
            return new_keys



    def stop_options(self) -> None:
        with self._lock:
            self._options_feed.stop()
            self._option_keys = ()
            self._options_started = False



    def stop_spot(self) -> None:
        with self._lock:
            self._spot_feed.stop()
            self._spot_keys = ()
            self._spot_started = False



    def stop(self) -> None:
        # Independent consumers: stopping one never clears the other's registry refs.
        self.stop_options()
        self.stop_spot()



    def snapshot(self) -> dict:
        with self._lock:
            return {
                "spot_active": self._spot_started,
                "spot_instruments": len(self._spot_keys),
                "options_active": self._options_started,
                "options_instruments": len(self._option_keys),
                "spot_consumer": self.SPOT_CONSUMER,
                "options_consumer": self.OPTIONS_CONSUMER,
                "shared_websocket": self._spot_feed.snapshot(),
            }
