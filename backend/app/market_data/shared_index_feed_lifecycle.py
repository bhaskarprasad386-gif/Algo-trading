"""Lifecycle primitives for a locked-universe shared index data runner.

This module only registers data subscriptions with the process-wide Angel feed.
It never creates a socket or submits orders.
"""
from __future__ import annotations

from threading import RLock
import math
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
    def _validate_locked_options(items: tuple[InstrumentDescriptor, ...]) -> None:
        """Fail closed unless the exact locked symmetric CE/PE strike grids exist."""
        expected = {"NIFTY": 13, "BANKNIFTY": 11}
        by_underlying: dict[str, list[InstrumentDescriptor]] = {name: [] for name in expected}
        for descriptor in items:
            if descriptor.underlying not in expected:
                raise ValueError("option descriptors contain an unlocked underlying")
            if descriptor.instrument_type != "option":
                raise ValueError("locked option set contains a non-option descriptor")
            if descriptor.exchange.strip().upper() != "NFO" or descriptor.segment.strip().upper() != "NFO":
                raise ValueError("option descriptor exchange/segment must be NFO")
            if descriptor.key.exchange.strip().upper() != "NFO" or descriptor.key.segment.strip().upper() != "NFO":
                raise ValueError("option instrument key exchange/segment must be NFO")
            if descriptor.option_type not in {"CE", "PE"}:
                raise ValueError("option_type must be CE or PE")
            if descriptor.strike is None or not isinstance(descriptor.strike, (int, float)) or isinstance(descriptor.strike, bool) or not math.isfinite(float(descriptor.strike)) or descriptor.strike <= 0:
                raise ValueError("option strike must be finite and positive")
            if not descriptor.expiry or not str(descriptor.expiry).strip():
                raise ValueError("option expiry is required")
            if not descriptor.symbol or not str(descriptor.symbol).strip():
                raise ValueError("option symbol is required")
            by_underlying[descriptor.underlying].append(descriptor)
        if len({d.key for d in items}) != len(items) or len({d.key.token for d in items}) != len(items):
            raise ValueError("duplicate option instrument/token")
        for name, strike_count in expected.items():
            group = by_underlying[name]
            if len(group) != strike_count * 2:
                raise ValueError(f"{name} must contain exactly {strike_count * 2} options")
            strikes = sorted({float(d.strike) for d in group})
            if len(strikes) != strike_count:
                raise ValueError(f"{name} must contain {strike_count} unique strikes")
            step = strikes[1] - strikes[0]
            if step <= 0 or any(abs((strikes[i + 1] - strikes[i]) - step) > 1e-7 for i in range(len(strikes) - 1)):
                raise ValueError(f"{name} strikes must have uniform spacing")
            if any(abs(strikes[i] + strikes[-1 - i] - (strikes[0] + strikes[-1])) > 1e-7 for i in range(len(strikes))):
                raise ValueError(f"{name} strike window must be symmetric")
            expiries = {str(d.expiry).strip() for d in group}
            if len(expiries) != 1:
                raise ValueError(f"{name} option expiry must be uniform")
            pairs = {(float(d.strike), d.option_type) for d in group}
            if len(pairs) != len(group) or any((strike, side) not in pairs for strike in strikes for side in ("CE", "PE")):
                raise ValueError(f"{name} requires exactly one CE and PE per strike")

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
        self._validate_locked_options(items)
        with self._lock:
            if self._options_started:
                raise RuntimeError("options subscription owner is already active")
            manager = self._options_feed.manager
            registered_keys = [d.key for d in items if manager.registry.get(d.key) is None]
            try:
                keys = self._options_feed.start(items, self._callback)
            except Exception:
                # CommonStrategyMarketFeed registers descriptors/callback before
                # subscribe/reconcile. A failed initial start must not leave a
                # ghost options consumer or unconsumed descriptors behind.
                try:
                    manager.clear_consumer(self.OPTIONS_CONSUMER)
                except Exception:
                    manager.registry.clear_consumer(self.OPTIONS_CONSUMER)
                    manager._callbacks.pop(self.OPTIONS_CONSUMER, None)
                    manager._record_callbacks.pop(self.OPTIONS_CONSUMER, None)
                    manager._record_observers.pop(self.OPTIONS_CONSUMER, None)
                manager.registry.unregister_many(registered_keys)
                raise
            self._option_keys = keys
            self._options_started = True
            return keys



    def rotate_options(self, descriptors: Iterable[InstrumentDescriptor]) -> tuple[InstrumentKey, ...]:
        items = self._validated_descriptors(descriptors)
        self._validate_locked_options(items)
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
            try:
                manager.subscribe(self.OPTIONS_CONSUMER, list(new_keys), mode=self._options_feed.mode)
            except Exception:
                # subscribe() mutates the registry before reconcile; rollback the
                # newly added consumer refs even when reconcile raises. Preserve
                # all prior option keys and independent spot ownership.
                added_keys = [key for key in new_keys if key not in set(old_keys)]
                try:
                    if added_keys:
                        manager.unsubscribe(self.OPTIONS_CONSUMER, added_keys)
                except Exception:
                    # unsubscribe() also mutates registry before reconcile.
                    for key in added_keys:
                        manager.registry.unsubscribe(self.OPTIONS_CONSUMER, key)
                manager.registry.unregister_many(added_keys)
                raise
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
