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
    FUTURES_CONSUMER = "shared-index-market-data:futures"



    def __init__(self, *, manager=None, record_callback: Callable | None = None):
        self._lock = RLock()
        self._spot_feed = CommonStrategyMarketFeed(self.SPOT_CONSUMER, manager=manager, warm_start=True)
        self._options_feed = CommonStrategyMarketFeed(self.OPTIONS_CONSUMER, manager=self._spot_feed.manager, warm_start=True)
        self._futures_feed = CommonStrategyMarketFeed(self.FUTURES_CONSUMER, manager=self._spot_feed.manager, warm_start=True)
        self._callback = record_callback or (lambda _record: None)
        self._spot_keys: tuple[InstrumentKey, ...] = ()
        self._option_keys: tuple[InstrumentKey, ...] = ()
        self._futures_keys: tuple[InstrumentKey, ...] = ()
        self._futures_started = False
        self._spot_started = False
        self._options_started = False



    @staticmethod
    def _reconcile_after_rollback(manager) -> None:
        """Best-effort transport reconciliation after restoring registry ownership."""
        reconcile = getattr(manager, "_reconcile", None)
        if callable(reconcile):
            try:
                reconcile()
            except Exception:
                # Preserve the original failure; the manager's supervisor retries.
                pass

    @staticmethod
    def _force_clear_consumer(manager, consumer: str) -> None:
        """Fallback cleanup if clear_consumer fails before or after mutation.

        Remove every callback route owned by this consumer, not just normalized
        callbacks, then restore the registry's desired state. Transport
        reconciliation is requested separately by the caller.
        """
        manager.registry.clear_consumer(consumer)
        for attribute in ("_callbacks", "_record_callbacks", "_record_observers", "_transport_observers", "callbacks"):
            routes = getattr(manager, attribute, None)
            if isinstance(routes, dict):
                routes.pop(consumer, None)

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
            required_expiry_type = "WEEKLY" if descriptor.underlying == "NIFTY" else "MONTHLY"
            if str(descriptor.expiry_type or "").strip().upper() != required_expiry_type:
                raise ValueError(
                    f"{descriptor.underlying} options must use {required_expiry_type} expiry"
                )
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
    def _restore_owner_keys(manager, consumer: str, old_keys, new_keys, mode: int) -> None:
        """Restore registry ownership after a transport mutation failed partially."""
        old_keys = tuple(old_keys)
        added = tuple(key for key in new_keys if key not in set(old_keys))
        # Try the transport-facing API first so managers with bookkeeping
        # mirrors update too. If another reconcile fails, force the registry
        # back to the desired state and let the supervisor retry.
        try:
            manager.subscribe(consumer, list(old_keys), mode=mode)
        except Exception:
            pass
        try:
            if added:
                manager.unsubscribe(consumer, list(added))
        except Exception:
            pass
        for key in old_keys:
            manager.registry.subscribe(consumer, key, mode)
        for key in added:
            manager.registry.unsubscribe(consumer, key)
        manager.registry.unregister_many(added)
        SharedIndexFeedLifecycle._reconcile_after_rollback(manager)

    @staticmethod
    def _validated_descriptors(descriptors: Iterable[InstrumentDescriptor]) -> tuple[InstrumentDescriptor, ...]:
        items = tuple(descriptors)
        if not items:
            raise ValueError("subscription descriptor set must not be empty")
        tokens = [str(d.key.token).strip() for d in items]
        if any(not token for token in tokens):
            raise ValueError("subscription token must not be empty")
        if len(set(tokens)) != len(tokens):
            raise ValueError("duplicate subscription tokens in descriptor set")
        if len({d.key for d in items}) != len(items):
            raise ValueError("duplicate instruments in descriptor set")
        return items



    def start_spot(self, descriptors: Iterable[InstrumentDescriptor]) -> tuple[InstrumentKey, ...]:
        items = self._validated_descriptors(descriptors)
        if len(items) != 2 or {d.underlying for d in items} != {"NIFTY", "BANKNIFTY"}:
            raise ValueError("spot subscription must be exactly NIFTY and BANKNIFTY")
        if any(
            d.instrument_type != "index"
            or d.exchange.strip().upper() != "NSE"
            or d.segment.strip().upper() != "NSE"
            or d.key.exchange.strip().upper() != "NSE"
            or d.key.segment.strip().upper() != "NSE"
            for d in items
        ):
            raise ValueError("spot descriptors must be NSE index instruments")
        with self._lock:
            if self._spot_started:
                raise RuntimeError("spot subscription owner is already active")
            manager = self._spot_feed.manager
            registered_keys = [d.key for d in items if manager.registry.get(d.key) is None]
            try:
                keys = self._spot_feed.start(items, self._callback)
            except Exception:
                # A failed subscribe/reconcile can leave registry state mutated.
                # Remove only this runner's spot owner and descriptors it added.
                try:
                    manager.clear_consumer(self.SPOT_CONSUMER)
                except Exception:
                    self._force_clear_consumer(manager, self.SPOT_CONSUMER)
                manager.registry.unregister_many(registered_keys)
                self._reconcile_after_rollback(manager)
                raise
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
                    self._force_clear_consumer(manager, self.OPTIONS_CONSUMER)
                manager.registry.unregister_many(registered_keys)
                self._reconcile_after_rollback(manager)
                raise
            self._option_keys = keys
            self._options_started = True
            return keys




    def start_futures(self, descriptors: Iterable[InstrumentDescriptor]) -> tuple[InstrumentKey, ...]:
        items = self._validated_descriptors(descriptors)
        if len(items) != 2 or {d.underlying for d in items} != {"NIFTY", "BANKNIFTY"}:
            raise ValueError("futures subscription must be exactly NIFTY and BANKNIFTY")
        if any(d.instrument_type != "future" or d.exchange.strip().upper() != "NFO" or d.segment.strip().upper() != "NFO" or not d.expiry for d in items):
            raise ValueError("futures descriptors must be valid NFO index futures with expiry")
        with self._lock:
            if self._futures_started:
                raise RuntimeError("futures subscription owner is already active")
            manager = self._futures_feed.manager
            registered_keys = [d.key for d in items if manager.registry.get(d.key) is None]
            try:
                keys = self._futures_feed.start(items, self._callback)
            except Exception:
                try:
                    manager.clear_consumer(self.FUTURES_CONSUMER)
                except Exception:
                    self._force_clear_consumer(manager, self.FUTURES_CONSUMER)
                manager.registry.unregister_many(registered_keys)
                self._reconcile_after_rollback(manager)
                raise
            self._futures_keys = keys
            self._futures_started = True
            return keys

    def rotate_futures(self, descriptors: Iterable[InstrumentDescriptor]) -> tuple[InstrumentKey, ...]:
        """Replace futures under the same owner, subscribing new contracts before releasing old."""
        items = self._validated_descriptors(descriptors)
        if len(items) != 2 or {d.underlying for d in items} != {"NIFTY", "BANKNIFTY"}:
            raise ValueError("futures subscription must be exactly NIFTY and BANKNIFTY")
        if any(d.instrument_type != "future" or d.exchange.strip().upper() != "NFO"
               or d.segment.strip().upper() != "NFO" or not d.expiry for d in items):
            raise ValueError("futures descriptors must be valid NFO index futures with expiry")
        new_keys = tuple(d.key for d in items)
        with self._lock:
            if not self._futures_started:
                raise RuntimeError("futures owner must be started before rotation")
            old_keys = self._futures_keys
            manager = self._futures_feed.manager
            fresh = [d for d in items if manager.registry.get(d.key) is None]
            if fresh:
                manager.registry.register_many(fresh)
            try:
                manager.subscribe(self.FUTURES_CONSUMER, list(new_keys), mode=self._futures_feed.mode)
            except Exception:
                self._restore_owner_keys(
                    manager, self.FUTURES_CONSUMER, old_keys, new_keys, self._futures_feed.mode,
                )
                raise
            obsolete = [key for key in old_keys if key not in set(new_keys)]
            if obsolete:
                try:
                    manager.unsubscribe(self.FUTURES_CONSUMER, obsolete)
                except Exception:
                    # Restore desired owner state even if transport operations fail again.
                    self._restore_owner_keys(
                        manager, self.FUTURES_CONSUMER, old_keys, new_keys, self._futures_feed.mode,
                    )
                    raise
            if obsolete:
                # Retire only unconsumed descriptors; registry protects shared refs.
                manager.registry.unregister_many(obsolete)
            self._futures_keys = new_keys
            return new_keys

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
                self._restore_owner_keys(
                    manager, self.OPTIONS_CONSUMER, old_keys, new_keys, self._options_feed.mode,
                )
                raise
            obsolete = [key for key in old_keys if key not in set(new_keys)]
            if obsolete:
                try:
                    manager.unsubscribe(self.OPTIONS_CONSUMER, obsolete)
                except Exception:
                    # Restore desired owner state even if transport operations fail again.
                    self._restore_owner_keys(
                        manager, self.OPTIONS_CONSUMER, old_keys, new_keys, self._options_feed.mode,
                    )
                    raise
            if obsolete:
                # Retire only unconsumed descriptors; registry protects shared refs.
                manager.registry.unregister_many(obsolete)
            self._option_keys = new_keys
            return new_keys



    def active_keys(self) -> tuple[InstrumentKey, ...]:
        """Return currently owned spot and option subscription keys."""
        with self._lock:
            return tuple(dict.fromkeys(self._spot_keys + self._option_keys + self._futures_keys))


    def option_descriptors(self) -> tuple[InstrumentDescriptor, ...]:
        """Return current descriptors owned by the option consumer."""
        with self._lock:
            manager = self._options_feed.manager
            return tuple(
                descriptor for key in self._option_keys
                if (descriptor := manager.registry.get(key)) is not None
            )


    def stop_futures(self) -> None:
        with self._lock:
            try:
                self._futures_feed.stop()
            finally:
                # clear_consumer mutates registry before reconcile; local ownership
                # must reflect that mutation even if transport reconcile raises.
                self._futures_keys = ()
                self._futures_started = False


    def stop_options(self) -> None:
        with self._lock:
            try:
                self._options_feed.stop()
            finally:
                self._option_keys = ()
                self._options_started = False


    def stop_spot(self) -> None:
        with self._lock:
            try:
                self._spot_feed.stop()
            finally:
                self._spot_keys = ()
                self._spot_started = False


    def stop(self) -> None:
        # Hold the lifecycle lock across the full OFF barrier so concurrent ON
        # cannot interleave between independent owner teardowns.
        with self._lock:
            errors = []
            for stop_owner in (self.stop_options, self.stop_futures, self.stop_spot):
                try:
                    stop_owner()
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise errors[0]



    def snapshot(self) -> dict:
        with self._lock:
            return {
                "spot_active": self._spot_started,
                "spot_instruments": len(self._spot_keys),
                "options_active": self._options_started,
                "options_instruments": len(self._option_keys),
                "futures_active": self._futures_started,
                "futures_instruments": len(self._futures_keys),
                "futures_consumer": self.FUTURES_CONSUMER,
                "spot_consumer": self.SPOT_CONSUMER,
                "options_consumer": self.OPTIONS_CONSUMER,
                "shared_websocket": self._spot_feed.snapshot(),
            }
