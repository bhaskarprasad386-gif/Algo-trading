"""Bounded, isolated desired-subscription lifecycle for Early Impulse Pro.

This controller is intentionally dependency-injected. It is not yet wired to the
shared Shoonya supervisor because that supervisor's unsubscribe API owns adapter
subscriptions globally; token ownership/ref-count isolation must be integrated
before this controller can safely mutate the live socket.
"""
from __future__ import annotations

from threading import RLock
from typing import Callable, Iterable

from app.market_data.registry import InstrumentDescriptor


ALLOWED_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"})
ALLOWED_EXCHANGES = frozenset({"NSE", "NFO", "BSE", "BFO"})
MAX_UNIVERSE = 150
MAX_OPTION_EXPIRIES_PER_UNDERLYING = 2


class EarlyImpulseSubscriptionLifecycle:
    """Validate and track a bounded scanner-only subscription set.

    Injected subscribe/unsubscribe functions should be consumer-scoped/ref-counted.
    No broker order or signal operation is performed here.
    """

    def __init__(
        self,
        *,
        subscribe: Callable[[tuple[InstrumentDescriptor, ...]], int] | None = None,
        unsubscribe: Callable[[tuple[InstrumentDescriptor, ...]], int] | None = None,
        max_universe: int = MAX_UNIVERSE,
    ) -> None:
        if isinstance(max_universe, bool) or not isinstance(max_universe, int) or not 1 <= max_universe <= MAX_UNIVERSE:
            raise ValueError(f"max_universe must be between 1 and {MAX_UNIVERSE}")
        self.max_universe = max_universe
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe
        self._lock = RLock()
        self._desired: dict[tuple[str, str], InstrumentDescriptor] = {}
        self._enabled = False

    @staticmethod
    def _key(item: InstrumentDescriptor) -> tuple[str, str]:
        return (item.key.exchange.strip().upper(), item.key.token.strip())

    @staticmethod
    def validate(items: Iterable[InstrumentDescriptor]) -> tuple[InstrumentDescriptor, ...]:
        batch = tuple(items)
        if not batch:
            raise ValueError("subscription universe cannot be empty")
        seen: dict[tuple[str, str], InstrumentDescriptor] = {}
        expiry_sets: dict[str, set[str]] = {}
        for item in batch:
            if not isinstance(item, InstrumentDescriptor):
                raise TypeError("all universe entries must be InstrumentDescriptor")
            exchange, segment, token = (str(item.key.exchange).strip().upper(), str(item.key.segment).strip().upper(), str(item.key.token).strip())
            if exchange not in ALLOWED_EXCHANGES or not segment or not token:
                raise ValueError("unsupported exchange/segment or empty token")
            if item.instrument_type not in {"index", "future", "option"}:
                raise ValueError("Early Impulse supports index, future, and option instruments only")
            underlying = (item.underlying or item.symbol).strip().upper()
            if underlying not in ALLOWED_UNDERLYINGS:
                raise ValueError("unsupported underlying")
            if item.instrument_type == "option":
                if item.expiry is None or not str(item.expiry).strip() or item.strike is None or item.strike <= 0 or item.option_type not in {"CE", "PE"}:
                    raise ValueError("option descriptor requires valid expiry, strike and CE/PE")
                expiry_sets.setdefault(underlying, set()).add(str(item.expiry).strip())
            elif item.option_type is not None or item.strike is not None:
                raise ValueError("strike and option_type are valid only for options")
            key = (exchange, token)
            if key in seen:
                if seen[key] != item:
                    raise ValueError("duplicate token with conflicting descriptors in requested universe")
                raise ValueError("duplicate token in requested universe")
            seen[key] = item
        if any(len(expiries) > MAX_OPTION_EXPIRIES_PER_UNDERLYING for expiries in expiry_sets.values()):
            raise ValueError(f"maximum {MAX_OPTION_EXPIRIES_PER_UNDERLYING} option expiries per underlying")
        return batch

    def set_universe(self, items: Iterable[InstrumentDescriptor]) -> dict:
        batch = self.validate(items)
        if len(batch) > self.max_universe:
            raise ValueError(f"universe cap exceeded ({self.max_universe})")
        staged = {self._key(item): item for item in batch}
        with self._lock:
            if self._enabled and self._subscribe is None:
                raise RuntimeError("subscription transport is not wired")
            old = self._desired
            added = tuple(item for key, item in staged.items() if key not in old)
            removed = tuple(item for key, item in old.items() if key not in staged)
            if self._enabled:
                if removed and self._unsubscribe is None:
                    raise RuntimeError("consumer-scoped unsubscribe transport is not wired")
                if added:
                    subscribed = self._subscribe(added)
                    if subscribed != len(added):
                        raise RuntimeError("consumer subscribe returned an incomplete count")
                try:
                    if removed:
                        unsubscribed = self._unsubscribe(removed)
                        if unsubscribed != len(removed):
                            raise RuntimeError("consumer unsubscribe returned an incomplete count")
                except Exception:
                    # Added physical tokens may need a consumer-scoped rollback.
                    if added and self._unsubscribe:
                        rolled_back = self._unsubscribe(added)
                        if rolled_back != len(added):
                            raise RuntimeError("consumer subscribe rollback returned an incomplete count")
                    raise
            self._desired = staged
            return {"configured": len(staged), "added": len(added), "removed": len(removed), "enabled": self._enabled}

    def enable(self) -> dict:
        with self._lock:
            if self._enabled:
                return self.snapshot()
            if not self._desired:
                raise ValueError("configure an instrument universe before enabling")
            if self._subscribe is None:
                raise RuntimeError("subscription transport is not wired")
            subscribed = self._subscribe(tuple(self._desired.values()))
            if subscribed != len(self._desired):
                raise RuntimeError("consumer subscribe returned an incomplete count")
            self._enabled = True
            return self.snapshot()

    def disable(self) -> dict:
        with self._lock:
            if not self._enabled:
                return self.snapshot()
            if self._unsubscribe is None:
                raise RuntimeError("consumer-scoped unsubscribe transport is not wired")
            unsubscribed = self._unsubscribe(tuple(self._desired.values()))
            if unsubscribed != len(self._desired):
                raise RuntimeError("consumer unsubscribe returned an incomplete count")
            self._enabled = False
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "strategy": "EARLY_IMPULSE_PRO",
                "configured_instruments": len(self._desired),
                "max_universe": self.max_universe,
                "enabled": self._enabled,
                "transport_wired": self._subscribe is not None and self._unsubscribe is not None,
                "instrument_keys": sorted(f"{e}:{t}" for e, t in self._desired),
                "scanner_enabled": False,
                "signals_enabled": False,
                "order_routing_enabled": False,
            }


class SubscriptionOwnershipConflict(RuntimeError):
    """Known preflight rejection before any physical adapter mutation."""


class ShoonyaConsumerSubscriptionMux:
    """Consumer ownership/ref-count gate for shared physical Shoonya tokens.

    Calls the adapter only for zero-to-one and one-to-zero ownership transitions.
    It quarantines tokens when adapter mutations report ambiguous results.
    """
    def __init__(self, *, subscribe_physical: Callable[[tuple[InstrumentDescriptor, ...]], int],
                 unsubscribe_physical: Callable[[tuple[InstrumentDescriptor, ...]], int],
                 max_tokens: int = 500):
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 500:
            raise ValueError("max_tokens must be 1..500")
        self._physical_subscribe = subscribe_physical
        self._physical_unsubscribe = unsubscribe_physical
        self._max_tokens = max_tokens
        self._lock = RLock()
        self._owners: dict[tuple[str, str], dict[str, InstrumentDescriptor]] = {}
        # Adapter may partially mutate before raising/returning short count.
        # Keep ambiguous tokens quarantined; never guess their physical state.
        self._uncertain: dict[tuple[str, str], InstrumentDescriptor] = {}
        self._reconciliation_required = False

    @staticmethod
    def _identity(item: InstrumentDescriptor) -> tuple[str, str]:
        return (item.key.exchange.strip().upper(), item.key.token.strip())

    def subscribe(self, consumer: str, descriptors: Iterable[InstrumentDescriptor]) -> int:
        name = str(consumer).strip()
        if not name:
            raise ValueError("consumer name required")
        batch = tuple(descriptors)
        if not batch:
            return 0
        if any(not isinstance(item, InstrumentDescriptor) for item in batch):
            raise TypeError("all subscription entries must be InstrumentDescriptor")
        staged_keys = [self._identity(item) for item in batch]
        if any(not exchange or not token for exchange, token in staged_keys):
            raise ValueError("exchange and token are required for physical subscription identity")
        if len(staged_keys) != len(set(staged_keys)):
            raise ValueError("duplicate token in consumer request")
        with self._lock:
            if self._reconciliation_required or any(key in self._uncertain for key in staged_keys):
                raise RuntimeError("physical subscription state uncertain; reconcile before reuse")
            staged = {key: dict(owners) for key, owners in self._owners.items()}
            physical_add = []
            for key, item in zip(staged_keys, batch):
                existing_owners = staged.get(key, {})
                if existing_owners and any(value != item for value in existing_owners.values()):
                    raise ValueError(f"descriptor conflict for physical token {key}")
                if name in existing_owners:
                    if existing_owners[name] != item:
                        raise ValueError("consumer cannot replace an owned descriptor in-place")
                    continue
                if not existing_owners:
                    physical_add.append(item)
                existing_owners[name] = item
                staged[key] = existing_owners
            if len(staged) > self._max_tokens:
                raise ValueError(f"physical token cap exceeded ({self._max_tokens})")
            if physical_add:
                physical_keys = [self._identity(item) for item in physical_add]
                try:
                    added = self._physical_subscribe(tuple(physical_add))
                    if added != len(physical_add):
                        raise RuntimeError("physical subscribe returned an incomplete count")
                except SubscriptionOwnershipConflict:
                    # A supervisor preflight rejected ownership before adapter
                    # mutation; physical state is known and needs no quarantine.
                    raise
                except Exception as exc:
                    self._uncertain.update(zip(physical_keys, physical_add))
                    self._reconciliation_required = True
                    raise RuntimeError("physical subscribe partially applied or uncertain; tokens quarantined") from exc
            self._owners = staged
            return len(batch)

    def unsubscribe(self, consumer: str, descriptors: Iterable[InstrumentDescriptor]) -> int:
        name = str(consumer).strip()
        batch = tuple(descriptors)
        if not name or not batch:
            return 0
        if any(not isinstance(item, InstrumentDescriptor) for item in batch):
            raise TypeError("all unsubscribe entries must be InstrumentDescriptor")
        keys = [self._identity(item) for item in batch]
        if any(not exchange or not token for exchange, token in keys):
            raise ValueError("exchange and token are required for physical subscription identity")
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate token in consumer unsubscribe request")
        with self._lock:
            staged = {key: dict(owners) for key, owners in self._owners.items()}
            physical_remove = []
            removed_count = 0
            for item in batch:
                key = self._identity(item)
                if key in self._uncertain:
                    raise RuntimeError("physical subscription state uncertain; reconcile before removal")
                owners = staged.get(key)
                if owners is None or name not in owners:
                    continue
                if owners[name] != item:
                    raise ValueError("unsubscribe descriptor does not match owned descriptor")
                owners.pop(name)
                removed_count += 1
                if not owners:
                    physical_remove.append(item)
                    staged.pop(key, None)
            if physical_remove:
                physical_keys = [self._identity(item) for item in physical_remove]
                try:
                    removed = self._physical_unsubscribe(tuple(physical_remove))
                    if removed != len(physical_remove):
                        raise RuntimeError("physical unsubscribe returned an incomplete count")
                except Exception as exc:
                    self._uncertain.update(zip(physical_keys, physical_remove))
                    self._reconciliation_required = True
                    raise RuntimeError("physical unsubscribe partially applied or uncertain; tokens quarantined") from exc
            self._owners = staged
            return removed_count

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "physical_tokens": len(self._owners),
                "consumer_count": len({name for owners in self._owners.values() for name in owners}),
                "max_tokens": self._max_tokens,
                "owners": {":".join(key): sorted(owners) for key, owners in sorted(self._owners.items())},
                "physical_transport_wired": True,
                "uncertain_tokens": sorted(":".join(key) for key in self._uncertain),
                "reconciliation_required": self._reconciliation_required,
            }
